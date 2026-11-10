-- Isolated staging migration against the existing ISM users/devices/clients.
-- No accounts, devices or customer remote-tool fields are duplicated or changed.
begin;
-- Refuse namespace collisions; never expand access to an unrelated private schema.
create schema remote_private;
revoke all on schema remote_private from public;
create table public.remote_device_enrollments (
    device_id uuid primary key references public.devices(id) on delete restrict,
    connection_id integer not null unique check (connection_id between 100000000 and 999999999),
    public_key bytea not null unique check (octet_length(public_key) = 32),
    enrolled_by uuid not null references public.users(id) on delete restrict,
    enrolled_at timestamptz not null default now(),
    revoked_at timestamptz,
    generation bigint not null default 1 check (generation > 0)
);
create table remote_private.enrollment_audit (
    id bigint generated always as identity primary key,
    device_id uuid not null references public.devices(id) on delete restrict,
    actor_id uuid not null references public.users(id) on delete restrict,
    action text not null check (action in ('enroll','revoke','rotate')),
    previous_key bytea,
    next_key bytea,
    generation bigint not null,
    occurred_at timestamptz not null default now()
);
alter table public.remote_device_enrollments enable row level security;
revoke all on public.remote_device_enrollments from public, anon, authenticated;
grant select on public.remote_device_enrollments to authenticated;
revoke all on remote_private.enrollment_audit from public, anon, authenticated;
-- Use verified auth.uid and live profile data. Do not infer staff from client
-- metadata, supplied profile IDs or an organization-less JWT claim.
create function remote_private.staff_actor() returns uuid
language plpgsql stable security definer set search_path = '' as $$
declare actor uuid;
begin
    if coalesce(auth.jwt()->>'aal','') <> 'aal2' then
        raise exception 'MFA-authenticated staff required' using errcode = '42501';
    end if;
    select u.id into actor from public.users u
    where u.auth_user_id = auth.uid() and u.is_active
      and u.organization_id is null and u.role::text in ('admin','agent');
    if actor is null then
        raise exception 'Active internal staff required' using errcode = '42501';
    end if;
    return actor;
end;
$$;
revoke all on function remote_private.staff_actor() from public, anon, authenticated;
create function remote_private.can_read_device(target uuid) returns boolean
language sql stable security definer set search_path = '' as $$
    select exists (
        select 1 from public.users u
        join public.devices d on d.id = target
        join public.clients c on c.id = d.client_id
        left join public.organizations o on o.id = c.organization_id
        where u.auth_user_id = auth.uid() and u.is_active
          and (c.organization_id is null or o.is_active)
          and (
            (u.organization_id is null and u.role::text in ('admin','agent')
             and coalesce(auth.jwt()->>'aal','') = 'aal2')
            or (u.organization_id is not null and u.organization_id = c.organization_id)
          )
    );
$$;
revoke all on function remote_private.can_read_device(uuid) from public, anon;
grant usage on schema remote_private to authenticated;
grant execute on function remote_private.can_read_device(uuid) to authenticated;
create policy remote_enrollments_tenant_read on public.remote_device_enrollments
for select to authenticated using (
    revoked_at is null and remote_private.can_read_device(device_id)
);
create function public.remote_enroll_device(target uuid, key_material bytea)
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
    where d.id = target and (c.organization_id is null or o.is_active) for update of d;
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
create function public.remote_revoke_device(target uuid, expected_generation bigint)
returns void language plpgsql security definer set search_path = '' as $$
declare actor uuid; existing public.remote_device_enrollments;
begin
    actor := remote_private.staff_actor();
    -- Keep the device -> enrollment lock order shared with enrollment/rotation.
    perform 1 from public.devices where id = target for update;
    if not found then raise exception 'Device unavailable' using errcode = '42501'; end if;
    select * into existing from public.remote_device_enrollments where device_id = target for update;
    if not found or existing.generation <> expected_generation then
        raise exception 'Enrollment version changed or unavailable' using errcode = '40001';
    end if;
    if existing.revoked_at is not null then return; end if;
    update public.remote_device_enrollments set revoked_at = now(), generation = generation + 1 where device_id = target;
    insert into remote_private.enrollment_audit(device_id,actor_id,action,previous_key,generation)
    values(target,actor,'revoke',existing.public_key,existing.generation + 1);
end;
$$;
create function public.remote_rotate_device(target uuid, expected_generation bigint, key_material bytea)
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
    where d.id = target and (c.organization_id is null or o.is_active) for update of d;
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
revoke all on function public.remote_enroll_device(uuid,bytea), public.remote_revoke_device(uuid,bigint), public.remote_rotate_device(uuid,bigint,bytea) from public, anon;
grant execute on function public.remote_enroll_device(uuid,bytea), public.remote_revoke_device(uuid,bigint), public.remote_rotate_device(uuid,bigint,bytea) to authenticated;
commit;
