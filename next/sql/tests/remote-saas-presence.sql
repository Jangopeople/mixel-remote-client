-- Remote SaaS presence and company rename (migration 025). One transaction that is rolled back, so it
-- can run against the live database after the migration without leaving data behind.
--   psql -v ON_ERROR_STOP=1 -f next/sql/tests/remote-saas-presence.sql
\set ON_ERROR_STOP on
\set QUIET on
begin;
set local client_min_messages = notice;

create function pg_temp.act(who text) returns void language plpgsql as $$ begin
    perform set_config('request.jwt.claim.sub', '', true);
    perform set_config('request.jwt.claims', case when who is null then '' else json_build_object(
        'sub', current_setting('t.' || who), 'role', 'authenticated', 'aal', 'aal1',
        'amr', json_build_array(json_build_object('method', 'password')))::text end, true);
end $$;
create function pg_temp.id(name text) returns uuid language sql as $$ select current_setting('t.' || name)::uuid $$;
create function pg_temp.num(name text) returns integer language sql as $$ select current_setting('t.' || name)::integer $$;
create function pg_temp.remember(name text, value text) returns void language sql as $$ select set_config('t.' || name, value, true) $$;
create function pg_temp.check(ok boolean, what text) returns void language plpgsql as $$ begin
    if not coalesce(ok, false) then raise exception 'FAILED: %', what; end if;
end $$;
create function pg_temp.status(who text, device text) returns text language plpgsql as $$
declare s text; begin
    select p.status into s from public.remote_saas_device_presence(pg_temp.id('tenant')) p where p.device_id = pg_temp.id(device);
    return s;
end $$;

do $$ declare n text; begin
    foreach n in array array['owner','tech','other'] loop
        perform pg_temp.remember(n, gen_random_uuid()::text);
        insert into auth.users(id, email, email_confirmed_at, aud, role, raw_user_meta_data)
        values (pg_temp.id(n), 'qa-presence-' || n || '-' || substr(md5(random()::text), 1, 6) || '@example.test',
                now(), 'authenticated', 'authenticated', jsonb_build_object('name', 'QA ' || n));
    end loop;
end $$;
-- Start without a broker report (the live broker may have reported; this is rolled back).
delete from remote_private.presence_reports;

\echo '-- tenant, devices, technician'
set local role authenticated;
select pg_temp.act('owner');
do $$ declare r jsonb; g uuid; d record; begin
    r := public.remote_saas_signup('QA Presence Alpha');
    perform pg_temp.remember('tenant', r->>'tenant_id');
    g := public.remote_saas_create_group(pg_temp.id('tenant'), 'QA Locked');
    perform pg_temp.remember('group', g::text);
    select * into d from public.remote_saas_add_this_device(pg_temp.id('tenant'), 'QA Grouped', 'windows',
        decode(md5('qa-presence-1') || md5('qa-presence-1b'), 'hex'), g);
    perform pg_temp.remember('grouped', d.device_id::text);
    perform pg_temp.remember('grouped_id', d.connection_id::text);
    select * into d from public.remote_saas_add_this_device(pg_temp.id('tenant'), 'QA Free', 'macos',
        decode(md5('qa-presence-2') || md5('qa-presence-2b'), 'hex'), null);
    perform pg_temp.remember('free', d.device_id::text);
    perform pg_temp.remember('free_id', d.connection_id::text);
end $$;
select pg_temp.act('other');
do $$ declare r jsonb; begin
    r := public.remote_saas_signup('QA Presence Beta');
    perform pg_temp.remember('tenant_b', r->>'tenant_id');
end $$;
reset role;
select pg_temp.act(null);
do $$ declare org uuid; prof uuid; begin
    select organization_id into org from remote_saas.tenants where id = pg_temp.id('tenant');
    insert into public.users(auth_user_id, name, email, role, organization_id, is_active)
    values (pg_temp.id('tech'), 'QA tech', 'qa-presence-tech@example.test', 'user', org, true) returning id into prof;
    insert into remote_saas.members(tenant_id, user_id, role) values (pg_temp.id('tenant'), prof, 'technician');
end $$;

\echo '-- before any broker report: unknown'
set local role authenticated;
select pg_temp.act('owner');
select pg_temp.check(pg_temp.status('owner', 'grouped') = 'unknown' and pg_temp.status('owner', 'free') = 'unknown', 'unknown without reports');

\echo '-- only the broker role may report'
do $$ begin
    begin perform public.remote_presence_report(array[pg_temp.num('free_id')]); raise exception 'FAILED: member reported presence';
    exception when insufficient_privilege then null; end;
end $$;
reset role;
set local role anon;
do $$ begin
    begin perform public.remote_presence_report(array[1]); raise exception 'FAILED: anon reported presence';
    exception when insufficient_privilege then null; end;
end $$;
reset role;

\echo '-- report 1: grouped online, free never seen'
set local role mixel_remote_presence;
select pg_temp.check(public.remote_presence_report(array[pg_temp.num('grouped_id'), 5, 999999999]) = 2, 'report counts valid ids only');
-- A second report in the same 10 s is ignored.
select pg_temp.check(public.remote_presence_report(array[pg_temp.num('free_id')]) = 0, 'throttled report');
do $$ begin
    begin perform * from remote_private.device_presence; raise exception 'FAILED: broker role reads tables';
    exception when insufficient_privilege then null; end;
end $$;
reset role;
set local role authenticated;
select pg_temp.act('owner');
select pg_temp.check(pg_temp.status('owner', 'grouped') = 'online', 'grouped online');
select pg_temp.check(pg_temp.status('owner', 'free') = 'never', 'free never seen');
do $$ declare r record; begin
    select * into r from public.remote_saas_device_presence(pg_temp.id('tenant')) p where p.device_id = pg_temp.id('grouped');
    perform pg_temp.check(r.online_since is not null and r.last_seen is not null and r.checked_at is not null, 'online fields');
end $$;

\echo '-- report 2 (40 s later): grouped gone, free online'
reset role;
update remote_private.presence_reports set reported_at = now() - interval '40 seconds';
update remote_private.device_presence set last_seen = now() - interval '40 seconds', online_since = now() - interval '2 hours';
set local role mixel_remote_presence;
select pg_temp.check(public.remote_presence_report(array[pg_temp.num('free_id')]) = 1, 'second report');
reset role;
set local role authenticated;
select pg_temp.act('owner');
do $$ declare r record; begin
    select * into r from public.remote_saas_device_presence(pg_temp.id('tenant')) p where p.device_id = pg_temp.id('grouped');
    perform pg_temp.check(r.status = 'offline' and r.last_seen < now() and r.online_since is null, 'grouped offline with last seen');
    select * into r from public.remote_saas_device_presence(pg_temp.id('tenant')) p where p.device_id = pg_temp.id('free');
    perform pg_temp.check(r.status = 'online' and r.online_since = now(), 'free online since now');
end $$;

\echo '-- report 3: free stays online and keeps online_since'
reset role;
update remote_private.presence_reports set reported_at = now() - interval '30 seconds';
update remote_private.device_presence set last_seen = now() - interval '30 seconds', online_since = now() - interval '1 hour'
    where connection_id = pg_temp.num('free_id');
set local role mixel_remote_presence;
select public.remote_presence_report(array[pg_temp.num('free_id')]);
reset role;
set local role authenticated;
select pg_temp.act('owner');
do $$ declare r record; begin
    select * into r from public.remote_saas_device_presence(pg_temp.id('tenant')) p where p.device_id = pg_temp.id('free');
    perform pg_temp.check(r.status = 'online' and r.online_since <= now() - interval '59 minutes', 'online_since kept');
end $$;

\echo '-- broker silent for 3 minutes: unknown'
reset role;
update remote_private.presence_reports set reported_at = now() - interval '4 minutes';
set local role authenticated;
select pg_temp.act('owner');
select pg_temp.check(pg_temp.status('owner', 'free') = 'unknown', 'stale broker report = unknown');
reset role;
update remote_private.presence_reports set reported_at = now();

\echo '-- access: technician sees only the device in no group; other tenants see nothing'
set local role authenticated;
select pg_temp.act('tech');
select pg_temp.check((select count(*) from public.remote_saas_device_presence(pg_temp.id('tenant'))) = 1
    and pg_temp.status('tech', 'free') is not null and pg_temp.status('tech', 'grouped') is null, 'technician scope');
select pg_temp.act('other');
do $$ begin
    begin perform public.remote_saas_device_presence(pg_temp.id('tenant')); raise exception 'FAILED: other tenant sees presence';
    exception when insufficient_privilege then null; end;
end $$;

\echo '-- rename'
select pg_temp.act('tech');
do $$ begin
    begin perform public.remote_saas_rename_tenant(pg_temp.id('tenant'), 'QA Hijack'); raise exception 'FAILED: technician renamed';
    exception when insufficient_privilege then null; end;
end $$;
select pg_temp.act('other');
do $$ begin
    begin perform public.remote_saas_rename_tenant(pg_temp.id('tenant'), 'QA Hijack'); raise exception 'FAILED: other tenant renamed';
    exception when insufficient_privilege then null; end;
end $$;
select pg_temp.act('owner');
do $$ begin
    begin perform public.remote_saas_rename_tenant(pg_temp.id('tenant'), ' x '); raise exception 'FAILED: short name accepted';
    exception when invalid_parameter_value then null; end;
end $$;
select pg_temp.check(public.remote_saas_rename_tenant(pg_temp.id('tenant'), '  QA Presence Renamed  ') = 'QA Presence Renamed', 'rename');
select pg_temp.check((select name from public.remote_saas_my_tenants()) = 'QA Presence Renamed', 'my tenants shows the new name');
select pg_temp.check((public.remote_saas_tenant_overview(pg_temp.id('tenant')))->>'name' = 'QA Presence Renamed', 'overview new name');
reset role;
select pg_temp.check((select o.name from public.organizations o join remote_saas.tenants t on t.organization_id = o.id
    where t.id = pg_temp.id('tenant')) = 'QA Presence Renamed', 'organization renamed');
select pg_temp.check((select slug from remote_saas.tenants where id = pg_temp.id('tenant')) like 'qa-presence-alpha%', 'slug kept');
select pg_temp.check(exists (select 1 from remote_saas.audit_log where tenant_id = pg_temp.id('tenant') and action = 'tenant.rename'
    and detail->>'from' = 'QA Presence Alpha' and detail->>'to' = 'QA Presence Renamed'), 'rename audited');

\echo 'remote-saas-presence: all checks passed'
rollback;
