-- Mixel Remote 2.0 server work of 17 Nov 2026 (contract: mixel-craft/docs/mixel-remote-device-admin-api.md).
--
-- 1. Device administration for the apps' Devices list: list, rename, move, note, favourite, unattended
--    setting, remove. One set of functions for Remote SaaS tenants and ISM companies. Admin rights are
--    checked here (owner/admin of a SaaS tenant; ISM staff with role admin and MFA; a company administrator
--    of that ISM company), every change writes an audit row. Devices the caller may not see are "not found".
-- 2. D1: company policy "unattended on by default" (on for the Mixel company only, by data below).
-- 3. D8: Remote SaaS unattended needs a per-device grant, or the controller is listed in the company
--    setting "accounts with automatic access to all company devices" (validated, audited; set below for
--    the Mixel company to the owner's account only). A device set to "off" refuses unattended everywhere.
-- 4. App version per device, reported by the broker with presence.
-- 5. Removal is visible to the admin API (remote_private.device_removed_since), which invalidates the
--    pairings and grants kept in the admin database.
-- Error hints (stable keys for the apps) are in the HINT of every raised error.
begin;
set local lock_timeout = '5s';

---------------------------------------------------------------------------------------------------
-- Tables

-- The organization that operates Mixel Remote (its own staff may be listed for its own companies only).
create table remote_private.operator_organization (
    organization_id uuid primary key references public.organizations(id) on delete restrict,
    singleton boolean not null default true unique check (singleton)
);

-- D1: per company (ISM client, also the client of a SaaS tenant).
create table remote_private.company_policies (
    client_id uuid primary key references public.clients(id) on delete cascade,
    unattended_default boolean not null default false,
    updated_by text not null check (char_length(updated_by) between 1 and 120),
    updated_at timestamptz not null default now()
);

-- D8: accounts with automatic unattended access to all devices of one company.
create table remote_private.company_auto_access (
    client_id uuid not null references public.clients(id) on delete cascade,
    profile_id uuid not null references public.users(id) on delete cascade,
    granted_by text not null check (char_length(granted_by) between 1 and 120),
    reason text not null check (char_length(reason) between 1 and 300),
    granted_at timestamptz not null default now(),
    primary key (client_id, profile_id)
);

create table remote_private.company_policy_audit (
    id bigint generated always as identity primary key,
    client_id uuid not null,
    actor text not null,
    action text not null check (action in ('policy.set', 'auto_access.grant', 'auto_access.revoke')),
    detail jsonb not null default '{}'::jsonb,
    occurred_at timestamptz not null default now()
);

-- Admin settings of one enrolled device (ISM companies and SaaS tenants).
create table remote_private.device_admin_settings (
    device_id uuid primary key references public.devices(id) on delete cascade,
    note text check (note is null or (char_length(note) between 1 and 500 and note !~ '[\x00-\x08\x0b-\x1f\x7f]')),
    -- ISM companies only (SaaS tenants use remote_saas.device_groups).
    group_name text check (group_name is null or (char_length(group_name) between 1 and 60
        and group_name !~ '[[:cntrl:]]' and group_name = btrim(group_name))),
    unattended text not null default 'default' check (unattended in ('default', 'on', 'off')),
    updated_by uuid references public.users(id) on delete set null,
    updated_at timestamptz not null default now()
);

-- Favourites of ISM company devices (SaaS devices use remote_saas.favorites).
create table remote_private.device_favorites (
    user_id uuid not null references public.users(id) on delete cascade,
    device_id uuid not null references public.devices(id) on delete cascade,
    created_at timestamptz not null default now(),
    primary key (user_id, device_id)
);

create table remote_private.device_admin_audit (
    id bigint generated always as identity primary key,
    device_id uuid not null,
    client_id uuid not null,
    actor_id uuid references public.users(id) on delete set null,
    action text not null check (action in ('device.rename', 'device.move', 'device.note', 'device.favorite',
        'device.unattended', 'device.remove', 'device.unattended_grant', 'device.unattended_revoke')),
    detail jsonb not null default '{}'::jsonb,
    occurred_at timestamptz not null default now()
);
create index device_admin_audit_device on remote_private.device_admin_audit(device_id, id desc);

-- D8: per-device unattended grants of Remote SaaS tenants.
create table remote_saas.unattended_grants (
    tenant_id uuid not null,
    device_id uuid not null references public.devices(id) on delete cascade,
    user_id uuid not null,
    granted_by uuid references public.users(id) on delete set null,
    granted_at timestamptz not null default now(),
    primary key (device_id, user_id),
    foreign key (tenant_id) references remote_saas.tenants(id) on delete cascade,
    -- Leaving the company drops the person's grants.
    foreign key (tenant_id, user_id) references remote_saas.members(tenant_id, user_id) on delete cascade
);
create index unattended_grants_tenant on remote_saas.unattended_grants(tenant_id);

alter table remote_private.device_presence
    add column if not exists app_version text check (app_version is null or app_version ~ '^[0-9]{1,3}(\.[0-9]{1,4}){1,3}$'),
    add column if not exists platform text check (platform is null or platform ~ '^[a-z0-9-]{1,32}$'),
    add column if not exists version_seen_at timestamptz;

do $$ declare t text; begin
    foreach t in array array['operator_organization','company_policies','company_auto_access','company_policy_audit',
                             'device_admin_settings','device_favorites','device_admin_audit'] loop
        execute format('alter table remote_private.%I enable row level security', t);
        execute format('revoke all on remote_private.%I from public, anon, authenticated', t);
    end loop;
end $$;
alter table remote_saas.unattended_grants enable row level security;
revoke all on remote_saas.unattended_grants from public, anon, authenticated;

---------------------------------------------------------------------------------------------------
-- D8 company setting: validation and audit (also for rows written by migrations).

create function remote_private.company_auto_access_check() returns trigger
language plpgsql security definer set search_path = '' as $$
declare t remote_saas.tenants; c public.clients; u public.users;
begin
    if tg_op = 'DELETE' then
        insert into remote_private.company_policy_audit(client_id, actor, action, detail)
        values (old.client_id, coalesce(nullif(current_setting('mixel.actor', true), ''), current_user),
                'auto_access.revoke', jsonb_build_object('profile', old.profile_id));
        return old;
    end if;
    if tg_op = 'UPDATE' and (new.client_id <> old.client_id or new.profile_id <> old.profile_id) then
        raise exception 'Delete and add the entry instead' using errcode = '22023';
    end if;
    select * into c from public.clients where id = new.client_id;
    select * into u from public.users where id = new.profile_id and is_active and deleted_at is null;
    if u.id is null then
        raise exception 'Automatic access needs an active account' using errcode = '22023';
    end if;
    select * into t from remote_saas.tenants where client_id = new.client_id;
    if t.id is not null then
        -- A SaaS tenant: only an owner of that tenant.
        if not exists (select 1 from remote_saas.members m where m.tenant_id = t.id and m.user_id = u.id
                       and m.role = 'owner' and u.organization_id = t.organization_id) then
            raise exception 'Automatic access is only for an owner of this company' using errcode = '22023';
        end if;
    else
        -- An ISM company: internal staff of the operator, for the operator's own companies only.
        if not (u.organization_id is null and u.role::text = 'admin'
                and exists (select 1 from remote_private.operator_organization op
                            where op.organization_id = c.organization_id)) then
            raise exception 'Automatic access is only for the operator''s own company' using errcode = '22023';
        end if;
    end if;
    insert into remote_private.company_policy_audit(client_id, actor, action, detail)
    values (new.client_id, new.granted_by, 'auto_access.grant',
            jsonb_build_object('profile', new.profile_id, 'reason', new.reason));
    return new;
end;
$$;
create trigger company_auto_access_check before insert or update or delete on remote_private.company_auto_access
    for each row execute function remote_private.company_auto_access_check();

create function remote_private.company_policy_audit_row() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
    insert into remote_private.company_policy_audit(client_id, actor, action, detail)
    values (new.client_id, new.updated_by, 'policy.set', jsonb_build_object('unattended_default', new.unattended_default));
    return new;
end;
$$;
create trigger company_policy_audit after insert or update on remote_private.company_policies
    for each row execute function remote_private.company_policy_audit_row();

---------------------------------------------------------------------------------------------------
-- Policy helpers (policy reader role and the functions below)

-- D1 + per-device setting: (unattended_default, unattended_allowed) of an enrolled device.
create function remote_private.device_unattended_policy(target uuid)
returns table (unattended_default boolean, unattended_allowed boolean)
language sql stable security definer set search_path = '' as $$
    select case coalesce(s.unattended, 'default') when 'on' then true when 'off' then false
                else coalesce(cp.unattended_default, false) end,
           coalesce(s.unattended, 'default') <> 'off'
    from public.devices d
    left join remote_private.device_admin_settings s on s.device_id = d.id
    left join remote_private.company_policies cp on cp.client_id = d.client_id
    where d.id = target;
$$;

-- D8: the controller account is listed for the host's company (and still active there).
create function remote_private.has_auto_access(controller_profile uuid, host_device uuid) returns boolean
language sql stable security definer set search_path = '' as $$
    select exists (
        select 1 from public.devices d
        join remote_private.company_auto_access a on a.client_id = d.client_id and a.profile_id = controller_profile
        join public.users u on u.id = a.profile_id and u.is_active and u.deleted_at is null
        where d.id = host_device
    );
$$;

-- For the admin API (ISM companies): may this controller get unattended access to this host by the company
-- setting, and does the host allow unattended at all. Both bindings are checked by resolve_support_pair first.
create function remote_private.unattended_device_check(controller_profile uuid, host_device uuid)
returns table (unattended_allowed boolean, auto_access boolean)
language sql stable security definer set search_path = '' as $$
    select coalesce((select p.unattended_allowed from remote_private.device_unattended_policy(host_device) p), false),
           remote_private.has_auto_access(controller_profile, host_device);
$$;

-- D1 for the broker: the policy of a connected device, checked against its current binding.
create function remote_private.device_policy_for(connection integer, device uuid, profile uuid, key_generation bigint,
                                                 device_key bytea)
returns table (unattended_default boolean, unattended_allowed boolean)
language sql stable security definer set search_path = '' as $$
    select p.unattended_default, p.unattended_allowed
    from public.remote_device_enrollments e
    join public.users u on u.id = profile and u.is_active and u.deleted_at is null
    cross join lateral remote_private.device_unattended_policy(e.device_id) p
    where e.connection_id = connection and e.device_id = device and e.generation = key_generation
      and e.public_key = device_key and e.revoked_at is null;
$$;

-- The admin API: was this device's enrolment revoked or removed after `since` (a pairing or grant made
-- before that no longer counts)? Also true when the device has no current enrolment.
create function remote_private.device_removed_since(device uuid, since timestamptz) returns boolean
language sql stable security definer set search_path = '' as $$
    select not exists (select 1 from public.remote_device_enrollments e where e.device_id = device and e.revoked_at is null)
        or exists (select 1 from remote_private.enrollment_audit a
                   where a.device_id = device and a.action in ('revoke', 'remove', 'self_remove') and a.occurred_at > since);
$$;

---------------------------------------------------------------------------------------------------
-- D8: Remote SaaS connection policy. As 021, plus: unattended needs a per-device grant or the company
-- setting, and a device whose admin turned unattended off refuses it.
create or replace function remote_private.saas_support_pair(
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
              and (not unattended or (
                  coalesce((select p.unattended_allowed from remote_private.device_unattended_policy(host_device) p), false)
                  and (exists (select 1 from remote_saas.unattended_grants g
                               where g.tenant_id = t.id and g.device_id = host_device and g.user_id = cu.id)
                       or remote_private.has_auto_access(cu.id, host_device))))
        ), false),
        l.max_sessions
    from public.devices hd
    join remote_saas.tenants t on t.client_id = hd.client_id
    join public.clients c on c.id = t.client_id
    join public.organizations o on o.id = t.organization_id and o.kind = 'remote_saas'
    cross join lateral remote_saas.limits(t.id) l
    where hd.id = host_device;
$$;

---------------------------------------------------------------------------------------------------
-- Device administration: who is calling, and what may they do with this device.

create function remote_private.raise_hint(state text, message text, hint text) returns void
language plpgsql volatile set search_path = '' as $$
begin
    raise exception '%', message using errcode = state, hint = hint;
end;
$$;

-- The caller's ISM profile (active, not deleted), or null.
create function remote_private.me() returns public.users
language sql stable security definer set search_path = '' as $$
    select u.* from public.users u
    where auth.uid() is not null and u.auth_user_id = auth.uid() and u.is_active and u.deleted_at is null
    limit 1;
$$;

-- May the caller administer this enrolled device (no visibility check here; see admin_context).
create function remote_private.can_admin_device(target uuid) returns boolean
language sql stable security definer set search_path = '' as $$
    select case when t.id is not null then exists (
            select 1 from remote_saas.members m
            join public.users u on u.id = m.user_id
            where m.tenant_id = t.id and m.role in ('owner', 'admin') and u.auth_user_id = auth.uid()
              and u.is_active and u.deleted_at is null and u.organization_id = t.organization_id)
        else coalesce(auth.jwt()->>'aal', '') = 'aal2' and (
            exists (select 1 from public.users u where u.auth_user_id = auth.uid() and u.is_active
                    and u.deleted_at is null and u.organization_id is null and u.role::text = 'admin')
            or exists (
                select 1 from remote_private.company_administrators ca
                join public.users u on u.id = ca.profile_id
                join public.organizations o on o.id = ca.organization_id and o.is_active
                join auth.identities i on i.user_id = u.auth_user_id and i.provider = 'keycloak'
                    and i.identity_data->>'iss' in ('https://auth.mixel.ch/application/o/mixel-ism-user',
                                                    'https://auth.mixel.ch/application/o/mixel-ism-user/')
                    and i.identity_data->>'sub' = ca.mixel_subject and i.provider_id = ca.mixel_subject
                where u.auth_user_id = auth.uid() and u.is_active and u.deleted_at is null
                  and ca.organization_id = c.organization_id))
        end
    from public.devices d
    join public.clients c on c.id = d.client_id
    left join remote_saas.tenants t on t.client_id = d.client_id
    where d.id = target;
$$;

-- Context for one device action. Raises PT404 device_not_found for a device the caller may not see (or
-- without a current enrolment), 42501 not_admin when `need_admin` and the caller may not administer it,
-- 25006 read_only for writes to a read-only SaaS tenant (`need` = 'write').
create type remote_private.device_admin_ctx as (
    device_id uuid, client_id uuid, tenant_id uuid, actor_id uuid, device_name text, connection_id integer
);
create function remote_private.admin_context(target uuid, need_admin boolean, need text default 'write')
returns remote_private.device_admin_ctx
language plpgsql stable security definer set search_path = '' as $$
declare ctx remote_private.device_admin_ctx; me public.users; t remote_saas.tenants; c public.clients;
begin
    me := remote_private.me();
    if target is null or me.id is null then
        perform remote_private.raise_hint('PT404', 'Device not found', 'device_not_found');
    end if;
    select d.id, d.client_id, null::uuid, me.id, d.name, e.connection_id into ctx
        from public.devices d
        join public.remote_device_enrollments e on e.device_id = d.id and e.revoked_at is null
        where d.id = target;
    if ctx.device_id is null or not remote_private.can_read_device(target) then
        -- MFA-gated visibility: say so instead of "not found" for staff and SaaS tenants requiring MFA.
        if ctx.device_id is not null and coalesce(auth.jwt()->>'aal', '') <> 'aal2'
            and (me.organization_id is null or exists (
                select 1 from remote_saas.tenants tt join remote_saas.auth_settings s on s.tenant_id = tt.id
                where tt.client_id = ctx.client_id and s.require_mfa and tt.organization_id = me.organization_id)) then
            perform remote_private.raise_hint('42501', 'Two-step sign-in is required', 'mfa_required');
        end if;
        perform remote_private.raise_hint('PT404', 'Device not found', 'device_not_found');
    end if;
    select * into t from remote_saas.tenants where client_id = ctx.client_id;
    ctx.tenant_id := t.id;
    if t.id is not null then
        select coalesce(dm.display_name, ctx.device_name) into ctx.device_name
            from remote_saas.device_meta dm where dm.device_id = target;
        ctx.device_name := coalesce(ctx.device_name, (select d.name from public.devices d where d.id = target));
    end if;
    if need_admin and not coalesce(remote_private.can_admin_device(target), false) then
        if t.id is null and coalesce(auth.jwt()->>'aal', '') <> 'aal2' then
            perform remote_private.raise_hint('42501', 'Two-step sign-in is required', 'mfa_required');
        end if;
        perform remote_private.raise_hint('42501', 'Only an administrator can change this device', 'not_admin');
    end if;
    if t.id is not null and need = 'write'
        and remote_saas.effective_status(t) not in ('active', 'trial', 'past_due') then
        perform remote_private.raise_hint('25006', 'This Mixel Remote account is read-only', 'read_only');
    end if;
    return ctx;
end;
$$;

create function remote_private.admin_audit(ctx remote_private.device_admin_ctx, what text, detail jsonb)
returns void language plpgsql security definer set search_path = '' as $$
begin
    insert into remote_private.device_admin_audit(device_id, client_id, actor_id, action, detail)
    values (ctx.device_id, ctx.client_id, ctx.actor_id, what, coalesce(detail, '{}'::jsonb));
    if ctx.tenant_id is not null then
        perform remote_saas.audit(ctx.tenant_id, ctx.actor_id, what, ctx.device_id::text, detail);
    end if;
end;
$$;

---------------------------------------------------------------------------------------------------
-- 2.1 The Devices list

create function remote_private.admin_device_rows(p_only uuid, p_company uuid, p_search text)
returns table (device_id uuid, connection_id integer, name text, os text, company_id uuid, company_name text,
               company_kind text, tenant_id uuid, group_id uuid, group_name text, note text, favorite boolean,
               unattended_setting text, unattended_default boolean, unattended_allowed boolean, status text,
               last_seen timestamptz, online_since timestamptz, app_version text, platform text,
               version_seen_at timestamptz, owner_id uuid, owner_name text, enrolled_at timestamptz,
               updated_at timestamptz, can_admin boolean)
language plpgsql stable security definer set search_path = '' as $$
declare me public.users; pattern text; reported timestamptz;
begin
    me := remote_private.me();
    if me.id is null then return; end if;
    if p_search is not null and char_length(p_search) > 80 then
        perform remote_private.raise_hint('22023', 'Search text too long', 'invalid_search');
    end if;
    pattern := '%' || replace(replace(replace(lower(btrim(coalesce(p_search, ''))), '\', '\\'), '%', '\%'), '_', '\_') || '%';
    select r.reported_at into reported from remote_private.presence_reports r where r.reporter = 'broker';
    return query
        select d.id, e.connection_id, coalesce(dm.display_name, d.name), d.os::text, c.id, c.name,
               case when t.id is null then 'company' else 'saas' end, t.id,
               dm.group_id, coalesce(g.name, s.group_name), s.note,
               case when t.id is null
                    then exists (select 1 from remote_private.device_favorites f where f.user_id = me.id and f.device_id = d.id)
                    else exists (select 1 from remote_saas.favorites f where f.user_id = me.id and f.device_id = d.id) end,
               coalesce(s.unattended, 'default'), pol.unattended_default, pol.unattended_allowed,
               remote_saas.presence_status(p.last_seen, reported), p.last_seen,
               case when remote_saas.presence_status(p.last_seen, reported) = 'online' then p.online_since end,
               p.app_version, p.platform, p.version_seen_at,
               ow.id, ow.name, e.enrolled_at,
               greatest(d.updated_at, s.updated_at),
               coalesce(remote_private.can_admin_device(d.id), false)
        from public.remote_device_enrollments e
        join public.devices d on d.id = e.device_id
        join public.clients c on c.id = d.client_id
        left join remote_saas.tenants t on t.client_id = d.client_id
        left join remote_saas.device_meta dm on dm.device_id = d.id
        left join remote_saas.device_groups g on g.id = dm.group_id
        left join remote_private.device_admin_settings s on s.device_id = d.id
        left join remote_private.device_presence p on p.connection_id = e.connection_id
        left join public.users ow on ow.id = e.enrolled_by
        cross join lateral remote_private.device_unattended_policy(d.id) pol
        where e.revoked_at is null
          and (p_only is null or d.id = p_only)
          and (p_company is null or d.client_id = p_company)
          and (lower(coalesce(dm.display_name, d.name)) like pattern or e.connection_id::text like pattern)
          and remote_private.can_read_device(d.id)
        order by lower(coalesce(dm.display_name, d.name)), d.id
        limit 2000;
end;
$$;

create function public.remote_admin_devices(company uuid default null, search text default null)
returns table (device_id uuid, connection_id integer, name text, os text, company_id uuid, company_name text,
               company_kind text, tenant_id uuid, group_id uuid, group_name text, note text, favorite boolean,
               unattended_setting text, unattended_default boolean, unattended_allowed boolean, status text,
               last_seen timestamptz, online_since timestamptz, app_version text, platform text,
               version_seen_at timestamptz, owner_id uuid, owner_name text, enrolled_at timestamptz,
               updated_at timestamptz, can_admin boolean)
language sql stable security definer set search_path = '' as $$
    select * from remote_private.admin_device_rows(null, company, search);
$$;

create function remote_private.admin_device_json(target uuid) returns jsonb
language sql stable security definer set search_path = '' as $$
    select to_jsonb(r) from remote_private.admin_device_rows(target, null, null) r;
$$;

---------------------------------------------------------------------------------------------------
-- 2.2 Rename (the connection ID, key and enrolment stay)

create function public.remote_admin_rename_device(device uuid, device_name text, expected_update timestamptz default null)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.device_admin_ctx; clean text := btrim(device_name); final text; current_update timestamptz;
        e public.remote_device_enrollments;
begin
    ctx := remote_private.admin_context(device, true);
    if clean is null or char_length(clean) not between 1 and 60 or clean ~ '[[:cntrl:]]' then
        perform remote_private.raise_hint('22023', 'Enter a device name of 1 to 60 characters', 'invalid_name');
    end if;
    perform 1 from public.devices d where d.id = device for update;
    select greatest(d.updated_at, s.updated_at) into current_update
        from public.devices d left join remote_private.device_admin_settings s on s.device_id = d.id where d.id = device;
    if expected_update is not null and current_update is distinct from expected_update then
        perform remote_private.raise_hint('PT409', 'The device was changed. Reload the list.', 'changed');
    end if;
    if ctx.tenant_id is not null then
        final := remote_saas.free_device_name(ctx.client_id, clean, device);
        insert into remote_saas.device_meta(device_id, tenant_id, display_name, added_by, added_via)
        values (device, ctx.tenant_id, final, null, 'staff')
        on conflict (device_id) do update set display_name = excluded.display_name;
    else
        final := clean;
    end if;
    update public.devices d set name = final, updated_at = clock_timestamp() where d.id = device;
    select * into e from public.remote_device_enrollments where device_id = device;
    insert into remote_private.enrollment_audit(device_id, actor_id, action, generation)
    values (device, ctx.actor_id, 'rename', e.generation);
    perform remote_private.admin_audit(ctx, 'device.rename', jsonb_build_object('from', ctx.device_name, 'to', final));
    return remote_private.admin_device_json(device);
end;
$$;

---------------------------------------------------------------------------------------------------
-- 2.3 Move to a group

create function public.remote_admin_move_device(device uuid, group_id uuid default null, group_name text default null)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.device_admin_ctx; before_group text; clean text := nullif(btrim(group_name), '');
begin
    ctx := remote_private.admin_context(device, true);
    if ctx.tenant_id is not null then
        if group_name is not null then
            perform remote_private.raise_hint('22023', 'Choose a group of this company', 'invalid_group');
        end if;
        begin
            perform remote_saas.require_feature(ctx.tenant_id, 'groups', 'Device groups need the Team plan');
        exception when insufficient_privilege then
            perform remote_private.raise_hint('42501', 'Device groups need the Team plan', 'plan_feature');
        end;
        if group_id is not null and not exists (select 1 from remote_saas.device_groups g
                where g.id = group_id and g.tenant_id = ctx.tenant_id) then
            perform remote_private.raise_hint('22023', 'Choose a group of this company', 'invalid_group');
        end if;
        select dm.group_id::text into before_group from remote_saas.device_meta dm where dm.device_id = device for update;
        insert into remote_saas.device_meta(device_id, tenant_id, group_id, added_by, added_via)
        values (device, ctx.tenant_id, group_id, null, 'staff')
        on conflict (device_id) do update set group_id = excluded.group_id;
        perform remote_private.admin_audit(ctx, 'device.move', jsonb_build_object('from', before_group, 'to', group_id));
    else
        if group_id is not null or (clean is not null and (char_length(clean) > 60 or clean ~ '[[:cntrl:]]')) then
            perform remote_private.raise_hint('22023', 'Enter a group name of 1 to 60 characters', 'invalid_group');
        end if;
        select s.group_name into before_group from remote_private.device_admin_settings s where s.device_id = device for update;
        insert into remote_private.device_admin_settings(device_id, group_name, updated_by, updated_at)
        values (device, clean, ctx.actor_id, clock_timestamp())
        on conflict (device_id) do update set group_name = excluded.group_name, updated_by = excluded.updated_by,
            updated_at = excluded.updated_at;
        perform remote_private.admin_audit(ctx, 'device.move', jsonb_build_object('from', before_group, 'to', clean));
    end if;
    insert into remote_private.enrollment_audit(device_id, actor_id, action, generation)
    select device, ctx.actor_id, 'move', e.generation from public.remote_device_enrollments e where e.device_id = device;
    return remote_private.admin_device_json(device);
end;
$$;

---------------------------------------------------------------------------------------------------
-- 2.4 Note, 2.5 favourite, 2.6 unattended setting

create function public.remote_admin_set_device_note(device uuid, note text default null)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.device_admin_ctx; clean text := nullif(btrim(note), '');
begin
    ctx := remote_private.admin_context(device, true);
    if clean is not null and (char_length(clean) > 500 or clean ~ '[\x00-\x08\x0b-\x1f\x7f]') then
        perform remote_private.raise_hint('22023', 'A note has at most 500 characters', 'invalid_note');
    end if;
    insert into remote_private.device_admin_settings(device_id, note, updated_by, updated_at)
    values (device, clean, ctx.actor_id, clock_timestamp())
    on conflict (device_id) do update set note = excluded.note, updated_by = excluded.updated_by,
        updated_at = excluded.updated_at;
    perform remote_private.admin_audit(ctx, 'device.note', jsonb_build_object('length', coalesce(char_length(clean), 0)));
    return remote_private.admin_device_json(device);
end;
$$;

create function public.remote_admin_set_device_favorite(device uuid, favorite boolean)
returns boolean language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.device_admin_ctx;
begin
    ctx := remote_private.admin_context(device, false, 'read');
    if favorite is null then
        perform remote_private.raise_hint('22023', 'Choose favourite or not', 'invalid_setting');
    end if;
    if ctx.tenant_id is not null then
        perform public.remote_saas_set_favorite(ctx.tenant_id, device, favorite);
        perform remote_private.admin_audit(ctx, 'device.favorite', jsonb_build_object('favorite', favorite));
        return favorite;
    end if;
    if not favorite then
        delete from remote_private.device_favorites f where f.user_id = ctx.actor_id and f.device_id = device;
    else
        perform 1 from public.users u where u.id = ctx.actor_id for update;
        if not exists (select 1 from remote_private.device_favorites f where f.user_id = ctx.actor_id and f.device_id = device)
            and (select count(*) from remote_private.device_favorites f where f.user_id = ctx.actor_id) >= 200 then
            perform remote_private.raise_hint('54000', 'At most 200 favourites', 'too_many');
        end if;
        insert into remote_private.device_favorites(user_id, device_id) values (ctx.actor_id, device)
        on conflict do nothing;
    end if;
    perform remote_private.admin_audit(ctx, 'device.favorite', jsonb_build_object('favorite', favorite));
    return favorite;
end;
$$;

create function public.remote_admin_set_device_unattended(device uuid, setting text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.device_admin_ctx; before text;
begin
    ctx := remote_private.admin_context(device, true);
    if setting is null or setting not in ('default', 'on', 'off') then
        perform remote_private.raise_hint('22023', 'Choose default, on or off', 'invalid_setting');
    end if;
    select s.unattended into before from remote_private.device_admin_settings s where s.device_id = device for update;
    insert into remote_private.device_admin_settings(device_id, unattended, updated_by, updated_at)
    values (device, setting, ctx.actor_id, clock_timestamp())
    on conflict (device_id) do update set unattended = excluded.unattended, updated_by = excluded.updated_by,
        updated_at = excluded.updated_at;
    perform remote_private.admin_audit(ctx, 'device.unattended',
        jsonb_build_object('from', coalesce(before, 'default'), 'to', setting));
    return remote_private.admin_device_json(device);
end;
$$;

---------------------------------------------------------------------------------------------------
-- 2.7 Remove

create function public.remote_admin_remove_device(device uuid, confirm_connection_id integer)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.device_admin_ctx; existing public.remote_device_enrollments;
begin
    -- Allowed while a SaaS tenant is read-only (as remote_saas_remove_device).
    ctx := remote_private.admin_context(device, true, 'read');
    if confirm_connection_id is distinct from ctx.connection_id then
        perform remote_private.raise_hint('22023', 'The confirmation does not match this device''s ID', 'confirm_mismatch');
    end if;
    perform remote_private.admin_audit(ctx, 'device.remove',
        jsonb_build_object('name', ctx.device_name, 'connection_id', ctx.connection_id));
    delete from remote_private.device_admin_settings s where s.device_id = device;
    delete from remote_private.device_favorites f where f.device_id = device;
    delete from remote_saas.unattended_grants g where g.device_id = device;
    if ctx.tenant_id is not null then
        perform remote_saas.remove_device(device, ctx.actor_id, 'remove');
    else
        perform 1 from public.devices d where d.id = device for update;
        select * into existing from public.remote_device_enrollments e where e.device_id = device for update;
        update public.remote_device_enrollments set revoked_at = now(), generation = generation + 1 where device_id = device;
        insert into remote_private.enrollment_audit(device_id, actor_id, action, previous_key, generation)
        values (device, ctx.actor_id, 'revoke', existing.public_key, existing.generation + 1);
    end if;
    return jsonb_build_object('removed', true, 'device_id', device, 'connection_id', ctx.connection_id);
end;
$$;

---------------------------------------------------------------------------------------------------
-- 2.8 D8 grants (SaaS tenants)

create function public.remote_admin_unattended_grants(device uuid)
returns table (user_id uuid, name text, role text, source text, granted_by uuid, granted_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
declare ctx remote_private.device_admin_ctx;
begin
    ctx := remote_private.admin_context(device, true, 'read');
    return query
        select g.user_id, u.name, m.role, 'grant'::text, g.granted_by, g.granted_at
        from remote_saas.unattended_grants g
        join public.users u on u.id = g.user_id
        join remote_saas.members m on m.tenant_id = g.tenant_id and m.user_id = g.user_id
        where g.device_id = device and g.tenant_id = ctx.tenant_id
        union all
        select a.profile_id, u.name, coalesce(m.role, 'staff'), 'company_setting'::text, null::uuid, a.granted_at
        from remote_private.company_auto_access a
        join public.users u on u.id = a.profile_id
        left join remote_saas.members m on m.tenant_id = ctx.tenant_id and m.user_id = a.profile_id
        where a.client_id = ctx.client_id
        order by 2;
end;
$$;

create function public.remote_admin_set_unattended_grant(device uuid, "user" uuid, granted boolean)
returns boolean language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.device_admin_ctx; changed integer;
begin
    ctx := remote_private.admin_context(device, true);
    if ctx.tenant_id is null then
        perform remote_private.raise_hint('22023', 'Company devices keep their grants in the admin console', 'not_saas');
    end if;
    if granted is null then
        perform remote_private.raise_hint('22023', 'Choose granted or not', 'invalid_setting');
    end if;
    if granted then
        if not exists (select 1 from remote_saas.members m join public.users u on u.id = m.user_id
                       where m.tenant_id = ctx.tenant_id and m.user_id = "user" and m.role in ('owner', 'admin', 'technician')
                         and u.is_active and u.deleted_at is null)
            or not remote_saas.can_access_device("user", device) then
            perform remote_private.raise_hint('22023', 'This person cannot reach this device', 'invalid_user');
        end if;
        insert into remote_saas.unattended_grants(tenant_id, device_id, user_id, granted_by)
        values (ctx.tenant_id, device, "user", ctx.actor_id) on conflict do nothing;
    else
        delete from remote_saas.unattended_grants g where g.device_id = device and g.user_id = "user";
    end if;
    get diagnostics changed = row_count;
    if changed > 0 then
        perform remote_private.admin_audit(ctx, case when granted then 'device.unattended_grant' else 'device.unattended_revoke' end,
            jsonb_build_object('user', "user"));
    end if;
    return granted;
end;
$$;

---------------------------------------------------------------------------------------------------
-- "Admin only" for the older SaaS device functions (they allowed technicians).

create or replace function public.remote_saas_rename_device(tenant uuid, device uuid, device_name text)
returns text language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; d public.devices; clean text := remote_saas.clean_device_name(device_name);
        final text; e public.remote_device_enrollments;
begin
    me := remote_saas.actor(tenant, '{owner,admin}');
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
    me := remote_saas.actor(tenant, '{owner,admin}');
    perform remote_saas.require_feature(tenant, 'groups', 'Device groups need the Team plan');
    d := remote_saas.tenant_device(tenant, device);
    if to_group is not null then perform remote_saas.group_of(tenant, to_group); end if;
    select dm.group_id into current_group from remote_saas.device_meta dm where dm.device_id = device for update;
    if not remote_saas.can_access_device(me.user_id, device) then
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

create or replace function public.remote_saas_remove_device(tenant uuid, device uuid)
returns boolean language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; d public.devices;
begin
    me := remote_saas.actor(tenant, '{owner,admin}', 'read');
    d := remote_saas.tenant_device(tenant, device);
    if not remote_saas.can_access_device(me.user_id, device) then
        raise exception 'Device unavailable' using errcode = '42501';
    end if;
    delete from remote_private.device_admin_settings s where s.device_id = device;
    perform remote_saas.remove_device(device, me.user_id, 'remove');
    perform remote_saas.audit(tenant, me.user_id, 'device.remove', device::text, jsonb_build_object('name', d.name));
    return true;
end;
$$;

---------------------------------------------------------------------------------------------------
-- 4. App versions reported by the broker (same role and throttling idea as remote_presence_report).
-- Body: [{"connection_id": 123456789, "version": "2.0.19", "platform": "macos-arm64"}, ...]
create function public.remote_presence_versions(versions jsonb)
returns integer language plpgsql security definer set search_path = '' as $$
declare n integer := 0; item jsonb;
begin
    if versions is null or jsonb_typeof(versions) <> 'array' or jsonb_array_length(versions) > 20000 then
        raise exception 'Invalid version report' using errcode = '22023';
    end if;
    for item in select value from jsonb_array_elements(versions) loop
        if jsonb_typeof(item) <> 'object' or (select count(*) from jsonb_object_keys(item)) <> 3
            or jsonb_typeof(item->'connection_id') <> 'number' or jsonb_typeof(item->'version') <> 'string'
            or jsonb_typeof(item->'platform') <> 'string'
            or (item->>'connection_id') !~ '^[1-9][0-9]{8}$'
            or (item->>'version') !~ '^[0-9]{1,3}(\.[0-9]{1,4}){1,3}$'
            or (item->>'platform') !~ '^[a-z0-9-]{1,32}$' then
            raise exception 'Invalid version report' using errcode = '22023';
        end if;
    end loop;
    update remote_private.device_presence p
        set app_version = v.version, platform = v.platform, version_seen_at = now()
        from (select (x->>'connection_id')::integer connection_id, x->>'version' version, x->>'platform' platform
              from jsonb_array_elements(versions) x) v
        where p.connection_id = v.connection_id;
    get diagnostics n = row_count;
    return n;
end;
$$;

---------------------------------------------------------------------------------------------------
-- Grants

do $$ declare f text; begin
    for f in select p.oid::regprocedure::text from pg_proc p
             where p.pronamespace = 'remote_private'::regnamespace
               and p.proname in ('operator_organization', 'company_auto_access_check', 'company_policy_audit_row',
                   'device_unattended_policy', 'has_auto_access', 'unattended_device_check', 'device_policy_for',
                   'device_removed_since', 'raise_hint', 'me', 'can_admin_device', 'admin_context', 'admin_audit',
                   'admin_device_rows', 'admin_device_json') loop
        execute format('revoke all on function %s from public, anon, authenticated', f);
    end loop;
    for f in select p.oid::regprocedure::text from pg_proc p
             where p.pronamespace = 'public'::regnamespace and p.proname like 'remote\_admin\_%' loop
        execute format('revoke all on function %s from public, anon', f);
        execute format('grant execute on function %s to authenticated', f);
    end loop;
end $$;
revoke all on function public.remote_presence_versions(jsonb) from public, anon, authenticated;
grant execute on function public.remote_presence_versions(jsonb) to mixel_remote_presence;
grant execute on function remote_private.unattended_device_check(uuid, uuid),
    remote_private.device_policy_for(integer, uuid, uuid, bigint, bytea),
    remote_private.device_removed_since(uuid, timestamptz) to mixel_remote_policy_reader;

---------------------------------------------------------------------------------------------------
-- Data (D1, D8): the Mixel company. Skipped where these rows do not exist (scratch and test databases).

insert into remote_private.operator_organization(organization_id)
select o.id from public.organizations o
where o.id = '6595a58d-5fd0-4f59-9dfd-75419c6bdc57' and o.mixel_org_id = 'bbbfb7af-7cb5-400f-b715-10fe24c80120';

insert into remote_private.company_policies(client_id, unattended_default, updated_by)
select c.id, true, 'migration:031 owner decision D1 (2026-11-17)'
from public.clients c
join remote_private.operator_organization op on op.organization_id = c.organization_id
where c.id = '59f417d9-9bcb-4f14-bc7c-25b1e88112da';

insert into remote_private.company_auto_access(client_id, profile_id, granted_by, reason)
select c.id, u.id, 'migration:031 owner decision D8 (2026-11-17)',
       'Owner decision D8: the owner''s account has automatic unattended access to all devices of the Mixel company'
from public.clients c
join remote_private.operator_organization op on op.organization_id = c.organization_id
join public.users u on u.id = 'd5b739c4-41e1-4da4-8f2e-04db3485c746' and u.is_active and u.deleted_at is null
where c.id = '59f417d9-9bcb-4f14-bc7c-25b1e88112da';

commit;
notify pgrst, 'reload schema';
