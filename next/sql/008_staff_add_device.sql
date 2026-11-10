-- Lets an MFA-verified Mixel team member add a device record for the computer they are
-- setting up, without database work. Only companies that are linked to a Mixel admin
-- company (organizations.mixel_org_id) are offered, because the connection policy refuses
-- sessions to devices of unlinked companies.
begin;

create or replace function public.remote_enrollment_companies()
returns table(client_id uuid, client_name text)
language plpgsql stable security definer set search_path = ''
as $$
begin
    perform remote_private.staff_actor();
    return query
        select c.id, c.name
        from public.clients c
        join public.organizations o on o.id = c.organization_id and o.is_active and o.mixel_org_id is not null
        where c.is_active
        order by c.name, c.id
        limit 200;
end;
$$;

create or replace function public.remote_add_device(target_client uuid, device_name text, device_os text)
returns uuid
language plpgsql security definer set search_path = ''
as $$
declare
    clean_name text := btrim(device_name);
    existing uuid;
    created uuid;
begin
    perform remote_private.staff_actor();
    if clean_name is null or char_length(clean_name) not between 1 and 80 or clean_name ~ '[[:cntrl:]]'
        or device_os is null or device_os not in ('macos','windows','linux')
    then raise exception 'Invalid Remote device' using errcode = '22023'; end if;
    perform 1 from public.clients c
        join public.organizations o on o.id = c.organization_id and o.is_active and o.mixel_org_id is not null
        where c.id = target_client and c.is_active
        for update of c;
    if not found then raise exception 'Company unavailable for Remote devices' using errcode = '42501'; end if;
    select d.id into existing from public.devices d
        where d.client_id = target_client and lower(d.name) = lower(clean_name) limit 1;
    if found then
        if exists (select 1 from public.remote_device_enrollments e where e.device_id = existing) then
            raise exception 'A computer with this name is already enrolled for this company' using errcode = '23505';
        end if;
        return existing;
    end if;
    if (select count(*) from public.devices d where d.client_id = target_client) >= 5000
    then raise exception 'Device limit reached' using errcode = '54000'; end if;
    insert into public.devices(client_id, name, type, os)
    values (target_client, clean_name, 'desktop', device_os::public.ism_device_os)
    returning id into created;
    return created;
end;
$$;

revoke all on function public.remote_enrollment_companies(), public.remote_add_device(uuid,text,text)
from public, anon, authenticated;
grant execute on function public.remote_enrollment_companies(), public.remote_add_device(uuid,text,text)
to authenticated;
commit;
