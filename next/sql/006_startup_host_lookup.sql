begin;

create function remote_private.resolve_startup_host(
    connection integer, device uuid, profile uuid, tenant uuid,
    key_generation bigint, device_key bytea
) returns table (customer uuid)
language sql stable security definer set search_path = '' as $$
    select o.mixel_org_id
    from public.remote_device_enrollments e
    join public.devices d on d.id = e.device_id
    join public.clients c on c.id = d.client_id and c.is_active
    join public.organizations o on o.id = c.organization_id and o.is_active
    join public.users u on u.id = profile and u.organization_id = o.id
      and u.is_active and u.deleted_at is null
    where o.id = tenant and o.mixel_org_id is not null
      and e.revoked_at is null and e.connection_id = connection and e.device_id = device
      and e.generation = key_generation and e.public_key = device_key;
$$;

revoke all on function remote_private.resolve_startup_host(integer,uuid,uuid,uuid,bigint,bytea)
    from public, anon, authenticated;
grant execute on function remote_private.resolve_startup_host(integer,uuid,uuid,uuid,bigint,bytea)
    to mixel_remote_policy_reader;

commit;
