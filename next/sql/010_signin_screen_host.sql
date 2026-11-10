-- Windows sign-in screen (and macOS startup) host: a separately enrolled device record
-- of a linked company. Also returns the verified Mixel ID of the enrolling profile,
-- so the console can pair it when that same person asks for the pairing. The enrolling profile is a member of that company or an active
-- Mixel team member, as in resolve_support_pair.
begin;

drop function if exists remote_private.resolve_startup_host(integer,uuid,uuid,uuid,bigint,bytea);
create function remote_private.resolve_startup_host(
    connection integer, device uuid, profile uuid, tenant uuid,
    key_generation bigint, device_key bytea
) returns table (customer uuid, enroller text)
language sql stable security definer set search_path = '' as $$
    select o.mixel_org_id, remote_private.verified_mixel_subject(u.auth_user_id)
    from public.remote_device_enrollments e
    join public.devices d on d.id = e.device_id
    join public.clients c on c.id = d.client_id and c.is_active
    join public.organizations o on o.id = c.organization_id and o.is_active
    join public.users u on u.id = profile and u.is_active and u.deleted_at is null
      and (u.organization_id = o.id
        or (u.organization_id is null and u.role::text in ('admin','agent')))
    where o.id = tenant and o.mixel_org_id is not null
      and e.revoked_at is null and e.connection_id = connection and e.device_id = device
      and e.generation = key_generation and e.public_key = device_key;
$$;

revoke all on function remote_private.resolve_startup_host(integer,uuid,uuid,uuid,bigint,bytea)
    from public, anon, authenticated;
grant execute on function remote_private.resolve_startup_host(integer,uuid,uuid,uuid,bigint,bytea)
    to mixel_remote_policy_reader;

-- The company (ISM organization) of a device a team member may enrol, so the
-- sign-in screen host can name its tenant.
create or replace function public.remote_device_tenant(target uuid)
returns uuid
language plpgsql stable security definer set search_path = ''
as $$
declare found_tenant uuid;
begin
    perform remote_private.staff_actor();
    select o.id into found_tenant
    from public.devices d
    join public.clients c on c.id = d.client_id and c.is_active
    join public.organizations o on o.id = c.organization_id and o.is_active and o.mixel_org_id is not null
    where d.id = target;
    if found_tenant is null then
        raise exception 'Device unavailable' using errcode = '42501';
    end if;
    return found_tenant;
end;
$$;
revoke all on function public.remote_device_tenant(uuid) from public, anon, authenticated;
grant execute on function public.remote_device_tenant(uuid) to authenticated;

commit;
