-- Remote SaaS security regressions (migration 028). Exploit checks against the ISM tables that tenant
-- profiles reach through the ordinary ISM row-level security, plus intra-tenant leaks. One
-- transaction that is rolled back, so it runs against the live database without leaving data behind.
-- Synthetic accounts only (*.example.test).
--   psql -v ON_ERROR_STOP=1 -f next/sql/tests/remote-saas-security.sql
-- Every check records PASS or FAIL; the script fails at the end when any check failed, so one run
-- shows every hole at once.
\set ON_ERROR_STOP on
\set QUIET on
begin;
set local client_min_messages = notice;
\o /dev/null

create temp table results(n serial, what text, ok boolean, detail text);
grant all on results to authenticated;
grant all on sequence results_n_seq to authenticated;

create function pg_temp.act(who text, aal text default 'aal1') returns void language plpgsql as $$ begin
    perform set_config('request.jwt.claim.sub', '', true);
    perform set_config('request.jwt.claims', case when who is null then '' else json_build_object(
        'sub', current_setting('t.' || who), 'role', 'authenticated', 'aal', aal,
        'amr', json_build_array(json_build_object('method', 'password')))::text end, true);
end $$;
create function pg_temp.id(name text) returns uuid language sql as $$ select current_setting('t.' || name)::uuid $$;
create function pg_temp.remember(name text, value uuid) returns void language sql as $$ select set_config('t.' || name, value::text, true) $$;
create function pg_temp.result(what text, ok boolean, detail text default null) returns void language sql as $$
    insert into pg_temp.results(what, ok, detail) values (what, coalesce(ok, false), detail) $$;
-- Runs one write as the current role and undoes it: PASS when it changes nothing or is refused.
create function pg_temp.attempt(what text, stmt text) returns void language plpgsql as $$
declare n integer;
begin
    begin
        execute stmt;
        get diagnostics n = row_count;
        raise exception using errcode = 'P0099', message = n::text;
    exception
        when sqlstate 'P0099' then perform pg_temp.result(what, sqlerrm = '0', sqlerrm || ' row(s) changed');
        when others then perform pg_temp.result(what, true, 'refused: ' || sqlerrm);
    end;
end $$;
create function pg_temp.key(n integer) returns bytea language sql as $$
    select (pgsodium.crypto_sign_seed_new_keypair(decode(lpad(to_hex(n), 64, '0'), 'hex'))).public $$;

-- Accounts. 'outsider' has a GoTrue account and no ISM profile (anyone can sign up at the portal).
do $$ declare n text; begin
    foreach n in array array['owner_a','admin_a','tech_a','member_a','owner_b','outsider','outsider2'] loop
        perform pg_temp.remember(n, gen_random_uuid());
        insert into auth.users(id, email, email_confirmed_at, aud, role, raw_user_meta_data)
        values (pg_temp.id(n), 'qa-sec-' || replace(n, '_', '-') || '-' || substr(md5(random()::text), 1, 6) || '@example.test',
                now(), 'authenticated', 'authenticated', jsonb_build_object('name', 'QA ' || n));
    end loop;
    -- An ordinary ISM customer organization (regression: ISM-wide self-join).
    perform pg_temp.remember('ism_org', gen_random_uuid());
    insert into public.organizations(id, name, slug, is_active)
    values (pg_temp.id('ism_org'), 'QA ISM Customer', 'qa-sec-ism-' || substr(md5(random()::text), 1, 6), true);
end $$;

set local role authenticated;
select pg_temp.act('owner_a');
do $$ declare r jsonb; begin
    r := public.remote_saas_signup('QA Sec Alpha');
    perform pg_temp.remember('tenant_a', (r->>'tenant_id')::uuid);
    perform pg_temp.remember('org_a', (r->>'organization_id')::uuid);
    perform pg_temp.remember('client_a', (r->>'client_id')::uuid);
end $$;
select pg_temp.act('owner_b');
do $$ declare r jsonb; begin
    r := public.remote_saas_signup('QA Sec Beta');
    perform pg_temp.remember('tenant_b', (r->>'tenant_id')::uuid);
end $$;
reset role;

-- Members of tenant A (inserted directly: the invitation path is covered by remote-saas.sql).
do $$ declare n text; r text; p uuid; begin
    foreach n in array array['admin_a','tech_a','member_a'] loop
        r := split_part(n, '_', 1); if r = 'tech' then r := 'technician'; end if;
        insert into public.users(auth_user_id, name, email, role, organization_id, is_active)
        select pg_temp.id(n), 'QA ' || n, u.email, 'user', pg_temp.id('org_a'), true from auth.users u where u.id = pg_temp.id(n)
        returning id into p;
        perform pg_temp.remember(n || '_profile', p);
        insert into remote_saas.members(tenant_id, user_id, role) values (pg_temp.id('tenant_a'), p, r);
    end loop;
    perform pg_temp.remember('owner_a_profile', (select id from public.users where auth_user_id = pg_temp.id('owner_a')));
    -- Two devices: one in a group the technician is not granted, one without a group, never enrolled.
    insert into remote_saas.device_groups(tenant_id, name) values (pg_temp.id('tenant_a'), 'Secret') returning id into p;
    perform pg_temp.remember('group_secret', p);
    insert into public.devices(client_id, name, type, os) values (pg_temp.id('client_a'), 'QA Sec Hidden', 'desktop', 'windows') returning id into p;
    perform pg_temp.remember('dev_hidden', p);
    insert into remote_saas.device_meta(device_id, tenant_id, group_id, added_via) values (p, pg_temp.id('tenant_a'), pg_temp.id('group_secret'), 'staff');
    insert into public.devices(client_id, name, type, os) values (pg_temp.id('client_a'), 'QA Sec Open', 'desktop', 'windows') returning id into p;
    perform pg_temp.remember('dev_open', p);
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- ISM tables reached by a tenant member through the ISM row-level security'
set local role authenticated;
select pg_temp.act('member_a');
do $$ declare n integer; begin
    perform pg_temp.attempt('member cannot deactivate the company organization',
        'update public.organizations set is_active = false where id = pg_temp.id(''org_a'')');
    perform pg_temp.attempt('member cannot relink the organization (kind, mixel_org_id)',
        'update public.organizations set kind = ''ism'', mixel_org_id = gen_random_uuid() where id = pg_temp.id(''org_a'')');
    perform pg_temp.attempt('member cannot rename the organization behind the RPC',
        'update public.organizations set name = ''Renamed by member'' where id = pg_temp.id(''org_a'')');
    perform pg_temp.attempt('member cannot insert device records (device limit, audit)',
        'insert into public.devices(client_id, name, type, os) select pg_temp.id(''client_a''), ''Filler '' || g, ''desktop'', ''linux'' from generate_series(1, 3) g');
    perform pg_temp.attempt('member cannot rename devices behind the RPC',
        'update public.devices set name = ''Renamed by member'' where id = pg_temp.id(''dev_open'')');
    perform pg_temp.attempt('member cannot delete devices behind the RPC',
        'delete from public.devices where id = pg_temp.id(''dev_open'')');
    perform pg_temp.attempt('member cannot delete another member''s profile (drops the membership)',
        'delete from public.users where id = pg_temp.id(''admin_a_profile'')');
    select count(*) into n from public.users where organization_id = pg_temp.id('org_a') and id <> pg_temp.id('member_a_profile');
    perform pg_temp.result('member cannot read other members'' profiles (emails) directly', n = 0, n || ' visible');
    select count(*) into n from public.devices where client_id = pg_temp.id('client_a');
    perform pg_temp.result('member reads no device records it may not reach', n = 0, n || ' visible');
    select count(*) into n from public.users where id = pg_temp.id('member_a_profile');
    perform pg_temp.result('member still reads its own profile', n = 1, n || ' visible');
end $$;

select pg_temp.act('tech_a');
do $$ declare n integer; begin
    select count(*) into n from public.devices where id = pg_temp.id('dev_hidden');
    perform pg_temp.result('technician cannot read devices of groups it is not granted', n = 0, n || ' visible');
    select count(*) into n from public.devices where id = pg_temp.id('dev_open');
    perform pg_temp.result('technician still reads ungrouped devices', n = 1, n || ' visible');
    select count(*) into n from public.users where organization_id = pg_temp.id('org_a');
    perform pg_temp.result('technician still reads the member list', n = 4, n || ' visible');
end $$;

select pg_temp.act('owner_a');
do $$ declare n integer; begin
    select count(*) into n from public.devices where client_id = pg_temp.id('client_a');
    perform pg_temp.result('owner still reads every device', n = 2, n || ' visible');
    select count(*) into n from public.organizations where id = pg_temp.id('org_a');
    perform pg_temp.result('owner still reads the organization', n = 1, n || ' visible');
    perform pg_temp.attempt('owner cannot deactivate the organization behind the RPC',
        'update public.organizations set is_active = false where id = pg_temp.id(''org_a'')');
    -- Renaming through the RPC keeps working.
    begin
        perform pg_temp.result('owner renames the company through the RPC',
            public.remote_saas_rename_tenant(pg_temp.id('tenant_a'), 'QA Sec Alpha Renamed') = 'QA Sec Alpha Renamed');
        perform pg_temp.result('organization name follows the RPC rename',
            (select name from public.organizations where id = pg_temp.id('org_a')) = 'QA Sec Alpha Renamed');
    exception when others then perform pg_temp.result('owner renames the company through the RPC', false, sqlerrm); end;
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- an account without a profile joining a company by inserting its own profile'
select pg_temp.act('outsider');
do $$ declare n integer; begin
    -- Not undone: the reads below show what such a profile would reach.
    begin
        insert into public.users(auth_user_id, name, email, role, organization_id, is_active)
        values (pg_temp.id('outsider'), 'Intruder', 'qa-sec-intruder@example.test', 'user', pg_temp.id('org_a'), true);
        perform pg_temp.result('outsider cannot insert a profile into a tenant organization', false, 'inserted');
    exception when others then perform pg_temp.result('outsider cannot insert a profile into a tenant organization', true, 'refused: ' || sqlerrm); end;
    select count(*) into n from public.users where organization_id = pg_temp.id('org_a') and auth_user_id <> pg_temp.id('outsider');
    perform pg_temp.result('outsider reads no tenant profiles', n = 0, n || ' visible');
    select count(*) into n from public.devices where client_id = pg_temp.id('client_a');
    perform pg_temp.result('outsider reads no tenant devices', n = 0, n || ' visible');
end $$;
-- Sign-up of an account without a profile keeps working (the RPC creates the profile).
select pg_temp.act('outsider2');
do $$ declare r jsonb; begin
    r := public.remote_saas_signup('QA Sec Gamma');
    perform pg_temp.result('sign-up still creates the owner profile', r->>'tenant_id' is not null);
exception when others then perform pg_temp.result('sign-up still creates the owner profile', false, sqlerrm); end $$;
-- An account without a profile cannot join an ordinary ISM customer either (ISM-wide).
reset role;
do $$ begin
    perform pg_temp.remember('outsider3', gen_random_uuid());
    insert into auth.users(id, email, email_confirmed_at, aud, role) values
        (pg_temp.id('outsider3'), 'qa-sec-outsider3-' || substr(md5(random()::text), 1, 6) || '@example.test', now(), 'authenticated', 'authenticated');
end $$;
set local role authenticated;
select pg_temp.act('outsider3');
do $$ begin
    perform pg_temp.attempt('outsider cannot insert a profile into an ISM customer organization',
        'insert into public.users(auth_user_id, name, email, role, organization_id, is_active) values (pg_temp.id(''outsider3''), ''Intruder'', ''qa-sec-intruder3@example.test'', ''user'', pg_temp.id(''ism_org''), true)');
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- a technician demoted to member keeps no device it only renamed or moved'
select pg_temp.act('tech_a');
do $$ begin
    perform public.remote_saas_rename_device(pg_temp.id('tenant_a'), pg_temp.id('dev_open'), 'QA Sec Open 2');
exception when others then perform pg_temp.result('technician renames a device through the RPC', false, sqlerrm); end $$;
select pg_temp.act('owner_a');
do $$ begin perform public.remote_saas_set_member_role(pg_temp.id('tenant_a'), pg_temp.id('tech_a_profile'), 'member');
exception when others then perform pg_temp.result('owner demotes the technician', false, sqlerrm); end $$;
select pg_temp.act('tech_a');
do $$ declare n integer; begin
    select count(*) into n from public.remote_saas_devices(pg_temp.id('tenant_a'));
    perform pg_temp.result('demoted technician (now member) sees no device it merely renamed', n = 0, n || ' visible');
exception when others then perform pg_temp.result('demoted technician (now member) sees no device it merely renamed', false, sqlerrm); end $$;

---------------------------------------------------------------------------------------------------
\echo '-- invitation e-mails from trial companies are capped'
select pg_temp.act('owner_b');
do $$ declare sent integer := 0; begin
    for i in 1..40 loop
        begin
            perform public.remote_saas_invite_member(pg_temp.id('tenant_b'), 'qa-sec-flood-' || i || '@example.test', 'member');
            sent := sent + 1;
        exception when program_limit_exceeded then exit; end;
    end loop;
    perform pg_temp.result('a trial company sends at most 20 invitations a day', sent = 20, sent || ' sent');
end $$;

---------------------------------------------------------------------------------------------------
\echo '-- company SSO provider must exist and belong to one company only'
reset role;
do $$ begin
    perform pg_temp.remember('sso_a', gen_random_uuid());
    insert into auth.sso_providers(id, created_at, updated_at) values (pg_temp.id('sso_a'), now(), now());
    update remote_saas.auth_settings set methods = '{password,sso}', sso_provider_id = pg_temp.id('sso_a') where tenant_id = pg_temp.id('tenant_a');
end $$;
set local role authenticated;
select pg_temp.act('owner_b');
do $$ begin
    begin
        perform public.remote_saas_set_auth_settings(pg_temp.id('tenant_b'), '{password,sso}', false, pg_temp.id('sso_a'));
        perform pg_temp.result('a company cannot claim another company''s SSO provider', false);
    exception when others then perform pg_temp.result('a company cannot claim another company''s SSO provider', true, sqlerrm); end;
    begin
        perform public.remote_saas_set_auth_settings(pg_temp.id('tenant_b'), '{password,sso}', false, gen_random_uuid());
        perform pg_temp.result('a company cannot name an SSO provider that does not exist', false);
    exception when others then perform pg_temp.result('a company cannot name an SSO provider that does not exist', true, sqlerrm); end;
end $$;

---------------------------------------------------------------------------------------------------
reset role;
\o
select case when ok then 'PASS' else 'FAIL' end || ': ' || what || coalesce(' (' || detail || ')', '') from results order by n;
do $$ declare bad integer; begin
    select count(*) into bad from pg_temp.results where not ok;
    if bad > 0 then raise exception '% security check(s) failed', bad; end if;
    raise notice 'ALL % SECURITY CHECKS PASSED', (select count(*) from pg_temp.results);
end $$;
rollback;
