-- Remote SaaS (migrations 018-021, invitations as changed by 026). Runs in one transaction that is rolled back, so it can run
-- against a scratch copy of the ISM schema or, after the migrations are applied, against the live
-- database without leaving data behind. Synthetic accounts only (*.example.test).
--   psql -v ON_ERROR_STOP=1 -f next/sql/tests/remote-saas.sql
\set ON_ERROR_STOP on
\set QUIET on
begin;
set local client_min_messages = notice;

-- Act as a GoTrue session: subject, assurance level and sign-in method (GoTrue "amr").
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
create function pg_temp.sign(n integer, message bytea) returns bytea language sql as $$
    select pgsodium.crypto_sign_detached(message, (pgsodium.crypto_sign_seed_new_keypair(decode(lpad(to_hex(n), 64, '0'), 'hex'))).secret) $$;
-- Does what the mail service does for one invitation (migration 026): claim it, store the hash of a
-- fresh secret, record the send. Returns the secret, which in production exists only in the e-mail.
create function pg_temp.mailed(invite uuid) returns text language plpgsql security definer as $$
declare claim_id uuid := gen_random_uuid(); secret text := 'mri_' || translate(encode(gen_random_bytes(32), 'base64'), '+/=', '-_');
begin
    update remote_saas.invites set mail_state = 'sending', mail_claim = claim_id, mail_claimed_at = now(),
        mail_attempts = mail_attempts + 1 where id = invite and mail_state = 'pending';
    if not found then raise exception 'FAILED: invitation % is not pending', invite; end if;
    if not remote_saas.mail_invite_secret(invite, claim_id, sha256(convert_to(secret, 'UTF8'))) then
        raise exception 'FAILED: secret not stored';
    end if;
    if remote_saas.mail_invite_result(invite, claim_id, 'qa-message', null) <> 'sent' then raise exception 'FAILED: not sent'; end if;
    return secret;
end $$;

-- Accounts (auth.users) and an ISM-linked company with a Mixel staff member for regressions.
do $$ declare n text; begin
    foreach n in array array['owner_a','admin_a','tech_a','member_a','owner_b','unconfirmed','staff','extra_a'] loop
        perform pg_temp.remember(n, gen_random_uuid());
        insert into auth.users(id, email, email_confirmed_at, aud, role, raw_user_meta_data)
        values (pg_temp.id(n), 'qa-saas-' || replace(n, '_', '-') || '@example.test',
                case when n = 'unconfirmed' then null else now() end, 'authenticated', 'authenticated',
                jsonb_build_object('name', 'QA ' || n));
    end loop;
    perform pg_temp.remember('ism_org', gen_random_uuid());
    perform pg_temp.remember('ism_client', gen_random_uuid());
    perform pg_temp.remember('staff_profile', gen_random_uuid());
    insert into public.organizations(id, name, slug, is_active, mixel_org_id)
    values (pg_temp.id('ism_org'), 'QA Linked Co', 'qa-linked-co-' || substr(md5(random()::text), 1, 6), true, gen_random_uuid());
    insert into public.clients(id, name, key, organization_id) values
        (pg_temp.id('ism_client'), 'QA Linked Co', 'qa-linked-' || substr(md5(random()::text), 1, 8), pg_temp.id('ism_org'));
    insert into public.users(id, auth_user_id, name, email, role, organization_id, is_active)
    values (pg_temp.id('staff_profile'), pg_temp.id('staff'), 'QA Staff', 'qa-saas-staff@example.test', 'agent', null, true);
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- sign-up and tenancy'
set local role authenticated;
select pg_temp.act('owner_a') \gset
do $$ declare r jsonb; begin
    r := public.remote_saas_signup('QA SaaS Alpha', true);
    perform pg_temp.remember('tenant_a', (r->>'tenant_id')::uuid);
    perform pg_temp.remember('org_a', (r->>'organization_id')::uuid);
    if r->>'plan' <> 'business' or r->>'status' <> 'trial'
        or (r->>'trial_ends_at')::timestamptz not between now() + interval '13 days 23 hours' and now() + interval '14 days 1 minute' then
        raise exception 'signup result %', r;
    end if;
    begin perform public.remote_saas_signup('Second Company', true); raise exception 'second signup allowed';
    exception when insufficient_privilege then null; end;
    begin perform public.remote_saas_signup(' ', true); raise exception 'empty name allowed';
    exception when invalid_parameter_value then null; end;
    raise notice 'PASS: sign-up creates a Business trial; one company per account; name validated';
end $$;
select pg_temp.act('unconfirmed') \gset
do $$ begin
    begin perform public.remote_saas_signup('No Email Co', true); raise exception 'unconfirmed signup allowed';
    exception when insufficient_privilege then null; end;
    raise notice 'PASS: sign-up needs a confirmed email address';
end $$;
select pg_temp.act('owner_b') \gset
do $$ declare r jsonb; begin
    r := public.remote_saas_signup('QA SaaS Alpha', true);
    perform pg_temp.remember('tenant_b', (r->>'tenant_id')::uuid);
    if r->>'slug' = (select slug from public.remote_saas_my_tenants() where tenant_id <> (r->>'tenant_id')::uuid limit 1) then
        raise exception 'slug reused';
    end if;
end $$;
reset role;
do $$ declare t remote_saas.tenants; o public.organizations; c public.clients; u public.users; begin
    select * into t from remote_saas.tenants where id = pg_temp.id('tenant_a');
    select * into o from public.organizations where id = t.organization_id;
    select * into c from public.clients where id = t.client_id;
    select * into u from public.users where auth_user_id = pg_temp.id('owner_a');
    if o.kind <> 'remote_saas' or o.mixel_org_id is not null or not o.is_active or c.name <> 'Devices'
        or c.organization_id <> o.id or u.organization_id <> o.id or u.role::text <> 'user' or o.owner_id <> u.id then
        raise exception 'tenant wiring wrong';
    end if;
    if (select slug from remote_saas.tenants where id = pg_temp.id('tenant_b')) = t.slug then raise exception 'duplicate slug'; end if;
    if not exists (select 1 from remote_saas.members where tenant_id = t.id and user_id = u.id and role = 'owner') then
        raise exception 'owner missing';
    end if;
    perform pg_temp.remember('owner_a_profile', u.id);
    perform pg_temp.remember('client_a', t.client_id);
    perform pg_temp.remember('client_b', (select client_id from remote_saas.tenants where id = pg_temp.id('tenant_b')));
    raise notice 'PASS: ISM organization (kind remote_saas), client "Devices", owner profile and member';
end $$;

set local role authenticated;
select pg_temp.act('owner_a') \gset
do $$ declare r jsonb; n integer; begin
    select count(*) into n from public.remote_saas_my_tenants();
    if n <> 1 or (select role from public.remote_saas_my_tenants()) <> 'owner' then raise exception 'my_tenants'; end if;
    r := public.remote_saas_tenant_overview(pg_temp.id('tenant_a'));
    if (r->'limits'->>'technicians')::int <> 15 or (r->'limits'->>'devices')::int <> 2000
        or (r->'limits'->>'sessions')::int <> 10 or (r->'usage'->>'technicians')::int <> 1 then
        raise exception 'overview %', r;
    end if;
    begin perform public.remote_saas_tenant_overview(pg_temp.id('tenant_b')); raise exception 'cross-tenant overview';
    exception when insufficient_privilege then null; end;
    begin perform * from public.remote_saas_members(pg_temp.id('tenant_b')); raise exception 'cross-tenant members';
    exception when insufficient_privilege then null; end;
    begin perform 1 from remote_saas.tenants; raise exception 'direct table read';
    exception when insufficient_privilege then null; end;
    raise notice 'PASS: my tenants, overview with Business limits, cross-tenant and direct-table isolation';
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- invitations, roles and technician limit'
do $$ begin
    perform pg_temp.remember('dummy', gen_random_uuid());
    perform set_config('t.invite_admin', pg_temp.mailed(public.remote_saas_invite_member(pg_temp.id('tenant_a'), 'QA-SAAS-ADMIN-A@example.test ', 'admin')), true);
    perform set_config('t.invite_tech', pg_temp.mailed(public.remote_saas_invite_member(pg_temp.id('tenant_a'), 'qa-saas-tech-a@example.test', 'technician')), true);
    perform set_config('t.invite_member', pg_temp.mailed(public.remote_saas_invite_member(pg_temp.id('tenant_a'), 'qa-saas-member-a@example.test', 'member')), true);
    begin perform public.remote_saas_invite_member(pg_temp.id('tenant_a'), 'not-an-email', 'member'); raise exception 'bad email';
    exception when invalid_parameter_value then null; end;
    begin perform public.remote_saas_invite_member(pg_temp.id('tenant_a'), 'x@example.test', 'owner'); raise exception 'owner invite';
    exception when invalid_parameter_value then null; end;
    if (select count(*) from public.remote_saas_invites(pg_temp.id('tenant_a'))) <> 3 then raise exception 'invite list'; end if;
    raise notice 'PASS: invitations created (secret only in the e-mail), email and role validated';
end $$;
select pg_temp.act('owner_b') \gset
do $$ begin
    begin perform public.remote_saas_accept_invite(current_setting('t.invite_admin')); raise exception 'wrong email accepted';
    exception when insufficient_privilege then null; end;
    begin perform public.remote_saas_invite_member(pg_temp.id('tenant_a'), 'evil@example.test', 'admin'); raise exception 'cross-tenant invite';
    exception when insufficient_privilege then null; end;
    raise notice 'PASS: an invitation only works for the invited email; no cross-tenant invites';
end $$;
select pg_temp.act('admin_a') \gset
do $$ begin
    if public.remote_saas_accept_invite(current_setting('t.invite_admin')) <> pg_temp.id('tenant_a') then raise exception 'accept'; end if;
    begin perform public.remote_saas_accept_invite(current_setting('t.invite_admin')); raise exception 'invite reused';
    exception when insufficient_privilege then null; end;
end $$;
select pg_temp.act('tech_a') \gset
select public.remote_saas_accept_invite(current_setting('t.invite_tech')) is not null as tech_joined \gset
select pg_temp.act('member_a') \gset
select public.remote_saas_accept_invite(current_setting('t.invite_member')) is not null as member_joined \gset
reset role;
do $$ begin
    perform pg_temp.remember('admin_a_profile', (select id from public.users where auth_user_id = pg_temp.id('admin_a')));
    perform pg_temp.remember('tech_a_profile', (select id from public.users where auth_user_id = pg_temp.id('tech_a')));
    perform pg_temp.remember('member_a_profile', (select id from public.users where auth_user_id = pg_temp.id('member_a')));
    perform pg_temp.remember('owner_b_profile', (select id from public.users where auth_user_id = pg_temp.id('owner_b')));
    if (select organization_id from public.users where id = pg_temp.id('tech_a_profile')) <> pg_temp.id('org_a') then
        raise exception 'invited profile not in organization';
    end if;
    update remote_saas.tenants set max_technicians_override = 3 where id = pg_temp.id('tenant_a');
end $$;
set local role authenticated;
select pg_temp.act('owner_a') \gset
do $$ begin
    if (select count(*) from public.remote_saas_members(pg_temp.id('tenant_a'))) <> 4 then raise exception 'members'; end if;
    begin perform public.remote_saas_invite_member(pg_temp.id('tenant_a'), 'qa-saas-extra-a@example.test', 'technician');
        raise exception 'technician limit ignored';
    exception when program_limit_exceeded then null; end;
    begin perform public.remote_saas_set_member_role(pg_temp.id('tenant_a'), pg_temp.id('member_a_profile'), 'technician');
        raise exception 'promotion over limit';
    exception when program_limit_exceeded then null; end;
    perform set_config('t.invite_extra', pg_temp.mailed(public.remote_saas_invite_member(pg_temp.id('tenant_a'), 'qa-saas-extra-a@example.test', 'member')), true);
    raise notice 'PASS: technician limit enforced on invitations and promotions; members do not count';
end $$;
reset role;
update remote_saas.tenants set max_technicians_override = null where id = pg_temp.id('tenant_a');
set local role authenticated;
select pg_temp.act('admin_a') \gset
do $$ begin
    begin perform public.remote_saas_set_member_role(pg_temp.id('tenant_a'), pg_temp.id('tech_a_profile'), 'owner'); raise exception 'admin made owner';
    exception when insufficient_privilege then null; end;
    begin perform public.remote_saas_remove_member(pg_temp.id('tenant_a'), pg_temp.id('owner_a_profile')); raise exception 'admin removed owner';
    exception when insufficient_privilege then null; end;
    raise notice 'PASS: admins cannot change or remove owners';
end $$;
select pg_temp.act('tech_a') \gset
do $$ begin
    begin perform public.remote_saas_invite_member(pg_temp.id('tenant_a'), 'x@example.test', 'member'); raise exception 'technician invited';
    exception when insufficient_privilege then null; end;
    begin perform public.remote_saas_set_member_role(pg_temp.id('tenant_a'), pg_temp.id('member_a_profile'), 'admin'); raise exception 'technician set role';
    exception when insufficient_privilege then null; end;
    raise notice 'PASS: technicians cannot invite or change roles';
end $$;
select pg_temp.act('member_a') \gset
do $$ begin
    begin perform * from public.remote_saas_members(pg_temp.id('tenant_a')); raise exception 'member listed members';
    exception when insufficient_privilege then null; end;
    raise notice 'PASS: plain members cannot list members';
end $$;
select pg_temp.act('owner_a') \gset
do $$ begin
    begin perform public.remote_saas_set_member_role(pg_temp.id('tenant_a'), pg_temp.id('owner_a_profile'), 'admin'); raise exception 'last owner demoted';
    exception when insufficient_privilege then null; end;
    begin perform public.remote_saas_remove_member(pg_temp.id('tenant_a'), pg_temp.id('owner_a_profile')); raise exception 'last owner removed';
    exception when insufficient_privilege then null; end;
    raise notice 'PASS: the last owner stays';
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- sign-in settings'
do $$ declare s jsonb; begin
    s := public.remote_saas_get_auth_settings(pg_temp.id('tenant_a'));
    if s->'methods' <> '["password","magic_link","google","azure","apple","mixel_id"]'::jsonb or (s->>'require_mfa')::boolean then
        raise exception 'default settings %', s;
    end if;
    begin perform public.remote_saas_set_auth_settings(pg_temp.id('tenant_a'), '{google}', false); raise exception 'self lock-out';
    exception when invalid_parameter_value then null; end;
    begin perform public.remote_saas_set_auth_settings(pg_temp.id('tenant_a'), '{password,sso}', false); raise exception 'sso without provider';
    exception when invalid_parameter_value then null; end;
    begin perform public.remote_saas_set_auth_settings(pg_temp.id('tenant_a'), '{password,bogus}', false); raise exception 'unknown method';
    exception when invalid_parameter_value then null; end;
    begin perform public.remote_saas_set_auth_settings(pg_temp.id('tenant_a'), '{password}', true); raise exception 'mfa required without mfa';
    exception when invalid_parameter_value then null; end;
    s := public.remote_saas_set_auth_settings(pg_temp.id('tenant_a'), '{password,magic_link}', false);
    if s->'methods' <> '["magic_link","password"]'::jsonb then raise exception 'set %', s; end if;
    raise notice 'PASS: sign-in settings validated (lock-out, SSO provider, unknown methods, MFA)';
end $$;
select pg_temp.act('owner_a', 'aal2') \gset
select public.remote_saas_set_auth_settings(pg_temp.id('tenant_a'), '{password,magic_link}', true) is not null as mfa_on \gset
select pg_temp.act('tech_a', 'aal1') \gset
do $$ begin
    begin perform * from public.remote_saas_devices(pg_temp.id('tenant_a')); raise exception 'MFA bypass';
    exception when insufficient_privilege then null; end;
    raise notice 'PASS: required two-step sign-in is enforced for members';
end $$;
select pg_temp.act('tech_a', 'aal2', 'oauth') \gset
do $$ begin
    begin perform * from public.remote_saas_devices(pg_temp.id('tenant_a')); raise exception 'disallowed method accepted';
    exception when insufficient_privilege then null; end;
    raise notice 'PASS: a sign-in method the company does not allow is refused';
end $$;
select pg_temp.act('owner_a', 'aal2') \gset
select public.remote_saas_set_auth_settings(pg_temp.id('tenant_a'), '{password,magic_link,google,azure,apple,mixel_id}', false) is not null as reset_settings \gset
select pg_temp.act('owner_a') \gset
do $$ declare proof text; begin
    proof := public.remote_saas_add_domain(pg_temp.id('tenant_a'), 'qa-alpha.example.test');
    if proof !~ '^mixel-remote-verify=[0-9a-f]{32}$' then raise exception 'proof %', proof; end if;
    begin perform public.remote_saas_verify_domain(pg_temp.id('tenant_a'), 'qa-alpha.example.test'); raise exception 'tenant verified own domain';
    exception when insufficient_privilege then null; end;
    raise notice 'PASS: domains need Mixel verification';
end $$;
reset role;
set local role anon;
select pg_temp.act(null) \gset
do $$ begin
    if exists (select 1 from public.remote_saas_sign_in_options('someone@qa-alpha.example.test')) then
        raise exception 'unverified domain used';
    end if;
    raise notice 'PASS: an unverified domain does not pick sign-in options';
end $$;
reset role;
select set_config('t.slug_a', slug, true) from remote_saas.tenants where id = pg_temp.id('tenant_a') \gset
set local role authenticated;
select pg_temp.act('staff', 'aal2') \gset
select public.remote_saas_verify_domain(pg_temp.id('tenant_a'), 'qa-alpha.example.test') as verified \gset
reset role;
set local role anon;
select pg_temp.act(null) \gset
do $$ begin
    if (select company from public.remote_saas_sign_in_options('Someone@QA-alpha.example.test')) <> 'QA SaaS Alpha'
        or (select cardinality(methods) from public.remote_saas_sign_in_options(current_setting('t.slug_a'))) <> 6
        or exists (select 1 from public.remote_saas_sign_in_options('nobody@unknown.example.test')) then
        raise exception 'sign-in options lookup';
    end if;
    begin perform public.remote_saas_my_tenants(); exception when insufficient_privilege then null; end;
    raise notice 'PASS: anonymous sign-in options by verified domain and slug; nothing for unknown';
end $$;
reset role;

---------------------------------------------------------------------------------------------------
\echo '-- devices: add this computer, groups, access, limits'
set local role authenticated;
select pg_temp.act('tech_a') \gset
do $$ declare r record; again record; begin
    select * into r from public.remote_saas_add_this_device(pg_temp.id('tenant_a'), 'Tech Laptop', 'macos', pg_temp.key(1));
    if r.connection_id not between 100000000 and 999999999 or r.generation <> 1 or r.profile <> pg_temp.id('tech_a_profile')
        or r.tenant_org <> pg_temp.id('org_a') then raise exception 'add %', r; end if;
    perform pg_temp.remember('d1', r.device_id);
    select * into again from public.remote_saas_add_this_device(pg_temp.id('tenant_a'), 'Tech Laptop', 'macos', pg_temp.key(1));
    if again.device_id <> r.device_id or again.connection_id <> r.connection_id then raise exception 'not idempotent'; end if;
    begin perform public.remote_saas_add_this_device(pg_temp.id('tenant_a'), 'Bad', 'macos', '\x01'::bytea); raise exception 'bad key';
    exception when invalid_parameter_value then null; end;
    begin perform public.remote_saas_add_this_device(pg_temp.id('tenant_a'), 'Bad', 'beos', pg_temp.key(99)); raise exception 'bad os';
    exception when invalid_parameter_value then null; end;
    raise notice 'PASS: a technician adds this computer (idempotent for the same key, input validated)';
end $$;
select pg_temp.act('member_a') \gset
do $$ begin
    begin perform public.remote_saas_add_this_device(pg_temp.id('tenant_a'), 'Member PC', 'windows', pg_temp.key(2)); raise exception 'member added device';
    exception when insufficient_privilege then null; end;
    if exists (select 1 from public.remote_saas_devices(pg_temp.id('tenant_a'))) then raise exception 'member sees others devices'; end if;
    raise notice 'PASS: plain members cannot add devices and only see their own';
end $$;
select pg_temp.act('owner_b') \gset
do $$ begin
    begin perform public.remote_saas_add_this_device(pg_temp.id('tenant_a'), 'Intruder', 'windows', pg_temp.key(3)); raise exception 'cross-tenant add';
    exception when insufficient_privilege then null; end;
    begin perform * from public.remote_saas_devices(pg_temp.id('tenant_a')); raise exception 'cross-tenant list';
    exception when insufficient_privilege then null; end;
    begin perform public.remote_saas_remove_device(pg_temp.id('tenant_a'), pg_temp.id('d1')); raise exception 'cross-tenant remove';
    exception when insufficient_privilege then null; end;
    begin perform public.remote_saas_remove_device(pg_temp.id('tenant_b'), pg_temp.id('d1')); raise exception 'foreign device via own tenant';
    exception when insufficient_privilege then null; end;
    perform pg_temp.remember('b1', (select device_id from public.remote_saas_add_this_device(pg_temp.id('tenant_b'), 'Beta PC', 'windows', pg_temp.key(20))));
    begin perform public.remote_saas_add_this_device(pg_temp.id('tenant_b'), 'Steal key', 'windows', pg_temp.key(1)); raise exception 'key rebound to another tenant';
    exception when insufficient_privilege then null; end;
    raise notice 'PASS: tenant B cannot add, list or remove devices of tenant A, nor take over its keys';
end $$;
select pg_temp.act('owner_a') \gset
do $$ declare g1 uuid; g2 uuid; begin
    g1 := public.remote_saas_create_group(pg_temp.id('tenant_a'), 'Office');
    g2 := public.remote_saas_create_group(pg_temp.id('tenant_a'), 'Servers');
    perform pg_temp.remember('g1', g1); perform pg_temp.remember('g2', g2);
    begin perform public.remote_saas_create_group(pg_temp.id('tenant_a'), 'office'); raise exception 'duplicate group';
    exception when unique_violation then null; end;
    if public.remote_saas_set_group_access(pg_temp.id('tenant_a'), g1, array[pg_temp.id('tech_a_profile')]) <> 1 then raise exception 'grant'; end if;
    begin perform public.remote_saas_set_group_access(pg_temp.id('tenant_a'), g1, array[pg_temp.id('member_a_profile')]); raise exception 'member granted';
    exception when invalid_parameter_value then null; end;
    begin perform public.remote_saas_set_group_access(pg_temp.id('tenant_a'), g1, array[pg_temp.id('owner_b_profile')]); raise exception 'foreign user granted';
    exception when invalid_parameter_value then null; end;
    perform pg_temp.remember('d2', (select device_id from public.remote_saas_add_this_device(pg_temp.id('tenant_a'), 'Server 1', 'linux', pg_temp.key(4), g2)));
    perform pg_temp.remember('d4', (select device_id from public.remote_saas_add_this_device(pg_temp.id('tenant_a'), 'Reception', 'windows', pg_temp.key(5))));
    if (select count(*) from public.remote_saas_devices(pg_temp.id('tenant_a'))) <> 3 then raise exception 'owner list'; end if;
    if (select count(*) from public.remote_saas_devices(pg_temp.id('tenant_a'), g2)) <> 1 then raise exception 'group filter'; end if;
    if (select count(*) from public.remote_saas_devices(pg_temp.id('tenant_a'), null, 'serv')) <> 1 then raise exception 'search'; end if;
    raise notice 'PASS: groups, technician access lists (technicians of this tenant only), device list filters';
end $$;
select pg_temp.act('tech_a') \gset
do $$ begin
    if exists (select 1 from public.remote_saas_devices(pg_temp.id('tenant_a')) where device_id = pg_temp.id('d2')) then
        raise exception 'technician sees a group without access';
    end if;
    if (select count(*) from public.remote_saas_devices(pg_temp.id('tenant_a'))) <> 2 then raise exception 'technician list'; end if;
    begin perform public.remote_saas_rename_device(pg_temp.id('tenant_a'), pg_temp.id('d2'), 'Mine'); raise exception 'rename without access';
    exception when insufficient_privilege then null; end;
    begin perform public.remote_saas_move_device(pg_temp.id('tenant_a'), pg_temp.id('d1'), pg_temp.id('g2')); raise exception 'move into foreign group';
    exception when insufficient_privilege then null; end;
    begin perform public.remote_saas_remove_device(pg_temp.id('tenant_a'), pg_temp.id('d2')); raise exception 'remove without access';
    exception when insufficient_privilege then null; end;
    perform public.remote_saas_move_device(pg_temp.id('tenant_a'), pg_temp.id('d1'), pg_temp.id('g1'));
    if public.remote_saas_rename_device(pg_temp.id('tenant_a'), pg_temp.id('d1'), 'Reception') <> 'Reception (2)' then
        raise exception 'rename must keep names unique';
    end if;
    if (select count(*) from public.remote_saas_groups(pg_temp.id('tenant_a'))) <> 1 then raise exception 'technician group list'; end if;
    begin perform public.remote_saas_create_group(pg_temp.id('tenant_a'), 'Mine'); raise exception 'technician created group';
    exception when insufficient_privilege then null; end;
    raise notice 'PASS: technicians reach ungrouped and granted groups only; rename keeps names unique; move checks both groups';
end $$;
reset role;
do $$ begin
    if (select count(*) from remote_private.enrollment_audit where device_id = pg_temp.id('d1') and action in ('enroll','rename','move')) <> 3 then
        raise exception 'device audit';
    end if;
    if (select name from public.devices where id = pg_temp.id('d1')) <> 'Reception (2)' then raise exception 'ISM device name'; end if;
    update remote_saas.tenants set max_devices_override = 4 where id = pg_temp.id('tenant_a');
    raise notice 'PASS: device actions audited in enrollment_audit; ISM device record renamed';
end $$;
set local role authenticated;
select pg_temp.act('owner_a') \gset
do $$ begin
    begin perform public.remote_saas_create_deploy_link(pg_temp.id('tenant_a'), 'Too many', 7, 2); raise exception 'deploy link over capacity';
    exception when program_limit_exceeded then null; end;
    perform set_config('t.link', public.remote_saas_create_deploy_link(pg_temp.id('tenant_a'), 'Rollout', 7, 1, pg_temp.id('g2')), true);
    if (select count(*) from public.remote_saas_deploy_links(pg_temp.id('tenant_a'))) <> 1 then raise exception 'links'; end if;
    raise notice 'PASS: deploy links are limited to the plan capacity';
end $$;
select pg_temp.act('owner_b') \gset
do $$ begin
    if exists (select 1 from public.remote_saas_deploy_links(pg_temp.id('tenant_b'))) then raise exception 'foreign links visible'; end if;
    if public.remote_saas_revoke_deploy_link(pg_temp.id('tenant_b'), gen_random_uuid()) then raise exception 'revoke unknown link'; end if;
end $$;
reset role;
set local role anon;
select pg_temp.act(null) \gset
do $$ declare r record; begin
    select * into r from public.remote_enroll_with_token(current_setting('t.link'), 'Rolled out', 'windows', pg_temp.key(6));
    if r.tenant <> pg_temp.id('org_a') or r.generation <> 1 then raise exception 'deploy enroll %', r; end if;
    perform pg_temp.remember('d3', r.device_id);
    perform set_config('t.d3_connection', r.connection_id::text, true);
    begin perform public.remote_enroll_with_token(current_setting('t.link'), 'Second', 'windows', pg_temp.key(7)); raise exception 'link reused';
    exception when insufficient_privilege then null; end;
    raise notice 'PASS: a computer enrols itself with a deploy link (group applied, uses limited)';
end $$;
reset role;
do $$ begin
    if (select group_id from remote_saas.device_meta where device_id = pg_temp.id('d3')) <> pg_temp.id('g2')
        or (select added_via from remote_saas.device_meta where device_id = pg_temp.id('d3')) <> 'deploy_link' then
        raise exception 'deploy meta';
    end if;
    update remote_private.rollout_tokens set max_uses = 10 where client_id = pg_temp.id('client_a');
end $$;
set local role anon;
do $$ begin
    begin perform public.remote_enroll_with_token(current_setting('t.link'), 'Over limit', 'windows', pg_temp.key(8)); raise exception 'device limit ignored by deploy link';
    exception when program_limit_exceeded then null; end;
    raise notice 'PASS: deploy links stop at the plan device limit';
end $$;
reset role;
set local role authenticated;
select pg_temp.act('owner_a') \gset
do $$ begin
    begin perform public.remote_saas_add_this_device(pg_temp.id('tenant_a'), 'Fifth', 'linux', pg_temp.key(9)); raise exception 'device limit ignored';
    exception when program_limit_exceeded then null; end;
    raise notice 'PASS: add this computer stops at the plan device limit';
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- connection policy and enrollment visibility'
reset role;
update remote_saas.tenants set max_devices_override = null where id = pg_temp.id('tenant_a');
do $$ declare
    c public.remote_device_enrollments; h public.remote_device_enrollments; h2 public.remote_device_enrollments;
    b public.remote_device_enrollments; r record;
begin
    select * into c from public.remote_device_enrollments where device_id = pg_temp.id('d1');
    select * into h from public.remote_device_enrollments where device_id = pg_temp.id('d4');
    select * into h2 from public.remote_device_enrollments where device_id = pg_temp.id('d2');
    select * into b from public.remote_device_enrollments where device_id = pg_temp.id('b1');
    perform set_config('t.c_gen', c.generation::text, true);
    execute 'set local role mixel_remote_policy_reader';
    select * into r from remote_private.saas_support_pair(pg_temp.id('tech_a_profile'), c.device_id, c.generation, c.public_key,
        pg_temp.id('owner_a_profile'), h.device_id, h.generation, h.public_key, false);
    if r.allowed is not true or r.max_sessions <> 10 or r.tenant <> pg_temp.id('tenant_a') or r.organization <> pg_temp.id('org_a') then
        raise exception 'allowed pair %', r;
    end if;
    select * into r from remote_private.saas_support_pair(pg_temp.id('tech_a_profile'), c.device_id, c.generation, c.public_key,
        pg_temp.id('owner_a_profile'), h2.device_id, h2.generation, h2.public_key, true);
    if r.allowed then raise exception 'group access ignored by policy'; end if;
    select * into r from remote_private.saas_support_pair(pg_temp.id('tech_a_profile'), c.device_id, c.generation + 1, c.public_key,
        pg_temp.id('owner_a_profile'), h.device_id, h.generation, h.public_key, false);
    if r.allowed then raise exception 'stale controller generation'; end if;
    select * into r from remote_private.saas_support_pair(pg_temp.id('owner_b_profile'), b.device_id, b.generation, b.public_key,
        pg_temp.id('owner_a_profile'), h.device_id, h.generation, h.public_key, false);
    if r.allowed then raise exception 'cross-tenant session'; end if;
    select * into r from remote_private.saas_support_pair(pg_temp.id('member_a_profile'), c.device_id, c.generation, c.public_key,
        pg_temp.id('owner_a_profile'), h.device_id, h.generation, h.public_key, false);
    if r.allowed then raise exception 'member controls'; end if;
    if exists (select 1 from remote_private.resolve_support_pair(pg_temp.id('staff_profile'), c.device_id, c.generation, c.public_key,
            pg_temp.id('owner_a_profile'), h.device_id, h.generation, h.public_key)) then
        raise exception 'linked-company policy accepts a SaaS host';
    end if;
    if not remote_private.saas_startup_host(h.connection_id, h.device_id, pg_temp.id('owner_a_profile'), pg_temp.id('org_a'), h.generation, h.public_key)
        or remote_private.saas_startup_host(h.connection_id, h.device_id, pg_temp.id('owner_b_profile'), pg_temp.id('org_a'), h.generation, h.public_key)
        or remote_private.saas_startup_host(h.connection_id, h.device_id, pg_temp.id('owner_a_profile'), pg_temp.id('org_a'), h.generation, b.public_key) then
        raise exception 'startup host policy';
    end if;
    if not remote_private.saas_device_bound(h.connection_id, h.device_id, pg_temp.id('owner_a_profile'), h.generation, h.public_key)
        or remote_private.saas_device_bound(h.connection_id, h.device_id, pg_temp.id('owner_b_profile'), h.generation, h.public_key)
        or remote_private.saas_device_bound(h.connection_id, h.device_id, pg_temp.id('owner_a_profile'), h.generation + 1, h.public_key) then
        raise exception 'device binding check';
    end if;
    begin perform 1 from remote_saas.tenants; raise exception 'policy reader reads tenant tables';
    exception when insufficient_privilege then null; end;
    execute 'reset role';
    raise notice 'PASS: session policy (group access, stale keys, cross-tenant, members, session limit) and startup hosts';
end $$;
set local role authenticated;
select pg_temp.act('tech_a') \gset
do $$ begin
    if (select count(*) from public.remote_device_enrollments) <> 2
        or exists (select 1 from public.remote_device_enrollments where device_id in (pg_temp.id('d2'), pg_temp.id('d3'))) then
        raise exception 'technician enrollment visibility';
    end if;
end $$;
select pg_temp.act('owner_b') \gset
do $$ begin
    if (select count(*) from public.remote_device_enrollments) <> 1 then raise exception 'tenant B visibility'; end if;
end $$;
select pg_temp.act('staff', 'aal2') \gset
do $$ begin
    if exists (select 1 from public.remote_device_enrollments e where e.device_id in (pg_temp.id('d1'), pg_temp.id('d4'), pg_temp.id('b1'))) then
        raise exception 'staff reads SaaS enrollments';
    end if;
    raise notice 'PASS: enrollment visibility per tenant and group; Mixel staff do not see tenant devices';
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- removal and self-removal'
select pg_temp.act('owner_a') \gset
do $$ declare old_connection integer; again record; begin
    select connection_id into old_connection from public.remote_device_enrollments where device_id = pg_temp.id('d2');
    perform set_config('t.d2_connection', old_connection::text, true);
    if not public.remote_saas_remove_device(pg_temp.id('tenant_a'), pg_temp.id('d2')) then raise exception 'remove'; end if;
    if exists (select 1 from public.remote_saas_devices(pg_temp.id('tenant_a')) where device_id = pg_temp.id('d2')) then raise exception 'still listed'; end if;
    select * into again from public.remote_saas_add_this_device(pg_temp.id('tenant_a'), 'Server 1', 'linux', pg_temp.key(4));
    if again.connection_id <> old_connection or again.device_id = pg_temp.id('d2') then raise exception 're-add %', again; end if;
    perform pg_temp.remember('d2b', again.device_id);
    raise notice 'PASS: remove revokes and deletes; the same computer can be added again and keeps its ID';
end $$;
reset role;
do $$ begin
    if exists (select 1 from public.devices where id = pg_temp.id('d2')) or exists (select 1 from public.remote_device_enrollments where device_id = pg_temp.id('d2')) then
        raise exception 'device not deleted';
    end if;
    if (select array_agg(action order by id) from remote_private.enrollment_audit where device_id = pg_temp.id('d2')) <> '{enroll,revoke,remove}' then
        raise exception 'removal audit';
    end if;
end $$;
set local role anon;
select pg_temp.act(null) \gset
do $$ declare issued bigint := extract(epoch from now())::bigint; msg bytea; conn integer := current_setting('t.d3_connection')::integer; begin
    msg := public.remote_device_self_remove_message(pg_temp.id('d3'), conn, 1, issued);
    if convert_from(msg, 'UTF8') <> format(E'mixel-remote/device-self-remove/v1\n%s\n%s\n1\n%s', pg_temp.id('d3'), conn, issued) then
        raise exception 'message format';
    end if;
    begin perform public.remote_device_self_remove(pg_temp.id('d3'), conn, 1, issued, pg_temp.sign(7, msg)); raise exception 'wrong key accepted';
    exception when insufficient_privilege then null; end;
    begin perform public.remote_device_self_remove(pg_temp.id('d3'), conn, 1, issued, pg_temp.sign(6, public.remote_device_self_remove_message(pg_temp.id('d3'), conn, 2, issued)));
        raise exception 'signature for other text accepted';
    exception when insufficient_privilege then null; end;
    begin perform public.remote_device_self_remove(pg_temp.id('d3'), conn, 1, issued - 31 * 86400,
            pg_temp.sign(6, public.remote_device_self_remove_message(pg_temp.id('d3'), conn, 1, issued - 31 * 86400)));
        raise exception 'expired request accepted';
    exception when invalid_parameter_value then null; end;
    raise notice 'PASS: self-removal rejects wrong keys, other texts and requests older than 30 days';
end $$;
do $$ declare issued bigint := extract(epoch from now())::bigint - 86400; conn integer := current_setting('t.d3_connection')::integer; sig bytea; begin
    -- Signed a day ago while offline, sent now; then retried.
    sig := pg_temp.sign(6, public.remote_device_self_remove_message(pg_temp.id('d3'), conn, 1, issued));
    if not public.remote_device_self_remove(pg_temp.id('d3'), conn, 1, issued, sig) then raise exception 'self remove'; end if;
    if not public.remote_device_self_remove(pg_temp.id('d3'), conn, 1, issued, sig) then raise exception 'retry not idempotent'; end if;
    raise notice 'PASS: a device removes itself with an offline-signed request; retries are harmless';
end $$;
do $$ declare issued bigint := extract(epoch from now())::bigint; r public.remote_device_enrollments; begin
    execute 'reset role';
    select * into r from public.remote_device_enrollments where device_id = pg_temp.id('d4');
    execute 'set local role anon';
    -- A request for another generation never removes the current enrollment.
    if public.remote_device_self_remove(r.device_id, r.connection_id, r.generation + 1, issued,
            pg_temp.sign(5, public.remote_device_self_remove_message(r.device_id, r.connection_id, r.generation + 1, issued))) then
        raise exception 'future generation removed device';
    end if;
    raise notice 'PASS: a request for another enrollment generation changes nothing';
end $$;
reset role;
do $$ begin
    if exists (select 1 from public.devices where id = pg_temp.id('d3')) or not exists (select 1 from public.devices where id = pg_temp.id('d4')) then
        raise exception 'self removal effect';
    end if;
    if not exists (select 1 from remote_private.enrollment_audit where device_id = pg_temp.id('d3') and action = 'self_remove')
        or not exists (select 1 from remote_saas.audit_log where tenant_id = pg_temp.id('tenant_a') and action = 'device.self_remove') then
        raise exception 'self removal audit';
    end if;
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- read-only after the trial, member removal, audit log'
update remote_saas.tenants set trial_ends_at = now() - interval '1 minute' where id = pg_temp.id('tenant_a');
set local role authenticated;
select pg_temp.act('owner_a') \gset
do $$ declare r record; begin
    if (select status from public.remote_saas_my_tenants()) <> 'read_only' then raise exception 'trial end status'; end if;
    begin perform public.remote_saas_rename_device(pg_temp.id('tenant_a'), pg_temp.id('d4'), 'Changed'); raise exception 'write while read-only';
    exception when read_only_sql_transaction then null; end;
    begin perform public.remote_saas_add_this_device(pg_temp.id('tenant_a'), 'New', 'linux', pg_temp.key(10)); raise exception 'add while read-only';
    exception when read_only_sql_transaction then null; end;
    begin perform public.remote_saas_invite_member(pg_temp.id('tenant_a'), 'late@example.test', 'member'); raise exception 'invite while read-only';
    exception when read_only_sql_transaction then null; end;
    if (select count(*) from public.remote_saas_devices(pg_temp.id('tenant_a'))) <> 3 then raise exception 'list while read-only'; end if;
    if not public.remote_saas_remove_device(pg_temp.id('tenant_a'), pg_temp.id('d2b')) then raise exception 'remove while read-only'; end if;
    raise notice 'PASS: after the trial the account is read-only: lists and removals work, changes do not';
end $$;
reset role;
do $$ declare c public.remote_device_enrollments; h public.remote_device_enrollments; r record; begin
    select * into c from public.remote_device_enrollments where device_id = pg_temp.id('d1');
    select * into h from public.remote_device_enrollments where device_id = pg_temp.id('d4');
    select * into r from remote_private.saas_support_pair(pg_temp.id('tech_a_profile'), c.device_id, c.generation, c.public_key,
        pg_temp.id('owner_a_profile'), h.device_id, h.generation, h.public_key, false);
    if r.allowed then raise exception 'session while read-only'; end if;
    raise notice 'PASS: no new sessions while read-only';
end $$;
update remote_saas.tenants set status = 'active', trial_ends_at = null where id = pg_temp.id('tenant_a');
set local role authenticated;
select pg_temp.act('owner_a') \gset
do $$ declare n integer; begin
    if not public.remote_saas_remove_member(pg_temp.id('tenant_a'), pg_temp.id('tech_a_profile')) then raise exception 'remove member'; end if;
    select count(*) into n from public.remote_saas_audit_log(pg_temp.id('tenant_a'));
    if n < 15 then raise exception 'audit log has % entries', n; end if;
    raise notice 'PASS: owner removes a member; audit log readable (% entries)', n;
end $$;
select pg_temp.act('tech_a') \gset
do $$ begin
    if exists (select 1 from public.remote_saas_my_tenants()) then raise exception 'removed member still has tenant'; end if;
    begin perform * from public.remote_saas_devices(pg_temp.id('tenant_a')); raise exception 'removed member lists devices';
    exception when insufficient_privilege then null; end;
    if exists (select 1 from public.remote_device_enrollments) then raise exception 'removed member reads enrollments'; end if;
end $$;
reset role;
do $$ declare c public.remote_device_enrollments; h public.remote_device_enrollments; r record; begin
    if (select organization_id from public.users where id = pg_temp.id('tech_a_profile')) is not null then raise exception 'profile kept organization'; end if;
    select * into c from public.remote_device_enrollments where device_id = pg_temp.id('d1');
    select * into h from public.remote_device_enrollments where device_id = pg_temp.id('d4');
    select * into r from remote_private.saas_support_pair(pg_temp.id('tech_a_profile'), c.device_id, c.generation, c.public_key,
        pg_temp.id('owner_a_profile'), h.device_id, h.generation, h.public_key, false);
    if r.allowed then raise exception 'removed member keeps sessions'; end if;
    raise notice 'PASS: a removed member loses tenant, devices and sessions';
end $$;
set local role authenticated;
select pg_temp.act('admin_a') \gset
do $$ begin
    begin perform public.remote_saas_audit_log(pg_temp.id('tenant_b')); raise exception 'foreign audit log';
    exception when insufficient_privilege then null; end;
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- existing staff paths unchanged'
select pg_temp.act('staff', 'aal2') \gset
do $$ declare device uuid; e public.remote_device_enrollments; secret text; r record; begin
    if exists (select 1 from public.remote_enrollment_companies() c where c.client_id in (pg_temp.id('client_a'), pg_temp.id('client_b'))) then
        raise exception 'staff offered SaaS tenants';
    end if;
    begin perform public.remote_add_device(pg_temp.id('client_a'), 'Staff adds', 'windows'); raise exception 'staff added to SaaS tenant';
    exception when insufficient_privilege then null; end;
    device := public.remote_add_device(pg_temp.id('ism_client'), 'Linked PC', 'windows');
    e := public.remote_enroll_device(device, pg_temp.key(30));
    if e.connection_id not between 100000000 and 999999999 then raise exception 'staff enroll'; end if;
    if not exists (select 1 from public.remote_device_enrollments x where x.device_id = device) then
        raise exception 'staff lost visibility of linked-company enrollments';
    end if;
    secret := public.remote_create_rollout_token(pg_temp.id('ism_client'), 'Linked rollout', 1, 2);
    perform pg_temp.remember('linked_device', device);
    perform set_config('t.linked_secret', secret, true);
    raise notice 'PASS: staff add/enrol/rollout for linked ISM companies unchanged; SaaS tenants not offered to staff';
end $$;
reset role;
set local role anon;
do $$ declare r record; issued bigint := extract(epoch from now())::bigint; e record; begin
    select * into r from public.remote_enroll_with_token(current_setting('t.linked_secret'), 'Linked rollout PC', 'windows', pg_temp.key(31));
    if r.tenant <> pg_temp.id('ism_org') then raise exception 'linked rollout'; end if;
    if not public.remote_device_self_remove(r.device_id, r.connection_id, r.generation, issued,
            pg_temp.sign(31, public.remote_device_self_remove_message(r.device_id, r.connection_id, r.generation, issued))) then
        raise exception 'linked self removal';
    end if;
    perform pg_temp.remember('linked_rollout_device', r.device_id);
    raise notice 'PASS: linked-company rollout unchanged; a linked device revokes itself';
end $$;
reset role;
do $$ begin
    if not exists (select 1 from public.devices where id = pg_temp.id('linked_rollout_device'))
        or (select revoked_at from public.remote_device_enrollments where device_id = pg_temp.id('linked_rollout_device')) is null then
        raise exception 'linked self removal must revoke and keep the ISM record';
    end if;
end $$;

\echo 'ALL REMOTE SAAS TESTS PASSED'
rollback;
