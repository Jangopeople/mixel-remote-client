-- Mixel Remote as SaaS, stage 1: device management for tenants. List, rename, move to a group and
-- remove (revoke + delete); device groups with technician access; "add this computer" for signed-in
-- technicians; deploy links (rollout tokens) with the plan device limit; and device self-removal
-- signed with the device's own enrolled Ed25519 key. Device actions are audited in
-- remote_private.enrollment_audit and the tenant audit log.
begin;

-- Ed25519 signature verification (crypto_sign_verify_detached) for device self-removal.
create extension if not exists pgsodium;

create function remote_saas.clean_device_name(requested text) returns text
language plpgsql immutable set search_path = '' as $$
declare clean text := btrim(requested);
begin
    if clean is null or char_length(clean) not between 1 and 60 or clean ~ '[[:cntrl:]]' then
        raise exception 'Enter a device name of 1 to 60 characters' using errcode = '22023';
    end if;
    return clean;
end;
$$;

-- A free device name within the tenant's client: "Name", "Name (2)", ...
create function remote_saas.free_device_name(client uuid, wanted text, except_device uuid default null) returns text
language plpgsql stable security definer set search_path = '' as $$
declare candidate text := wanted;
begin
    for attempt in 1..50 loop
        exit when not exists (select 1 from public.devices d where d.client_id = client
            and lower(d.name) = lower(candidate) and d.id is distinct from except_device);
        candidate := wanted || ' (' || (attempt + 1) || ')';
    end loop;
    return candidate;
end;
$$;

create function remote_saas.group_of(tenant uuid, target uuid) returns remote_saas.device_groups
language plpgsql stable security definer set search_path = '' as $$
declare g remote_saas.device_groups;
begin
    select * into g from remote_saas.device_groups where id = target and tenant_id = tenant;
    if not found then raise exception 'Group unavailable' using errcode = '42501'; end if;
    return g;
end;
$$;

create function remote_saas.require_feature(tenant uuid, feature text, message text) returns void
language plpgsql stable security definer set search_path = '' as $$
begin
    if not coalesce(((select l.features from remote_saas.limits(tenant) l)->>feature)::boolean, false) then
        raise exception '%', message using errcode = '42501';
    end if;
end;
$$;

-- A technician may use a group they are granted; owners and admins every group.
create function remote_saas.can_use_group(me remote_saas.members, target uuid) returns boolean
language sql stable security definer set search_path = '' as $$
    select target is null or me.role in ('owner', 'admin') or exists (
        select 1 from remote_saas.group_access ga where ga.group_id = target and ga.user_id = me.user_id);
$$;

create function remote_saas.tenant_device(tenant uuid, target uuid) returns public.devices
language plpgsql stable security definer set search_path = '' as $$
declare d public.devices;
begin
    select dv.* into d from public.devices dv join remote_saas.tenants t on t.client_id = dv.client_id
        where dv.id = target and t.id = tenant;
    if not found then raise exception 'Device unavailable' using errcode = '42501'; end if;
    return d;
end;
$$;

create function remote_saas.check_device_capacity(tenant uuid, adding integer default 1) returns void
language plpgsql stable security definer set search_path = '' as $$
declare l record;
begin
    select * into l from remote_saas.limits(tenant);
    if remote_saas.device_count(tenant) + adding > l.max_devices then
        raise exception 'Device limit of your plan reached' using errcode = '54000';
    end if;
end;
$$;

do $$ declare f text; begin
    for f in select p.oid::regprocedure::text from pg_proc p
             where p.pronamespace = 'remote_saas'::regnamespace loop
        execute format('revoke all on function %s from public, anon, authenticated', f);
    end loop;
end $$;

---------------------------------------------------------------------------------------------------
-- Devices

create function public.remote_saas_devices(tenant uuid, in_group uuid default null, search text default null)
returns table (device_id uuid, name text, os text, group_id uuid, group_name text, connection_id integer,
               generation bigint, enrolled_at timestamptz, revoked boolean, added_via text)
language plpgsql stable security definer set search_path = '' as $$
declare me remote_saas.members; t remote_saas.tenants; pattern text;
begin
    me := remote_saas.actor(tenant, '{owner,admin,technician,member}', 'read');
    select * into t from remote_saas.tenants where id = tenant;
    if search is not null and char_length(search) > 80 then
        raise exception 'Search text too long' using errcode = '22023';
    end if;
    pattern := '%' || replace(replace(replace(lower(btrim(coalesce(search, ''))), '\', '\\'), '%', '\%'), '_', '\_') || '%';
    return query
        select d.id, coalesce(dm.display_name, d.name), d.os::text, dm.group_id, g.name, e.connection_id,
               e.generation, e.enrolled_at, e.revoked_at is not null, dm.added_via
        from public.devices d
        left join remote_saas.device_meta dm on dm.device_id = d.id
        left join remote_saas.device_groups g on g.id = dm.group_id
        left join public.remote_device_enrollments e on e.device_id = d.id
        where d.client_id = t.client_id
          and (in_group is null or dm.group_id = in_group)
          and lower(coalesce(dm.display_name, d.name)) like pattern
          and remote_saas.can_access_device(me.user_id, d.id)
        order by lower(coalesce(dm.display_name, d.name)), d.id
        limit 2000;
end;
$$;

create function public.remote_saas_rename_device(tenant uuid, device uuid, device_name text)
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
    values (device, tenant, final, me.user_id, 'staff')
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

-- Move a device to a group (or out of groups with null). Technicians need access to both groups.
create function public.remote_saas_move_device(tenant uuid, device uuid, to_group uuid)
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
    values (device, tenant, to_group, me.user_id, 'staff')
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

-- Remove a device: revoke its enrollment, delete it, and end its sessions (the broker's policy
-- check fails for the missing enrollment). Allowed while the tenant is read-only.
create function public.remote_saas_remove_device(tenant uuid, device uuid)
returns boolean language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; d public.devices;
begin
    me := remote_saas.actor(tenant, '{owner,admin,technician}', 'read');
    d := remote_saas.tenant_device(tenant, device);
    if not remote_saas.can_access_device(me.user_id, device) then
        raise exception 'Device unavailable' using errcode = '42501';
    end if;
    perform remote_saas.remove_device(device, me.user_id, 'remove');
    perform remote_saas.audit(tenant, me.user_id, 'device.remove', device::text, jsonb_build_object('name', d.name));
    return true;
end;
$$;

-- "Add this computer": a signed-in technician registers the computer they are using, with its own
-- device key, in one step. Same result shape as remote_enroll_with_token.
create function public.remote_saas_add_this_device(tenant uuid, device_name text, device_os text,
                                                   key_material bytea, in_group uuid default null)
returns table (device_id uuid, connection_id integer, generation bigint, profile uuid, tenant_org uuid)
language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; t remote_saas.tenants; clean text := remote_saas.clean_device_name(device_name);
        created uuid; enrolled public.remote_device_enrollments;
begin
    me := remote_saas.actor(tenant, '{owner,admin,technician}');
    if device_os is null or device_os not in ('macos', 'windows', 'linux') or octet_length(key_material) is distinct from 32 then
        raise exception 'Invalid Remote device' using errcode = '22023';
    end if;
    select * into t from remote_saas.tenants where id = tenant for update;
    if in_group is not null then
        perform remote_saas.require_feature(tenant, 'groups', 'Device groups need the Team plan');
        perform remote_saas.group_of(tenant, in_group);
        if not remote_saas.can_use_group(me, in_group) then
            raise exception 'Not allowed for this group' using errcode = '42501';
        end if;
    end if;
    -- The same computer again (installer or app re-run) gets its registration back.
    select e.* into enrolled from public.remote_device_enrollments e
        join public.devices d on d.id = e.device_id and d.client_id = t.client_id
        where e.public_key = key_material and e.revoked_at is null;
    if found then
        if not remote_saas.can_access_device(me.user_id, enrolled.device_id) then
            raise exception 'Device unavailable' using errcode = '42501';
        end if;
        return query select enrolled.device_id, enrolled.connection_id, enrolled.generation, enrolled.enrolled_by, t.organization_id;
        return;
    end if;
    perform remote_saas.check_device_capacity(tenant);
    insert into public.devices(client_id, name, type, os)
    values (t.client_id, remote_saas.free_device_name(t.client_id, clean), 'desktop', device_os::public.ism_device_os)
    returning id into created;
    insert into remote_saas.device_meta(device_id, tenant_id, group_id, added_by, added_via)
    values (created, tenant, in_group, me.user_id, 'this_device');
    enrolled := remote_saas.enroll_key(created, key_material, me.user_id);
    perform remote_saas.audit(tenant, me.user_id, 'device.add', created::text,
        jsonb_build_object('via', 'this_device', 'connection_id', enrolled.connection_id));
    return query select enrolled.device_id, enrolled.connection_id, enrolled.generation, enrolled.enrolled_by, t.organization_id;
end;
$$;

---------------------------------------------------------------------------------------------------
-- Device groups and technician access (Team plan and above)

create function public.remote_saas_groups(tenant uuid)
returns table (group_id uuid, name text, devices integer, technicians uuid[])
language plpgsql stable security definer set search_path = '' as $$
declare me remote_saas.members;
begin
    me := remote_saas.actor(tenant, '{owner,admin,technician}', 'read');
    return query select g.id, g.name,
            (select count(*)::integer from remote_saas.device_meta dm where dm.group_id = g.id),
            coalesce((select array_agg(ga.user_id order by ga.user_id) from remote_saas.group_access ga where ga.group_id = g.id), '{}')
        from remote_saas.device_groups g
        where g.tenant_id = tenant and remote_saas.can_use_group(me, g.id)
        order by lower(g.name), g.id;
end;
$$;

create function public.remote_saas_create_group(tenant uuid, group_name text)
returns uuid language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; created uuid; clean text := btrim(group_name);
begin
    me := remote_saas.actor(tenant, '{owner,admin}');
    perform remote_saas.require_feature(tenant, 'groups', 'Device groups need the Team plan');
    if clean is null or char_length(clean) not between 1 and 60 or clean ~ '[[:cntrl:]]' then
        raise exception 'Enter a group name of 1 to 60 characters' using errcode = '22023';
    end if;
    if (select count(*) from remote_saas.device_groups g where g.tenant_id = tenant) >= 500 then
        raise exception 'Too many groups' using errcode = '54000';
    end if;
    begin
        insert into remote_saas.device_groups(tenant_id, name) values (tenant, clean) returning id into created;
    exception when unique_violation then
        raise exception 'A group with this name exists' using errcode = '23505';
    end;
    perform remote_saas.audit(tenant, me.user_id, 'group.create', created::text, jsonb_build_object('name', clean));
    return created;
end;
$$;

create function public.remote_saas_rename_group(tenant uuid, target uuid, group_name text)
returns boolean language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; clean text := btrim(group_name);
begin
    me := remote_saas.actor(tenant, '{owner,admin}');
    perform remote_saas.group_of(tenant, target);
    if clean is null or char_length(clean) not between 1 and 60 or clean ~ '[[:cntrl:]]' then
        raise exception 'Enter a group name of 1 to 60 characters' using errcode = '22023';
    end if;
    begin
        update remote_saas.device_groups set name = clean where id = target and tenant_id = tenant;
    exception when unique_violation then
        raise exception 'A group with this name exists' using errcode = '23505';
    end;
    perform remote_saas.audit(tenant, me.user_id, 'group.rename', target::text, jsonb_build_object('name', clean));
    return true;
end;
$$;

-- Deleting a group keeps its devices (they become ungrouped).
create function public.remote_saas_delete_group(tenant uuid, target uuid)
returns boolean language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; g remote_saas.device_groups;
begin
    me := remote_saas.actor(tenant, '{owner,admin}', 'read');
    g := remote_saas.group_of(tenant, target);
    delete from remote_saas.device_groups where id = target and tenant_id = tenant;
    perform remote_saas.audit(tenant, me.user_id, 'group.delete', target::text, jsonb_build_object('name', g.name));
    return true;
end;
$$;

-- Replace the technicians of a group. Only members with the technician role can be granted
-- (owners and admins see every group anyway).
create function public.remote_saas_set_group_access(tenant uuid, target uuid, technicians uuid[])
returns integer language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; granted integer;
begin
    me := remote_saas.actor(tenant, '{owner,admin}');
    perform remote_saas.require_feature(tenant, 'access_rights', 'Access rights need the Team plan');
    perform remote_saas.group_of(tenant, target);
    if technicians is null or cardinality(technicians) > 500 or array_position(technicians, null) is not null then
        raise exception 'Invalid technician list' using errcode = '22023';
    end if;
    if exists (select 1 from unnest(technicians) x where not exists (
            select 1 from remote_saas.members m where m.tenant_id = tenant and m.user_id = x and m.role = 'technician')) then
        raise exception 'Only technicians of this company can be granted a group' using errcode = '22023';
    end if;
    delete from remote_saas.group_access where group_id = target and not (user_id = any(technicians));
    insert into remote_saas.group_access(group_id, tenant_id, user_id, granted_by)
    select target, tenant, x, me.user_id from (select distinct unnest(technicians) x) s
    on conflict (group_id, user_id) do nothing;
    select count(*) into granted from remote_saas.group_access where group_id = target;
    perform remote_saas.audit(tenant, me.user_id, 'group.access', target::text,
        jsonb_build_object('technicians', to_jsonb(technicians)));
    return granted;
end;
$$;

---------------------------------------------------------------------------------------------------
-- Deploy links (mass rollout): tenant-scoped rollout tokens

create function public.remote_saas_create_deploy_link(tenant uuid, link_label text, valid_days integer,
                                                      computers integer, in_group uuid default null)
returns text language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; t remote_saas.tenants; l record; secret text;
begin
    me := remote_saas.actor(tenant, '{owner,admin}');
    select * into t from remote_saas.tenants where id = tenant;
    select * into l from remote_saas.limits(tenant);
    if valid_days is null or valid_days not between 1 and 365 or computers is null or computers not between 1 and 10000
        or link_label is null or char_length(btrim(link_label)) not between 1 and 80 or link_label ~ '[[:cntrl:]]' then
        raise exception 'Invalid deploy link' using errcode = '22023';
    end if;
    if computers > greatest(l.max_devices - remote_saas.device_count(tenant), 0) then
        raise exception 'More computers than your plan has room for' using errcode = '54000';
    end if;
    if in_group is not null then
        perform remote_saas.require_feature(tenant, 'groups', 'Device groups need the Team plan');
        perform remote_saas.group_of(tenant, in_group);
    end if;
    if (select count(*) from remote_private.rollout_tokens r where r.client_id = t.client_id
            and r.created_at > now() - interval '1 day') >= 50 then
        raise exception 'Too many deploy links today' using errcode = '54000';
    end if;
    secret := 'mrt_' || translate(pg_catalog.encode(public.gen_random_bytes(32), 'base64'), '+/=', '-_');
    insert into remote_private.rollout_tokens(token_hash, client_id, created_by, label, expires_at, max_uses, saas_group_id)
    values (pg_catalog.sha256(convert_to(secret, 'UTF8')), t.client_id, me.user_id, btrim(link_label),
            now() + make_interval(days => valid_days), computers, in_group);
    perform remote_saas.audit(tenant, me.user_id, 'deploy_link.create', btrim(link_label),
        jsonb_build_object('days', valid_days, 'computers', computers, 'group', in_group));
    return secret;
end;
$$;

create function public.remote_saas_deploy_links(tenant uuid)
returns table (id uuid, label text, group_id uuid, created_at timestamptz, expires_at timestamptz,
               max_uses integer, uses integer, revoked_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
declare t remote_saas.tenants;
begin
    perform remote_saas.actor(tenant, '{owner,admin}', 'read');
    select * into t from remote_saas.tenants where remote_saas.tenants.id = tenant;
    return query select r.id, r.label, r.saas_group_id, r.created_at, r.expires_at, r.max_uses, r.uses, r.revoked_at
        from remote_private.rollout_tokens r where r.client_id = t.client_id order by r.created_at desc limit 100;
end;
$$;

create function public.remote_saas_revoke_deploy_link(tenant uuid, link uuid)
returns boolean language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; t remote_saas.tenants;
begin
    me := remote_saas.actor(tenant, '{owner,admin}', 'read');
    select * into t from remote_saas.tenants where id = tenant;
    update remote_private.rollout_tokens set revoked_at = now()
        where id = link and client_id = t.client_id and revoked_at is null;
    if found then perform remote_saas.audit(tenant, me.user_id, 'deploy_link.revoke', link::text); end if;
    return found;
end;
$$;

-- Computer (anonymous, holds the token): add itself to the token's company and enrol its key.
-- Unchanged for linked ISM companies; SaaS tenants are accepted with their plan device limit,
-- status and the creator's continued owner/admin role.
create or replace function public.remote_enroll_with_token(token text, device_name text, device_os text, key_material bytea)
returns table(device_id uuid, connection_id integer, generation bigint, profile uuid, tenant uuid)
language plpgsql security definer set search_path = '' as $$
declare grant_row remote_private.rollout_tokens; clean_name text := btrim(device_name); name_try text;
        created uuid; enrolled public.remote_device_enrollments; org uuid; candidate bigint; saas remote_saas.tenants;
begin
    if token is null or char_length(token) not between 20 and 100 or octet_length(key_material) is distinct from 32
        or clean_name is null or char_length(clean_name) not between 1 and 60 or clean_name ~ '[[:cntrl:]]'
        or device_os not in ('windows', 'macos', 'linux') then
        raise exception 'Invalid rollout request' using errcode = '22023';
    end if;
    select * into grant_row from remote_private.rollout_tokens t
        where t.token_hash = pg_catalog.sha256(convert_to(token, 'UTF8')) for update;
    if not found or grant_row.revoked_at is not null or grant_row.expires_at <= now() then
        raise exception 'Rollout token invalid, expired or used up' using errcode = '42501';
    end if;
    select * into saas from remote_saas.tenants st where st.client_id = grant_row.client_id;
    select o.id into org from public.clients c
        join public.organizations o on o.id = c.organization_id and o.is_active
            and (o.mixel_org_id is not null or (saas.id is not null and o.id = saas.organization_id and o.kind = 'remote_saas'))
        where c.id = grant_row.client_id and c.is_active;
    if org is null then raise exception 'Company unavailable for Remote devices' using errcode = '42501'; end if;
    perform 1 from public.users u where u.id = grant_row.created_by and u.is_active and u.deleted_at is null;
    if not found then raise exception 'Rollout token owner is no longer active' using errcode = '42501'; end if;
    if saas.id is not null then
        if not exists (select 1 from remote_saas.members m join public.users u on u.id = m.user_id
                       where m.tenant_id = saas.id and m.user_id = grant_row.created_by
                         and m.role in ('owner', 'admin') and u.organization_id = saas.organization_id) then
            raise exception 'Rollout token owner is no longer active' using errcode = '42501';
        end if;
        if remote_saas.effective_status(saas) not in ('active', 'trial', 'past_due') then
            raise exception 'This Mixel Remote account is read-only' using errcode = '25006';
        end if;
    end if;
    -- A key that is already enrolled returns its record (re-running the installer is harmless).
    select e.* into enrolled from public.remote_device_enrollments e
        join public.devices d on d.id = e.device_id and d.client_id = grant_row.client_id
        where e.public_key = key_material and e.revoked_at is null;
    if found then
        return query select enrolled.device_id, enrolled.connection_id, enrolled.generation, enrolled.enrolled_by, org;
        return;
    end if;
    if grant_row.uses >= grant_row.max_uses then
        raise exception 'Rollout token invalid, expired or used up' using errcode = '42501';
    end if;
    if saas.id is not null then
        perform 1 from remote_saas.tenants st where st.id = saas.id for update;
        perform remote_saas.check_device_capacity(saas.id);
    end if;
    name_try := clean_name;
    for attempt in 1..20 loop
        exit when not exists (select 1 from public.devices d where d.client_id = grant_row.client_id and lower(d.name) = lower(name_try));
        name_try := clean_name || ' (' || (attempt + 1) || ')';
    end loop;
    insert into public.devices(client_id, name, type, os)
        values (grant_row.client_id, name_try, 'desktop', device_os::public.ism_device_os) returning id into created;
    if saas.id is not null then
        insert into remote_saas.device_meta(device_id, tenant_id, group_id, added_by, added_via)
        values (created, saas.id, grant_row.saas_group_id, grant_row.created_by, 'deploy_link');
        enrolled := remote_saas.enroll_key(created, key_material, grant_row.created_by);
        update remote_private.rollout_tokens set uses = uses + 1 where id = grant_row.id;
        perform remote_saas.audit(saas.id, grant_row.created_by, 'device.add', created::text,
            jsonb_build_object('via', 'deploy_link', 'link', grant_row.id, 'connection_id', enrolled.connection_id));
        return query select enrolled.device_id, enrolled.connection_id, enrolled.generation, enrolled.enrolled_by, org;
        return;
    end if;
    for attempt in 1..128 loop
        candidate := (('x' || pg_catalog.encode(pg_catalog.substring(pg_catalog.uuid_send(pg_catalog.gen_random_uuid()),1,4),'hex'))::bit(32)::bigint);
        if candidate >= 3600000000 then continue; end if;
        candidate := 100000000 + candidate % 900000000;
        begin
            insert into public.remote_device_enrollments(device_id, connection_id, public_key, enrolled_by)
                values (created, candidate, key_material, grant_row.created_by) returning * into enrolled;
        exception when unique_violation then
            continue;
        end;
        insert into remote_private.enrollment_audit(device_id, actor_id, action, next_key, generation)
            values (created, grant_row.created_by, 'enroll', key_material, enrolled.generation);
        update remote_private.rollout_tokens set uses = uses + 1 where id = grant_row.id;
        return query select enrolled.device_id, enrolled.connection_id, enrolled.generation, enrolled.enrolled_by, org;
        return;
    end loop;
    raise exception 'Connection identifier allocation exhausted' using errcode = '54000';
end;
$$;

---------------------------------------------------------------------------------------------------
-- Device self-removal. The device signs, with its enrolled Ed25519 key, the exact text
--   mixel-remote/device-self-remove/v1\n<device uuid>\n<connection id>\n<generation>\n<unix seconds>
-- The request needs no account and can be created offline and sent later (up to 30 days). It only
-- removes the enrollment generation it names, so a replay after removal, rotation or a new
-- enrollment changes nothing. The key is looked up in the connection reservation, which outlives
-- removal, so a retry of an already-removed device still verifies and returns true.

create function public.remote_device_self_remove_message(device uuid, connection integer, key_generation bigint, issued_at bigint)
returns bytea language sql immutable set search_path = '' as $$
    select convert_to('mixel-remote/device-self-remove/v1' || chr(10) || device::text || chr(10)
        || connection::text || chr(10) || key_generation::text || chr(10) || issued_at::text, 'UTF8');
$$;

create function public.remote_device_self_remove(device uuid, connection integer, key_generation bigint,
                                                 issued_at bigint, signature bytea)
returns boolean language plpgsql security definer set search_path = '' as $$
declare reserved remote_private.connection_reservations; current_row public.remote_device_enrollments;
        personal remote_private.personal_devices; saas_tenant uuid; now_unix bigint := extract(epoch from now())::bigint;
begin
    if device is null or connection is null or connection not between 100000000 and 999999999
        or key_generation is null or key_generation < 1 or issued_at is null
        or issued_at < now_unix - 30 * 86400 or issued_at > now_unix + 300
        or octet_length(signature) is distinct from 64 then
        raise exception 'Invalid removal request' using errcode = '22023';
    end if;
    select * into reserved from remote_private.connection_reservations r
        where r.connection_id = connection and r.device_id = device;
    if not found or not pgsodium.crypto_sign_verify_detached(signature,
            public.remote_device_self_remove_message(device, connection, key_generation, issued_at), reserved.public_key) then
        raise exception 'Invalid removal request' using errcode = '42501';
    end if;
    -- Personal Remote device.
    select * into personal from remote_private.personal_devices p where p.device_id = device and p.connection_id = connection for update;
    if found then
        if personal.public_key <> reserved.public_key then return false; end if;
        if personal.revoked_at is not null then return true; end if;
        if personal.generation <> key_generation then return false; end if;
        update remote_private.personal_devices set revoked_at = now(), generation = generation + 1 where device_id = device;
        return true;
    end if;
    perform 1 from public.devices d where d.id = device for update;
    select * into current_row from public.remote_device_enrollments e where e.device_id = device for update;
    if not found then return true; end if;  -- already removed
    if current_row.connection_id <> connection or current_row.public_key <> reserved.public_key then return false; end if;
    if current_row.generation <> key_generation then
        -- Already revoked (generation moved on): done. A newer active enrollment is never touched.
        return current_row.revoked_at is not null;
    end if;
    select t.id into saas_tenant from remote_saas.tenants t join public.devices d on d.client_id = t.client_id where d.id = device;
    if saas_tenant is not null then
        perform remote_saas.remove_device(device, current_row.enrolled_by, 'self_remove');
        perform remote_saas.audit(saas_tenant, null, 'device.self_remove', device::text,
            jsonb_build_object('connection_id', connection));
        return true;
    end if;
    -- Other companies: the device revokes its own enrollment; the ISM device record stays.
    if current_row.revoked_at is null then
        update public.remote_device_enrollments set revoked_at = now(), generation = generation + 1 where device_id = device;
        insert into remote_private.enrollment_audit(device_id, actor_id, action, previous_key, generation)
        values (device, current_row.enrolled_by, 'self_remove', current_row.public_key, current_row.generation + 1);
    end if;
    return true;
end;
$$;

do $$ declare f text; begin
    for f in select p.oid::regprocedure::text from pg_proc p
             where p.pronamespace = 'public'::regnamespace and p.proname like 'remote\_saas\_%'
               and p.oid::regprocedure::text not like 'remote_saas_sign_in_options%' loop
        execute format('revoke all on function %s from public, anon, authenticated', f);
        execute format('grant execute on function %s to authenticated', f);
    end loop;
end $$;
revoke all on function public.remote_enroll_with_token(text,text,text,bytea) from public;
grant execute on function public.remote_enroll_with_token(text,text,text,bytea) to anon, authenticated;
revoke all on function public.remote_device_self_remove(uuid,integer,bigint,bigint,bytea),
    public.remote_device_self_remove_message(uuid,integer,bigint,bigint) from public;
grant execute on function public.remote_device_self_remove(uuid,integer,bigint,bigint,bytea),
    public.remote_device_self_remove_message(uuid,integer,bigint,bigint) to anon, authenticated;

commit;
notify pgrst, 'reload schema';
