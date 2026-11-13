-- Removes the QA tenants of the portal's live e2e run (company "QA Portal <run>", users
-- qa-portal-<run>-*@example.com, delivered+qa-portal-<run>-owner@resend.dev) and everything they created. Run as the database owner:
--   docker exec -i ism-postgres psql -U postgres -d ism -v ON_ERROR_STOP=1 -v run=<run> < cleanup.sql
-- (:run = the QA_RUN value; use the run id, never a wildcard.)
begin;
create temp table qa_t on commit drop as
    select * from remote_saas.tenants where slug = 'qa-portal-' || :'run' and name = 'QA Portal ' || :'run';
create temp table qa_users on commit drop as
    select u.id, u.auth_user_id from public.users u
    where u.email like 'qa-portal-' || :'run' || '-%@example.com'
       or u.email like 'delivered+qa-portal-' || :'run' || '-%@resend.dev'
       or u.organization_id in (select organization_id from qa_t);
create temp table qa_dev on commit drop as
    select d.id from public.devices d where d.client_id in (select client_id from qa_t);

-- Devices (including the web viewer's browser registrations) and their reserved IDs.
delete from remote_private.connection_reservations
    where device_id in (select id from qa_dev)
       or public_key in (select e.public_key from public.remote_device_enrollments e where e.device_id in (select id from qa_dev));
delete from remote_private.connection_reservations r
    where exists (select 1 from remote_private.enrollment_audit a
                  where a.actor_id in (select id from qa_users) and (a.previous_key = r.public_key or a.next_key = r.public_key));
delete from remote_private.enrollment_audit where device_id in (select id from qa_dev) or actor_id in (select id from qa_users);
delete from public.remote_device_enrollments where device_id in (select id from qa_dev);
delete from public.devices where id in (select id from qa_dev);
delete from remote_private.rollout_tokens where client_id in (select client_id from qa_t) or created_by in (select id from qa_users);
delete from remote_private.support_codes where created_by in (select id from qa_users);

-- Tenant (members, invites, settings, domains, groups, device metadata, audit log cascade).
delete from remote_saas.tenants where id in (select id from qa_t);
delete from public.clients where id in (select client_id from qa_t);
update public.organizations set owner_id = null where id in (select organization_id from qa_t);
delete from public.users where id in (select id from qa_users);
delete from public.organizations where id in (select organization_id from qa_t) and kind = 'remote_saas';

select (select count(*) from qa_t) as tenants, (select count(*) from qa_users) as profiles, (select count(*) from qa_dev) as devices;
commit;
