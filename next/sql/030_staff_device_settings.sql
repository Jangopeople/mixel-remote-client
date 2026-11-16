begin;
set local lock_timeout = '5s';

-- Edit the ISM inventory record behind an enrolled computer. SaaS devices use their
-- tenant's own management rules. The connection ID, key and generation stay attached.
create function public.remote_staff_device_settings(
    target uuid, expected_update timestamptz default null,
    device_name text default null, target_client uuid default null
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    actor uuid;
    current_device public.devices;
    clean_name text := btrim(device_name);
    company_list jsonb;
begin
    actor := remote_private.staff_actor();
    if not exists (select 1 from public.users u where u.id = actor and u.deleted_at is null) then
        raise exception 'Active internal staff required' using errcode = '42501';
    end if;
    select d.* into current_device
        from public.devices d
        join public.remote_device_enrollments e on e.device_id = d.id and e.revoked_at is null
        where d.id = target and d.os::text in ('macos', 'windows', 'linux')
          and not remote_private.saas_client(d.client_id)
        for update of d, e;
    if not found then
        raise exception 'This computer is unavailable for editing' using errcode = '42501';
    end if;

    if expected_update is not null then
        if current_device.updated_at <> expected_update then
            raise exception 'The device was changed. Open its settings again.' using errcode = 'PT409';
        end if;
        if clean_name is null or char_length(clean_name) not between 1 and 80 or clean_name ~ '[[:cntrl:]]' then
            raise exception 'Enter a device name between 1 and 80 characters.' using errcode = '22023';
        end if;
        if target_client is not null and target_client <> current_device.client_id then
            perform 1 from public.clients c
                join public.organizations o on o.id = c.organization_id
                where c.id = target_client and c.is_active and o.is_active and o.mixel_org_id is not null
                  and not remote_private.saas_client(c.id)
                for share of c, o;
            if not found then
                raise exception 'This company is unavailable for Remote devices' using errcode = '42501';
            end if;
        end if;
        update public.devices d
            set name = clean_name, client_id = coalesce(target_client, current_device.client_id),
                updated_at = clock_timestamp()
            where d.id = current_device.id
            returning d.* into current_device;
    elsif device_name is not null or target_client is not null then
        raise exception 'Open the device settings before saving changes' using errcode = '22023';
    end if;

    select coalesce(jsonb_agg(to_jsonb(c) order by c.client_name, c.client_id), '[]'::jsonb) into company_list
        from public.remote_enrollment_companies() c
        where c.client_id <> current_device.client_id and not remote_private.saas_client(c.client_id);
    return jsonb_build_object('name', current_device.name, 'updated_at', current_device.updated_at,
        'current_company', (select c.name from public.clients c where c.id = current_device.client_id),
        'companies', company_list);
end;
$$;

revoke all on function public.remote_staff_device_settings(uuid, timestamptz, text, uuid) from public, anon, authenticated;
grant execute on function public.remote_staff_device_settings(uuid, timestamptz, text, uuid) to authenticated;
notify pgrst, 'reload schema';
commit;
