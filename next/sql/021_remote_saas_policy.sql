-- Mixel Remote as SaaS, stage 1: connection policy for tenants. The broker's policy service
-- (mixel_remote_policy_reader) asks remote_private.saas_support_pair for a session between two
-- enrolled devices; it answers only for hosts of a SaaS tenant (no row otherwise, so linked ISM
-- companies and personal accounts keep their existing checks). SaaS tenants are treated like linked
-- companies: no Mixel admin company is needed. Mixel staff cannot open sessions to tenant devices.
begin;

-- Session between two enrolled devices of one SaaS tenant. allowed: the controller is an active
-- owner/admin/technician of that tenant with access to the host device, both device bindings are
-- current, both profiles belong to the tenant, the tenant may hold sessions (active, trial,
-- past_due). max_sessions: the plan's concurrent-session limit, enforced by the broker.
create function remote_private.saas_support_pair(
    controller_profile uuid, controller_device uuid, controller_generation bigint, controller_key bytea,
    host_profile uuid, host_device uuid, host_generation bigint, host_key bytea, unattended boolean
) returns table (tenant uuid, organization uuid, allowed boolean, max_sessions integer)
language sql stable security definer set search_path = '' as $$
    select t.id, t.organization_id,
        coalesce((
            select true
            from public.remote_device_enrollments he
            join public.users hu on hu.id = host_profile and hu.is_active and hu.deleted_at is null
                and hu.organization_id = t.organization_id
            join remote_saas.members hm on hm.tenant_id = t.id and hm.user_id = hu.id
            join public.remote_device_enrollments ce on ce.device_id = controller_device
                and ce.generation = controller_generation and ce.public_key = controller_key and ce.revoked_at is null
            join public.devices cd on cd.id = ce.device_id and cd.client_id = t.client_id
            join public.users cu on cu.id = controller_profile and cu.is_active and cu.deleted_at is null
                and cu.organization_id = t.organization_id
            join remote_saas.members cm on cm.tenant_id = t.id and cm.user_id = cu.id
                and cm.role in ('owner', 'admin', 'technician')
            where he.device_id = host_device and he.generation = host_generation and he.public_key = host_key
              and he.revoked_at is null and controller_device <> host_device
              and o.is_active and c.is_active
              and remote_saas.effective_status(t) in ('active', 'trial', 'past_due')
              and remote_saas.can_access_device(cu.id, host_device)
        ), false),
        l.max_sessions
    from public.devices hd
    join remote_saas.tenants t on t.client_id = hd.client_id
    join public.clients c on c.id = t.client_id
    join public.organizations o on o.id = t.organization_id and o.kind = 'remote_saas'
    cross join lateral remote_saas.limits(t.id) l
    where hd.id = host_device;
$$;

-- An always-on (sign-in screen / startup) host of a SaaS tenant may register when its enrollment
-- is current and its enrolling profile is still an active member of that tenant.
create function remote_private.saas_startup_host(
    connection integer, device uuid, profile uuid, tenant uuid, key_generation bigint, device_key bytea
) returns boolean
language sql stable security definer set search_path = '' as $$
    select exists (
        select 1 from public.remote_device_enrollments e
        join public.devices d on d.id = e.device_id
        join remote_saas.tenants t on t.client_id = d.client_id and t.organization_id = tenant
        join public.clients c on c.id = t.client_id and c.is_active
        join public.organizations o on o.id = t.organization_id and o.is_active and o.kind = 'remote_saas'
        join public.users u on u.id = profile and u.is_active and u.deleted_at is null
            and u.organization_id = t.organization_id
        join remote_saas.members m on m.tenant_id = t.id and m.user_id = u.id
        where e.connection_id = connection and e.device_id = device and e.generation = key_generation
          and e.public_key = device_key and e.revoked_at is null
          and remote_saas.effective_status(t) in ('active', 'trial', 'past_due')
    );
$$;

revoke all on function remote_private.saas_support_pair(uuid,uuid,bigint,bytea,uuid,uuid,bigint,bytea,boolean),
    remote_private.saas_startup_host(integer,uuid,uuid,uuid,bigint,bytea) from public, anon, authenticated;
grant execute on function remote_private.saas_support_pair(uuid,uuid,bigint,bytea,uuid,uuid,bigint,bytea,boolean),
    remote_private.saas_startup_host(integer,uuid,uuid,uuid,bigint,bytea) to mixel_remote_policy_reader;
-- The policy functions read remote_saas as their owner; the reader role needs no schema rights.

-- Enrollment visibility (RLS of public.remote_device_enrollments, used by the identity authority
-- and the device directory). Unchanged for ISM companies. SaaS tenant devices: only members of that
-- tenant who may access the device (groups for technicians, own devices for members), honouring the
-- tenant's required two-step sign-in and allowed sign-in methods; not Mixel staff.
create or replace function remote_private.can_read_device(target uuid) returns boolean
language sql stable security definer set search_path = '' as $$
    select case when t.id is null then exists (
        select 1 from public.users u
        join public.devices d on d.id = target
        join public.clients c on c.id = d.client_id
        left join public.organizations o on o.id = c.organization_id
        where u.auth_user_id = auth.uid() and u.is_active and c.is_active
          and (c.organization_id is null or o.is_active)
          and (
            (u.organization_id is null and u.role::text in ('admin','agent')
             and coalesce(auth.jwt()->>'aal','') = 'aal2')
            or (u.organization_id is not null and u.organization_id = c.organization_id)
          )
    ) else exists (
        select 1 from public.users u
        join remote_saas.members m on m.tenant_id = t.id and m.user_id = u.id
        join public.organizations o on o.id = t.organization_id and o.is_active
        join public.clients c on c.id = t.client_id and c.is_active
        left join remote_saas.auth_settings s on s.tenant_id = t.id
        where u.auth_user_id = auth.uid() and u.is_active and u.deleted_at is null
          and u.organization_id = t.organization_id
          and remote_saas.effective_status(t) <> 'closed'
          and (s.tenant_id is null or ((not s.require_mfa or coalesce(auth.jwt()->>'aal','') = 'aal2')
               and remote_saas.method_allowed(s.methods)))
          and remote_saas.can_access_device(u.id, target)
    ) end
    from (select 1) one
    left join public.devices d on d.id = target
    left join remote_saas.tenants t on t.client_id = d.client_id;
$$;

commit;
notify pgrst, 'reload schema';
