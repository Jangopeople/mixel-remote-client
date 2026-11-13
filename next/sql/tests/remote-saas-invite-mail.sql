-- Remote SaaS invitation e-mails (migration 026). One transaction that is rolled back, so it can run
-- against the live database after the migration without leaving data behind. Synthetic accounts only.
--   psql -v ON_ERROR_STOP=1 -f next/sql/tests/remote-saas-invite-mail.sql
\set ON_ERROR_STOP on
\set QUIET on
begin;
set local client_min_messages = notice;
\o /dev/null

create function pg_temp.act(who text) returns void language plpgsql as $$ begin
    perform set_config('request.jwt.claim.sub', '', true);
    perform set_config('request.jwt.claims', case when who is null then '' else json_build_object(
        'sub', current_setting('t.' || who), 'role', 'authenticated', 'aal', 'aal1',
        'amr', json_build_array(json_build_object('method', 'password')))::text end, true);
end $$;
create function pg_temp.id(name text) returns uuid language sql as $$ select current_setting('t.' || name)::uuid $$;
create function pg_temp.remember(name text, value uuid) returns void language sql as $$ select set_config('t.' || name, value::text, true) $$;
create function pg_temp.check(ok boolean, what text) returns void language plpgsql as $$ begin
    if not coalesce(ok, false) then raise exception 'FAILED: %', what; end if;
end $$;
create function pg_temp.secret() returns text language sql as $$
    select 'mri_' || translate(encode(gen_random_bytes(32), 'base64'), '+/=', '-_') $$;

do $$ declare n text; begin
    foreach n in array array['owner','invitee','tech','stranger'] loop
        perform pg_temp.remember(n, gen_random_uuid());
        insert into auth.users(id, email, email_confirmed_at, aud, role, raw_user_meta_data)
        values (pg_temp.id(n), 'qa-mail-' || n || '@example.test', now(), 'authenticated', 'authenticated',
                jsonb_build_object('name', 'QA Mail ' || n));
    end loop;
end $$;

set local role authenticated;
select pg_temp.act('owner');
do $$ declare r jsonb; begin
    r := public.remote_saas_signup('QA Mail Company');
    perform pg_temp.remember('tenant', (r->>'tenant_id')::uuid);
end $$;
reset role;
update public.users set language = 'de' where auth_user_id = pg_temp.id('owner');

---------------------------------------------------------------------------------------------------
\echo '-- creating an invitation stores no secret and queues the e-mail'
set local role authenticated;
select pg_temp.act('owner');
do $$ begin
    perform pg_temp.remember('inv_fr', public.remote_saas_invite_member(pg_temp.id('tenant'), 'QA-Mail-Invitee@example.test', 'technician', 'fr'));
    perform pg_temp.remember('inv_default', public.remote_saas_invite_member(pg_temp.id('tenant'), 'qa-mail-other@example.test', 'member'));
    perform pg_temp.remember('inv_bad_locale', public.remote_saas_invite_member(pg_temp.id('tenant'), 'qa-mail-third@example.test', 'member', 'xx'));
    perform pg_temp.check((select count(*) = 3 and bool_and(mail_state = 'pending') and bool_and(mail_sent_at is null)
        from public.remote_saas_invites(pg_temp.id('tenant'))), 'three pending invitations listed');
end $$;
reset role;
select pg_temp.check((select token_hash is null and mail_locale = 'fr' and mail_state = 'pending'
    from remote_saas.invites where id = pg_temp.id('inv_fr')), 'no hash, chosen language');
select pg_temp.check((select mail_locale = 'de' from remote_saas.invites where id = pg_temp.id('inv_default')), 'inviter profile language');
select pg_temp.check((select mail_locale = 'de' from remote_saas.invites where id = pg_temp.id('inv_bad_locale')), 'unknown language falls back');
\echo 'PASS: invitations are created pending, without a secret, in the right language'

---------------------------------------------------------------------------------------------------
\echo '-- only the mail service may claim'
set local role authenticated;
select pg_temp.act('owner');
do $$ begin
    begin perform remote_saas.mail_claim_invites(10); raise exception 'FAILED: owner claimed';
    exception when insufficient_privilege then null; end;
    begin perform remote_saas.mail_invite_secret(pg_temp.id('inv_fr'), gen_random_uuid(), sha256('x')); raise exception 'FAILED: owner set secret';
    exception when insufficient_privilege then null; end;
end $$;
reset role;
-- Other pending invitations in the live database must not be touched by this test: only this
-- tenant's rows are due here.
update remote_saas.invites set mail_next_at = now() + interval '1 hour'
    where mail_state = 'pending' and tenant_id <> pg_temp.id('tenant');
set local role mixel_remote_mail;
create temporary table claimed on commit drop as select * from remote_saas.mail_claim_invites(10);
reset role;
select pg_temp.check((select count(*) = 3 from claimed), 'three claimed');
select pg_temp.check((select company = 'QA Mail Company' and inviter_name = 'QA Mail owner' and locale = 'fr'
    and role = 'technician' and email = 'qa-mail-invitee@example.test' and attempt = 1
    from claimed where invite_id = pg_temp.id('inv_fr')), 'claim carries the mail details');
set local role mixel_remote_mail;
select pg_temp.check((select count(*) = 0 from remote_saas.mail_claim_invites(10)), 'claimed rows are not claimed twice');
reset role;
\echo 'PASS: claims are exclusive and carry company, inviter, role and language'

---------------------------------------------------------------------------------------------------
\echo '-- send, accept, failure and retry'
select set_config('t.secret_fr', pg_temp.secret(), true);
set local role mixel_remote_mail;
select pg_temp.check(not remote_saas.mail_invite_secret(pg_temp.id('inv_fr'), gen_random_uuid(),
    sha256(convert_to(current_setting('t.secret_fr'), 'UTF8'))), 'wrong claim refused');
select pg_temp.check(remote_saas.mail_invite_secret(pg_temp.id('inv_fr'), (select claim from claimed where invite_id = pg_temp.id('inv_fr')),
    sha256(convert_to(current_setting('t.secret_fr'), 'UTF8'))), 'secret hash stored');
select pg_temp.check(remote_saas.mail_invite_result(pg_temp.id('inv_fr'), (select claim from claimed where invite_id = pg_temp.id('inv_fr')),
    're_qa_1', null) = 'sent', 'recorded as sent');
-- A send failure: retried later, the link removed.
select pg_temp.check(remote_saas.mail_invite_secret(pg_temp.id('inv_default'), (select claim from claimed where invite_id = pg_temp.id('inv_default')),
    sha256('unused')), 'hash before failed send');
select pg_temp.check(remote_saas.mail_invite_result(pg_temp.id('inv_default'), (select claim from claimed where invite_id = pg_temp.id('inv_default')),
    null, 'resend 503') = 'pending', 'failure goes back to pending');
select pg_temp.check(remote_saas.mail_invite_result(pg_temp.id('inv_default'), (select claim from claimed where invite_id = pg_temp.id('inv_default')),
    're_late', null) = 'stale', 'an old claim cannot record a result');
reset role;
select pg_temp.check((select token_hash is null and mail_next_at > now() and mail_error = 'resend 503'
    from remote_saas.invites where id = pg_temp.id('inv_default')), 'failed send keeps no link and waits');
select pg_temp.check((select mail_state = 'sent' and mail_sends = 1 and mail_message_id = 're_qa_1' and token_hash is not null
    from remote_saas.invites where id = pg_temp.id('inv_fr')), 'sent state');
-- Five failed attempts give up.
update remote_saas.invites set mail_attempts = 4, mail_next_at = now() where id = pg_temp.id('inv_default');
set local role mixel_remote_mail;
create temporary table claimed2 on commit drop as select * from remote_saas.mail_claim_invites(10);
select pg_temp.check(remote_saas.mail_invite_result(pg_temp.id('inv_default'), (select claim from claimed2 where invite_id = pg_temp.id('inv_default')),
    null, 'resend 503') = 'failed', 'gives up after five attempts');
reset role;
set local role authenticated;
select pg_temp.act('invitee');
select pg_temp.check(public.remote_saas_accept_invite(current_setting('t.secret_fr')) = pg_temp.id('tenant'), 'mailed link accepted');
\echo 'PASS: the e-mailed link works, failures retry with back-off and give up after five attempts'

---------------------------------------------------------------------------------------------------
\echo '-- resend and revoke'
select pg_temp.act('owner');
do $$ begin
    perform pg_temp.remember('inv_tech', public.remote_saas_invite_member(pg_temp.id('tenant'), 'qa-mail-tech@example.test', 'technician', 'it'));
    begin perform public.remote_saas_resend_invite(pg_temp.id('tenant'), pg_temp.id('inv_tech')); raise exception 'FAILED: resend within a minute';
    exception when program_limit_exceeded then null; end;
end $$;
reset role;
-- First send of inv_tech (as the service), then the owner resends a minute later.
update remote_saas.invites set mail_next_at = now() + interval '1 hour' where id = pg_temp.id('inv_bad_locale');
select set_config('t.secret_tech', pg_temp.secret(), true);
set local role mixel_remote_mail;
create temporary table claimed3 on commit drop as select * from remote_saas.mail_claim_invites(10);
select pg_temp.check((select count(*) = 1 and bool_and(locale = 'it') from claimed3), 'only the new invitation is due');
select remote_saas.mail_invite_secret(pg_temp.id('inv_tech'), (select claim from claimed3), sha256(convert_to(current_setting('t.secret_tech'), 'UTF8')));
select remote_saas.mail_invite_result(pg_temp.id('inv_tech'), (select claim from claimed3), 're_qa_2', null);
reset role;
update remote_saas.invites set mail_requested_at = now() - interval '2 minutes' where id = pg_temp.id('inv_tech');
set local role authenticated;
select pg_temp.act('owner');
select pg_temp.check(public.remote_saas_resend_invite(pg_temp.id('tenant'), pg_temp.id('inv_tech'), 'en'), 'resend accepted');
reset role;
select pg_temp.check((select mail_state = 'pending' and token_hash is null and mail_locale = 'en' and expires_at > now() + interval '6 days'
    from remote_saas.invites where id = pg_temp.id('inv_tech')), 'resend queues a new e-mail and drops the old link');
set local role authenticated;
select pg_temp.act('tech');
do $$ begin
    begin perform public.remote_saas_accept_invite(current_setting('t.secret_tech')); raise exception 'FAILED: old link still works';
    exception when insufficient_privilege then null; end;
end $$;
-- Revoking a pending invitation: the service is refused when it tries to store the secret.
reset role;
set local role mixel_remote_mail;
create temporary table claimed4 on commit drop as select * from remote_saas.mail_claim_invites(10);
reset role;
set local role authenticated;
select pg_temp.act('owner');
select pg_temp.check(public.remote_saas_revoke_invite(pg_temp.id('tenant'), pg_temp.id('inv_tech')), 'revoked');
reset role;
set local role mixel_remote_mail;
select pg_temp.check(not remote_saas.mail_invite_secret(pg_temp.id('inv_tech'), (select claim from claimed4 where invite_id = pg_temp.id('inv_tech')),
    sha256('x')), 'no secret for a revoked invitation');
reset role;
-- A stranger and a technician cannot resend.
set local role authenticated;
select pg_temp.act('stranger');
do $$ begin
    begin perform public.remote_saas_resend_invite(pg_temp.id('tenant'), pg_temp.id('inv_bad_locale')); raise exception 'FAILED: stranger resent';
    exception when insufficient_privilege then null; end;
end $$;
select pg_temp.act('invitee');
do $$ begin
    begin perform public.remote_saas_resend_invite(pg_temp.id('tenant'), pg_temp.id('inv_bad_locale')); raise exception 'FAILED: technician resent';
    exception when insufficient_privilege then null; end;
end $$;
reset role;
-- A claim older than five minutes (crashed service) is taken over with a new claim.
update remote_saas.invites set mail_state = 'sending', mail_claim = gen_random_uuid(), mail_claimed_at = now() - interval '6 minutes',
    mail_next_at = now() where id = pg_temp.id('inv_bad_locale');
set local role mixel_remote_mail;
select pg_temp.check((select count(*) = 1 from remote_saas.mail_claim_invites(10) where invite_id = pg_temp.id('inv_bad_locale')), 'stale claim taken over');
reset role;
\echo 'PASS: resend makes a new link, revoke stops unsent e-mail, only owners/admins resend, stale claims recover'

rollback;
\echo 'ALL INVITE MAIL TESTS PASSED (rolled back)'
