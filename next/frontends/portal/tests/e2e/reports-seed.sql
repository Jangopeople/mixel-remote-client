-- Seeds two finished remote sessions for the reports e2e run (tests/e2e/reports.spec.ts). Session
-- rows are written by the connection broker in production; this stands in for it. Run by the spec
-- through QA_SQL with psql variables (all validated by the spec: uuids and 9-digit Remote IDs):
--   :tenant      the QA company (remote_saas.tenants.id)
--   :technician  the QA owner's profile (public.users.id), who "connected"
--   :device_a / :conn_a   QA device 1 and its Remote ID: attended session 3 h ago, 25 minutes
--   :device_b / :conn_b   QA device 2 and its Remote ID: unattended session 2 days ago, 70 minutes
-- The spec expects afterwards: remote_saas_session_report(tenant) returns exactly these two rows for the
-- owner, remote_saas_recent_devices(tenant) lists device_a before device_b, and
-- remote_saas_session_note(tenant, note, connection => conn_a) finds the first one (it is under 24 h old).
--
-- Session A goes through the broker's own function (role mixel_remote_presence, as in production);
-- session B is older than the broker function accepts (1 day), so it is inserted directly.
\set ON_ERROR_STOP on
begin;
set local role mixel_remote_presence;
select public.remote_session_report(jsonb_build_object('events', jsonb_build_array(
  jsonb_build_object('type', 'start', 'id', gen_random_uuid(), 'tenant', :'tenant', 'technician', :'technician',
    'controller_device', :'device_b', 'controller_connection', :'conn_b'::integer, 'device', :'device_a',
    'connection', :'conn_a'::integer, 'unattended', false, 'colleague', false,
    'at', extract(epoch from now() - interval '3 hours')::bigint)))) as started;
reset role;
update remote_saas.sessions set ended_at = started_at + interval '25 minutes'
 where tenant_id = :'tenant' and device_id = :'device_a';
insert into remote_saas.sessions (id, tenant_id, technician_id, controller_device_id, controller_connection, device_id,
  host_connection, device_name, kind, colleague, started_at, ended_at)
select gen_random_uuid(), :'tenant', :'technician', :'device_a', :'conn_a'::integer, :'device_b', :'conn_b'::integer,
  'QA device 2', 'unattended', false, now() - interval '50 hours', now() - interval '50 hours' + interval '70 minutes';
select count(*) from remote_saas.sessions where tenant_id = :'tenant';
commit;
