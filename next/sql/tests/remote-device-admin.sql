-- Device administration, D1 company policy, D8 unattended grants and automatic access, app versions
-- (migration 031). One transaction that is rolled back; synthetic accounts only (*.example.test).
--   psql -v ON_ERROR_STOP=1 -f next/sql/tests/remote-device-admin.sql
\set ON_ERROR_STOP on
\set QUIET on
begin;
set local client_min_messages = notice;

create function pg_temp.act(who text, aal text default 'aal1') returns void language plpgsql as $$ begin
    perform set_config('request.jwt.claim.sub', '', true);
    perform set_config('request.jwt.claims', case when who is null then '' else json_build_object(
        'sub', current_setting('t.' || who), 'role', 'authenticated', 'aal', aal,
        'amr', json_build_array(json_build_object('method', 'password')))::text end, true);
end $$;
create function pg_temp.id(name text) returns uuid language sql as $$ select current_setting('t.' || name)::uuid $$;
create function pg_temp.num(name text) returns integer language sql as $$ select current_setting('t.' || name)::integer $$;
create function pg_temp.remember(name text, value text) returns void language sql as $$ select set_config('t.' || name, value, true) $$;
create function pg_temp.check(ok boolean, what text) returns void language plpgsql as $$ begin
    if not coalesce(ok, false) then raise exception 'FAILED: %', what; end if;
end $$;
-- true when the statement fails with this SQLSTATE and (when given) this hint.
create function pg_temp.fails(q text, state text, want_hint text default null) returns boolean language plpgsql as $$
declare got_hint text; begin
    execute q;
    raise notice 'no error (wanted % %): %', state, want_hint, q;
    return false;
exception when others then
    get stacked diagnostics got_hint = pg_exception_hint;
    if sqlstate = state and (want_hint is null or got_hint = want_hint) then return true; end if;
    raise notice 'got % % hint=% (wanted % %): %', sqlstate, sqlerrm, got_hint, state, want_hint, q;
    return false;
end $$;
create function pg_temp.key(seed text) returns bytea language sql as $$ select decode(md5(seed) || md5(seed || 'b'), 'hex') $$;
-- The policy decision for a pair of enrolled devices (as the admin API asks it).
create function pg_temp.saas_allowed(ctrl_profile text, ctrl_dev text, host_profile text, host_dev text, unattended boolean)
returns boolean language sql security definer as $$
    select p.allowed from public.remote_device_enrollments ce, public.remote_device_enrollments he,
        lateral remote_private.saas_support_pair(
        pg_temp.id(ctrl_profile), pg_temp.id(ctrl_dev), ce.generation, ce.public_key,
        pg_temp.id(host_profile), pg_temp.id(host_dev), he.generation, he.public_key, unattended) p
    where ce.device_id = pg_temp.id(ctrl_dev) and he.device_id = pg_temp.id(host_dev) $$;
create function pg_temp.udc(profile text, dev text) returns table (unattended_allowed boolean, auto_access boolean)
language sql security definer as $$ select * from remote_private.unattended_device_check(pg_temp.id(profile), pg_temp.id(dev)) $$;
create function pg_temp.audit_count(dev text, what text) returns bigint language sql security definer as $$
    select count(*) from remote_private.device_admin_audit where device_id = pg_temp.id(dev) and action = what $$;

do $$ declare n text; begin
    foreach n in array array['owner','admin','tech','member','other','staff','agent','outsider','cadmin'] loop
        perform pg_temp.remember(n, gen_random_uuid()::text);
        insert into auth.users(id, email, email_confirmed_at, aud, role, raw_user_meta_data)
        values (pg_temp.id(n), 'qa-devadmin-' || n || '-' || substr(md5(random()::text), 1, 6) || '@example.test',
                now(), 'authenticated', 'authenticated', jsonb_build_object('name', 'QA ' || n));
    end loop;
end $$;
create function pg_temp.email(who text) returns text language sql security definer as $$
    select email from auth.users where id = current_setting('t.' || who)::uuid $$;

\echo '-- SaaS tenants A and B, devices, members'
set local role authenticated;
select pg_temp.act('owner');
do $$ declare r jsonb; d record; g uuid; begin
    r := public.remote_saas_signup('QA Device Admin Alpha', true);
    perform pg_temp.remember('tenant', r->>'tenant_id');
    perform pg_temp.remember('client', r->>'client_id');
    g := public.remote_saas_create_group(pg_temp.id('tenant'), 'QA Admin Group');
    perform pg_temp.remember('group', g::text);
    select * into d from public.remote_saas_add_this_device(pg_temp.id('tenant'), 'QA Host', 'windows', pg_temp.key('da-1'), null);
    perform pg_temp.remember('host', d.device_id::text); perform pg_temp.remember('host_conn', d.connection_id::text);
    select * into d from public.remote_saas_add_this_device(pg_temp.id('tenant'), 'QA Controller', 'macos', pg_temp.key('da-2'), null);
    perform pg_temp.remember('ctrl', d.device_id::text);
    select * into d from public.remote_saas_add_this_device(pg_temp.id('tenant'), 'QA Spare', 'linux', pg_temp.key('da-3'), null);
    perform pg_temp.remember('spare', d.device_id::text); perform pg_temp.remember('spare_conn', d.connection_id::text);
end $$;
select pg_temp.act('other');
do $$ declare r jsonb; d record; begin
    r := public.remote_saas_signup('QA Device Admin Beta', true);
    perform pg_temp.remember('tenant_b', r->>'tenant_id');
    perform pg_temp.remember('client_b', r->>'client_id');
    select * into d from public.remote_saas_add_this_device(pg_temp.id('tenant_b'), 'QA Beta PC', 'linux', pg_temp.key('da-4'), null);
    perform pg_temp.remember('dev_b', d.device_id::text);
end $$;
reset role;
select pg_temp.act(null);
do $$ declare org uuid; prof uuid; n text; begin
    select organization_id into org from remote_saas.tenants where id = pg_temp.id('tenant');
    foreach n in array array['admin','tech','member'] loop
        insert into public.users(auth_user_id, name, email, role, organization_id, is_active)
        values (pg_temp.id(n), 'QA ' || n, pg_temp.email(n), 'user', org, true) returning id into prof;
        insert into remote_saas.members(tenant_id, user_id, role)
        values (pg_temp.id('tenant'), prof, case n when 'tech' then 'technician' else n end);
        perform pg_temp.remember('p_' || n, prof::text);
    end loop;
    perform pg_temp.remember('p_owner', (select user_id::text from remote_saas.members where tenant_id = pg_temp.id('tenant') and role = 'owner'));
    perform pg_temp.remember('p_other', (select user_id::text from remote_saas.members where tenant_id = pg_temp.id('tenant_b') and role = 'owner'));
end $$;

\echo '-- list: admins see can_admin, technicians see without it, another tenant sees nothing'
set local role authenticated;
select pg_temp.act('owner');
select pg_temp.check((select count(*) from public.remote_admin_devices(null, null)) = 3, 'owner lists 3 devices');
select pg_temp.check((select bool_and(can_admin) from public.remote_admin_devices()), 'owner can administer');
select pg_temp.check((select unattended_default = false and unattended_allowed and unattended_setting = 'default'
    and company_kind = 'saas' and owner_id = pg_temp.id('p_owner') from public.remote_admin_devices() where device_id = pg_temp.id('host')),
    'D1: SaaS default off, allowed, owner shown');
select pg_temp.check((select count(*) from public.remote_admin_devices(null, 'spare')) = 1, 'search by name');
select pg_temp.check((select count(*) from public.remote_admin_devices(null, current_setting('t.spare_conn'))) = 1, 'search by ID');
select pg_temp.act('tech');
select pg_temp.check((select count(*) from public.remote_admin_devices()) = 3 and not (select bool_or(can_admin) from public.remote_admin_devices()),
    'technician sees, may not administer');
select pg_temp.act('other');
select pg_temp.check((select count(*) from public.remote_admin_devices(pg_temp.id('client'), null)) = 0, 'other tenant sees none of A');
select pg_temp.check((select count(*) from public.remote_admin_devices()) = 1, 'other tenant sees only its own');
select pg_temp.act('outsider');
select pg_temp.check((select count(*) from public.remote_admin_devices()) = 0, 'an account without profile sees nothing');

\echo '-- rename'
select pg_temp.act('owner');
select pg_temp.check((public.remote_admin_rename_device(pg_temp.id('host'), '  Reception PC ')->>'name') = 'Reception PC', 'owner renames');
select pg_temp.check((select connection_id = pg_temp.num('host_conn') from public.remote_admin_devices() where device_id = pg_temp.id('host')),
    'rename keeps the connection ID');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_rename_device(%L, %L, %L)', pg_temp.id('host'), 'X',
    '2000-01-01T00:00:00Z'), 'PT409', 'changed'), 'stale expected_update');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_rename_device(%L, %L)', pg_temp.id('host'), ''), '22023', 'invalid_name'), 'empty name');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_rename_device(%L, %L)', pg_temp.id('host'), repeat('a', 61)), '22023', 'invalid_name'), 'long name');
select pg_temp.act('admin');
select pg_temp.check((public.remote_admin_rename_device(pg_temp.id('spare'), 'Reception PC')->>'name') = 'Reception PC (2)', 'admin renames, unique in tenant');
select pg_temp.act('tech');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_rename_device(%L, %L)', pg_temp.id('host'), 'Tech'), '42501', 'not_admin'), 'technician may not rename');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_rename_device(%L, %L, %L)', pg_temp.id('tenant'), pg_temp.id('host'), 'Tech'), '42501'),
    'technician may not rename through the old function');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_remove_device(%L, %L)', pg_temp.id('tenant'), pg_temp.id('host')), '42501'),
    'technician may not remove through the old function');
select pg_temp.act('other');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_rename_device(%L, %L)', pg_temp.id('host'), 'Mine'), 'PT404', 'device_not_found'),
    'other tenant: not found');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_rename_device(%L, %L)', gen_random_uuid(), 'Mine'), 'PT404', 'device_not_found'),
    'unknown device: not found');

\echo '-- move, note, favourite, unattended setting'
select pg_temp.act('owner');
select pg_temp.check((public.remote_admin_move_device(pg_temp.id('host'), pg_temp.id('group'))->>'group_name') = 'QA Admin Group', 'move to group');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_move_device(%L, %L)', pg_temp.id('host'), gen_random_uuid()), '22023', 'invalid_group'),
    'move to unknown group');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_move_device(%L, null, %L)', pg_temp.id('host'), 'Text'), '22023', 'invalid_group'),
    'SaaS: no free-text group');
select pg_temp.check((public.remote_admin_move_device(pg_temp.id('host'), null)->>'group_id') is null, 'move out of group');
select pg_temp.check((public.remote_admin_set_device_note(pg_temp.id('host'), 'Desk 3')->>'note') = 'Desk 3', 'note');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_set_device_note(%L, %L)', pg_temp.id('host'), repeat('n', 501)), '22023', 'invalid_note'), 'long note');
select pg_temp.check(public.remote_admin_set_device_favorite(pg_temp.id('host'), true), 'favourite');
select pg_temp.check((select favorite from public.remote_admin_devices() where device_id = pg_temp.id('host')), 'favourite listed');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_set_device_unattended(%L, %L)', pg_temp.id('host'), 'sometimes'), '22023', 'invalid_setting'),
    'bad unattended setting');
select pg_temp.check((public.remote_admin_set_device_unattended(pg_temp.id('host'), 'on')->>'unattended_default')::boolean, 'unattended on: default true');
select pg_temp.act('tech');
select pg_temp.check(public.remote_admin_set_device_favorite(pg_temp.id('host'), true), 'technician may favourite');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_set_device_note(%L, %L)', pg_temp.id('host'), 'x'), '42501', 'not_admin'), 'technician may not note');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_set_device_unattended(%L, %L)', pg_temp.id('host'), 'off'), '42501', 'not_admin'),
    'technician may not change unattended');
reset role;
select pg_temp.check(pg_temp.audit_count('host', 'device.rename') = 1 and pg_temp.audit_count('host', 'device.move') = 2
    and pg_temp.audit_count('host', 'device.note') = 1 and pg_temp.audit_count('host', 'device.favorite') = 2
    and pg_temp.audit_count('host', 'device.unattended') = 1, 'every change audited');
select pg_temp.check((select count(*) from remote_saas.audit_log where tenant_id = pg_temp.id('tenant') and action like 'device.%') >= 6,
    'SaaS changes also in the tenant audit log');

\echo '-- D8: unattended needs a per-device grant (or the company setting); off refuses it'
select pg_temp.check(pg_temp.saas_allowed('p_tech', 'ctrl', 'p_owner', 'host', false), 'attended allowed');
select pg_temp.check(not pg_temp.saas_allowed('p_tech', 'ctrl', 'p_owner', 'host', true), 'unattended without grant refused');
select pg_temp.check(not pg_temp.saas_allowed('p_owner', 'ctrl', 'p_owner', 'host', true), 'even the owner needs a grant');
set local role authenticated;
select pg_temp.act('owner');
select pg_temp.check(public.remote_admin_set_unattended_grant(pg_temp.id('host'), pg_temp.id('p_tech'), true), 'grant to technician');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_set_unattended_grant(%L, %L, true)', pg_temp.id('host'), pg_temp.id('p_member')),
    '22023', 'invalid_user'), 'no grant for a member role');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_set_unattended_grant(%L, %L, true)', pg_temp.id('host'), pg_temp.id('p_other')),
    '22023', 'invalid_user'), 'no grant for another tenant');
select pg_temp.check((select count(*) from public.remote_admin_unattended_grants(pg_temp.id('host'))) = 1, 'grant listed');
select pg_temp.act('tech');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_set_unattended_grant(%L, %L, true)', pg_temp.id('host'), pg_temp.id('p_tech')),
    '42501', 'not_admin'), 'technician may not grant');
reset role;
select pg_temp.check(pg_temp.saas_allowed('p_tech', 'ctrl', 'p_owner', 'host', true), 'unattended with grant allowed');
set local role authenticated;
select pg_temp.act('owner');
select public.remote_admin_set_device_unattended(pg_temp.id('host'), 'off');
reset role;
select pg_temp.check(not pg_temp.saas_allowed('p_tech', 'ctrl', 'p_owner', 'host', true), 'device off: unattended refused despite grant');
select pg_temp.check(pg_temp.saas_allowed('p_tech', 'ctrl', 'p_owner', 'host', false), 'device off: attended still allowed');
select pg_temp.check((select not unattended_default and not unattended_allowed from remote_private.device_unattended_policy(pg_temp.id('host'))),
    'policy reports off');
set local role authenticated;
select pg_temp.act('owner');
select public.remote_admin_set_device_unattended(pg_temp.id('host'), 'default');
reset role;
-- Company setting: only an owner of this tenant, audited, never across tenants.
select pg_temp.check(pg_temp.fails(format($q$insert into remote_private.company_auto_access(client_id, profile_id, granted_by, reason)
    values (%L, %L, 'test', 'test')$q$, pg_temp.id('client'), pg_temp.id('p_tech')), '22023'), 'technician not allowed in the company setting');
select pg_temp.check(pg_temp.fails(format($q$insert into remote_private.company_auto_access(client_id, profile_id, granted_by, reason)
    values (%L, %L, 'test', 'test')$q$, pg_temp.id('client'), pg_temp.id('p_other')), '22023'), 'another tenant''s owner refused');
insert into remote_private.company_auto_access(client_id, profile_id, granted_by, reason)
values (pg_temp.id('client'), pg_temp.id('p_owner'), 'test', 'test');
select pg_temp.check(pg_temp.saas_allowed('p_owner', 'ctrl', 'p_owner', 'host', true), 'company setting: owner allowed without grant');
select pg_temp.check(not pg_temp.saas_allowed('p_admin', 'ctrl', 'p_owner', 'host', true), 'company setting: an unlisted admin still needs a grant');
select pg_temp.check((select count(*) from remote_private.company_policy_audit where client_id = pg_temp.id('client') and action = 'auto_access.grant') = 1,
    'company setting audited');
delete from remote_private.company_auto_access where client_id = pg_temp.id('client');
select pg_temp.check((select count(*) from remote_private.company_policy_audit where client_id = pg_temp.id('client') and action = 'auto_access.revoke') = 1,
    'removal audited');
select pg_temp.check(not pg_temp.saas_allowed('p_owner', 'ctrl', 'p_owner', 'host', true), 'setting removed: refused again');

\echo '-- D1: company policy reaches the device policy; the per-device setting overrides it'
insert into remote_private.company_policies(client_id, unattended_default, updated_by) values (pg_temp.id('client'), true, 'test');
select pg_temp.check((select unattended_default from remote_private.device_unattended_policy(pg_temp.id('spare'))), 'company default on');
select pg_temp.check((select p.unattended_default and p.unattended_allowed from public.remote_device_enrollments e,
    remote_private.device_policy_for(e.connection_id, e.device_id, pg_temp.id('p_owner'), e.generation, e.public_key) p
    where e.device_id = pg_temp.id('spare')), 'device_policy_for with the current binding');
select pg_temp.check((select count(*) from public.remote_device_enrollments e,
    remote_private.device_policy_for(e.connection_id, e.device_id, pg_temp.id('p_owner'), e.generation + 1, e.public_key) p
    where e.device_id = pg_temp.id('spare')) = 0, 'stale binding gets no policy');
select pg_temp.check((select count(*) from remote_private.company_policy_audit where client_id = pg_temp.id('client') and action = 'policy.set') = 1,
    'policy change audited');

\echo '-- remove: confirmation, revocation, cleanup, visible to the admin API'
set local role authenticated;
select pg_temp.act('owner');
select public.remote_admin_set_device_note(pg_temp.id('spare'), 'to go');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_remove_device(%L, 123456789)', pg_temp.id('spare')), '22023', 'confirm_mismatch'),
    'remove needs the device ID');
select pg_temp.act('tech');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_remove_device(%L, %s)', pg_temp.id('spare'), pg_temp.num('spare_conn')), '42501', 'not_admin'),
    'technician may not remove');
select pg_temp.act('owner');
reset role;
select pg_temp.remember('before_remove', now()::text);
select pg_temp.check(not remote_private.device_removed_since(pg_temp.id('host'), now() - interval '1 hour'), 'live device not removed');
set local role authenticated;
select pg_temp.act('owner');
select pg_temp.check((public.remote_admin_remove_device(pg_temp.id('spare'), pg_temp.num('spare_conn'))->>'removed')::boolean, 'owner removes');
select pg_temp.check((select count(*) from public.remote_admin_devices() where device_id = pg_temp.id('spare')) = 0, 'removed device gone');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_remove_device(%L, %s)', pg_temp.id('spare'), pg_temp.num('spare_conn')), 'PT404', 'device_not_found'),
    'remove twice: not found');
reset role;
select pg_temp.check(remote_private.device_removed_since(pg_temp.id('spare'), now() - interval '1 hour'), 'admin API sees the removal');
select pg_temp.check((select count(*) from remote_private.device_admin_settings where device_id = pg_temp.id('spare')) = 0, 'settings deleted');
select pg_temp.check(pg_temp.audit_count('spare', 'device.remove') = 1, 'removal audited');

\echo '-- ISM company devices: staff admin with MFA, company administrator, nobody else'
reset role;
select pg_temp.act(null);
do $$ declare org uuid; org2 uuid; cl uuid; cl2 uuid; dev uuid; dev2 uuid; prof uuid; begin
    insert into public.organizations(name, slug, is_active, mixel_org_id)
    values ('QA DevAdmin ISM', 'qa-devadmin-' || substr(md5(random()::text), 1, 8), true, gen_random_uuid()) returning id into org;
    insert into public.organizations(name, slug, is_active, mixel_org_id)
    values ('QA DevAdmin ISM 2', 'qa-devadmin2-' || substr(md5(random()::text), 1, 8), true, gen_random_uuid()) returning id into org2;
    insert into public.clients(name, key, onboarding_slug, organization_id, is_active) values ('QA DevAdmin Client', 'QDA' || substr(md5(random()::text), 1, 6), 'qa-da-' || substr(md5(random()::text), 1, 8), org, true) returning id into cl;
    insert into public.clients(name, key, onboarding_slug, organization_id, is_active) values ('QA DevAdmin Client 2', 'QDB' || substr(md5(random()::text), 1, 6), 'qa-db-' || substr(md5(random()::text), 1, 8), org2, true) returning id into cl2;
    perform pg_temp.remember('ism_org', org::text); perform pg_temp.remember('ism_client', cl::text);
    perform pg_temp.remember('ism_client2', cl2::text);
    insert into public.users(auth_user_id, name, email, role, organization_id, is_active)
    values (pg_temp.id('staff'), 'QA staff', pg_temp.email('staff'), 'admin', null, true) returning id into prof;
    perform pg_temp.remember('p_staff', prof::text);
    insert into public.users(auth_user_id, name, email, role, organization_id, is_active)
    values (pg_temp.id('agent'), 'QA agent', pg_temp.email('agent'), 'agent', null, true);
    insert into public.users(auth_user_id, name, email, role, organization_id, is_active)
    values (pg_temp.id('cadmin'), 'QA company user', pg_temp.email('cadmin'), 'user', org2, true);
    insert into public.devices(client_id, name, type, os) values (cl, 'QA ISM PC', 'desktop', 'windows') returning id into dev;
    insert into public.remote_device_enrollments(device_id, connection_id, public_key, enrolled_by)
    values (dev, 100000000 + (random() * 899999999)::integer, pg_temp.key('da-ism-1'), prof);
    perform pg_temp.remember('ism_dev', dev::text);
    perform pg_temp.remember('ism_conn', (select connection_id::text from public.remote_device_enrollments where device_id = dev));
    insert into public.devices(client_id, name, type, os) values (cl2, 'QA ISM PC 2', 'desktop', 'linux') returning id into dev2;
    insert into public.remote_device_enrollments(device_id, connection_id, public_key, enrolled_by)
    values (dev2, 100000000 + (random() * 899999999)::integer, pg_temp.key('da-ism-2'), prof);
    perform pg_temp.remember('ism_dev2', dev2::text);
end $$;
set local role authenticated;
select pg_temp.act('staff', 'aal2');
select pg_temp.check((select company_kind = 'company' and can_admin from public.remote_admin_devices(pg_temp.id('ism_client'), null)), 'staff admin lists ISM device');
select pg_temp.check((public.remote_admin_rename_device(pg_temp.id('ism_dev'), 'QA ISM Renamed')->>'name') = 'QA ISM Renamed', 'staff admin renames');
select pg_temp.check((public.remote_admin_move_device(pg_temp.id('ism_dev'), null, 'Front desk')->>'group_name') = 'Front desk', 'ISM free-text group');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_move_device(%L, %L)', pg_temp.id('ism_dev'), gen_random_uuid()), '22023', 'invalid_group'),
    'ISM: no group id');
select pg_temp.check(public.remote_admin_set_device_favorite(pg_temp.id('ism_dev'), true), 'staff favourite');
select pg_temp.check((public.remote_admin_set_device_unattended(pg_temp.id('ism_dev'), 'off')->>'unattended_allowed')::boolean = false, 'staff sets off');
select pg_temp.check((select not unattended_allowed and auto_access = false from pg_temp.udc('p_staff', 'ism_dev')),
    'admin API check: off, no automatic access');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_set_unattended_grant(%L, %L, true)', pg_temp.id('ism_dev'), pg_temp.id('p_staff')),
    '22023', 'not_saas'), 'ISM grants stay in the admin console');
select pg_temp.act('staff', 'aal1');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_rename_device(%L, %L)', pg_temp.id('ism_dev'), 'x'), '42501', 'mfa_required'),
    'staff without MFA');
select pg_temp.act('agent', 'aal2');
select pg_temp.check((select not can_admin from public.remote_admin_devices(pg_temp.id('ism_client'), null)), 'staff agent sees, no admin');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_rename_device(%L, %L)', pg_temp.id('ism_dev'), 'x'), '42501', 'not_admin'),
    'staff agent may not rename');
select pg_temp.act('cadmin', 'aal2');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_rename_device(%L, %L)', pg_temp.id('ism_dev'), 'x'), 'PT404', 'device_not_found'),
    'user of another company: not found');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_rename_device(%L, %L)', pg_temp.id('ism_dev2'), 'x'), '42501', 'not_admin'),
    'company user without administrator grant may not rename');
select pg_temp.act('owner');
select pg_temp.check(pg_temp.fails(format('select public.remote_admin_rename_device(%L, %L)', pg_temp.id('ism_dev'), 'x'), 'PT404', 'device_not_found'),
    'SaaS owner: ISM device not found');
reset role;
-- Automatic access for ISM companies: staff only for the operator's own organization.
select pg_temp.check(pg_temp.fails(format($q$insert into remote_private.company_auto_access(client_id, profile_id, granted_by, reason)
    values (%L, %L, 'test', 'test')$q$, pg_temp.id('ism_client'), pg_temp.id('p_staff')), '22023'), 'staff refused for a customer company');
delete from remote_private.operator_organization;
insert into remote_private.operator_organization(organization_id) values (pg_temp.id('ism_org'));
insert into remote_private.company_auto_access(client_id, profile_id, granted_by, reason)
values (pg_temp.id('ism_client'), pg_temp.id('p_staff'), 'test', 'test');
select pg_temp.check((select auto_access from pg_temp.udc('p_staff', 'ism_dev')),
    'staff automatic access to the operator company');
select pg_temp.check(not (select auto_access from pg_temp.udc('p_staff', 'ism_dev2')),
    'never to another company');
set local role authenticated;
select pg_temp.act('staff', 'aal2');
select pg_temp.check((public.remote_admin_remove_device(pg_temp.id('ism_dev'), pg_temp.num('ism_conn'))->>'removed')::boolean, 'staff removes ISM device');
reset role;
select pg_temp.check((select revoked_at is not null from public.remote_device_enrollments where device_id = pg_temp.id('ism_dev')), 'ISM enrolment revoked');
select pg_temp.check(remote_private.device_removed_since(pg_temp.id('ism_dev'), now() - interval '1 hour'), 'ISM removal visible');
select pg_temp.check((select count(*) from remote_private.device_favorites where device_id = pg_temp.id('ism_dev')) = 0, 'ISM favourites deleted');

\echo '-- app versions: broker role only, strict body'
set local role authenticated;
select pg_temp.act('owner');
select pg_temp.check(pg_temp.fails($q$select public.remote_presence_versions('[]')$q$, '42501'), 'member cannot report versions');
reset role;
insert into remote_private.device_presence(connection_id, last_seen, online_since) values (pg_temp.num('host_conn'), now(), now())
on conflict (connection_id) do update set last_seen = now();
set local role mixel_remote_presence;
select pg_temp.check(public.remote_presence_versions(jsonb_build_array(jsonb_build_object('connection_id', pg_temp.num('host_conn'),
    'version', '2.0.19', 'platform', 'windows-x64'))) = 1, 'version reported');
select pg_temp.check(pg_temp.fails($q$select public.remote_presence_versions('[{"connection_id":123456789,"version":"2.0.19; drop","platform":"x"}]')$q$, '22023'), 'bad version');
select pg_temp.check(pg_temp.fails($q$select public.remote_presence_versions('[{"connection_id":123456789,"version":"2.0.19","platform":"x","extra":1}]')$q$, '22023'), 'extra key');
select pg_temp.check(pg_temp.fails($q$select public.remote_presence_versions('{}')$q$, '22023'), 'not an array');
reset role;
set local role authenticated;
select pg_temp.act('owner');
select pg_temp.check((select app_version = '2.0.19' and platform = 'windows-x64' from public.remote_admin_devices() where device_id = pg_temp.id('host')),
    'version in the Devices list');
reset role;

\echo 'remote-device-admin: all checks passed'
rollback;
