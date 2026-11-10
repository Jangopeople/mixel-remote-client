begin;

-- Provision a dedicated login and grant it membership in this role outside
-- the migration. The login needs no ISM table privileges.
create role mixel_remote_policy_reader nologin;
grant usage on schema remote_private to mixel_remote_policy_reader;

create function remote_private.resolve_support_pair(
    controller_profile uuid, controller_device uuid, controller_generation bigint, controller_key bytea,
    host_profile uuid, host_device uuid, host_generation bigint, host_key bytea
) returns table (subject text, customer uuid)
language sql stable security definer set search_path = '' as $$
    select identity.provider_id, host_org.mixel_org_id
    from public.users controller
    join auth.identities identity on identity.user_id = controller.auth_user_id
      and identity.provider = 'keycloak'
      and identity.identity_data->>'iss' in (
        'https://auth.mixel.ch/application/o/mixel-ism-user',
        'https://auth.mixel.ch/application/o/mixel-ism-user/'
      )
      and identity.provider_id = identity.identity_data->>'sub'
      and length(identity.provider_id) between 1 and 255
    join public.remote_device_enrollments source on source.device_id = controller_device
      and source.generation = controller_generation and source.public_key = controller_key and source.revoked_at is null
    join public.devices source_device on source_device.id = source.device_id
    join public.clients source_client on source_client.id = source_device.client_id and source_client.is_active
    left join public.organizations source_org on source_org.id = source_client.organization_id
    join public.remote_device_enrollments target on target.device_id = host_device
      and target.generation = host_generation and target.public_key = host_key and target.revoked_at is null
    join public.devices target_device on target_device.id = target.device_id
    join public.clients target_client on target_client.id = target_device.client_id and target_client.is_active
    join public.organizations host_org on host_org.id = target_client.organization_id and host_org.is_active
      and host_org.mixel_org_id is not null
    join public.users host on host.id = host_profile and host.is_active and host.deleted_at is null
      and (host.organization_id = host_org.id
        or (host.organization_id is null and host.role::text in ('admin','agent')))
    where controller.id = controller_profile and controller.is_active and controller.deleted_at is null
      and controller_device <> host_device
      and (source_client.organization_id is null or source_org.is_active)
      and (controller.organization_id = source_org.id
        or (controller.organization_id is null and controller.role::text in ('admin','agent')))
    limit 2;
$$;

revoke all on function remote_private.resolve_support_pair(uuid,uuid,bigint,bytea,uuid,uuid,bigint,bytea)
    from public, anon, authenticated;
grant execute on function remote_private.resolve_support_pair(uuid,uuid,bigint,bytea,uuid,uuid,bigint,bytea)
    to mixel_remote_policy_reader;

commit;
