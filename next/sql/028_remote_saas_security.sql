-- Mixel Remote as SaaS: security fixes from the 2026-11-13 review (tests: next/sql/tests/remote-saas-security.sql).
--
-- 1. Tenant profiles are ordinary ISM profiles, so the ISM row-level security let them reach the ISM
--    tables of their organization directly through PostgREST, around the remote_saas RPCs: any member
--    could deactivate or relink the company's organization (locking out every owner), insert, rename
--    and delete device records, delete other members' profiles (dropping their membership) and read
--    every member's email address and every device regardless of role and groups. Restrictive
--    policies now close the ISM tables of remote_saas organizations to everything but Mixel staff and
--    the SECURITY DEFINER RPCs (which run as the table owner); reads follow the remote_saas rules.
-- 2. Any signed-in account without a profile could INSERT its own public.users row with any
--    organization_id (the ISM policy users_org_scoped accepts auth_user_id = auth.uid() for any
--    organization) and so join any company, SaaS tenant or ISM customer, with full ISM rights there.
--    Non-staff callers may now add a profile only to the organization they already belong to (or
--    that their sign-in was provisioned for), and never link another person's sign-in identity.
-- 3. A technician who renamed or moved a device was recorded as the person who added it, so after a
--    demotion to member it kept seeing that device. Rename and move no longer set added_by.
-- 4. Trial companies can invite at most 20 people a day (invitation e-mails go out from Mixel).
-- 5. A company SSO provider must exist and can belong to one company only.
begin;
set local lock_timeout = '5s';

---------------------------------------------------------------------------------------------------
-- Helpers for the policies (SECURITY DEFINER: authenticated has no rights on remote_saas).

create function remote_private.saas_org(org uuid) returns boolean
language sql stable security definer set search_path = '' as $$
    select org is not null and exists (select 1 from public.organizations o where o.id = org and o.kind = 'remote_saas');
$$;

create function remote_private.saas_client(client uuid) returns boolean
language sql stable security definer set search_path = '' as $$
    select client is not null and exists (select 1 from remote_saas.tenants t where t.client_id = client);
$$;

-- May the caller list the profiles of this tenant organization (same rule as remote_saas_members:
-- an active owner, admin or technician of that tenant)?
create function remote_private.saas_can_list_members(org uuid) returns boolean
language sql stable security definer set search_path = '' as $$
    select exists (
        select 1 from remote_saas.tenants t
        join remote_saas.members m on m.tenant_id = t.id and m.role in ('owner', 'admin', 'technician')
        join public.users u on u.id = m.user_id and u.auth_user_id = auth.uid() and auth.uid() is not null
            and u.is_active and u.deleted_at is null and u.organization_id = t.organization_id
        join public.organizations o on o.id = t.organization_id and o.is_active
        where t.organization_id = org and remote_saas.effective_status(t) <> 'closed');
$$;

revoke all on function remote_private.saas_org(uuid), remote_private.saas_client(uuid),
    remote_private.saas_can_list_members(uuid) from public, anon, authenticated;
grant execute on function remote_private.saas_org(uuid), remote_private.saas_client(uuid),
    remote_private.saas_can_list_members(uuid) to authenticated;

---------------------------------------------------------------------------------------------------
-- 1. Restrictive policies on the ISM tables for remote_saas organizations.

-- organizations: no direct writes to a SaaS organization, and no ISM organization turned into one.
create policy remote_saas_org_no_insert on public.organizations as restrictive for insert to authenticated
    with check (kind <> 'remote_saas' or (select public.is_mixel_staff()));
create policy remote_saas_org_no_update on public.organizations as restrictive for update to authenticated
    using (kind <> 'remote_saas' or (select public.is_mixel_staff()))
    with check (kind <> 'remote_saas' or (select public.is_mixel_staff()));
create policy remote_saas_org_no_delete on public.organizations as restrictive for delete to authenticated
    using (kind <> 'remote_saas' or (select public.is_mixel_staff()));

-- devices: SaaS device records change only through the RPCs (limits, roles, audit); reads follow the
-- tenant's device rules (owners/admins all, technicians their groups, members their own devices).
create policy remote_saas_devices_read on public.devices as restrictive for select to authenticated
    using (not remote_private.saas_client(client_id) or (select public.is_mixel_staff())
           or remote_private.can_read_device(id));
create policy remote_saas_devices_no_insert on public.devices as restrictive for insert to authenticated
    with check (not remote_private.saas_client(client_id) or (select public.is_mixel_staff()));
create policy remote_saas_devices_no_update on public.devices as restrictive for update to authenticated
    using (not remote_private.saas_client(client_id) or (select public.is_mixel_staff()))
    with check (not remote_private.saas_client(client_id) or (select public.is_mixel_staff()));
create policy remote_saas_devices_no_delete on public.devices as restrictive for delete to authenticated
    using (not remote_private.saas_client(client_id) or (select public.is_mixel_staff()));

-- users: in a SaaS organization a profile sees itself; owners, admins and technicians see the
-- company's profiles. Profiles are created and removed only by the RPCs; a person may still edit
-- their own harmless fields (protect_user_contact_fields keeps role, organization, email, active).
create policy remote_saas_users_read on public.users as restrictive for select to authenticated
    using (not remote_private.saas_org(organization_id) or auth_user_id = auth.uid()
           or (select public.is_mixel_staff()) or remote_private.saas_can_list_members(organization_id));
create policy remote_saas_users_no_insert on public.users as restrictive for insert to authenticated
    with check (not remote_private.saas_org(organization_id) or (select public.is_mixel_staff()));
create policy remote_saas_users_own_update on public.users as restrictive for update to authenticated
    using (not remote_private.saas_org(organization_id) or auth_user_id = auth.uid() or (select public.is_mixel_staff()))
    with check (not remote_private.saas_org(organization_id) or auth_user_id = auth.uid() or (select public.is_mixel_staff()));
create policy remote_saas_users_no_delete on public.users as restrictive for delete to authenticated
    using (not remote_private.saas_org(organization_id) or (select public.is_mixel_staff()));

---------------------------------------------------------------------------------------------------
-- 2. No joining another organization by inserting one's own profile (all organizations).
-- Runs as the caller (not SECURITY DEFINER) so that it applies to PostgREST requests only; the
-- SECURITY DEFINER sign-up and invitation RPCs run as the table owner and are not affected. Named
-- zz_ so it runs after link_existing_auth_for_reporter has filled auth_user_id from the email.
create function public.guard_users_profile_insert() returns trigger
language plpgsql set search_path = '' as $$
begin
    if current_user <> 'authenticated' or auth.uid() is null or public.is_mixel_staff() then
        return new;
    end if;
    if new.organization_id is not null and new.organization_id is distinct from public.viewer_org_id() then
        raise exception 'Only Mixel staff can add a profile to another organization' using errcode = '42501';
    end if;
    if new.auth_user_id is not null and new.auth_user_id <> auth.uid() then
        raise exception 'Only Mixel staff can link another person''s sign-in to a profile' using errcode = '42501';
    end if;
    return new;
end;
$$;
revoke all on function public.guard_users_profile_insert() from public, anon, authenticated;
create trigger zz_guard_users_profile_insert before insert on public.users
    for each row execute function public.guard_users_profile_insert();

---------------------------------------------------------------------------------------------------
-- 3. Rename and move do not make the caller the device's "adder".
update remote_saas.device_meta dm set added_by = null
where dm.added_via = 'staff' and dm.added_by is not null;

create or replace function public.remote_saas_rename_device(tenant uuid, device uuid, device_name text)
returns text language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; d public.devices; clean text := remote_saas.clean_device_name(device_name);
        final text; e public.remote_device_enrollments;
begin
    me := remote_saas.actor(tenant, '{owner,admin,technician}');
    d := remote_saas.tenant_device(tenant, device);
    if not remote_saas.can_access_device(me.user_id, device) then
        raise exception 'Device unavailable' using errcode = '42501';
    end if;
    perform 1 from public.devices where id = device for update;
    final := remote_saas.free_device_name(d.client_id, clean, device);
    update public.devices set name = final, updated_at = now() where id = device;
    insert into remote_saas.device_meta(device_id, tenant_id, display_name, added_by, added_via)
    values (device, tenant, final, null, 'staff')
    on conflict (device_id) do update set display_name = excluded.display_name;
    select * into e from public.remote_device_enrollments where device_id = device;
    if found then
        insert into remote_private.enrollment_audit(device_id, actor_id, action, generation)
        values (device, me.user_id, 'rename', e.generation);
    end if;
    perform remote_saas.audit(tenant, me.user_id, 'device.rename', device::text,
        jsonb_build_object('from', d.name, 'to', final));
    return final;
end;
$$;

create or replace function public.remote_saas_move_device(tenant uuid, device uuid, to_group uuid)
returns boolean language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; d public.devices; current_group uuid; e public.remote_device_enrollments;
begin
    me := remote_saas.actor(tenant, '{owner,admin,technician}');
    perform remote_saas.require_feature(tenant, 'groups', 'Device groups need the Team plan');
    d := remote_saas.tenant_device(tenant, device);
    if to_group is not null then perform remote_saas.group_of(tenant, to_group); end if;
    select dm.group_id into current_group from remote_saas.device_meta dm where dm.device_id = device for update;
    if not remote_saas.can_access_device(me.user_id, device) or not remote_saas.can_use_group(me, to_group)
        or (me.role = 'technician' and to_group is null and current_group is not null
            and not remote_saas.can_use_group(me, current_group)) then
        raise exception 'Not allowed for this device or group' using errcode = '42501';
    end if;
    insert into remote_saas.device_meta(device_id, tenant_id, group_id, added_by, added_via)
    values (device, tenant, to_group, null, 'staff')
    on conflict (device_id) do update set group_id = excluded.group_id;
    select * into e from public.remote_device_enrollments where device_id = device;
    if found then
        insert into remote_private.enrollment_audit(device_id, actor_id, action, generation)
        values (device, me.user_id, 'move', e.generation);
    end if;
    perform remote_saas.audit(tenant, me.user_id, 'device.move', device::text,
        jsonb_build_object('from', current_group, 'to', to_group, 'name', d.name));
    return true;
end;
$$;

---------------------------------------------------------------------------------------------------
-- 4. Invitations from trial companies: at most 20 a day.
create function remote_saas.invite_trial_cap() returns trigger
language plpgsql security definer set search_path = '' as $$
declare t remote_saas.tenants;
begin
    select * into t from remote_saas.tenants where id = new.tenant_id;
    if remote_saas.effective_status(t) = 'trial' and (select count(*) from remote_saas.invites i
            where i.tenant_id = new.tenant_id and i.created_at > now() - interval '1 day') >= 20 then
        raise exception 'A trial can invite 20 people a day; choose a plan to invite more' using errcode = '54000';
    end if;
    return new;
end;
$$;
revoke all on function remote_saas.invite_trial_cap() from public, anon, authenticated;
create trigger invite_trial_cap before insert on remote_saas.invites
    for each row execute function remote_saas.invite_trial_cap();

---------------------------------------------------------------------------------------------------
-- 5. Company SSO provider: must exist in GoTrue and belongs to one company.
create unique index auth_settings_sso_provider_once on remote_saas.auth_settings(sso_provider_id)
    where sso_provider_id is not null;
create function remote_saas.check_sso_provider() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
    if new.sso_provider_id is not null and new.sso_provider_id is distinct from old.sso_provider_id
        and not exists (select 1 from auth.sso_providers p where p.id = new.sso_provider_id) then
        raise exception 'Unknown SSO provider' using errcode = '22023';
    end if;
    return new;
end;
$$;
revoke all on function remote_saas.check_sso_provider() from public, anon, authenticated;
create trigger check_sso_provider before insert or update of sso_provider_id on remote_saas.auth_settings
    for each row execute function remote_saas.check_sso_provider();

do $$ declare f text; begin
    for f in select p.oid::regprocedure::text from pg_proc p
             where p.pronamespace = 'public'::regnamespace
               and p.proname in ('remote_saas_rename_device', 'remote_saas_move_device') loop
        execute format('revoke all on function %s from public, anon, authenticated', f);
        execute format('grant execute on function %s to authenticated', f);
    end loop;
end $$;

commit;
notify pgrst, 'reload schema';
