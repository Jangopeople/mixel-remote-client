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
-- CHECK AGAINST SQL 029 before the first live run: the table and column names below follow the report
-- contract (session_id, technician_id, device_id, connection_id, started_at, ended_at, kind, colleague);
-- adjust the INSERT if 029 stores sessions differently. Set QA_REPORTS_SEED to use another file.
\set ON_ERROR_STOP on
begin;
insert into remote_saas.sessions (tenant_id, technician_id, device_id, connection_id, started_at, ended_at, kind, colleague)
values
  (:'tenant', :'technician', :'device_a', :'conn_a'::integer, now() - interval '3 hours', now() - interval '3 hours' + interval '25 minutes', 'attended', false),
  (:'tenant', :'technician', :'device_b', :'conn_b'::integer, now() - interval '50 hours', now() - interval '50 hours' + interval '70 minutes', 'unattended', false);
select count(*) from remote_saas.sessions where tenant_id = :'tenant';
commit;
