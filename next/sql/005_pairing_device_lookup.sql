begin;

create function remote_private.resolve_pairing_device(
    connection integer, device uuid, profile uuid, key_generation bigint, device_key bytea
) returns table (device_id uuid, generation bigint, customer uuid, device_name text)
language sql stable security definer set search_path = '' as $$
    select d.id, e.generation, o.mixel_org_id, d.name
    from public.remote_device_enrollments e
    join public.devices d on d.id = e.device_id
    join public.clients c on c.id = d.client_id and c.is_active
    join public.organizations o on o.id = c.organization_id and o.is_active
      and o.mixel_org_id is not null
    where e.revoked_at is null and e.connection_id = connection
      and (
        (device is null and profile is null and key_generation is null and device_key is null)
        or (
          e.device_id = device and e.generation = key_generation and e.public_key = device_key
          and exists (
            select 1 from public.users u
            where u.id = profile and u.is_active and u.deleted_at is null
              and (u.organization_id = o.id
                or (u.organization_id is null and u.role::text in ('admin','agent')))
          )
        )
      );
$$;

revoke all on function remote_private.resolve_pairing_device(integer,uuid,uuid,bigint,bytea)
    from public, anon, authenticated;
grant execute on function remote_private.resolve_pairing_device(integer,uuid,uuid,bigint,bytea)
    to mixel_remote_policy_reader;

commit;
