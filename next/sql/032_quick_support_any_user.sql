-- Allow any authenticated Mixel Remote user (Mixel staff, SaaS tenant member, or ISM org user)
-- to claim and connect to a Quick Support device by its 9-digit Connection ID, and allow Mixel
-- staff and cross-tenant attended requests to verify peer enrollment keys.
begin;

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
        -- If this key already has a reserved connection_id, reuse it so its 9-digit ID stays stable.
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
        update remote_private.quick_phones q set last_seen = now(), device_name = clean_name where q.public_key = key_material;
    end if;
    select e.* into enrolled from public.remote_device_enrollments e
        where e.device_id = row_found.device_id and e.public_key = key_material and e.revoked_at is null;
    if row_found.claimed_by is not null and enrolled.device_id is not null and row_found.claimed_at > now() - interval '8 hours' then
        select o.id into org from public.devices d join public.clients c on c.id = d.client_id
            join public.organizations o on o.id = c.organization_id where d.id = enrolled.device_id;
        return query select row_found.connection_id, enrolled.device_id, enrolled.generation, row_found.claimed_by, org;
    else
        return query select row_found.connection_id, null::uuid, null::bigint, null::uuid, null::uuid;
    end if;
end;
$$;

create or replace function public.remote_quick_phone_claim(connection integer)
returns boolean
language plpgsql security definer set search_path = '' as $$
declare
    actor uuid;
    phone remote_private.quick_phones;
    client uuid;
    created uuid;
    name_try text;
    enrolled public.remote_device_enrollments;
    existing public.remote_device_enrollments;
begin
    -- Resolve any active authenticated user (Mixel staff, SaaS tenant member, or ISM org user)
    select u.id,
           coalesce(
               (select t.client_id
                from remote_saas.tenants t
                join remote_saas.members m on m.tenant_id = t.id and m.user_id = u.id
                where t.organization_id = u.organization_id
                  and remote_saas.effective_status(t) in ('active', 'trial', 'past_due')
                limit 1),
               (select c.id
                from public.clients c
                where u.organization_id is not null
                  and c.organization_id = u.organization_id
                  and c.is_active
                order by c.created_at
                limit 1),
               remote_private.quick_phone_client()
           )
    into actor, client
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
    if client is null then raise exception 'Company unavailable for Remote devices' using errcode = '42501'; end if;

    created := phone.device_id;
    select e.* into existing from public.remote_device_enrollments e where e.public_key = phone.public_key for update;
    if found and (created is null or existing.device_id <> created) then
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
        name_try := phone.device_name;
        for attempt in 1..50 loop
            exit when not exists (select 1 from public.devices d where d.client_id = client and lower(d.name) = lower(name_try));
            name_try := phone.device_name || ' (' || (attempt + 1) || ')';
        end loop;
        insert into public.devices(client_id, name, type, os) values (client, name_try, 'phone', 'other'::public.ism_device_os) returning id into created;
    else
        name_try := phone.device_name;
        for attempt in 1..50 loop
            exit when not exists (select 1 from public.devices d where d.client_id = client and d.id <> created and lower(d.name) = lower(name_try));
            name_try := phone.device_name || ' (' || (attempt + 1) || ')';
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

-- Allow SaaS support sessions for any active owner/admin/technician, and for any user who claimed
-- an active Quick Support device (remote_private.quick_phones) in an attended session.
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
                      where q.device_id = host_device and q.claimed_by = cu.id and q.claimed_at > now() - interval '8 hours'
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

revoke all on function public.remote_quick_phone_register(text,bytea), public.remote_quick_phone_claim(integer) from public;
grant execute on function public.remote_quick_phone_register(text,bytea) to anon, authenticated;
grant execute on function public.remote_quick_phone_claim(integer) to authenticated;
revoke all on function remote_private.saas_support_pair(uuid,uuid,bigint,bytea,uuid,uuid,bigint,bytea,boolean) from public, anon, authenticated;
grant execute on function remote_private.saas_support_pair(uuid,uuid,bigint,bytea,uuid,uuid,bigint,bytea,boolean) to mixel_remote_policy_reader;

commit;
notify pgrst, 'reload schema';
