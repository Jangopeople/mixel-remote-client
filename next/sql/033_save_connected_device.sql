-- Allow any authenticated technician (Mixel staff, SaaS tenant member, or ISM org user) to save
-- a connected device after a remote session with its Device Name, Company, and User, and protect
-- saved Quick Support devices from the 8-hour sweep while keeping them online indefinitely.
begin;
set local lock_timeout = '5s';

alter table remote_private.quick_phones
    add column if not exists saved_at timestamptz,
    add column if not exists user_name text;

-- Ends unsaved claims older than 8 hours and forgets phones not seen for 90 days.
-- Saved Quick Support devices (q.saved_at is not null) are never revoked by the 8-hour sweep.
create or replace function remote_private.sweep_quick_phones() returns void
language plpgsql security definer set search_path = '' as $$
declare stale record;
begin
    for stale in
        select e.device_id, e.public_key, e.generation, q.claimed_by
        from remote_private.quick_phones q
        join public.remote_device_enrollments e on e.device_id = q.device_id and e.revoked_at is null
        where q.claimed_at < now() - interval '8 hours'
          and q.saved_at is null
        for update of e
    loop
        update public.remote_device_enrollments set revoked_at = now(), generation = generation + 1 where device_id = stale.device_id;
        insert into remote_private.enrollment_audit(device_id, actor_id, action, previous_key, generation)
            select stale.device_id, coalesce(stale.claimed_by, e.enrolled_by), 'revoke', stale.public_key, stale.generation + 1
            from public.remote_device_enrollments e where e.device_id = stale.device_id;
    end loop;
    update remote_private.quick_phones set claimed_by = null, claimed_at = null where claimed_at < now() - interval '8 hours' and saved_at is null;
    delete from remote_private.quick_phones where last_seen < now() - interval '90 days' and device_id is null;
end;
$$;
revoke all on function remote_private.sweep_quick_phones() from public, anon, authenticated;

-- Saved Quick Support devices stay online even after 8 hours.
create or replace function public.remote_quick_phone_register(device_name text, key_material bytea)
returns table(connection_id integer, device_id uuid, generation bigint, profile uuid, tenant uuid)
language plpgsql security definer set search_path = '' as $$
declare
    clean_name text := btrim(device_name);
    row_found remote_private.quick_phones;
    candidate bigint;
    org uuid;
    enrolled public.remote_device_enrollments;
    existing_conn integer;
begin
    if octet_length(key_material) is distinct from 32 or clean_name is null
        or char_length(clean_name) not between 1 and 60 or clean_name ~ '[[:cntrl:]]' then
        raise exception 'Invalid request' using errcode = '22023';
    end if;
    select * into row_found from remote_private.quick_phones q where q.public_key = key_material for update;
    if not found then
        if (select count(*) from remote_private.quick_phones q where q.created_at > now() - interval '10 minutes') >= 120 then
            raise exception 'Too many requests' using errcode = '54000';
        end if;
        perform remote_private.sweep_quick_phones();
        select r.connection_id into existing_conn
        from remote_private.connection_reservations r
        where r.public_key = key_material
          and not exists (select 1 from remote_private.quick_phones q where q.connection_id = r.connection_id);
        if existing_conn is not null then
            insert into remote_private.quick_phones(public_key, connection_id, device_name)
                values (key_material, existing_conn, clean_name) returning * into row_found;
        else
            for attempt in 1..128 loop
                candidate := (('x' || pg_catalog.encode(pg_catalog.substring(pg_catalog.uuid_send(pg_catalog.gen_random_uuid()),1,4),'hex'))::bit(32)::bigint);
                if candidate >= 3600000000 then continue; end if;
                candidate := 100000000 + candidate % 900000000;
                continue when exists (select 1 from public.remote_device_enrollments e where e.connection_id = candidate)
                    or exists (select 1 from remote_private.connection_reservations r where r.connection_id = candidate)
                    or exists (select 1 from remote_private.quick_phones q where q.connection_id = candidate);
                insert into remote_private.quick_phones(public_key, connection_id, device_name)
                    values (key_material, candidate, clean_name) returning * into row_found;
                exit;
            end loop;
        end if;
        if row_found.public_key is null then raise exception 'Connection identifier allocation exhausted' using errcode = '54000'; end if;
    else
        update remote_private.quick_phones q
            set last_seen = now(),
                device_name = case when q.saved_at is not null then q.device_name else clean_name end
            where q.public_key = key_material;
    end if;
    select e.* into enrolled from public.remote_device_enrollments e
        where e.device_id = row_found.device_id and e.public_key = key_material and e.revoked_at is null;
    if row_found.claimed_by is not null and enrolled.device_id is not null and (row_found.saved_at is not null or row_found.claimed_at > now() - interval '8 hours') then
        select o.id into org from public.devices d join public.clients c on c.id = d.client_id
            join public.organizations o on o.id = c.organization_id where d.id = enrolled.device_id;
        return query select row_found.connection_id, enrolled.device_id, enrolled.generation, row_found.claimed_by, org;
    else
        return query select row_found.connection_id, null::uuid, null::bigint, null::uuid, null::uuid;
    end if;
end;
$$;

-- Allow SaaS support sessions for any active owner/admin/technician, and for any user who claimed
-- or saved a Quick Support device in an attended session.
create or replace function remote_private.saas_support_pair(
    controller_profile uuid, controller_device uuid, controller_generation bigint, controller_key bytea,
    host_profile uuid, host_device uuid, host_generation bigint, host_key bytea, unattended boolean
) returns table(tenant uuid, organization uuid, allowed boolean, max_sessions integer)
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
            where he.device_id = host_device and he.generation = host_generation and he.public_key = host_key
              and he.revoked_at is null and controller_device <> host_device
              and o.is_active and c.is_active
              and remote_saas.effective_status(t) in ('active', 'trial', 'past_due')
              and remote_saas.can_access_device(cu.id, host_device)
              and (
                  cm.role in ('owner', 'admin', 'technician')
                  or (not unattended and exists (
                      select 1 from remote_private.quick_phones q
                      where q.device_id = host_device and q.claimed_by = cu.id and (q.saved_at is not null or q.claimed_at > now() - interval '8 hours')
                  ))
              )
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

-- Save a connected device for next time with Device Name, Company, and User.
create or replace function public.remote_save_connected_device(
    target_connection_id integer,
    device_name text,
    company_name text,
    user_name text,
    target_client uuid default null
) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
    me public.users;
    clean_name text := btrim(device_name);
    clean_company text := btrim(company_name);
    clean_user text := btrim(user_name);
    enrolled public.remote_device_enrollments;
    current_device public.devices;
    t remote_saas.tenants;
    grp_id uuid;
    final_name text;
    combined_note text;
    chosen_client uuid;
begin
    me := remote_private.me();
    if me.id is null then
        raise exception 'Sign in with an active Mixel Remote account first' using errcode = '42501';
    end if;
    if target_connection_id is null or target_connection_id not between 100000000 and 999999999 then
        raise exception 'Invalid connection ID' using errcode = '22023';
    end if;
    if clean_name is null or char_length(clean_name) not between 1 and 60 or clean_name ~ '[[:cntrl:]]' then
        raise exception 'Enter a device name of 1 to 60 characters' using errcode = '22023';
    end if;
    if clean_company is null or char_length(clean_company) not between 1 and 60 or clean_company ~ '[[:cntrl:]]' then
        raise exception 'Enter a company name of 1 to 60 characters' using errcode = '22023';
    end if;
    if clean_user is null or char_length(clean_user) not between 1 and 80 or clean_user ~ '[[:cntrl:]]' then
        raise exception 'Enter a user name of 1 to 80 characters' using errcode = '22023';
    end if;

    -- Ensure a Quick Support device with this connection_id is claimed if not yet enrolled.
    if not exists (select 1 from public.remote_device_enrollments e where e.connection_id = target_connection_id and e.revoked_at is null) then
        perform public.remote_quick_phone_claim(target_connection_id);
    end if;

    select e.* into enrolled
    from public.remote_device_enrollments e
    where e.connection_id = target_connection_id and e.revoked_at is null
    for update;

    if not found then
        raise exception 'Device not found' using errcode = 'PT404';
    end if;

    select d.* into current_device from public.devices d where d.id = enrolled.device_id for update;
    if not found or not remote_private.can_read_device(current_device.id) then
        raise exception 'Device not found' using errcode = 'PT404';
    end if;

    select * into t from remote_saas.tenants where client_id = current_device.client_id;

    -- Resolve target ISM client if caller is Mixel staff.
    chosen_client := current_device.client_id;
    if t.id is null and me.organization_id is null and me.role::text in ('admin', 'agent') then
        if target_client is not null then
            if exists (
                select 1 from public.clients c
                join public.organizations o on o.id = c.organization_id
                where c.id = target_client and c.is_active and o.is_active and o.mixel_org_id is not null
                  and not remote_private.saas_client(c.id)
            ) then
                chosen_client := target_client;
            end if;
        else
            select c.id into chosen_client
            from public.clients c
            join public.organizations o on o.id = c.organization_id
            where lower(btrim(c.name)) = lower(clean_company)
              and c.is_active and o.is_active and o.mixel_org_id is not null
              and not remote_private.saas_client(c.id)
            limit 1;
            chosen_client := coalesce(chosen_client, current_device.client_id);
        end if;
    end if;

    if t.id is not null then
        final_name := remote_saas.free_device_name(chosen_client, clean_name, current_device.id);
        -- Match or create a device group for clean_company when possible.
        select g.id into grp_id
        from remote_saas.device_groups g
        where g.tenant_id = t.id and lower(g.name) = lower(clean_company)
        limit 1;
        if grp_id is null and (select count(*) from remote_saas.device_groups g where g.tenant_id = t.id) < 500 then
            begin
                insert into remote_saas.device_groups(tenant_id, name) values (t.id, clean_company) returning id into grp_id;
            exception when unique_violation then
                select g.id into grp_id from remote_saas.device_groups g where g.tenant_id = t.id and lower(g.name) = lower(clean_company) limit 1;
            end;
        end if;
        insert into remote_saas.device_meta(device_id, tenant_id, display_name, group_id, added_by, added_via)
        values (current_device.id, t.id, final_name, grp_id, me.id, 'staff')
        on conflict (device_id) do update
            set display_name = excluded.display_name,
                group_id = coalesce(excluded.group_id, remote_saas.device_meta.group_id);
    else
        final_name := clean_name;
        for attempt in 1..50 loop
            exit when not exists (
                select 1 from public.devices d
                where d.client_id = chosen_client and d.id <> current_device.id and lower(d.name) = lower(final_name)
            );
            final_name := clean_name || ' (' || (attempt + 1) || ')';
        end loop;
    end if;

    update public.devices d
        set name = final_name,
            client_id = chosen_client,
            updated_at = clock_timestamp()
        where d.id = current_device.id;

    combined_note := left('Company: ' || clean_company || ' · User: ' || clean_user, 500);

    insert into remote_private.device_admin_settings(device_id, group_name, note, updated_by, updated_at)
    values (
        current_device.id,
        case when t.id is null then clean_company else null end,
        combined_note,
        me.id,
        clock_timestamp()
    )
    on conflict (device_id) do update
        set group_name = case when t.id is null then excluded.group_name else remote_private.device_admin_settings.group_name end,
            note = excluded.note,
            updated_by = excluded.updated_by,
            updated_at = excluded.updated_at;

    update remote_private.quick_phones q
        set device_name = final_name,
            user_name = clean_user,
            saved_at = now(),
            claimed_by = coalesce(q.claimed_by, me.id),
            claimed_at = now()
        where q.connection_id = target_connection_id;

    return jsonb_build_object(
        'saved', true,
        'device_id', current_device.id,
        'connection_id', target_connection_id,
        'name', final_name,
        'company', clean_company,
        'user', clean_user
    );
end;
$$;

revoke all on function public.remote_save_connected_device(integer, text, text, text, uuid) from public, anon;
grant execute on function public.remote_save_connected_device(integer, text, text, text, uuid) to authenticated;

commit;
notify pgrst, 'reload schema';
