-- Admin console (admin.mixel.ch/remote): list a customer's enrolled devices by
-- name and revoke one. The console checks owner rights in its own database
-- first; these functions only accept devices of the given linked customer.
begin;

create or replace function remote_private.customer_devices(customer uuid)
returns table(device_id uuid, name text, os text, connection_id integer, generation bigint,
              enrolled_at timestamptz, revoked boolean)
language sql stable security definer set search_path = ''
as $$
    select d.id, d.name, d.os::text, e.connection_id, e.generation, e.enrolled_at, e.revoked_at is not null
    from public.organizations o
    join public.clients c on c.organization_id = o.id and c.is_active
    join public.devices d on d.client_id = c.id
    join public.remote_device_enrollments e on e.device_id = d.id
    where o.mixel_org_id = customer and o.is_active
    order by e.revoked_at is not null, lower(d.name), d.id
    limit 500;
$$;

create or replace function remote_private.revoke_customer_device(customer uuid, target uuid, actor_subject text)
returns boolean
language plpgsql security definer set search_path = ''
as $$
declare
    actor uuid;
    existing public.remote_device_enrollments;
begin
    select u.id into actor
    from auth.identities i
    join public.users u on u.auth_user_id = i.user_id and u.is_active and u.deleted_at is null
    where i.provider = 'keycloak' and i.provider_id = actor_subject
    limit 1;
    if actor is null then return false; end if;
    perform 1 from public.devices d
        join public.clients c on c.id = d.client_id
        join public.organizations o on o.id = c.organization_id and o.mixel_org_id = customer
        where d.id = target
        for update of d;
    if not found then return false; end if;
    select * into existing from public.remote_device_enrollments where device_id = target for update;
    if not found then return false; end if;
    if existing.revoked_at is not null then return true; end if;
    update public.remote_device_enrollments set revoked_at = now(), generation = generation + 1 where device_id = target;
    insert into remote_private.enrollment_audit(device_id, actor_id, action, previous_key, generation)
    values (target, actor, 'revoke', existing.public_key, existing.generation + 1);
    return true;
end;
$$;

revoke all on function remote_private.customer_devices(uuid), remote_private.revoke_customer_device(uuid,uuid,text)
from public, anon, authenticated;
grant execute on function remote_private.customer_devices(uuid), remote_private.revoke_customer_device(uuid,uuid,text)
to mixel_remote_policy_reader;
commit;
