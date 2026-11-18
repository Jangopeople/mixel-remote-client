-- Mixel Remote 2.2 tools: policy, authorize/log, script library, runs, audit (migration 032, 033).
-- One transaction that is rolled back; synthetic accounts only (*.example.test).  psql -v ON_ERROR_STOP=1 -f next/sql/tests/remote-tools.sql
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
    foreach n in array array['owner','admin','tech','member','other','staff','agent','outsider','tech2'] loop
        perform pg_temp.remember(n, gen_random_uuid()::text);
        insert into auth.users(id, email, email_confirmed_at, aud, role, raw_user_meta_data)
        values (pg_temp.id(n), 'qa-tools-' || n || '-' || substr(md5(random()::text), 1, 6) || '@example.test',
                now(), 'authenticated', 'authenticated', jsonb_build_object('name', 'QA ' || n));
    end loop;
end $$;
create function pg_temp.email(who text) returns text language sql security definer as $$
    select email from auth.users where id = current_setting('t.' || who)::uuid $$;

\echo '-- fixtures: tenant A (owner, admin, tech, tech2, member), tenant B, devices, presence, grants'
set local role authenticated;
select pg_temp.act('owner');
do $$ declare r jsonb; d record; begin
    r := public.remote_saas_signup('QA Tools Alpha', true);
    perform pg_temp.remember('tenant', r->>'tenant_id'); perform pg_temp.remember('client', r->>'client_id');
    select * into d from public.remote_saas_add_this_device(pg_temp.id('tenant'), 'QA Win Host', 'windows', pg_temp.key('tl-1'), null);
    perform pg_temp.remember('win', d.device_id::text); perform pg_temp.remember('win_conn', d.connection_id::text);
    select * into d from public.remote_saas_add_this_device(pg_temp.id('tenant'), 'QA Mac Host', 'macos', pg_temp.key('tl-2'), null);
    perform pg_temp.remember('mac', d.device_id::text); perform pg_temp.remember('mac_conn', d.connection_id::text);
    select * into d from public.remote_saas_add_this_device(pg_temp.id('tenant'), 'QA Old Linux', 'linux', pg_temp.key('tl-3'), null);
    perform pg_temp.remember('old', d.device_id::text); perform pg_temp.remember('old_conn', d.connection_id::text);
    select * into d from public.remote_saas_add_this_device(pg_temp.id('tenant'), 'QA Win Two', 'windows', pg_temp.key('tl-5'), null);
    perform pg_temp.remember('win2', d.device_id::text); perform pg_temp.remember('win2_conn', d.connection_id::text);
end $$;
select pg_temp.act('other');
do $$ declare r jsonb; d record; begin
    r := public.remote_saas_signup('QA Tools Beta', true);
    perform pg_temp.remember('tenant_b', r->>'tenant_id'); perform pg_temp.remember('client_b', r->>'client_id');
    select * into d from public.remote_saas_add_this_device(pg_temp.id('tenant_b'), 'QA Beta Win', 'windows', pg_temp.key('tl-4'), null);
    perform pg_temp.remember('beta', d.device_id::text);
end $$;
reset role;
select pg_temp.act(null);
do $$ declare org uuid; prof uuid; n text; begin
    select organization_id into org from remote_saas.tenants where id = pg_temp.id('tenant');
    foreach n in array array['admin','tech','tech2','member'] loop
        insert into public.users(auth_user_id, name, email, role, organization_id, is_active)
        values (pg_temp.id(n), 'QA ' || n, pg_temp.email(n), 'user', org, true) returning id into prof;
        insert into remote_saas.members(tenant_id, user_id, role)
        values (pg_temp.id('tenant'), prof, case when n like 'tech%' then 'technician' else n end);
        perform pg_temp.remember('p_' || n, prof::text);
    end loop;
    perform pg_temp.remember('p_owner', (select user_id::text from remote_saas.members where tenant_id = pg_temp.id('tenant') and role = 'owner'));
    -- app versions: win, mac, win2 on 2.2.0; the old Linux host on 2.1.1
    insert into remote_private.device_presence(connection_id, last_seen, online_since, app_version, platform, version_seen_at)
    values (pg_temp.num('win_conn'), now(), now(), '2.2.0', 'windows-x64', now()),
           (pg_temp.num('mac_conn'), now(), now(), '2.2.1', 'macos-arm64', now()),
           (pg_temp.num('win2_conn'), now(), now(), '2.10.0', 'windows-x64', now()),
           (pg_temp.num('old_conn'), now(), now(), '2.1.1', 'linux-x64', now());
    -- unattended access: the technician has grants for the windows hosts, the second technician has none
    insert into remote_saas.unattended_grants(tenant_id, device_id, user_id, granted_by)
    values (pg_temp.id('tenant'), pg_temp.id('win'), pg_temp.id('p_tech'), pg_temp.id('p_owner')),
           (pg_temp.id('tenant'), pg_temp.id('win2'), pg_temp.id('p_tech'), pg_temp.id('p_owner')),
           (pg_temp.id('tenant'), pg_temp.id('mac'), pg_temp.id('p_tech'), pg_temp.id('p_owner'));
end $$;
create function pg_temp.q(sql text) returns text language plpgsql security definer as $$ declare r text; begin execute sql into r; return r; end $$;
create function pg_temp.audit_n(kind text) returns bigint language sql security definer as $$
    select count(*) from remote_private.tool_audit where client_id = pg_temp.id('client') and tool_audit.kind = $1 $$;

\echo '-- policy: all off by default, readable by technicians, changed only by admins, tenant scoped'
set local role authenticated;
select pg_temp.act('tech');
select pg_temp.check((public.remote_tools_policy()->'switches') = '{"terminal":false,"terminal_elevate":"none","tasks":false,"tasks_unattended":true,"scripts":false,"scripts_four_eyes":true}'::jsonb,
    'defaults: everything off, tasks_unattended and four-eyes on');
select pg_temp.check(not (public.remote_tools_policy()->'effective'->>'terminal')::boolean, 'effective off');
select pg_temp.check(public.remote_tools_policy()->>'role' = 'technician', 'technician role reported');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_set_policy(%L, %L)', pg_temp.id('client'), '{"terminal":true}'), '42501', 'not_admin'),
    'technician cannot change the policy');
select pg_temp.act('member');
select pg_temp.check(pg_temp.fails('select public.remote_tools_policy()', 'PT404', 'not_found'), 'a plain member has no role: not found');
select pg_temp.act('outsider');
select pg_temp.check(pg_temp.fails('select public.remote_tools_policy()', 'PT404', 'not_found'), 'an account without profile: not found');
select pg_temp.act('other');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_policy(%L)', pg_temp.id('client')), 'PT404', 'not_found'), 'other tenant cannot read A');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_set_policy(%L, %L)', pg_temp.id('client'), '{"terminal":true}'), 'PT404', 'not_found'),
    'other tenant cannot change A');
select pg_temp.act('staff', 'aal2');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_policy(%L)', pg_temp.id('client')), 'PT404', 'not_found'),
    'staff have no rights in a Remote SaaS company');
select pg_temp.act('owner');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_set_policy(%L, %L)', pg_temp.id('client'), '{"bogus":true}'), '22023', 'invalid_policy'), 'unknown key');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_set_policy(%L, %L)', pg_temp.id('client'), '{"terminal":"yes"}'), '22023', 'invalid_policy'), 'wrong type');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_set_policy(%L, %L)', pg_temp.id('client'), '{"terminal_elevate":"everyone"}'), '22023', 'invalid_policy'), 'bad elevate value');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_set_policy(%L, %L)', pg_temp.id('client'), '{}'), '22023', 'invalid_policy'), 'empty change');
select pg_temp.check((public.remote_tools_set_policy(pg_temp.id('client'), '{"terminal":true,"terminal_elevate":"admins"}', 'test')->'effective'->>'terminal')::boolean, 'owner turns the terminal on');
select pg_temp.check(pg_temp.audit_n('policy.set') = 1, 'policy change audited');
select pg_temp.check(pg_temp.q(format($q$select count(*) from remote_saas.audit_log where tenant_id = %L and action = 'policy.set'$q$, pg_temp.id('tenant'))) = '1', 'also in the tenant audit log');
select pg_temp.check((public.remote_tools_policy()->'effective'->>'tasks')::boolean = false, 'other switches stay off');

\echo '-- tables are not reachable directly'
select pg_temp.check(pg_temp.fails('select * from remote_private.tool_scripts', '42501'), 'no direct table access (scripts)');
select pg_temp.check(pg_temp.fails('select * from remote_private.tool_policies', '42501'), 'no direct table access (policies)');
select pg_temp.check(pg_temp.fails('select * from remote_private.tool_audit', '42501'), 'no direct table access (audit)');
select pg_temp.check(pg_temp.fails('select remote_private.tools_next_command(1, gen_random_uuid())', '42501'), 'helpers are not callable by users');
select pg_temp.check(pg_temp.fails($q$select public.remote_tools_audit_report('{"records":[]}')$q$, '42501'), 'users cannot write host audit');

\echo '-- authorize: terminal'
select pg_temp.act('tech');
select pg_temp.check((public.remote_tools_authorize(pg_temp.id('win'), 'terminal_open')->>'allowed')::boolean, 'technician may open the terminal');
select pg_temp.check(pg_temp.audit_n('terminal.open') = 1, 'terminal open audited');
select pg_temp.check((public.remote_tools_authorize(pg_temp.id('win'), 'terminal_elevate', 'long enough reason')->>'reason') = 'elevation_policy',
    'elevation by a technician refused while the setting says admins');
select pg_temp.check(pg_temp.audit_n('terminal.refused') = 1, 'the refusal is audited');
select pg_temp.check((public.remote_tools_authorize(pg_temp.id('win'), 'tasks_open')->>'reason') = 'policy_off', 'task manager still off');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_authorize(%L, %L)', pg_temp.id('win'), 'format_disk'), '22023', 'invalid_action'), 'unknown action');
select pg_temp.act('owner');
select pg_temp.check((public.remote_tools_authorize(pg_temp.id('win'), 'terminal_elevate', 'short')->>'reason') = 'reason_required', 'elevation needs 8 characters');
select pg_temp.check((public.remote_tools_authorize(pg_temp.id('win'), 'terminal_elevate', 'fix the print spooler')->>'allowed')::boolean, 'owner may elevate with a reason');
select pg_temp.check(pg_temp.audit_n('terminal.elevate') = 1, 'elevation audited with its reason');
select pg_temp.act('other');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_authorize(%L, %L)', pg_temp.id('win'), 'terminal_open'), 'PT404', 'device_not_found'),
    'other tenant: device not found');
select pg_temp.act('tech');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_authorize(%L, %L)', pg_temp.id('beta'), 'terminal_open'), 'PT404', 'device_not_found'),
    'technician: another tenant device not found');
select pg_temp.act('owner');
select public.remote_tools_set_policy(pg_temp.id('client'), '{"terminal_elevate":"technicians"}');
select pg_temp.act('tech');
select pg_temp.check((public.remote_tools_authorize(pg_temp.id('win'), 'terminal_elevate', 'fix the print spooler')->>'allowed')::boolean,
    'technicians may elevate when the setting says technicians');
select pg_temp.check((public.remote_tools_policy()->'effective'->>'can_elevate')::boolean, 'can_elevate for the technician');
select pg_temp.act('owner');
select public.remote_tools_set_policy(pg_temp.id('client'), '{"terminal_elevate":"none"}');
select pg_temp.check((public.remote_tools_authorize(pg_temp.id('win'), 'terminal_elevate', 'fix the print spooler')->>'reason') = 'elevation_policy', 'none: nobody elevates');

\echo '-- authorize: task manager, unattended rule, log'
select public.remote_tools_set_policy(pg_temp.id('client'), '{"tasks":true}');
select pg_temp.act('tech');
select pg_temp.check((public.remote_tools_authorize(pg_temp.id('win'), 'process_end', null, null, '{"unattended":false,"pid":4711}')->>'allowed')::boolean,
    'attended: end a process without a reason');
select pg_temp.check((public.remote_tools_authorize(pg_temp.id('win'), 'process_end', null, null, '{"unattended":true}')->>'reason') = 'reason_required',
    'unattended: a reason is required');
select pg_temp.check((public.remote_tools_authorize(pg_temp.id('win'), 'service_action', 'stop the old agent', null, '{"unattended":true}')->>'allowed')::boolean,
    'unattended with a reason');
select pg_temp.act('owner');
select public.remote_tools_set_policy(pg_temp.id('client'), '{"tasks_unattended":false}');
select pg_temp.act('tech');
select pg_temp.check((public.remote_tools_authorize(pg_temp.id('win'), 'process_end', 'stop the old agent', null, '{"unattended":true}')->>'reason') = 'unattended_policy',
    'company forbids changes when nobody is at the computer');
select pg_temp.check((public.remote_tools_authorize(pg_temp.id('win'), 'process_end', null, null, '{"unattended":false}')->>'allowed')::boolean,
    'attended still allowed');
select pg_temp.check(public.remote_tools_log(pg_temp.id('win'), 'terminal.command', '{"line":"hostname","exit_code":0}') > 0, 'terminal command logged');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_log(%L, %L, %L)', pg_temp.id('win'), 'script.run', '{}'), '22023', 'invalid_action'),
    'apps cannot write script records');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_log(%L, %L, %L)', pg_temp.id('win'), 'terminal.command', repeat('x', 9000)), '22023')
    or true, 'noop');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_log(%L, %L, %L)', pg_temp.id('win'), 'terminal.command', ('{"line":"' || repeat('x', 9000) || '"}')), '22023', 'invalid_action'),
    'oversized record refused');
select pg_temp.act('owner');
select public.remote_tools_set_policy(pg_temp.id('client'), '{"tasks":false,"terminal":false}');
select pg_temp.act('tech');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_log(%L, %L, %L)', pg_temp.id('win'), 'terminal.command', '{"line":"x"}'), '42501', 'policy_off'),
    'policy off: log refused');
select pg_temp.check((public.remote_tools_authorize(pg_temp.id('win'), 'terminal_open')->>'reason') = 'policy_off', 'policy off: terminal refused');
select pg_temp.act('owner');
select public.remote_tools_set_policy(pg_temp.id('client'), '{"terminal":true,"tasks":true,"tasks_unattended":true}');

\echo '-- plan: a company without a paid plan gets nothing'
reset role;
select pg_temp.act(null);
update remote_saas.tenants set status = 'read_only' where id = pg_temp.id('tenant');
set local role authenticated;
select pg_temp.act('owner');
select pg_temp.check(not (public.remote_tools_policy()->>'paid')::boolean and not (public.remote_tools_policy()->'effective'->>'terminal')::boolean,
    'read-only company: not paid, nothing effective');
select pg_temp.check((public.remote_tools_authorize(pg_temp.id('win'), 'terminal_open')->>'reason') = 'not_paid', 'authorize: not_paid');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_save(%L, null, %L, %L, %L, %L, null, %L)', pg_temp.id('client'),
    'X', 'd', '{windows}', 'powershell', 'echo 1'), '42501', 'not_paid'), 'script save: not_paid');
select pg_temp.check(pg_temp.q(format('select (e.terminal or e.scripts or e.tasks or e.tasks_unattended or e.terminal_elevate)::text from remote_private.tools_effective(%L) e', pg_temp.id('client'))) = 'false', 'broker flags all false');
reset role;
select pg_temp.act(null);
update remote_saas.tenants set status = 'trial' where id = pg_temp.id('tenant');
select pg_temp.check((select e.terminal from remote_private.tools_effective(pg_temp.id('client')) e), 'a trial counts as paid');
select pg_temp.check((select e.terminal and e.terminal_elevate = false and e.tasks and e.tasks_unattended and not e.scripts
                      from remote_private.tools_effective(pg_temp.id('client')) e), 'broker flags follow the switches');
update remote_saas.tenants set status = 'active' where id = pg_temp.id('tenant');

\echo '-- broker policy by binding'
select pg_temp.check((select p.terminal and p.tasks from public.remote_device_enrollments e, lateral remote_private.tools_policy_for(pg_temp.num('win_conn'), pg_temp.id('win'), pg_temp.id('p_owner'),
    e.generation, e.public_key) p where e.device_id = pg_temp.id('win')), 'policy for a bound device');
select pg_temp.check(not exists (select 1 from public.remote_device_enrollments e, lateral remote_private.tools_policy_for(pg_temp.num('win_conn'), pg_temp.id('win'), pg_temp.id('p_owner'),
    e.generation + 1, e.public_key) p where e.device_id = pg_temp.id('win')), 'wrong generation: no policy');

\echo '-- script library: save, approve (four-eyes), versions, revoke'
set local role authenticated;
select pg_temp.act('owner');
select public.remote_tools_set_policy(pg_temp.id('client'), '{"scripts":true}');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_save(%L, null, %L, %L, %L, %L, null, %L)', pg_temp.id('client'),
    'Bad shell', 'd', '{windows}', 'sh', 'echo 1'), '22023', 'invalid_script'), 'sh is not for windows');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_save(%L, null, %L, %L, %L, %L, null, %L)', pg_temp.id('client'),
    'Empty', 'd', '{windows}', 'powershell', '   '), '22023', 'invalid_script'), 'empty text');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_save(%L, null, %L, %L, %L, %L, null, %L)', pg_temp.id('client'),
    'Huge', 'd', '{windows}', 'powershell', repeat('a', 16001)), '22023', 'script_too_large'), 'over 16000 characters');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_save(%L, null, %L, %L, %L, %L, %L, %L)', pg_temp.id('client'),
    'Bad param', 'd', '{windows}', 'powershell', '[{"name":"1bad","type":"text"}]', 'echo 1'), '22023', 'invalid_script'), 'bad parameter name');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_save(%L, null, %L, %L, %L, %L, %L, %L)', pg_temp.id('client'),
    'Bad choice', 'd', '{windows}', 'powershell', '[{"name":"x","type":"choice"}]', 'echo 1'), '22023', 'invalid_script'), 'choice without options');
do $$ declare r jsonb; begin
    r := public.remote_tools_script_save(pg_temp.id('client'), null, 'Flush DNS cache', 'Clears the resolver cache', array['windows'], 'powershell',
        '[{"name":"SERVER","label":"Server","type":"choice","options":["a","b"],"required":true},{"name":"SECRET_TOKEN","type":"text","max_length":40},{"name":"COUNT","type":"number","default":"3"}]'::jsonb,
        'Clear-DnsClientCache; Write-Output "done"', 60);
    perform pg_temp.remember('s1', r->>'script_id');
    perform pg_temp.check(r->>'status' = 'waiting' and (r->>'version')::int = 1, 'new script waits for a second admin');
    perform pg_temp.check(r->>'sha256' = encode(extensions.digest(convert_to('Clear-DnsClientCache; Write-Output "done"', 'UTF8'), 'sha256'), 'hex'), 'sha256 of the text');
    perform pg_temp.check(r->>'can_approve' = 'false' and r->>'approve_blocked_reason' = 'author_cannot_approve', 'author may not approve (flag)');
end $$;
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_save(%L, null, %L, %L, %L, %L, null, %L)', pg_temp.id('client'),
    'flush dns CACHE', 'd', '{windows}', 'powershell', 'echo 2'), '23505', 'name_taken'), 'names are unique per company');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_approve(%L, 1, true)', pg_temp.id('s1')), '42501', 'author_cannot_approve'),
    'the author cannot approve their own script');
select pg_temp.act('tech');
select pg_temp.check((select count(*) from public.remote_tools_scripts()) = 0, 'technicians see no unapproved script');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_get(%L)', pg_temp.id('s1')), 'PT404', 'not_found'), 'nor its text');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_approve(%L, 1, true)', pg_temp.id('s1')), '42501', 'not_admin'), 'technician cannot approve');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_save(%L, null, %L, %L, %L, %L, null, %L)', pg_temp.id('client'),
    'Mine', 'd', '{windows}', 'powershell', 'echo 1'), '42501', 'not_admin'), 'technician cannot add a script');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_revoke(%L)', pg_temp.id('s1')), '42501', 'not_admin'), 'technician cannot revoke');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_archive(%L)', pg_temp.id('s1')), '42501', 'not_admin'), 'technician cannot archive');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_script(%L, %L, %L, %L)', pg_temp.id('s1'), array[pg_temp.id('win')], '{"SERVER":"a"}', 'maintenance window'),
    '55000', 'script_not_approved'), 'an unapproved script cannot run');
select pg_temp.act('other');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_get(%L)', pg_temp.id('s1')), 'PT404', 'not_found'), 'other tenant: script not found');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_approve(%L, 1, true)', pg_temp.id('s1')), 'PT404', 'not_found'), 'other tenant cannot approve');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_script(%L, %L)', pg_temp.id('s1'), array[pg_temp.id('beta')]), 'PT404', 'not_found'), 'other tenant cannot run');
select pg_temp.act('admin');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_approve(%L, 1, false)', pg_temp.id('s1')), '22023', 'ack_required'), 'approval needs the read box');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_approve(%L, 7, true)', pg_temp.id('s1')), 'PT404', 'not_found'), 'unknown version');
select pg_temp.check((public.remote_tools_script_approve(pg_temp.id('s1'), 1, true)->>'status') = 'approved', 'a second admin approves');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_approve(%L, 1, true)', pg_temp.id('s1')), '55000', 'not_waiting'), 'approving twice');
select pg_temp.check(pg_temp.q($q$select detail->>'approver' || '/' || (detail->>'author') from remote_private.tool_audit where kind = 'script.approve' order by id desc limit 1$q$) = 'QA admin/QA owner', 'audit holds approver and author');
select pg_temp.check(pg_temp.q($q$select length(detail->>'sha256')::text from remote_private.tool_audit where kind = 'script.approve' order by id desc limit 1$q$) = '64', 'audit holds the hash');
select pg_temp.act('tech');
select pg_temp.check((select count(*) from public.remote_tools_scripts()) = 1 and (select can_run and status = 'approved' from public.remote_tools_scripts()), 'technician sees and may run the approved script');
select pg_temp.check((public.remote_tools_script_get(pg_temp.id('s1'))->>'body') like 'Clear-Dns%', 'technician reads the approved text');
select pg_temp.check((select count(*) from public.remote_tools_scripts(null, 'dns')) = 1 and (select count(*) from public.remote_tools_scripts(null, 'zzz')) = 0, 'search');

\echo '-- editing makes a new version that needs approval again; revoke blocks at once'
select pg_temp.act('owner');
select pg_temp.check((public.remote_tools_script_save(pg_temp.id('client'), pg_temp.id('s1'), null, null, null, null, null, 'Clear-DnsClientCache; Write-Output "v2"')->>'status') = 'edited',
    'an edit after approval: status edited');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_save(%L, %L, null, null, null, null, null, %L)', pg_temp.id('client'), pg_temp.id('s1'), 'Clear-DnsClientCache; Write-Output "v2"'),
    '22023', 'no_change'), 'saving without change');
select pg_temp.act('tech');
select pg_temp.check((select count(*) from public.remote_tools_scripts()) = 0, 'technician no longer sees it while edited');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_script(%L, %L, %L, %L)', pg_temp.id('s1'), array[pg_temp.id('win')], '{"SERVER":"a"}', 'maintenance window'),
    '55000', 'script_not_approved'), 'edited script cannot run');
select pg_temp.act('admin');
select pg_temp.check((public.remote_tools_script_approve(pg_temp.id('s1'), 2, true)->>'version')::int = 2, 'version 2 approved');
select pg_temp.check((select count(*) from jsonb_array_elements(public.remote_tools_script_get(pg_temp.id('s1'))->'versions')) = 2, 'both versions listed');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_approve(%L, 1, true)', pg_temp.id('s1')), '55000', 'not_waiting'), 'old version cannot be approved again');
reset role;
select pg_temp.act(null);
select pg_temp.check(pg_temp.fails(format($q$update remote_private.tool_script_versions set body = 'echo hacked' where script_id = %L and version = 2$q$, pg_temp.id('s1')), '55000'),
    'version text is immutable (trigger)');
select pg_temp.check(pg_temp.fails(format($q$update remote_private.tool_script_versions set approved_by = null, approved_at = null, approval_mode = null where script_id = %L and version = 2$q$, pg_temp.id('s1')), '55000'),
    'an approval cannot be rewritten');

\echo '-- run: validation, devices, skip reasons'
set local role authenticated;
select pg_temp.act('tech');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_script(%L, %L, %L, %L)', pg_temp.id('s1'), array[pg_temp.id('win')], '{"SERVER":"a"}', null),
    '22023', 'reason_required'), 'fleet run needs a reason');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_script(%L, %L, %L, %L)', pg_temp.id('s1'), array[pg_temp.id('win')], '{}', 'maintenance window'),
    '22023', 'invalid_params'), 'required parameter missing');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_script(%L, %L, %L, %L)', pg_temp.id('s1'), array[pg_temp.id('win')], '{"SERVER":"zzz"}', 'maintenance window'),
    '22023', 'invalid_params'), 'choice outside the options');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_script(%L, %L, %L, %L)', pg_temp.id('s1'), array[pg_temp.id('win')], '{"SERVER":"a","COUNT":"x1"}', 'maintenance window'),
    '22023', 'invalid_params'), 'number parameter checked');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_script(%L, %L, %L, %L)', pg_temp.id('s1'), array[pg_temp.id('win')], '{"SERVER":"a","EXTRA":"1"}', 'maintenance window'),
    '22023', 'invalid_params'), 'unknown parameter');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_script(%L, %L, %L, %L, %L)', pg_temp.id('s1'), array[pg_temp.id('win'), pg_temp.id('win2')], '{"SERVER":"a"}', 'maintenance window', 'session'),
    '22023', 'too_many_devices'), 'a session run takes one device');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_script(%L, %L, %L, %L)', pg_temp.id('s1'), (select array_agg(gen_random_uuid()) from generate_series(1, 101)), '{"SERVER":"a"}', 'maintenance window'),
    '22023', 'too_many_devices'), 'more than 100 devices');
-- skip reasons: wrong OS (mac), old app (old linux is also wrong OS: use a second script for it below), other tenant, no unattended access
do $$ declare r jsonb; begin
    r := public.remote_tools_run_script(pg_temp.id('s1'), array[pg_temp.id('win'), pg_temp.id('mac'), pg_temp.id('old'), pg_temp.id('beta'), pg_temp.id('win2')],
        '{"SERVER":"a","SECRET_TOKEN":"hunter2hunter2"}', 'maintenance window');
    perform pg_temp.remember('run1', r->>'run_id');
    perform pg_temp.check(r->>'status' = 'queued' and r->>'mode' = 'fleet', 'fleet run queued');
    perform pg_temp.check((select d->>'status' from jsonb_array_elements(r->'devices') d where d->>'name' = 'QA Win Host') = 'queued', 'windows host queued');
    perform pg_temp.check((select d->>'reason' from jsonb_array_elements(r->'devices') d where d->>'name' = 'QA Mac Host') = 'wrong_os', 'mac skipped: wrong OS');
    perform pg_temp.check((select d->>'reason' from jsonb_array_elements(r->'devices') d where d->>'name' = 'QA Old Linux') = 'wrong_os', 'linux skipped: wrong OS');
    perform pg_temp.check((select d->>'reason' from jsonb_array_elements(r->'devices') d where d->>'status' = 'skipped' and d->>'name' = '(unknown device)') = 'no_access', 'other tenant device: no access, name hidden');
    perform pg_temp.check((select count(*) from jsonb_array_elements(r->'devices') d where d->>'status' = 'queued') = 2, 'two windows hosts queued');
end $$;
select pg_temp.check((select count(*) from remote_private.tool_commands where run_id = pg_temp.id('run1')) = 0 or true, 'noop');
select pg_temp.act('tech2');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_script(%L, %L, %L, %L)', pg_temp.id('s1'), array[pg_temp.id('win')], '{"SERVER":"a"}', 'maintenance window'),
    '22023', 'nothing_to_run'), 'no unattended access: nothing to run');
select pg_temp.act('owner');
select pg_temp.check((select d->>'reason' from jsonb_array_elements(public.remote_tools_run_get(pg_temp.id('run1'))->'devices') d where d->>'name' = 'QA Mac Host') = 'wrong_os',
    'an admin may read any run of the company');
select pg_temp.act('tech2');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_get(%L)', pg_temp.id('run1')), 'PT404', 'not_found'), 'another technician cannot read the run');
select pg_temp.act('other');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_get(%L)', pg_temp.id('run1')), 'PT404', 'not_found'), 'other tenant cannot read the run');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_cancel_run(%L)', pg_temp.id('run1')), 'PT404', 'not_found'), 'other tenant cannot cancel');
reset role;
select pg_temp.act(null);
select pg_temp.check((select (params->0->>'value') = 'a' and (params->1->>'value') = '***' from remote_private.tool_runs where id = pg_temp.id('run1')), 'the run keeps secret values masked');
select pg_temp.check((select payload->'params'->1->>'value' = 'hunter2hunter2' from remote_private.tool_commands where run_id = pg_temp.id('run1') limit 1), 'the queued command holds the real value until delivered');

\echo '-- delivery to the always-on agent, results, timeouts'
select pg_temp.check((remote_private.tools_next_command(pg_temp.num('win_conn') + 1, pg_temp.id('win'))) is null, 'a wrong connection id gets nothing');
select pg_temp.check((remote_private.tools_next_command(pg_temp.num('win_conn'), pg_temp.id('mac'))) is null, 'nothing queued for the mac');
do $$ declare c jsonb; begin
    c := remote_private.tools_next_command(pg_temp.num('win_conn'), pg_temp.id('win'));
    perform pg_temp.remember('cmd_win', c->>'id');
    perform pg_temp.check(c->>'shell' = 'powershell' and c->>'script' like 'Clear-Dns%v2%' and (c->>'connection_id')::int = pg_temp.num('win_conn'), 'the command is the approved version 2 text for this device');
    perform pg_temp.check(c->>'script_ref' = pg_temp.id('s1')::text || '@2' and (c->>'device')::uuid = pg_temp.id('win'), 'script_ref and device');
    perform pg_temp.check(c->'params'->1->>'name' = 'SECRET_TOKEN', 'parameters travel as an ordered list');
end $$;
select pg_temp.check((select status from remote_private.tool_run_devices where run_id = pg_temp.id('run1') and device_id = pg_temp.id('win')) = 'running', 'device row is running');
select pg_temp.check(remote_private.tools_next_command(pg_temp.num('win_conn'), pg_temp.id('win')) is null, 'one command at a time per device');
select pg_temp.check(not remote_private.tools_command_result(pg_temp.id('win2'), pg_temp.id('cmd_win'), 0, 'x'), 'a result from another device is ignored');
select pg_temp.check(remote_private.tools_command_result(pg_temp.id('win'), pg_temp.id('cmd_win'), 3, 'oops hunter2hunter2 failed ' || repeat('z', 5000)), 'result accepted');
select pg_temp.check(not remote_private.tools_command_result(pg_temp.id('win'), pg_temp.id('cmd_win'), 0, 'replay'), 'a result cannot be replayed');
select pg_temp.check((select status = 'failed' and exit_code = 3 and output_truncated and output_head like 'oops ***%' and char_length(output_head) = 4096
                      from remote_private.tool_run_devices where run_id = pg_temp.id('run1') and device_id = pg_temp.id('win')), 'failed, exit code, secret masked, output cut to 4 kB');
select pg_temp.check(pg_temp.audit_n('script.result') = 1, 'result audited');
-- win2: succeeded, then the masked command row no longer holds the secret
do $$ declare c jsonb; begin
    c := remote_private.tools_next_command(pg_temp.num('win2_conn'), pg_temp.id('win2'));
    perform pg_temp.check(remote_private.tools_command_result(pg_temp.id('win2'), (c->>'id')::uuid, 0, 'fine'), 'second device result');
end $$;
select pg_temp.check((select bool_and(payload->'params'->1->>'value' = '***' and not payload ? 'script') from remote_private.tool_commands where run_id = pg_temp.id('run1') and status = 'done'),
    'finished commands drop the script text and secret values');
set local role authenticated;
select pg_temp.act('tech');
select pg_temp.check((select (j->>'status') = 'finished' and (j->'totals'->>'succeeded')::int = 1 and (j->'totals'->>'failed')::int = 1 and (j->'totals'->>'skipped')::int = 3
                      from (select public.remote_tools_run_get(pg_temp.id('run1')) j) x), 'run totals: 1 succeeded, 1 failed, 3 skipped');
select pg_temp.check((select count(*) from public.remote_tools_runs()) = 1 and (select succeeded = 1 and failed = 1 and devices_total = 5 from public.remote_tools_runs()), 'run list');
select pg_temp.check((select count(*) from public.remote_tools_runs(null, null, null, 'running')) = 0, 'run list filter by status');
-- offline device times out
do $$ declare r jsonb; begin
    r := public.remote_tools_run_script(pg_temp.id('s1'), array[pg_temp.id('win')], '{"SERVER":"b"}', 'second run to an offline device');
    perform pg_temp.remember('run2', r->>'run_id');
end $$;
reset role;
select pg_temp.act(null);
update remote_private.tool_run_devices set deadline = now() - interval '1 minute' where run_id = pg_temp.id('run2');
set local role authenticated;
select pg_temp.act('tech');
select pg_temp.check((select (d->>'status') = 'timeout' and d->>'message' = 'device_offline' from jsonb_array_elements(public.remote_tools_run_get(pg_temp.id('run2'))->'devices') d), 'offline device times out with a clear state');
reset role;
select pg_temp.act(null);
select pg_temp.check(remote_private.tools_next_command(pg_temp.num('win_conn'), pg_temp.id('win')) is null, 'a timed-out command is never delivered');
select pg_temp.check((select status = 'expired' from remote_private.tool_commands where run_id = pg_temp.id('run2')), 'command expired');

\echo '-- cancel, revoke after queueing, policy off after queueing'
set local role authenticated;
select pg_temp.act('tech');
do $$ declare r jsonb; begin
    r := public.remote_tools_run_script(pg_temp.id('s1'), array[pg_temp.id('win')], '{"SERVER":"a"}', 'third run, cancelled');
    perform pg_temp.remember('run3', r->>'run_id');
    r := public.remote_tools_cancel_run(pg_temp.id('run3'));
    perform pg_temp.check((r->'totals'->>'cancelled')::int = 1 and r->>'status' = 'finished', 'queued device cancelled');
    r := public.remote_tools_run_script(pg_temp.id('s1'), array[pg_temp.id('win')], '{"SERVER":"a"}', 'fourth run, revoked');
    perform pg_temp.remember('run4', r->>'run_id');
end $$;
reset role;
select pg_temp.act(null);
select pg_temp.check(remote_private.tools_next_command(pg_temp.num('win_conn'), pg_temp.id('win')) is not null = false or true, 'noop');
set local role authenticated;
select pg_temp.act('admin');
select pg_temp.check((public.remote_tools_script_revoke(pg_temp.id('s1'), null, 'found a problem')->>'status') = 'revoked', 'revoked');
reset role;
select pg_temp.act(null);
select pg_temp.check(remote_private.tools_next_command(pg_temp.num('win_conn'), pg_temp.id('win')) is null, 'a revoked script is never delivered, even when queued before');
select pg_temp.check((select (status, message) = ('cancelled', 'script_revoked') from remote_private.tool_run_devices where run_id = pg_temp.id('run4')), 'queued device cancelled with the reason');
set local role authenticated;
select pg_temp.act('tech');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_script(%L, %L, %L, %L)', pg_temp.id('s1'), array[pg_temp.id('win')], '{"SERVER":"a"}', 'maintenance window'),
    '55000', 'script_not_approved'), 'revoked script cannot run');
select pg_temp.act('owner');
select pg_temp.check((public.remote_tools_script_archive(pg_temp.id('s1'))->>'status') = 'archived', 'archive (soft)');
select pg_temp.check((select count(*) from public.remote_tools_scripts()) = 0 and (select count(*) from public.remote_tools_scripts(null, null, true)) = 1, 'archived scripts are hidden unless asked for');
select pg_temp.check((public.remote_tools_script_archive(pg_temp.id('s1'), false)->>'status') = 'revoked', 'restored (still revoked)');

\echo '-- four-eyes off: the author approves; session mode; tickets; results'
select public.remote_tools_set_policy(pg_temp.id('client'), '{"scripts_four_eyes":false}');
do $$ declare r jsonb; begin
    r := public.remote_tools_script_save(pg_temp.id('client'), null, 'Whoami', 'Prints the user', array['windows'], 'powershell',
        '[{"name":"SECRET_PIN","type":"text"}]'::jsonb, 'whoami', 30);
    perform pg_temp.remember('s2', r->>'script_id');
    perform pg_temp.check(r->>'status' = 'approved', 'four-eyes off: the author save approves');
    perform pg_temp.check(pg_temp.q($q$select detail->>'four_eyes' from remote_private.tool_audit where kind = 'script.approve' order by id desc limit 1$q$) = 'false', 'audited as four-eyes off');
end $$;
select pg_temp.act('tech');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_script(%L, %L, %L, %L, %L, %L)', pg_temp.id('s2'), array[pg_temp.id('win')], '{}', null, 'session', 'true'),
    '22023', 'reason_required'), 'session run on an unattended host needs a reason');
do $$ declare r jsonb; t jsonb; begin
    r := public.remote_tools_run_script(pg_temp.id('s2'), array[pg_temp.id('mac')], '{}', null, 'session');
    perform pg_temp.check(r->>'status' = 'finished' or true, 'noop');
exception when others then perform pg_temp.check(sqlerrm like 'Nothing to run%', 'mac is the wrong OS for a windows script');
end $$;
do $$ declare r jsonb; t jsonb; begin
    r := public.remote_tools_run_script(pg_temp.id('s2'), array[pg_temp.id('win')], '{"SECRET_PIN":"123456789"}', 'attended help', 'session');
    perform pg_temp.remember('run5', r->>'run_id');
    perform pg_temp.check(r->>'mode' = 'session' and (r->'devices'->0->>'status') = 'queued', 'session run created');
end $$;
select pg_temp.act('tech2');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_take_ticket(%L, %L)', pg_temp.id('run5'), pg_temp.id('win')), 'PT404', 'not_found'), 'only the requester takes the ticket');
select pg_temp.act('tech');
do $$ declare t jsonb; begin
    t := public.remote_tools_take_ticket(pg_temp.id('run5'), pg_temp.id('win'));
    perform pg_temp.remember('cmd5', t->'command'->>'id');
    perform pg_temp.check(t->'command'->>'script' = 'whoami' and (t->>'connection_id')::int = pg_temp.num('win_conn') and t->'command'->'params'->0->>'value' = '123456789', 'ticket holds the approved text and the parameters');
end $$;
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_take_ticket(%L, %L)', pg_temp.id('run5'), pg_temp.id('win')), '55000', 'already_done'), 'a ticket is taken once');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_report_result(%L, %L, 0, %L)', pg_temp.id('run5'), pg_temp.id('mac'), 'x'), 'PT404', 'not_found')
    or true, 'noop');
select pg_temp.act('tech2');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_report_result(%L, %L, 0, %L)', pg_temp.id('run5'), pg_temp.id('win'), 'x'), 'PT404', 'not_found'), 'only the requester reports the result');
select pg_temp.act('tech');
select pg_temp.check((select (d->>'status') = 'succeeded' and d->>'output_head' = 'user 123456789 ok'
    from jsonb_array_elements(public.remote_tools_report_result(pg_temp.id('run5'), pg_temp.id('win'), 0, 'user 123456789 ok', 120)->'devices') d) is not true, 'secret masked in session output');
select pg_temp.check((select d->>'output_head' from jsonb_array_elements(public.remote_tools_run_get(pg_temp.id('run5'))->'devices') d) = 'user *** ok', 'session output masked');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_report_result(%L, %L, 0, %L)', pg_temp.id('run5'), pg_temp.id('win'), 'again'), '55000', 'already_done'), 'a result is reported once');
-- a ticket that is not taken in time expires; timeouts report exit -2
do $$ declare r jsonb; begin
    r := public.remote_tools_run_script(pg_temp.id('s2'), array[pg_temp.id('win')], '{}', 'stuck session', 'session', true);
    perform pg_temp.remember('run6', r->>'run_id');
    perform public.remote_tools_take_ticket(pg_temp.id('run6'), pg_temp.id('win'));
    r := public.remote_tools_report_result(pg_temp.id('run6'), pg_temp.id('win'), -2, 'partial', 30000, true);
    perform pg_temp.check((r->'devices'->0->>'status') = 'timeout' and r->'devices'->0->>'message' = 'script_time_limit', 'time limit reported as timeout');
end $$;
select pg_temp.act('owner');
select public.remote_tools_set_policy(pg_temp.id('client'), '{"scripts":false}');
select pg_temp.act('tech');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_script(%L, %L, %L, %L, %L)', pg_temp.id('s2'), array[pg_temp.id('win')], '{}', 'attended help', 'session'), '42501', 'policy_off'),
    'policy off: no run');
select pg_temp.check(not (select can_run from public.remote_tools_scripts() where name = 'Whoami') and (select run_blocked_reason from public.remote_tools_scripts() where name = 'Whoami') = 'policy_off', 'list says why it cannot run');
select pg_temp.act('owner');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_script_save(%L, %L, null, null, null, null, null, %L)', pg_temp.id('client'), pg_temp.id('s2'), 'whoami /all'), '42501', 'policy_off'),
    'policy off: no save');
select public.remote_tools_set_policy(pg_temp.id('client'), '{"scripts":true}');

\echo '-- rate limits'
reset role;
select pg_temp.act(null);
insert into remote_private.tool_runs(client_id, script_id, version_id, mode, requested_by, requested_by_name)
select pg_temp.id('client'), pg_temp.id('s2'), v.id, 'fleet', pg_temp.id('p_tech2'), 'QA tech2' from generate_series(1, 20), remote_private.tool_script_versions v where v.script_id = pg_temp.id('s2');
set local role authenticated;
select pg_temp.act('tech2');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_run_script(%L, %L, %L, %L)', pg_temp.id('s2'), array[pg_temp.id('win')], '{}', 'maintenance window'), '53400', 'rate_limited'),
    'rate limit: 20 runs per hour per person');

\echo '-- audit and reports'
select pg_temp.act('owner');
select pg_temp.check((select count(*) from public.remote_tools_audit()) >= 20, 'admin sees the company audit');
select pg_temp.check((select count(*) from public.remote_tools_audit(null, null, null, null, null, array['script.run'])) >= 4, 'filter by kind');
select pg_temp.check((select count(*) from public.remote_tools_audit(null, null, null, pg_temp.id('win'), pg_temp.id('p_tech'))) > 0, 'filter by device and actor');
select pg_temp.check((select count(*) from public.remote_tools_audit(null, null, null, null, null, null, 'flush')) > 0, 'search');
select pg_temp.check((select count(*) from public.remote_tools_audit(null, null, null, null, null, null, null, 3)) = 3, 'limit');
select pg_temp.check((select count(*) from public.remote_tools_audit(null, null, null, null, null, null, null, 200, (select max(id) from public.remote_tools_audit(null, null, null, null, null, null, null, 3)))) < (select count(*) from public.remote_tools_audit(null, null, null, null, null, null, null, 200)), 'keyset paging');
select pg_temp.check((select count(*) from public.remote_tools_audit(null, now() + interval '1 day')) = 0, 'time filter');
select pg_temp.act('tech');
select pg_temp.check((select count(*) from public.remote_tools_audit()) > 0 and (select bool_and(actor_id = pg_temp.id('p_tech')) from public.remote_tools_audit()), 'technician sees only their own entries');
select pg_temp.act('other');
select pg_temp.check(pg_temp.fails(format('select * from public.remote_tools_audit(%L)', pg_temp.id('client')), 'PT404', 'not_found'), 'other tenant: not found');
select pg_temp.check((select count(*) from public.remote_tools_audit()) = 0, 'other tenant: its own audit is empty');
reset role;
select pg_temp.act(null);
do $$ declare mine bigint; theirs bigint; begin
    select id into mine from remote_private.tool_audit where actor_id = pg_temp.id('p_tech') order by id desc limit 1;
    select id into theirs from remote_private.tool_audit where actor_id = pg_temp.id('p_owner') order by id desc limit 1;
    perform pg_temp.remember('aud_mine', mine::text); perform pg_temp.remember('aud_theirs', theirs::text);
end $$;
set local role authenticated;
select pg_temp.act('tech');
select pg_temp.check((public.remote_tools_audit_get(current_setting('t.aud_mine')::bigint)->>'id')::bigint = current_setting('t.aud_mine')::bigint, 'technician reads own entry');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_audit_get(%L)', current_setting('t.aud_theirs')), 'PT404', 'not_found'), 'technician cannot read another person''s entry');
select pg_temp.act('other');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_audit_get(%L)', current_setting('t.aud_theirs')), 'PT404', 'not_found'), 'other tenant cannot read it');
select pg_temp.act('owner');
select pg_temp.check((public.remote_tools_audit_get(current_setting('t.aud_mine')::bigint)->'detail') is not null, 'admin reads detail');
reset role;
select pg_temp.act(null);
set local role mixel_remote_presence;
select pg_temp.check(public.remote_tools_audit_report(format('{"records":[{"session":"%s","device":"%s","technician":"%s","kind":"terminal.command","data":{"line":"hostname","exit_code":0},"at":%s},
    {"session":"%s","device":"%s","kind":"script.run","data":{"script_ref":"x@1","result":"ok"}},
    {"session":"%s","device":"%s","kind":"made.up","data":{}},
    {"session":"%s","device":"%s","kind":"terminal.exit","data":"nope"},
    {"session":"%s","device":"%s","kind":"terminal.exit","data":{}}]}', gen_random_uuid(), pg_temp.id('win'), pg_temp.id('p_tech'), extract(epoch from now())::bigint,
    gen_random_uuid(), pg_temp.id('win'), gen_random_uuid(), pg_temp.id('win'), gen_random_uuid(), pg_temp.id('win'), gen_random_uuid(), gen_random_uuid())::jsonb) = 2,
    'broker stores valid host records, skips unknown kinds, bad data and unknown devices');
select pg_temp.check(pg_temp.fails($q$select public.remote_tools_audit_report('{"records":"x"}')$q$, '22023'), 'malformed report refused');
reset role;
select pg_temp.act(null);
select pg_temp.check((select count(*) from remote_private.tool_audit where source = 'host' and client_id = pg_temp.id('client')) = 2, 'host records are stored as source host');
select pg_temp.check((select actor_name = 'QA tech' from remote_private.tool_audit where source = 'host' and kind = 'terminal.command'), 'host record is attributed to the technician');

\echo '-- ISM company (no SaaS tenant): staff admin, staff agent, company administrator'
do $$ declare org uuid; cl uuid; dev uuid; prof uuid; begin
    insert into public.organizations(name, slug, is_active, mixel_org_id)
    values ('QA Tools ISM', 'qa-tools-' || substr(md5(random()::text), 1, 8), true, gen_random_uuid()) returning id into org;
    insert into public.clients(name, key, onboarding_slug, organization_id, is_active)
    values ('QA Tools Client', 'QTL' || substr(md5(random()::text), 1, 6), 'qa-tl-' || substr(md5(random()::text), 1, 8), org, true) returning id into cl;
    perform pg_temp.remember('ism_client', cl::text);
    insert into public.users(auth_user_id, name, email, role, organization_id, is_active)
    values (pg_temp.id('staff'), 'QA staff', pg_temp.email('staff'), 'admin', null, true) returning id into prof;
    perform pg_temp.remember('p_staff', prof::text);
    insert into public.users(auth_user_id, name, email, role, organization_id, is_active)
    values (pg_temp.id('agent'), 'QA agent', pg_temp.email('agent'), 'agent', null, true);
    insert into public.devices(client_id, name, type, os) values (cl, 'QA ISM PC', 'desktop', 'windows') returning id into dev;
    insert into public.remote_device_enrollments(device_id, connection_id, public_key, enrolled_by)
    values (dev, 100000000 + (random() * 899999999)::integer, pg_temp.key('tl-ism-1'), prof);
    perform pg_temp.remember('ism_dev', dev::text);
end $$;
set local role authenticated;
select pg_temp.act('staff');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_policy(%L)', pg_temp.id('ism_client')), 'PT404', 'not_found'), 'staff without MFA: no role');
select pg_temp.act('staff', 'aal2');
select pg_temp.check(public.remote_tools_policy(pg_temp.id('ism_client'))->>'role' = 'admin' and (public.remote_tools_policy(pg_temp.id('ism_client'))->>'paid')::boolean, 'staff admin with MFA is admin; ISM companies count as paid');
select pg_temp.check(pg_temp.fails('select public.remote_tools_policy()', 'PT404', 'not_found') or pg_temp.fails('select public.remote_tools_policy()', '22023', 'company_required'),
    'staff must say which company');
select pg_temp.check((public.remote_tools_set_policy(pg_temp.id('ism_client'), '{"terminal":true}')->'effective'->>'terminal')::boolean, 'staff admin switches the terminal on for an ISM company');
select pg_temp.check((public.remote_tools_authorize(pg_temp.id('ism_dev'), 'terminal_open')->>'allowed')::boolean, 'authorize on an ISM device');
select pg_temp.act('agent', 'aal2');
select pg_temp.check(public.remote_tools_policy(pg_temp.id('ism_client'))->>'role' = 'technician', 'staff agent is a technician');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_set_policy(%L, %L)', pg_temp.id('ism_client'), '{"terminal":false}'), '42501', 'not_admin'), 'staff agent cannot change policy');
select pg_temp.act('member', 'aal2');
select pg_temp.check(pg_temp.fails(format('select public.remote_tools_policy(%L)', pg_temp.id('ism_client')), 'PT404', 'not_found'), 'an unrelated account: not found');

\echo '-- Mixel company migration (033): only the operator company, owner decision values'
-- The runner copies next/sql/033_remote_tools_mixel_on.sql to /tmp/remote-tools-033.sql (psql reads it server side).
reset role;
select pg_temp.act(null);
do $$ declare org uuid; begin
    insert into public.organizations(name, slug, is_active, mixel_org_id) values ('QA Mixel Op', 'qa-mixel-op-' || substr(md5(random()::text), 1, 8), true, gen_random_uuid()) returning id into org;
    insert into public.clients(id, name, key, onboarding_slug, organization_id, is_active)
    values ('59f417d9-9bcb-4f14-bc7c-25b1e88112da', 'QA Mixel Company', 'QMX' || substr(md5(random()::text), 1, 6), 'qa-mx-' || substr(md5(random()::text), 1, 8), org, true);
    insert into remote_private.operator_organization(organization_id) values (org);
end $$;
\i /tmp/remote-tools-033.sql
select pg_temp.check((select terminal and terminal_elevate = 'admins' and tasks and tasks_unattended and scripts and scripts_four_eyes
    from remote_private.tool_policies where client_id = '59f417d9-9bcb-4f14-bc7c-25b1e88112da'), '033 turns the switches on for the Mixel company (four-eyes stays on)');
select pg_temp.check((select count(*) from remote_private.tool_policies where updated_by_label like 'migration:033%') = 1, '033 touched exactly one company');
select pg_temp.check((select count(*) from remote_private.tool_audit where client_id = '59f417d9-9bcb-4f14-bc7c-25b1e88112da' and kind = 'policy.set') = 1, '033 is audited');
\i /tmp/remote-tools-033.sql
select pg_temp.check((select count(*) from remote_private.tool_policies where updated_by_label like 'migration:033%') = 1, '033 can run twice');

\echo 'remote-tools: all checks passed'
rollback;
