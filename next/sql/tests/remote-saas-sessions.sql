-- Remote SaaS session reports, favorites and recent devices (migration 029). One transaction that is
-- rolled back, so it can run against the live database after the migration without leaving data
-- behind. Synthetic accounts only (*.example.test).
--   psql -v ON_ERROR_STOP=1 -f next/sql/tests/remote-saas-sessions.sql
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
-- true when the statement fails with this SQLSTATE (any other outcome is reported and false).
create function pg_temp.fails(q text, state text) returns boolean language plpgsql as $$ begin
    execute q;
    raise notice 'no error (wanted %): %', state, q;
    return false;
exception when others then
    if sqlstate = state then return true; end if;
    raise notice 'got % % (wanted %): %', sqlstate, sqlerrm, state, q;
    return false;
end $$;
-- The broker's time stamp `ago` seconds before now (whole seconds, as the broker sends them).
create function pg_temp.unix(ago integer) returns bigint language sql as $$ select floor(extract(epoch from now()))::bigint - ago $$;
create function pg_temp.at(ago integer) returns timestamptz language sql as $$ select to_timestamp(pg_temp.unix(ago)) $$;
create function pg_temp.ev_start(rid text, tenant text, tech text, device text, conn text, ago integer,
                                 unattended boolean default false, colleague boolean default false) returns jsonb
language sql as $$ select jsonb_build_object('type', 'start', 'id', pg_temp.id(rid), 'tenant', pg_temp.id(tenant),
    'technician', pg_temp.id(tech), 'controller_device', pg_temp.id('ctrl_device'), 'controller_connection', 123456789,
    'device', pg_temp.id(device), 'connection', pg_temp.num(conn), 'unattended', unattended, 'colleague', colleague,
    'at', pg_temp.unix(ago)) $$;
create function pg_temp.ev_end(rid text, ago integer) returns jsonb language sql as $$
    select jsonb_build_object('type', 'end', 'id', pg_temp.id(rid), 'at', pg_temp.unix(ago)) $$;
create function pg_temp.report(body jsonb) returns integer language sql as $$ select public.remote_session_report(body) $$;
create function pg_temp.row_of(rid text) returns remote_saas.sessions language sql security definer as $$
    select * from remote_saas.sessions where id = pg_temp.id(rid) $$;

do $$ declare n text; begin
    foreach n in array array['owner','admin','tech','tech2','member','other'] loop
        perform pg_temp.remember(n, gen_random_uuid()::text);
        insert into auth.users(id, email, email_confirmed_at, aud, role, raw_user_meta_data)
        values (pg_temp.id(n), 'qa-sessions-' || n || '-' || substr(md5(random()::text), 1, 6) || '@example.test',
                now(), 'authenticated', 'authenticated', jsonb_build_object('name', 'QA ' || n));
    end loop;
    foreach n in array array['s1','s2','s3','s4','s5','s6','s9','sb','sb_future','sb_old','sx1','sx2','sx3','s_old','ctrl_device'] loop
        perform pg_temp.remember(n, gen_random_uuid()::text);
    end loop;
end $$;
create function pg_temp.email(who text) returns text language sql security definer as $$
    select email from auth.users where id = current_setting('t.' || who)::uuid $$;

\echo '-- companies, devices, members'
set local role authenticated;
select pg_temp.act('owner');
do $$ declare r jsonb; g uuid; d record; begin
    r := public.remote_saas_signup('QA Sessions Alpha', true);
    perform pg_temp.remember('tenant', r->>'tenant_id');
    g := public.remote_saas_create_group(pg_temp.id('tenant'), 'QA Locked');
    perform pg_temp.remember('group', g::text);
    select * into d from public.remote_saas_add_this_device(pg_temp.id('tenant'), 'QA Free', 'windows',
        decode(md5('qa-sessions-1') || md5('qa-sessions-1b'), 'hex'), null);
    perform pg_temp.remember('dev_a1', d.device_id::text);
    perform pg_temp.remember('conn_a1', d.connection_id::text);
    select * into d from public.remote_saas_add_this_device(pg_temp.id('tenant'), 'QA Grouped', 'macos',
        decode(md5('qa-sessions-2') || md5('qa-sessions-2b'), 'hex'), g);
    perform pg_temp.remember('dev_a2', d.device_id::text);
    perform pg_temp.remember('conn_a2', d.connection_id::text);
end $$;
select pg_temp.act('other');
do $$ declare r jsonb; d record; begin
    r := public.remote_saas_signup('QA Sessions Beta', true);
    perform pg_temp.remember('tenant_b', r->>'tenant_id');
    select * into d from public.remote_saas_add_this_device(pg_temp.id('tenant_b'), 'QA Beta PC', 'linux',
        decode(md5('qa-sessions-3') || md5('qa-sessions-3b'), 'hex'), null);
    perform pg_temp.remember('dev_b1', d.device_id::text);
    perform pg_temp.remember('conn_b1', d.connection_id::text);
end $$;
reset role;
select pg_temp.act(null);
do $$ declare org uuid; prof uuid; n text; begin
    select organization_id into org from remote_saas.tenants where id = pg_temp.id('tenant');
    foreach n in array array['admin','tech','tech2','member'] loop
        insert into public.users(auth_user_id, name, email, role, organization_id, is_active)
        values (pg_temp.id(n), 'QA ' || n, pg_temp.email(n), 'user', org, true) returning id into prof;
        insert into remote_saas.members(tenant_id, user_id, role)
        values (pg_temp.id('tenant'), prof, case n when 'admin' then 'admin' when 'member' then 'member' else 'technician' end);
        perform pg_temp.remember('p_' || n, prof::text);
    end loop;
    perform pg_temp.remember('p_owner', (select user_id::text from remote_saas.members where tenant_id = pg_temp.id('tenant') and role = 'owner'));
    perform pg_temp.remember('p_other', (select user_id::text from remote_saas.members where tenant_id = pg_temp.id('tenant_b') and role = 'owner'));
end $$;
set local role authenticated;
select pg_temp.act('owner');
-- The technician may reach the grouped device; tech2 may not.
select pg_temp.check(public.remote_saas_set_group_access(pg_temp.id('tenant'), pg_temp.id('group'), array[pg_temp.id('p_tech')]) = 1, 'group access');
reset role;

\echo '-- only the broker role may report'
set local role authenticated;
select pg_temp.act('owner');
select pg_temp.check(pg_temp.fails($q$select public.remote_session_report('{"events":[]}')$q$, '42501'), 'member reported sessions');
reset role;
set local role anon;
select pg_temp.check(pg_temp.fails($q$select public.remote_session_report('{"events":[]}')$q$, '42501'), 'anon reported sessions');
reset role;
set local role mixel_remote_presence;
select pg_temp.check(pg_temp.fails('select * from remote_saas.sessions', '42501'), 'broker role reads the table');
select pg_temp.check(pg_temp.fails(format('select * from public.remote_saas_session_report(%L)', pg_temp.id('tenant')), '42501'),
    'broker role reads reports');
select pg_temp.check(pg_temp.report('{"events":[]}') = 0 and pg_temp.report('{}') = 0, 'empty reports');
reset role;

\echo '-- invalid bodies are refused as a whole'
set local role mixel_remote_presence;
do $$ declare bad jsonb; good jsonb := pg_temp.ev_start('s9', 'tenant', 'p_owner', 'dev_a1', 'conn_a1', 60); begin
    foreach bad in array array[
        'null'::jsonb, '[]', '"x"', '{"events":{}}', '{"events":[],"other":1}', '{"open":"x"}', '{"open":["not-a-uuid"]}',
        '{"open":[null]}', '{"events":[1]}', ('{"events":[{"type":"pause","id":"' || gen_random_uuid() || '","at":1}]}')::jsonb,
        jsonb_build_object('events', jsonb_build_array(good, jsonb_build_object('type', 'end', 'id', 'x', 'at', 1))),
        jsonb_build_object('events', jsonb_build_array(good, jsonb_build_object('type', 'end', 'id', gen_random_uuid()))),
        jsonb_build_object('events', jsonb_build_array(good, jsonb_build_object('type', 'end', 'id', gen_random_uuid(), 'at', 'soon'))),
        jsonb_build_object('events', jsonb_build_array(good, jsonb_build_object('type', 'end', 'id', gen_random_uuid(), 'at', -5))),
        jsonb_build_object('events', jsonb_build_array(good, jsonb_build_object('type', 'end', 'id', gen_random_uuid(), 'at', 1, 'x', 1))),
        jsonb_build_object('events', jsonb_build_array(good - 'colleague')),
        jsonb_build_object('events', jsonb_build_array(good || '{"connection": 99999999}')),
        jsonb_build_object('events', jsonb_build_array(good || '{"controller_connection": 1000000000}')),
        jsonb_build_object('events', jsonb_build_array(good || '{"unattended": "yes"}')),
        jsonb_build_object('events', jsonb_build_array(good || '{"tenant": 5}')),
        jsonb_build_object('events', jsonb_build_array(good || '{"extra": true}')),
        (select jsonb_build_object('events', jsonb_agg(jsonb_build_object('type', 'end', 'id', gen_random_uuid(), 'at', 1)))
            from generate_series(1, 1001)),
        (select jsonb_build_object('open', jsonb_agg(gen_random_uuid())) from generate_series(1, 20001))] loop
        perform pg_temp.check(pg_temp.fails(format('select public.remote_session_report(%L::jsonb)', bad), '22023'),
            'invalid body accepted: ' || left(coalesce(bad::text, 'null'), 200));
    end loop;
    perform pg_temp.check(pg_temp.fails('select public.remote_session_report(null)', '22023'), 'null report');
    -- 1000 events and 20000 open ids are fine.
    perform pg_temp.check((select pg_temp.report(jsonb_build_object('events', jsonb_agg(jsonb_build_object('type', 'end',
        'id', gen_random_uuid(), 'at', 1)))) from generate_series(1, 1000)) = 0, '1000 unknown ends');
end $$;
reset role;
select pg_temp.check(pg_temp.row_of('s9') is null, 'nothing applied from a refused report');

\echo '-- start and end events, idempotent, skipping foreign data'
set local role mixel_remote_presence;
select pg_temp.check(pg_temp.report(jsonb_build_object('events', jsonb_build_array(
    pg_temp.ev_start('s1', 'tenant', 'p_owner', 'dev_a1', 'conn_a1', 600),
    pg_temp.ev_start('s2', 'tenant', 'p_tech', 'dev_a2', 'conn_a2', 400),
    pg_temp.ev_start('s3', 'tenant', 'p_tech2', 'dev_a1', 'conn_a1', 300, true),
    pg_temp.ev_start('s4', 'tenant', 'p_tech', 'dev_a1', 'conn_a1', 200, false, true),
    pg_temp.ev_start('s5', 'tenant', 'p_owner', 'dev_a2', 'conn_a2', 10),
    -- unknown company, a device of another company, a technician of another company
    pg_temp.ev_start('sx1', 'ctrl_device', 'p_owner', 'dev_a1', 'conn_a1', 300),
    pg_temp.ev_start('sx2', 'tenant', 'p_owner', 'dev_b1', 'conn_b1', 300),
    pg_temp.ev_start('sx3', 'tenant', 'p_other', 'dev_a1', 'conn_a1', 300),
    pg_temp.ev_end('s1', 500)))) = 6, 'five starts and one end applied');
-- Repeated: no new rows, no time moved.
select pg_temp.check(pg_temp.report(jsonb_build_object('events', jsonb_build_array(
    pg_temp.ev_start('s1', 'tenant', 'p_owner', 'dev_a1', 'conn_a1', 100), pg_temp.ev_end('s1', 500), pg_temp.ev_end('s1', 550),
    jsonb_build_object('type', 'end', 'id', gen_random_uuid(), 'at', pg_temp.unix(5))))) = 0, 'repeated events change nothing');
reset role;
do $$ declare r remote_saas.sessions; begin
    r := pg_temp.row_of('s1');
    perform pg_temp.check(r.started_at = pg_temp.at(600) and r.ended_at = pg_temp.at(500), 's1 times kept ' || r::text);
    perform pg_temp.check(r.tenant_id = pg_temp.id('tenant') and r.technician_id = pg_temp.id('p_owner')
        and r.device_id = pg_temp.id('dev_a1') and r.host_connection = pg_temp.num('conn_a1')
        and r.controller_device_id = pg_temp.id('ctrl_device') and r.controller_connection = 123456789
        and r.device_name = 'QA Free' and r.kind = 'attended' and not r.colleague, 's1 fields ' || r::text);
    r := pg_temp.row_of('s3');
    perform pg_temp.check(r.kind = 'unattended' and r.ended_at is null, 's3 unattended running');
    perform pg_temp.check((pg_temp.row_of('s4')).colleague, 's4 colleague');
    perform pg_temp.check(pg_temp.row_of('sx1') is null and pg_temp.row_of('sx2') is null and pg_temp.row_of('sx3') is null, 'foreign starts skipped');
end $$;
set local role mixel_remote_presence;
-- A later end moves the end forward (never backwards); an end before the start ends at the start.
select pg_temp.check(pg_temp.report(jsonb_build_object('events', jsonb_build_array(pg_temp.ev_end('s1', 450)))) = 1, 'later end');
reset role;
select pg_temp.check((pg_temp.row_of('s1')).ended_at = pg_temp.at(450), 'end moved forward');
-- Times are clamped to the last day; tenant B sessions.
set local role mixel_remote_presence;
select pg_temp.check(pg_temp.report(jsonb_build_object('events', jsonb_build_array(
    pg_temp.ev_start('sb', 'tenant_b', 'p_other', 'dev_b1', 'conn_b1', 100),
    pg_temp.ev_start('sb_future', 'tenant_b', 'p_other', 'dev_b1', 'conn_b1', -3600),
    pg_temp.ev_start('sb_old', 'tenant_b', 'p_other', 'dev_b1', 'conn_b1', 3 * 86400),
    pg_temp.ev_end('sb_old', 3 * 86400 - 5)))) = 4, 'tenant B starts');
reset role;
select pg_temp.check((pg_temp.row_of('sb_future')).started_at = now() and (pg_temp.row_of('sb_old')).started_at = now() - interval '1 day'
    and (pg_temp.row_of('sb_old')).ended_at = now() - interval '1 day', 'times clamped to the last day');

\echo '-- open list: running sessions seen, lost ones closed'
set local role mixel_remote_presence;
select pg_temp.check(pg_temp.report(jsonb_build_object('open', jsonb_build_array(
    pg_temp.id('s2'), pg_temp.id('s3'), pg_temp.id('sb'), gen_random_uuid()))) = 0, 'open-only report');
reset role;
do $$ begin
    perform pg_temp.check((pg_temp.row_of('s2')).seen_at = now() and (pg_temp.row_of('s2')).ended_at is null, 's2 seen');
    perform pg_temp.check((pg_temp.row_of('s3')).seen_at = now() and (pg_temp.row_of('s3')).ended_at is null, 's3 seen');
    perform pg_temp.check((pg_temp.row_of('s4')).ended_at = pg_temp.at(200), 's4 not listed: ended at its start (never seen)');
    perform pg_temp.check((pg_temp.row_of('s5')).ended_at is null, 's5 too new to close');
    perform pg_temp.check((pg_temp.row_of('sb_future')).ended_at is null, 'sb_future too new to close');
    perform pg_temp.check((pg_temp.row_of('s1')).ended_at = pg_temp.at(450), 'ended s1 untouched');
end $$;
update remote_saas.sessions set seen_at = now() - interval '60 seconds' where id = pg_temp.id('s3');
set local role mixel_remote_presence;
select pg_temp.report(jsonb_build_object('open', jsonb_build_array(pg_temp.id('s2'), pg_temp.id('sb'))));
reset role;
select pg_temp.check((pg_temp.row_of('s3')).ended_at = now() - interval '60 seconds', 's3 ended when last seen');
select pg_temp.check((pg_temp.row_of('s2')).ended_at is null and (pg_temp.row_of('s5')).ended_at is null, 's2/s5 still running');
-- A later session of the technician (ended normally).
set local role mixel_remote_presence;
select pg_temp.check(pg_temp.report(jsonb_build_object('events', jsonb_build_array(
    pg_temp.ev_start('s6', 'tenant', 'p_tech', 'dev_a1', 'conn_a1', 100), pg_temp.ev_end('s6', 50)),
    'open', jsonb_build_array(pg_temp.id('s2'), pg_temp.id('sb')))) = 2, 's6 start + end with open list');
reset role;
select pg_temp.check((pg_temp.row_of('s6')).ended_at = pg_temp.at(50) and (pg_temp.row_of('s5')).ended_at is null, 's6 ended normally, s5 open');

\echo '-- reports: scope and filters'
set local role authenticated;
select pg_temp.act('owner');
do $$ declare r record; n integer; begin
    select count(*) into n from public.remote_saas_session_report(pg_temp.id('tenant'));
    perform pg_temp.check(n = 6, 'owner sees every session of the company: ' || n);
    select * into r from public.remote_saas_session_report(pg_temp.id('tenant')) x where x.session_id = pg_temp.id('s1');
    perform pg_temp.check(r.duration_seconds = 150 and r.technician_id = pg_temp.id('p_owner')
        and r.technician_email = pg_temp.email('owner') and r.connection_id = pg_temp.num('conn_a1')
        and r.device_name = 'QA Free' and r.kind = 'attended' and r.note is null, 's1 row ' || r::text);
    select * into r from public.remote_saas_session_report(pg_temp.id('tenant')) x where x.session_id = pg_temp.id('s2');
    perform pg_temp.check(r.ended_at is null and r.duration_seconds between 400 and 401 and r.technician_name = 'QA tech', 'running s2 ' || r::text);
    perform pg_temp.check((select array_agg(x.session_id order by x.started_at desc) from public.remote_saas_session_report(pg_temp.id('tenant')) x)
        = (select array_agg(x.session_id) from public.remote_saas_session_report(pg_temp.id('tenant')) x), 'newest first');
    perform pg_temp.check((select x.session_id from public.remote_saas_session_report(pg_temp.id('tenant')) x limit 1) = pg_temp.id('s5'), 's5 first');
    -- filters
    perform pg_temp.check((select count(*) from public.remote_saas_session_report(pg_temp.id('tenant'), technician => pg_temp.id('p_tech2'))) = 1, 'technician filter');
    perform pg_temp.check((select count(*) from public.remote_saas_session_report(pg_temp.id('tenant'), device => pg_temp.id('dev_a2'))) = 2, 'device filter');
    perform pg_temp.check((select count(*) from public.remote_saas_session_report(pg_temp.id('tenant'), now() - interval '250 seconds')) = 3, 'from filter');
    perform pg_temp.check((select count(*) from public.remote_saas_session_report(pg_temp.id('tenant'), null, now() - interval '350 seconds')) = 2, 'to filter');
    perform pg_temp.check((select count(*) from public.remote_saas_session_report(pg_temp.id('tenant'), now() - interval '500 seconds', now() - interval '250 seconds')) = 2, 'from + to');
    perform pg_temp.check((select count(*) from public.remote_saas_session_report(pg_temp.id('tenant'), now() - interval '366 days', now())) = 6, '366 days allowed');
    perform pg_temp.check(pg_temp.fails(format('select * from public.remote_saas_session_report(%L, now() - interval ''367 days'', now())', pg_temp.id('tenant')), '22023'), 'over 366 days');
    perform pg_temp.check(pg_temp.fails(format('select * from public.remote_saas_session_report(%L, now(), now() - interval ''1 day'')', pg_temp.id('tenant')), '22023'), 'from after to');
end $$;
select pg_temp.check(pg_temp.fails(format('select * from public.remote_saas_session_report(%L)', pg_temp.id('tenant_b')), '42501'), 'owner A reads tenant B');
select pg_temp.act('admin');
select pg_temp.check((select count(*) from public.remote_saas_session_report(pg_temp.id('tenant'))) = 6, 'admin sees all');
select pg_temp.act('tech');
select pg_temp.check((select array_agg(x.session_id order by x.started_at) from public.remote_saas_session_report(pg_temp.id('tenant')) x)
    = array[pg_temp.id('s2'), pg_temp.id('s4'), pg_temp.id('s6')], 'technician sees own sessions only');
select pg_temp.check((select count(*) from public.remote_saas_session_report(pg_temp.id('tenant'), technician => pg_temp.id('p_tech'))) = 3, 'own filter');
select pg_temp.check(pg_temp.fails(format('select * from public.remote_saas_session_report(%L, technician => %L)', pg_temp.id('tenant'), pg_temp.id('p_tech2')), '42501'),
    'technician asks for a colleague');
select pg_temp.act('tech2');
select pg_temp.check((select count(*) from public.remote_saas_session_report(pg_temp.id('tenant'))) = 1, 'tech2 own');
select pg_temp.act('member');
select pg_temp.check((select count(*) from public.remote_saas_session_report(pg_temp.id('tenant'))) = 0, 'member: none');
select pg_temp.act('other');
select pg_temp.check(pg_temp.fails(format('select * from public.remote_saas_session_report(%L)', pg_temp.id('tenant')), '42501'), 'other company refused');
-- Defaults to the last 30 days; an older session shows with a wider range.
reset role;
update remote_saas.sessions set started_at = now() - interval '40 days', ended_at = now() - interval '40 days' where id = pg_temp.id('sb_old');
set local role authenticated;
select pg_temp.act('other');
select pg_temp.check((select count(*) from public.remote_saas_session_report(pg_temp.id('tenant_b'))) = 2
    and (select count(*) from public.remote_saas_session_report(pg_temp.id('tenant_b'), now() - interval '60 days')) = 3, 'default 30 days');
-- Current device name while the device exists.
select pg_temp.act('owner');
select pg_temp.check(public.remote_saas_rename_device(pg_temp.id('tenant'), pg_temp.id('dev_a1'), 'QA Free Renamed') = 'QA Free Renamed', 'rename');
select pg_temp.check((select x.device_name from public.remote_saas_session_report(pg_temp.id('tenant')) x where x.session_id = pg_temp.id('s1')) = 'QA Free Renamed',
    'current device name');
reset role;

\echo '-- plan gate and read-only companies'
update remote_saas.plans set features = features || '{"reports": false}'
    where code = (select plan from remote_saas.tenants where id = pg_temp.id('tenant'));
set local role authenticated;
select pg_temp.act('owner');
do $$ begin
    begin perform * from public.remote_saas_session_report(pg_temp.id('tenant')); raise exception 'FAILED: report without the feature';
    exception when insufficient_privilege then
        perform pg_temp.check(sqlerrm = 'Session reports need a paid plan', 'gate message ' || sqlerrm);
    end;
end $$;
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_session_note(%L, %L, %L)', pg_temp.id('tenant'), 'x', pg_temp.id('s1')), '42501'), 'note without the feature');
select pg_temp.check(public.remote_saas_set_favorite(pg_temp.id('tenant'), pg_temp.id('dev_a1'), true)
    and public.remote_saas_set_favorite(pg_temp.id('tenant'), pg_temp.id('dev_a1'), false) = false, 'favorites need no paid feature');
select pg_temp.check((select count(*) from public.remote_saas_recent_devices(pg_temp.id('tenant'))) = 2, 'recent devices need no paid feature');
reset role;
update remote_saas.plans set features = features || '{"reports": true}'
    where code = (select plan from remote_saas.tenants where id = pg_temp.id('tenant'));
select pg_temp.check((select bool_and((features->>'reports')::boolean = (code <> 'personal')) from remote_saas.plans), 'plan features');
update remote_saas.tenants set status = 'read_only' where id = pg_temp.id('tenant');
set local role authenticated;
select pg_temp.act('owner');
select pg_temp.check((select count(*) from public.remote_saas_session_report(pg_temp.id('tenant'))) = 6, 'read-only company reads reports');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_session_note(%L, %L, %L)', pg_temp.id('tenant'), 'x', pg_temp.id('s1')), '25006'), 'read-only: no notes');
reset role;
update remote_saas.tenants set status = 'trial' where id = pg_temp.id('tenant');

\echo '-- notes'
insert into remote_saas.sessions(id, tenant_id, technician_id, controller_device_id, controller_connection, device_id,
        host_connection, device_name, kind, started_at, ended_at)
values (pg_temp.id('s_old'), pg_temp.id('tenant'), pg_temp.id('p_tech'), pg_temp.id('ctrl_device'), 123456789,
        pg_temp.id('dev_a1'), 987654321, 'QA Free', 'attended', now() - interval '25 hours', now() - interval '24 hours');
set local role authenticated;
select pg_temp.act('tech');
select pg_temp.check(public.remote_saas_session_note(pg_temp.id('tenant'), E'  Fixed the printer\r\nand\tmail  ', pg_temp.id('s2')) = pg_temp.id('s2'), 'own note');
select pg_temp.check((select x.note from public.remote_saas_session_report(pg_temp.id('tenant')) x where x.session_id = pg_temp.id('s2'))
    = E'Fixed the printer\nand\tmail', 'note cleaned');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_session_note(%L, %L, %L)', pg_temp.id('tenant'), 'mine', pg_temp.id('s3')), '42501'), 'note on a colleague session');
-- By Remote ID: the technician's newest session to that computer in the last 24 hours.
select pg_temp.check(public.remote_saas_session_note(pg_temp.id('tenant'), 'by connection', connection => pg_temp.num('conn_a1')) = pg_temp.id('s6'), 'note by connection (newest)');
select pg_temp.check(public.remote_saas_session_note(pg_temp.id('tenant'), 'grouped', connection => pg_temp.num('conn_a2')) = pg_temp.id('s2'), 'note by connection a2');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_session_note(%L, %L, connection => 987654321)', pg_temp.id('tenant'), 'old'), 'P0002'), 'only the last 24 hours');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_session_note(%L, %L, connection => 111111111)', pg_temp.id('tenant'), 'none'), 'P0002'), 'no session');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_session_note(%L, %L)', pg_temp.id('tenant'), 'none'), '22023'), 'neither session nor connection');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_session_note(%L, %L, %L, %s)', pg_temp.id('tenant'), 'both', pg_temp.id('s2'), pg_temp.num('conn_a2')), '22023'), 'both');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_session_note(%L, %L, %L)', pg_temp.id('tenant'), repeat('x', 4001), pg_temp.id('s2')), '22023'), '4001 characters');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_session_note(%L, %L, %L)', pg_temp.id('tenant'), E'a\x01b', pg_temp.id('s2')), '22023'), 'control character');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_session_note(%L, %L, %L)', pg_temp.id('tenant'), E'a\rb', pg_temp.id('s2')), '22023'), 'lone carriage return');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_session_note(%L, %L, %L)', pg_temp.id('tenant'), 'x', pg_temp.id('sb')), 'P0002'), 'session of another company');
select pg_temp.check(public.remote_saas_session_note(pg_temp.id('tenant'), repeat('é', 4000), pg_temp.id('s4')) = pg_temp.id('s4'), '4000 characters');
select pg_temp.check(public.remote_saas_session_note(pg_temp.id('tenant'), E' \n\t ', pg_temp.id('s4')) = pg_temp.id('s4'), 'blank note');
select pg_temp.check((select x.note from public.remote_saas_session_report(pg_temp.id('tenant')) x where x.session_id = pg_temp.id('s4')) is null, 'blank note clears');
select pg_temp.act('tech2');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_session_note(%L, %L, connection => %s)', pg_temp.id('tenant'), 'x', pg_temp.num('conn_a2')), 'P0002'), 'tech2 has no session to a2');
select pg_temp.act('member');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_session_note(%L, %L, %L)', pg_temp.id('tenant'), 'x', pg_temp.id('s2')), '42501'), 'member notes another session');
select pg_temp.act('other');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_session_note(%L, %L, %L)', pg_temp.id('tenant'), 'x', pg_temp.id('s2')), '42501'), 'other company notes');
select pg_temp.act('owner');
select pg_temp.check(public.remote_saas_session_note(pg_temp.id('tenant'), 'owner override', pg_temp.id('s3')) = pg_temp.id('s3'), 'owner notes any session');
reset role;
select pg_temp.check((pg_temp.row_of('s3')).note = 'owner override' and (pg_temp.row_of('s3')).note_by = pg_temp.id('p_owner')
    and (pg_temp.row_of('s2')).note_by = pg_temp.id('p_tech') and (pg_temp.row_of('s4')).note_by is null, 'note authors');
select pg_temp.check((select count(*) from remote_saas.audit_log where tenant_id = pg_temp.id('tenant') and action = 'session.note') = 6
    and not exists (select 1 from remote_saas.audit_log where action = 'session.note' and detail::text like '%printer%'), 'notes audited without their text');

\echo '-- favorites'
set local role authenticated;
select pg_temp.act('tech');
select pg_temp.check(public.remote_saas_set_favorite(pg_temp.id('tenant'), pg_temp.id('dev_a1'), true)
    and public.remote_saas_set_favorite(pg_temp.id('tenant'), pg_temp.id('dev_a2'), true)
    and public.remote_saas_set_favorite(pg_temp.id('tenant'), pg_temp.id('dev_a1'), true), 'set favorites (repeat is fine)');
select pg_temp.check((select array_agg(x.device_id order by x.device_id) from public.remote_saas_favorites(pg_temp.id('tenant')) x)
    = (select array_agg(d order by d) from unnest(array[pg_temp.id('dev_a1'), pg_temp.id('dev_a2')]) d), 'favorites listed once each');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_set_favorite(%L, %L, true)', pg_temp.id('tenant'), pg_temp.id('dev_b1')), '42501'), 'device of another company');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_set_favorite(%L, %L, true)', pg_temp.id('tenant'), gen_random_uuid()), '42501'), 'unknown device');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_set_favorite(%L, %L, null)', pg_temp.id('tenant'), pg_temp.id('dev_a1')), '22023'), 'null favorite');
select pg_temp.act('tech2');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_set_favorite(%L, %L, true)', pg_temp.id('tenant'), pg_temp.id('dev_a2')), '42501'), 'device without access');
select pg_temp.check(public.remote_saas_set_favorite(pg_temp.id('tenant'), pg_temp.id('dev_a1'), true), 'tech2 favorite');
select pg_temp.check((select count(*) from public.remote_saas_favorites(pg_temp.id('tenant'))) = 1, 'favorites are per person');
select pg_temp.act('other');
select pg_temp.check(pg_temp.fails(format('select * from public.remote_saas_favorites(%L)', pg_temp.id('tenant')), '42501'), 'other company favorites');
select pg_temp.act('member');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_set_favorite(%L, %L, true)', pg_temp.id('tenant'), pg_temp.id('dev_a1')), '42501'), 'member without the device');
-- Limit of 200 (199 more devices, inserted directly).
reset role;
do $$ declare c uuid; begin
    select client_id into c from remote_saas.tenants where id = pg_temp.id('tenant');
    create temp table qa_bulk on commit drop as select gen_random_uuid() as id from generate_series(1, 199);
    insert into public.devices(id, client_id, name, type, os)
    select b.id, c, 'QA Bulk ' || b.id, 'desktop', 'windows' from qa_bulk b;
    insert into remote_saas.favorites(user_id, device_id, tenant_id)
    select pg_temp.id('p_owner'), b.id, pg_temp.id('tenant') from qa_bulk b;
    perform pg_temp.remember('bulk_extra', (select id::text from qa_bulk limit 1));
end $$;
set local role authenticated;
select pg_temp.act('owner');
select pg_temp.check(public.remote_saas_set_favorite(pg_temp.id('tenant'), pg_temp.id('dev_a1'), true), '200th favorite');
select pg_temp.check(public.remote_saas_set_favorite(pg_temp.id('tenant'), pg_temp.id('dev_a1'), true), 'existing favorite at the limit');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_set_favorite(%L, %L, true)', pg_temp.id('tenant'), pg_temp.id('dev_a2')), '54000'), '201st favorite');
select pg_temp.check((select count(*) from public.remote_saas_favorites(pg_temp.id('tenant'))) = 200, 'owner lists 200');
select pg_temp.check(public.remote_saas_set_favorite(pg_temp.id('tenant'), pg_temp.id('bulk_extra'), false) = false
    and public.remote_saas_set_favorite(pg_temp.id('tenant'), pg_temp.id('dev_a2'), true), 'room again after removing one');
-- Losing access hides a favorite (and it comes back with the access).
select pg_temp.check(public.remote_saas_set_group_access(pg_temp.id('tenant'), pg_temp.id('group'), array[]::uuid[]) = 0, 'revoke group');
select pg_temp.act('tech');
select pg_temp.check((select array_agg(x.device_id) from public.remote_saas_favorites(pg_temp.id('tenant')) x) = array[pg_temp.id('dev_a1')], 'favorite hidden without access');
select pg_temp.check(public.remote_saas_set_favorite(pg_temp.id('tenant'), pg_temp.id('dev_a2'), false) = false, 'unfavorite without access');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_set_favorite(%L, %L, true)', pg_temp.id('tenant'), pg_temp.id('dev_a2')), '42501'), 'favorite without access');

\echo '-- recent devices'
-- tech: s6 (dev_a1, 100 s ago), s4 (dev_a1, 200 s), s2 (dev_a2, 400 s), s_old (dev_a1, 25 h).
select pg_temp.check((select array_agg(x.device_id) from public.remote_saas_recent_devices(pg_temp.id('tenant')) x) = array[pg_temp.id('dev_a1')],
    'recent: deduplicated, inaccessible device hidden');
select pg_temp.act('owner');
select pg_temp.check(public.remote_saas_set_group_access(pg_temp.id('tenant'), pg_temp.id('group'), array[pg_temp.id('p_tech')]) = 1, 'grant group again');
select pg_temp.act('tech');
do $$ declare r record; begin
    perform pg_temp.check((select array_agg(x.device_id order by x.last_connected_at desc) from public.remote_saas_recent_devices(pg_temp.id('tenant')) x)
        = array[pg_temp.id('dev_a1'), pg_temp.id('dev_a2')], 'recent order');
    select * into r from public.remote_saas_recent_devices(pg_temp.id('tenant'), 1) x;
    perform pg_temp.check(r.device_id = pg_temp.id('dev_a1') and r.last_connected_at = pg_temp.at(100), 'recent newest ' || r::text);
    perform pg_temp.check((select count(*) from public.remote_saas_recent_devices(pg_temp.id('tenant'), 1)) = 1
        and (select count(*) from public.remote_saas_recent_devices(pg_temp.id('tenant'), null)) = 2, 'max_count');
    perform pg_temp.check(pg_temp.fails(format('select * from public.remote_saas_recent_devices(%L, 0)', pg_temp.id('tenant')), '22023')
        and pg_temp.fails(format('select * from public.remote_saas_recent_devices(%L, 51)', pg_temp.id('tenant')), '22023'), 'max_count bounds');
end $$;
select pg_temp.act('member');
select pg_temp.check((select count(*) from public.remote_saas_recent_devices(pg_temp.id('tenant'))) = 0, 'member: no recent devices');
select pg_temp.act('other');
select pg_temp.check(pg_temp.fails(format('select * from public.remote_saas_recent_devices(%L)', pg_temp.id('tenant')), '42501'), 'other company recent');
-- A removed device: gone from favorites and recents, the report keeps its name.
select pg_temp.act('owner');
select pg_temp.check(public.remote_saas_remove_device(pg_temp.id('tenant'), pg_temp.id('dev_a2')), 'remove device');
select pg_temp.check((select x.device_name from public.remote_saas_session_report(pg_temp.id('tenant')) x where x.session_id = pg_temp.id('s2')) = 'QA Grouped',
    'report keeps the name of a removed device');
select pg_temp.act('tech');
select pg_temp.check((select array_agg(x.device_id) from public.remote_saas_recent_devices(pg_temp.id('tenant')) x) = array[pg_temp.id('dev_a1')], 'removed device not recent');
reset role;
select pg_temp.check(not exists (select 1 from remote_saas.favorites where device_id = pg_temp.id('dev_a2')), 'favorites of a removed device gone');

\echo '-- export, member removal, account deletion, company deletion'
set local role authenticated;
select pg_temp.act('owner');
do $$ declare r jsonb; s jsonb; begin
    r := public.remote_saas_export(pg_temp.id('tenant'));
    perform pg_temp.check(r->>'format' = 'mixel-remote-export' and r ? 'members' and r ? 'audit_log', 'export keeps the 027 content');
    perform pg_temp.check(jsonb_array_length(r->'sessions') = 7, 'export lists the sessions: ' || jsonb_array_length(r->'sessions'));
    select x into s from jsonb_array_elements(r->'sessions') x where x->>'session_id' = pg_temp.id('s2')::text;
    perform pg_temp.check(s->>'technician' = pg_temp.email('tech') and s->>'note_by' = pg_temp.email('tech')
        and s->>'device_name' = 'QA Grouped' and (s->>'remote_id')::integer = pg_temp.num('conn_a2') and s->>'ended_at' is null, 'export row ' || s::text);
end $$;
select pg_temp.act('admin');
select pg_temp.check(pg_temp.fails(format('select public.remote_saas_export(%L)', pg_temp.id('tenant')), '42501'), 'admin cannot export');
-- Removing a member drops their favorites; the company's session records stay.
select pg_temp.act('owner');
select pg_temp.check(public.remote_saas_remove_member(pg_temp.id('tenant'), pg_temp.id('p_tech2')), 'remove tech2');
reset role;
select pg_temp.check(not exists (select 1 from remote_saas.favorites where user_id = pg_temp.id('p_tech2'))
    and (pg_temp.row_of('s3')).technician_id = pg_temp.id('p_tech2'), 'member removal: favorites gone, sessions kept');
-- Deleting one's own account removes the person from the records.
set local role authenticated;
select pg_temp.act('tech');
select pg_temp.check((public.remote_saas_delete_my_account(pg_temp.email('tech'))->>'deleted')::boolean, 'tech deletes own account');
reset role;
select pg_temp.check(not exists (select 1 from public.users where id = pg_temp.id('p_tech')), 'profile gone');
select pg_temp.check(not exists (select 1 from remote_saas.favorites where user_id = pg_temp.id('p_tech'))
    and not exists (select 1 from remote_saas.sessions where technician_id = pg_temp.id('p_tech') or note_by = pg_temp.id('p_tech')),
    'account deletion: favorites gone, person removed from sessions');
select pg_temp.check((pg_temp.row_of('s2')).technician_id is null and (pg_temp.row_of('s2')).note_by is null
    and (pg_temp.row_of('s6')).technician_id is null and (select count(*) from remote_saas.sessions where tenant_id = pg_temp.id('tenant')) = 7,
    'session records kept without the person');
set local role authenticated;
select pg_temp.act('owner');
do $$ declare r record; begin
    select * into r from public.remote_saas_session_report(pg_temp.id('tenant')) x where x.session_id = pg_temp.id('s2');
    perform pg_temp.check(r.technician_id is null and r.technician_name is null and r.technician_email is null, 'report row of a deleted person');
end $$;
-- Deleting the company deletes its sessions and favorites.
do $$ declare r jsonb; begin
    r := public.remote_saas_delete_company(pg_temp.id('tenant'), 'QA Sessions Alpha');
    perform pg_temp.check((r->>'deleted')::boolean, 'company deleted ' || r::text);
end $$;
reset role;
select pg_temp.check(not exists (select 1 from remote_saas.sessions where tenant_id = pg_temp.id('tenant'))
    and not exists (select 1 from remote_saas.favorites where tenant_id = pg_temp.id('tenant')), 'company deletion: sessions and favorites gone');
select pg_temp.check((select count(*) from remote_saas.sessions where tenant_id = pg_temp.id('tenant_b')) = 3, 'other company untouched');

\echo 'remote-saas-sessions: all checks passed'
rollback;
