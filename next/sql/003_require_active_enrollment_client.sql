-- Keep an enrolled device's public ID reserved when its ISM client is disabled.
begin;

create or replace function remote_private.can_read_device(target uuid) returns boolean
language sql stable security definer set search_path = '' as $$
    select exists (
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
    );
$$;

create or replace function public.remote_enroll_device(target uuid, key_material bytea)
returns public.remote_device_enrollments
language plpgsql security definer set search_path = '' as $$
declare actor uuid; existing public.remote_device_enrollments; candidate bigint;
begin
    actor := remote_private.staff_actor();
    if octet_length(key_material) is distinct from 32 then
        raise exception 'Device public key must contain 32 bytes' using errcode = '22023';
    end if;
    -- Serialize enrollment and client reassignment against the existing device.
    perform 1 from public.devices d join public.clients c on c.id = d.client_id
    left join public.organizations o on o.id = c.organization_id
    where d.id = target and c.is_active and (c.organization_id is null or o.is_active)
    for update of d for share of c;
    if not found then
        raise exception 'Active device binding unavailable' using errcode = '42501';
    end if;
    select * into existing from public.remote_device_enrollments where device_id = target;
    if found then
        if existing.revoked_at is not null or existing.public_key <> key_material then
            raise exception 'Existing enrollment requires explicit rotation' using errcode = '42501';
        end if;
        return existing;
    end if;
    -- UUIDv4 first four bytes are uniformly random (version/variant bits occur
    -- later). Use the PostgreSQL core CSPRNG, independent of extension schema.
    -- Rejection sampling avoids biased public identifiers.
    for attempt in 1..128 loop
        candidate := (('x' || pg_catalog.encode(pg_catalog.substring(pg_catalog.uuid_send(pg_catalog.gen_random_uuid()),1,4),'hex'))::bit(32)::bigint);
        if candidate >= 3600000000 then continue; end if;
        candidate := 100000000 + candidate % 900000000;
        begin
            insert into public.remote_device_enrollments(device_id,connection_id,public_key,enrolled_by)
            values(target,candidate,key_material,actor) returning * into existing;
        exception when unique_violation then
            -- A duplicate identity is never silently rebound to another device.
            if exists(select 1 from public.remote_device_enrollments where public_key = key_material) then
                raise exception 'Public identity already bound' using errcode = '42501';
            end if;
            continue;
        end;
        insert into remote_private.enrollment_audit(device_id,actor_id,action,next_key,generation)
        values(target,actor,'enroll',key_material,existing.generation);
        return existing;
    end loop;
    raise exception 'Connection identifier allocation exhausted' using errcode = '54000';
end;
$$;

create or replace function public.remote_rotate_device(target uuid, expected_generation bigint, key_material bytea)
returns public.remote_device_enrollments
language plpgsql security definer set search_path = '' as $$
declare actor uuid; existing public.remote_device_enrollments; changed public.remote_device_enrollments;
begin
    actor := remote_private.staff_actor();
    if octet_length(key_material) is distinct from 32 then
        raise exception 'Device public key must contain 32 bytes' using errcode = '22023';
    end if;
    -- Same lock order as enrollment, including the live tenant binding.
    perform 1 from public.devices d join public.clients c on c.id = d.client_id
    left join public.organizations o on o.id = c.organization_id
    where d.id = target and c.is_active and (c.organization_id is null or o.is_active)
    for update of d for share of c;
    if not found then raise exception 'Active device binding unavailable' using errcode = '42501'; end if;
    select * into existing from public.remote_device_enrollments where device_id = target for update;
    if not found or existing.generation <> expected_generation then
        raise exception 'Enrollment version changed or unavailable' using errcode = '40001';
    end if;
    if existing.public_key = key_material then
        raise exception 'Rotation requires a new identity' using errcode = '22023';
    end if;
    update public.remote_device_enrollments set public_key = key_material, revoked_at = null,
        generation = generation + 1, enrolled_by = actor, enrolled_at = now()
    where device_id = target returning * into changed;
    insert into remote_private.enrollment_audit(device_id,actor_id,action,previous_key,next_key,generation)
    values(target,actor,'rotate',existing.public_key,key_material,changed.generation);
    return changed;
end;
$$;

commit;
