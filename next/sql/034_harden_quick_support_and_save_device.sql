-- 034_harden_quick_support_and_save_device.sql
-- Hardens public.remote_quick_phone_claim and public.remote_save_connected_device:
-- 1. Prevents cross-tenant hijacking or un-revocation of already saved/owned Quick Support devices.
-- 2. Blocks suspended/closed SaaS tenants from falling back to Mixel's shared Quick Support client.
-- 3. Requires admin/technician privileges and active tenant status in remote_save_connected_device.
-- 4. Respects remote_saas.require_feature(tenant, 'groups') before creating SaaS device groups.
-- 5. Bounds disambiguated device names to <= 60 characters and records an audit entry.

begin;

create or replace function public.remote_quick_phone_claim(connection integer)
returns boolean
language plpgsql security definer set search_path = '' as $$
declare
    actor uuid;
    actor_is_staff boolean := false;
    has_saas_membership boolean := false;
    phone remote_private.quick_phones;
    client uuid;
    created uuid;
    existing_client uuid;
    name_try text;
    enrolled public.remote_device_enrollments;
    existing public.remote_device_enrollments;
begin
    select u.id,
           (u.organization_id is null and u.role::text in ('admin', 'agent')),
           exists (select 1 from remote_saas.members m where m.user_id = u.id),
           coalesce(
               (select t.client_id
                from remote_saas.tenants t
                join remote_saas.members m on m.tenant_id = t.id and m.user_id = u.id
                where t.organization_id = u.organization_id
                  and m.role in ('owner', 'admin', 'technician')
                  and remote_saas.effective_status(t) in ('active', 'trial', 'past_due')
                limit 1),
               case when exists (select 1 from remote_saas.members m where m.user_id = u.id) then null
                    else coalesce(
                        (select c.id
                         from public.clients c
                         where u.organization_id is not null
                           and c.organization_id = u.organization_id
                           and c.is_active
                         order by c.created_at
                         limit 1),
                        remote_private.quick_phone_client()
                    )
               end
           )
    into actor, actor_is_staff, has_saas_membership, client
    from public.users u
    where u.auth_user_id = auth.uid()
      and u.is_active
      and u.deleted_at is null
      and (
          (u.organization_id is null and u.role::text in ('admin', 'agent'))
          or u.organization_id is not null
      )
    limit 1;

    if actor is null then
        raise exception 'Sign in with an active Mixel Remote account to connect' using errcode = '42501';
    end if;

    select * into phone from remote_private.quick_phones q where q.connection_id = connection for update;
    if not found then return false; end if;
    if client is null then
        if has_saas_membership then
            raise exception 'Active technician membership required to claim a Quick Support device' using errcode = '42501';
        end if;
        raise exception 'Company unavailable for Remote devices' using errcode = '42501';
    end if;

    -- Prevent cross-tenant hijacking of a Quick Support device already saved or owned by another tenant.
    if phone.device_id is not null then
        select d.client_id into existing_client from public.devices d where d.id = phone.device_id;
        if existing_client is not null and existing_client <> client and not actor_is_staff then
            if phone.saved_at is not null then
                raise exception 'This device is already saved in another company' using errcode = '42501';
            end if;
            if exists (
                select 1 from public.remote_device_enrollments e
                where e.device_id = phone.device_id and e.revoked_at is not null
            ) then
                raise exception 'This device is no longer available' using errcode = '42501';
            end if;
        end if;
    end if;

    created := phone.device_id;
    select e.* into existing from public.remote_device_enrollments e where e.public_key = phone.public_key for update;
    if found and (created is null or existing.device_id <> created) then
        select d.client_id into existing_client from public.devices d where d.id = existing.device_id;
        if existing_client is not null and existing_client <> client and not actor_is_staff then
            if phone.saved_at is not null or existing.revoked_at is not null then
                raise exception 'This device belongs to another company' using errcode = '42501';
            end if;
        end if;
        created := existing.device_id;
        update public.remote_device_enrollments e
            set connection_id = phone.connection_id, revoked_at = null, enrolled_by = actor, generation = e.generation + 1
            where e.device_id = created returning * into enrolled;
        update public.devices d set client_id = client where d.id = created;
        update remote_private.support_codes s set device_id = null where s.device_id = created;
        insert into remote_private.enrollment_audit(device_id, actor_id, action, next_key, generation)
            values (created, actor, 'enroll', phone.public_key, enrolled.generation);
        update remote_private.quick_phones q set device_id = created, claimed_by = actor, claimed_at = now() where q.public_key = phone.public_key;
        return true;
    end if;
    if created is null or not exists (select 1 from public.devices d where d.id = created) then
        name_try := left(phone.device_name, 60);
        for attempt in 1..50 loop
            exit when not exists (select 1 from public.devices d where d.client_id = client and lower(d.name) = lower(name_try));
            name_try := left(phone.device_name, 54) || ' (' || (attempt + 1) || ')';
        end loop;
        insert into public.devices(client_id, name, type, os) values (client, name_try, 'phone', 'other'::public.ism_device_os) returning id into created;
    else
        name_try := left(phone.device_name, 60);
        for attempt in 1..50 loop
            exit when not exists (select 1 from public.devices d where d.client_id = client and d.id <> created and lower(d.name) = lower(name_try));
            name_try := left(phone.device_name, 54) || ' (' || (attempt + 1) || ')';
        end loop;
        update public.devices d set client_id = client, name = name_try where d.id = created and (d.client_id is distinct from client or d.name is distinct from name_try);
    end if;
    select e.* into enrolled from public.remote_device_enrollments e where e.device_id = created for update;
    if not found then
        insert into public.remote_device_enrollments(device_id, connection_id, public_key, enrolled_by)
            values (created, phone.connection_id, phone.public_key, actor) returning * into enrolled;
    else
        update public.remote_device_enrollments e set revoked_at = null, enrolled_by = actor, connection_id = phone.connection_id,
            generation = case when e.revoked_at is null and e.connection_id = phone.connection_id and e.enrolled_by = actor then e.generation else e.generation + 1 end
            where e.device_id = created returning * into enrolled;
    end if;
    insert into remote_private.enrollment_audit(device_id, actor_id, action, next_key, generation)
        values (created, actor, 'enroll', phone.public_key, enrolled.generation);
    update remote_private.quick_phones q set device_id = created, claimed_by = actor, claimed_at = now() where q.public_key = phone.public_key;
    return true;
end;
$$;

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
    is_staff boolean := false;
    can_manage boolean := false;
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

    -- Ensure an active Quick Support device with this connection_id is claimed if not yet enrolled.
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

    is_staff := (me.organization_id is null and me.role::text in ('admin', 'agent'));
    select * into t from remote_saas.tenants where client_id = current_device.client_id;

    -- Enforce management permissions (prevent read-only members or suspended tenants from mutating devices).
    if t.id is not null then
        if remote_saas.effective_status(t) not in ('active', 'trial', 'past_due') then
            raise exception 'This company subscription is not active' using errcode = '42501';
        end if;
        can_manage := is_staff or exists (
            select 1 from remote_saas.members m
            where m.tenant_id = t.id and m.user_id = me.id and m.role in ('owner', 'admin', 'technician')
        );
    else
        can_manage := is_staff or remote_private.can_admin_device(current_device.id);
    end if;

    if not can_manage then
        raise exception 'You do not have permission to save or edit this device' using errcode = '42501';
    end if;

    -- Resolve target ISM client if caller is Mixel staff admin.
    chosen_client := current_device.client_id;
    if t.id is null and is_staff then
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
        -- Match an existing group, or create one only if the tenant's plan includes groups.
        select g.id into grp_id
        from remote_saas.device_groups g
        where g.tenant_id = t.id and lower(g.name) = lower(clean_company)
        limit 1;
        if grp_id is null and (select count(*) from remote_saas.device_groups g where g.tenant_id = t.id) < 500 then
            begin
                perform remote_saas.require_feature(t.id, 'groups', 'Groups require Business plan');
                insert into remote_saas.device_groups(tenant_id, name) values (t.id, clean_company) returning id into grp_id;
            exception when others then
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
            final_name := left(clean_name, 54) || ' (' || (attempt + 1) || ')';
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

    insert into remote_private.device_admin_audit(device_id, client_id, actor_id, action, detail)
    values (
        current_device.id,
        chosen_client,
        me.id,
        'device.note',
        jsonb_build_object(
            'via', 'save_connected_device',
            'name', final_name,
            'company', clean_company,
            'user', clean_user
        )
    );

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

commit;
