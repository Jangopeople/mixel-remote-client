-- Mixel Remote as SaaS: the broker's pairing and agent checks ask the policy service about the
-- device binding of a connected device. Console pairing does not exist for SaaS tenants, so the
-- policy service answers "nothing pending" when the binding is a current enrollment of a SaaS
-- tenant device whose profile is an active member of that tenant.
begin;
create function remote_private.saas_device_bound(
    connection integer, device uuid, profile uuid, key_generation bigint, device_key bytea
) returns boolean
language sql stable security definer set search_path = '' as $$
    select exists (
        select 1 from public.remote_device_enrollments e
        join public.devices d on d.id = e.device_id
        join remote_saas.tenants t on t.client_id = d.client_id
        join public.organizations o on o.id = t.organization_id and o.is_active and o.kind = 'remote_saas'
        join public.users u on u.id = profile and u.is_active and u.deleted_at is null
            and u.organization_id = t.organization_id
        join remote_saas.members m on m.tenant_id = t.id and m.user_id = u.id
        where e.connection_id = connection and e.device_id = device and e.generation = key_generation
          and e.public_key = device_key and e.revoked_at is null
          and remote_saas.effective_status(t) <> 'closed'
    );
$$;
revoke all on function remote_private.saas_device_bound(integer,uuid,uuid,bigint,bytea) from public, anon, authenticated;
grant execute on function remote_private.saas_device_bound(integer,uuid,uuid,bigint,bytea) to mixel_remote_policy_reader;
commit;
