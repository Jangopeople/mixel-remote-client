-- Remote SaaS legal features (migration 027): terms acceptance at sign-up, data export, deleting
-- one's own account, deleting a company, the Stripe clean-up queue. One transaction that is rolled
-- back, so it runs against a scratch copy or the live database without leaving data behind.
-- Synthetic accounts only (*.example.test).
--   psql -v ON_ERROR_STOP=1 -f next/sql/tests/remote-saas-legal.sql
\set ON_ERROR_STOP on
\set QUIET on
begin;
set local client_min_messages = notice;

create function pg_temp.act(who text, aal text default 'aal1', method text default 'password') returns void
language plpgsql as $$ begin
    perform set_config('request.jwt.claim.sub', '', true);
    perform set_config('request.jwt.claims', case when who is null then '' else json_build_object(
        'sub', current_setting('t.' || who), 'role', 'authenticated', 'aal', aal,
        'amr', json_build_array(json_build_object('method', method)))::text end, true);
end $$;
create function pg_temp.id(name text) returns uuid language sql as $$ select current_setting('t.' || name)::uuid $$;
create function pg_temp.remember(name text, value uuid) returns void language sql as $$ select set_config('t.' || name, value::text, true) $$;
create function pg_temp.key(n integer) returns bytea language sql as $$
    select (pgsodium.crypto_sign_seed_new_keypair(decode(lpad(to_hex(n), 64, '0'), 'hex'))).public $$;
create function pg_temp.mailed(invite uuid) returns text language plpgsql security definer as $$
declare claim_id uuid := gen_random_uuid(); secret text := 'mri_' || translate(encode(gen_random_bytes(32), 'base64'), '+/=', '-_');
begin
    update remote_saas.invites set mail_state = 'sending', mail_claim = claim_id, mail_claimed_at = now(),
        mail_attempts = mail_attempts + 1 where id = invite and mail_state = 'pending';
    if not remote_saas.mail_invite_secret(invite, claim_id, sha256(convert_to(secret, 'UTF8'))) then raise exception 'FAILED: secret'; end if;
    if remote_saas.mail_invite_result(invite, claim_id, 'qa-message', null) <> 'sent' then raise exception 'FAILED: not sent'; end if;
    return secret;
end $$;
create function pg_temp.expect(ok boolean, what text) returns void language plpgsql as $$
begin if not coalesce(ok, false) then raise exception 'FAILED: %', what; end if; end $$;

do $$ declare n text; tag text := substr(md5(random()::text), 1, 6); begin
    foreach n in array array['owner','admin','tech','member','loner','owner_b','personal'] loop
        perform pg_temp.remember(n, gen_random_uuid());
        insert into auth.users(id, email, email_confirmed_at, aud, role, raw_user_meta_data)
        values (pg_temp.id(n), 'qa-legal-' || n || '-' || tag || '@example.test', now(), 'authenticated', 'authenticated',
                jsonb_build_object('name', 'QA ' || n));
    end loop;
    -- The member also has a personal Mixel Remote account: deleting the company keeps that sign-in.
    insert into remote_private.personal_accounts(auth_user_id, mixel_subject, display_name)
    values (pg_temp.id('personal'), 'qa-legal-subject-' || tag, 'QA personal');
end $$;
create function pg_temp.email(who text) returns text language sql security definer as $$
    select email from auth.users where id = current_setting('t.' || who)::uuid $$;

---------------------------------------------------------------------------------------------------
\echo '-- legal versions and terms acceptance at sign-up'
set local role anon;
do $$ declare v text; begin
    select version into v from public.remote_saas_legal_versions() where document = 'terms';
    perform pg_temp.expect(v is not null and (select count(*) from public.remote_saas_legal_versions()) = 3, 'three current documents');
    perform set_config('t.terms', v, true);
    raise notice 'PASS: anyone reads the current versions of the Terms, Privacy Policy and DPA';
end $$;
set local role authenticated;
select pg_temp.act('owner') \gset
do $$ declare r jsonb; begin
    begin perform public.remote_saas_signup('QA Legal AG'); raise exception 'FAILED: sign-up without terms';
    exception when invalid_parameter_value then null; end;
    begin perform public.remote_saas_signup('QA Legal AG', true, '1999-01-01'); raise exception 'FAILED: old terms version accepted';
    exception when invalid_parameter_value then null; end;
    r := public.remote_saas_signup('QA Legal AG', true, current_setting('t.terms'), 'portal');
    perform pg_temp.remember('tenant', (r->>'tenant_id')::uuid);
    perform pg_temp.remember('org', (r->>'organization_id')::uuid);
    perform pg_temp.remember('client', (r->>'client_id')::uuid);
    perform pg_temp.expect(r->>'terms_version' = current_setting('t.terms') and r->>'status' = 'trial', 'sign-up result ' || r::text);
    r := public.remote_saas_terms_status(pg_temp.id('tenant'));
    perform pg_temp.expect((r->>'up_to_date')::boolean and r->>'accepted_by' = pg_temp.email('owner'), 'terms status ' || r::text);
    raise notice 'PASS: sign-up needs the Terms + DPA accepted, refuses an outdated version, stores version and time';
end $$;
reset role;
do $$ begin
    perform pg_temp.expect((select count(*) from remote_saas.terms_acceptances where tenant_id = pg_temp.id('tenant')) = 2
        and exists (select 1 from remote_saas.terms_acceptances where tenant_id = pg_temp.id('tenant') and document = 'dpa'
            and source = 'portal' and accepted_at > now() - interval '1 minute'), 'terms and dpa rows');
    perform pg_temp.expect(exists (select 1 from remote_saas.audit_log where tenant_id = pg_temp.id('tenant')
        and action = 'legal.accept_terms'), 'acceptance audited');
end $$;
set local role authenticated;
select pg_temp.act('owner_b') \gset
do $$ declare r jsonb; begin
    r := public.remote_saas_signup('QA Legal Other', true, null, 'app');
    perform pg_temp.remember('tenant_b', (r->>'tenant_id')::uuid);
    begin perform public.remote_saas_terms_status(pg_temp.id('tenant')); raise exception 'FAILED: other company terms visible';
    exception when insufficient_privilege then null; end;
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- members, devices, groups for the export and deletions'
select pg_temp.act('owner') \gset
do $$ declare g uuid; begin
    perform set_config('t.inv_admin', pg_temp.mailed(public.remote_saas_invite_member(pg_temp.id('tenant'), pg_temp.email('admin'), 'admin')), true);
    perform set_config('t.inv_tech', pg_temp.mailed(public.remote_saas_invite_member(pg_temp.id('tenant'), pg_temp.email('tech'), 'technician')), true);
    perform set_config('t.inv_member', pg_temp.mailed(public.remote_saas_invite_member(pg_temp.id('tenant'), pg_temp.email('personal'), 'member')), true);
    g := public.remote_saas_create_group(pg_temp.id('tenant'), 'QA Office');
    perform pg_temp.remember('group', g);
end $$;
select pg_temp.act('admin') \gset
select public.remote_saas_accept_invite(current_setting('t.inv_admin')) is not null as ok \gset
select pg_temp.act('tech') \gset
select public.remote_saas_accept_invite(current_setting('t.inv_tech')) is not null as ok \gset
select pg_temp.act('personal') \gset
select public.remote_saas_accept_invite(current_setting('t.inv_member')) is not null as ok \gset
select pg_temp.act('owner') \gset
do $$ declare r record; begin
    perform public.remote_saas_set_group_access(pg_temp.id('tenant'), pg_temp.id('group'), array[(select user_id from remote_saas_members(pg_temp.id('tenant')) where role = 'technician')]);
    select * into r from public.remote_saas_add_this_device(pg_temp.id('tenant'), 'QA Legal PC 1', 'windows', pg_temp.key(9101), pg_temp.id('group'));
    perform pg_temp.remember('dev1', r.device_id); perform set_config('t.conn1', r.connection_id::text, true);
    select * into r from public.remote_saas_add_this_device(pg_temp.id('tenant'), 'QA Legal Mac', 'macos', pg_temp.key(9102));
    perform pg_temp.remember('dev2', r.device_id);
end $$;
select pg_temp.act('tech') \gset
do $$ declare r record; begin
    -- The technician adds a computer and invites someone: both must survive the technician's account deletion.
    select * into r from public.remote_saas_add_this_device(pg_temp.id('tenant'), 'QA Tech Laptop', 'linux', pg_temp.key(9103), pg_temp.id('group'));
    perform pg_temp.remember('dev3', r.device_id);
end $$;
select pg_temp.act('admin') \gset
do $$ declare link text; begin
    link := public.remote_saas_create_deploy_link(pg_temp.id('tenant'), 'QA rollout', 7, 5);
    perform pg_temp.remember('pending_invite', public.remote_saas_invite_member(pg_temp.id('tenant'), 'qa-legal-later@example.test', 'member'));
end $$;
reset role;
do $$ begin
    insert into remote_private.device_presence(connection_id, last_seen, online_since)
    values (current_setting('t.conn1')::integer, now(), now() - interval '1 hour');
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- data export'
set local role authenticated;
select pg_temp.act('owner') \gset
do $$ declare r jsonb; begin
    r := public.remote_saas_export(pg_temp.id('tenant'));
    perform pg_temp.expect(r->>'format' = 'mixel-remote-export' and r->'company'->>'name' = 'QA Legal AG', 'export company');
    perform pg_temp.expect(jsonb_array_length(r->'members') = 4, 'export members ' || (r->'members')::text);
    perform pg_temp.expect(jsonb_array_length(r->'devices') = 3, 'export devices');
    perform pg_temp.expect(exists (select 1 from jsonb_array_elements(r->'devices') d where d->>'name' = 'QA Legal PC 1'
        and d->>'group' = 'QA Office' and d->>'status' in ('online', 'unknown') and d->>'last_seen' is not null), 'export device detail');
    perform pg_temp.expect(jsonb_array_length(r->'groups') = 1 and jsonb_array_length(r->'groups'->0->'technicians') = 1, 'export groups');
    perform pg_temp.expect(jsonb_array_length(r->'deploy_links') = 1 and not (r->'deploy_links'->0 ? 'token_hash'), 'export links without secrets');
    perform pg_temp.expect(jsonb_array_length(r->'terms_acceptances') = 2, 'export terms');
    perform pg_temp.expect(jsonb_array_length(r->'audit_log') >= 8 and r::text not like '%mri_%', 'export audit, no invitation secret');
    perform pg_temp.expect(jsonb_array_length(r->'invitations') = 4, 'export invitations');
    raise notice 'PASS: the owner exports company, members, invitations, devices, groups, links, sign-in, terms and audit log';
end $$;
select pg_temp.act('admin') \gset
do $$ begin
    begin perform public.remote_saas_export(pg_temp.id('tenant')); raise exception 'FAILED: admin exported';
    exception when insufficient_privilege then null; end;
end $$;
select pg_temp.act('owner_b') \gset
do $$ begin
    begin perform public.remote_saas_export(pg_temp.id('tenant')); raise exception 'FAILED: other company exported';
    exception when insufficient_privilege then null; end;
    raise notice 'PASS: only an owner of the company exports';
end $$;
-- Read-only (trial over): the export still works.
reset role;
update remote_saas.tenants set status = 'read_only' where id = pg_temp.id('tenant');
set local role authenticated;
select pg_temp.act('owner') \gset
do $$ begin
    perform pg_temp.expect(public.remote_saas_export(pg_temp.id('tenant'))->'company'->>'status' = 'read_only', 'read-only export');
    raise notice 'PASS: a read-only company can still export its data';
end $$;
reset role;
update remote_saas.tenants set status = 'trial' where id = pg_temp.id('tenant');

---------------------------------------------------------------------------------------------------
\echo '-- terms acceptance by admins only'
set local role authenticated;
select pg_temp.act('tech') \gset
do $$ begin
    begin perform public.remote_saas_accept_terms(pg_temp.id('tenant'), current_setting('t.terms')); raise exception 'FAILED: technician accepted';
    exception when insufficient_privilege then null; end;
end $$;
select pg_temp.act('admin') \gset
do $$ begin
    perform pg_temp.expect((public.remote_saas_accept_terms(pg_temp.id('tenant'), current_setting('t.terms'))->>'accepted_by') = pg_temp.email('admin'), 'admin accepts');
    raise notice 'PASS: owners and admins accept a new version; technicians cannot';
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- delete my account'
select pg_temp.act('owner') \gset
do $$ begin
    begin perform public.remote_saas_delete_my_account(pg_temp.email('owner')); raise exception 'FAILED: last owner deleted';
    exception when object_not_in_prerequisite_state then null; end;
    raise notice 'PASS: the last owner must delete the company or hand it over first';
end $$;
select pg_temp.act('tech') \gset
do $$ begin
    begin perform public.remote_saas_delete_my_account('someone@example.test'); raise exception 'FAILED: wrong email confirmed';
    exception when invalid_parameter_value then null; end;
end $$;
select pg_temp.act('admin') \gset
do $$ declare r jsonb; begin
    r := public.remote_saas_delete_my_account(upper(pg_temp.email('admin')));
    perform pg_temp.expect((r->>'deleted')::boolean and (r->>'invites_revoked')::int = 1, 'admin deletion ' || r::text);
end $$;
select pg_temp.act('tech') \gset
do $$ declare r jsonb; begin
    r := public.remote_saas_delete_my_account(pg_temp.email('tech'));
    perform pg_temp.expect((r->>'deleted')::boolean, 'tech deletion');
end $$;
reset role;
do $$ begin
    perform pg_temp.expect(not exists (select 1 from auth.users where id in (pg_temp.id('admin'), pg_temp.id('tech'))), 'sign-in accounts gone');
    perform pg_temp.expect(not exists (select 1 from public.users where auth_user_id in (pg_temp.id('admin'), pg_temp.id('tech'))), 'profiles gone');
    perform pg_temp.expect((select count(*) from remote_saas.members where tenant_id = pg_temp.id('tenant')) = 2, 'two members left');
    perform pg_temp.expect((select revoked_at is not null from remote_saas.invites where id = pg_temp.id('pending_invite')), 'their pending invitation revoked');
    perform pg_temp.expect(exists (select 1 from public.devices where id = pg_temp.id('dev3')), 'the technician''s computer stays with the company');
    perform pg_temp.expect((select r.created_by = (select user_id from remote_saas.members where tenant_id = pg_temp.id('tenant') and role = 'owner')
        from remote_private.rollout_tokens r where r.client_id = pg_temp.id('client')), 'deploy link handed to the owner');
    perform pg_temp.expect((select count(*) from remote_saas.group_access where tenant_id = pg_temp.id('tenant')) = 0, 'group access gone');
    perform pg_temp.expect(exists (select 1 from remote_saas.audit_log where tenant_id = pg_temp.id('tenant') and action = 'member.account_deleted'
        and actor_id is null), 'deletion audited without the person');
    raise notice 'PASS: members delete their own account: profile and sign-in gone, invitations revoked, company objects stay';
end $$;
set local role authenticated;
select pg_temp.act('loner') \gset
do $$ begin
    perform pg_temp.expect((public.remote_saas_delete_my_account(pg_temp.email('loner'))->>'deleted')::boolean, 'loner');
end $$;
reset role;
do $$ begin
    perform pg_temp.expect(not exists (select 1 from auth.users where id = pg_temp.id('loner')), 'loner gone');
    raise notice 'PASS: an account without a company deletes itself';
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- delete a company'
-- A Stripe customer and subscription, as after a Checkout.
update remote_saas.billing set stripe_customer_id = 'cus_QALegal1', stripe_subscription_id = 'sub_QALegal1',
    subscription_status = 'active' where tenant_id = pg_temp.id('tenant');
insert into remote_saas.billing(tenant_id, stripe_customer_id, stripe_subscription_id, subscription_status)
select pg_temp.id('tenant'), 'cus_QALegal1', 'sub_QALegal1', 'active'
where not exists (select 1 from remote_saas.billing where tenant_id = pg_temp.id('tenant'));
do $$ begin
    perform set_config('t.persprofile', (select id::text from public.users where auth_user_id = pg_temp.id('personal')), true);
    perform set_config('t.ownerprofile', (select id::text from public.users where auth_user_id = pg_temp.id('owner')), true);
end $$;
set local role authenticated;
select pg_temp.act('owner') \gset
do $$ begin
    perform public.remote_saas_invite_member(pg_temp.id('tenant'), 'qa-legal-queued@example.test', 'member');
end $$;
select pg_temp.act('personal') \gset
do $$ begin
    begin perform public.remote_saas_delete_company(pg_temp.id('tenant'), 'QA Legal AG'); raise exception 'FAILED: member deleted company';
    exception when insufficient_privilege then null; end;
end $$;
select pg_temp.act('owner_b') \gset
do $$ begin
    begin perform public.remote_saas_delete_company(pg_temp.id('tenant'), 'QA Legal AG'); raise exception 'FAILED: other owner deleted company';
    exception when insufficient_privilege then null; end;
end $$;
select pg_temp.act('owner') \gset
do $$ declare r jsonb; begin
    begin perform public.remote_saas_delete_company(pg_temp.id('tenant'), 'QA Legal'); raise exception 'FAILED: wrong name';
    exception when invalid_parameter_value then null; end;
    r := public.remote_saas_delete_company(pg_temp.id('tenant'), ' qa legal ag ');
    perform pg_temp.expect((r->>'devices_removed')::int = 3 and (r->>'members_removed')::int = 2
        and (r->>'accounts_deleted')::int = 1 and (r->>'invites_revoked')::int = 1 and r->>'billing' = 'pending', 'company deletion ' || r::text);
    raise notice 'PASS: only the owner deletes the company, after typing its name';
end $$;
reset role;
do $$ begin
    perform pg_temp.expect(not exists (select 1 from remote_saas.tenants where id = pg_temp.id('tenant')), 'tenant gone');
    perform pg_temp.expect(not exists (select 1 from public.organizations where id = pg_temp.id('org')), 'organization gone');
    perform pg_temp.expect(not exists (select 1 from public.clients where id = pg_temp.id('client')), 'client gone');
    perform pg_temp.expect(not exists (select 1 from public.devices where id in (pg_temp.id('dev1'), pg_temp.id('dev2'), pg_temp.id('dev3'))), 'devices gone');
    perform pg_temp.expect(not exists (select 1 from public.remote_device_enrollments where device_id in (pg_temp.id('dev1'), pg_temp.id('dev2'), pg_temp.id('dev3'))), 'enrolments gone');
    perform pg_temp.expect((select count(*) from remote_private.connection_reservations where device_id in (pg_temp.id('dev1'), pg_temp.id('dev2'), pg_temp.id('dev3'))) = 3, 'keys stay reserved');
    perform pg_temp.expect(not exists (select 1 from remote_private.device_presence where connection_id = current_setting('t.conn1')::integer), 'presence gone');
    perform pg_temp.expect(not exists (select 1 from remote_private.enrollment_audit where device_id in (pg_temp.id('dev1'), pg_temp.id('dev2'), pg_temp.id('dev3'))), 'device audit gone');
    perform pg_temp.expect(not exists (select 1 from remote_saas.invites i where i.email like 'qa-legal-%'), 'invitations gone');
    perform pg_temp.expect(not exists (select 1 from remote_saas.audit_log where tenant_id = pg_temp.id('tenant')), 'audit log gone');
    perform pg_temp.expect(not exists (select 1 from remote_saas.billing where tenant_id = pg_temp.id('tenant')), 'billing state gone');
    perform pg_temp.expect(not exists (select 1 from remote_saas.terms_acceptances where tenant_id = pg_temp.id('tenant')), 'acceptances gone');
    perform pg_temp.expect(not exists (select 1 from public.users where id in (current_setting('t.ownerprofile')::uuid, current_setting('t.persprofile')::uuid)), 'profiles gone');
    perform pg_temp.expect(not exists (select 1 from auth.users where id = pg_temp.id('owner')), 'owner sign-in gone');
    perform pg_temp.expect(exists (select 1 from auth.users where id = pg_temp.id('personal')), 'a personal Mixel Remote account keeps its sign-in');
    perform pg_temp.expect((select stripe_state = 'pending' and stripe_customer_id = 'cus_QALegal1' and company_name = 'QA Legal AG'
        and terms_version = current_setting('t.terms') and retain_until > now() + interval '9 years'
        from remote_saas.closures where tenant_id = pg_temp.id('tenant')), 'billing record kept for 10 years');
    perform pg_temp.expect(exists (select 1 from remote_saas.tenants where id = pg_temp.id('tenant_b')), 'other company untouched');
    raise notice 'PASS: deleting a company removes devices, invitations, members, accounts and data; a billing record stays';
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- Stripe clean-up queue (billing service)'
set local role mixel_remote_billing;
do $$ declare c record; begin
    select * into c from remote_saas.billing_claim_closures(5) x where x.tenant_id = pg_temp.id('tenant');
    perform pg_temp.expect(c.stripe_customer_id = 'cus_QALegal1' and c.stripe_subscription_id = 'sub_QALegal1' and c.attempt = 1, 'claimed');
    perform pg_temp.expect(not exists (select 1 from remote_saas.billing_claim_closures(5) x where x.tenant_id = pg_temp.id('tenant')), 'claimed once');
    perform pg_temp.expect(remote_saas.billing_close_result(c.closure_id, gen_random_uuid(), null) = 'stale', 'foreign claim');
    perform pg_temp.expect(remote_saas.billing_close_result(c.closure_id, c.claim, 'stripe_unavailable') = 'pending', 'retry');
    perform pg_temp.expect(not exists (select 1 from remote_saas.billing_claim_closures(5) x where x.tenant_id = pg_temp.id('tenant')), 'back-off');
end $$;
reset role;
update remote_saas.closures set stripe_next_at = now() - interval '1 second' where tenant_id = pg_temp.id('tenant');
set local role mixel_remote_billing;
do $$ declare c record; begin
    select * into c from remote_saas.billing_claim_closures(5) x where x.tenant_id = pg_temp.id('tenant');
    perform pg_temp.expect(c.attempt = 2 and remote_saas.billing_close_result(c.closure_id, c.claim, null) = 'done', 'done');
    raise notice 'PASS: the billing service claims, retries with back-off and completes the Stripe clean-up';
end $$;
reset role;
set local role authenticated;
do $$ begin
    begin perform remote_saas.billing_claim_closures(5); raise exception 'FAILED: app reads the queue';
    exception when insufficient_privilege then null; end;
    raise notice 'PASS: only the billing service reaches the clean-up queue';
end $$;

\echo 'ALL REMOTE SAAS LEGAL TESTS PASSED'
rollback;
