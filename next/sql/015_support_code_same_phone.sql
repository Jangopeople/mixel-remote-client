-- Quick Support for phones: a phone that was helped before can be helped again with a new code.
-- Its device key is already registered (keys are unique), so the earlier registration is renewed
-- for the new code instead of refusing the phone.
begin;
-- Phone (anonymous, holds the code): register itself for one support session.
create or replace function public.remote_enroll_with_support_code(code text, device_name text, key_material bytea)
returns table(device_id uuid, connection_id integer, generation bigint, profile uuid, tenant uuid)
language plpgsql security definer set search_path = '' as $$
declare grant_row remote_private.support_codes; clean_code text; clean_name text := btrim(device_name); name_try text;
        created uuid; enrolled public.remote_device_enrollments; org uuid; candidate bigint;
begin
    clean_code := upper(regexp_replace(coalesce(code, ''), '[^A-Za-z0-9]', '', 'g'));
    if char_length(clean_code) <> 12 or octet_length(key_material) is distinct from 32
        or clean_name is null or char_length(clean_name) not between 1 and 60 or clean_name ~ '[[:cntrl:]]' then
        raise exception 'Invalid support request' using errcode = '22023';
    end if;
    select * into grant_row from remote_private.support_codes s
        where s.code_hash = pg_catalog.sha256(convert_to(clean_code, 'UTF8')) for update;
    if not found or grant_row.expires_at <= now() then
        raise exception 'Support code invalid or expired' using errcode = '42501';
    end if;
    select o.id into org from public.clients c
        join public.organizations o on o.id = c.organization_id and o.is_active and o.mixel_org_id is not null
        where c.id = grant_row.client_id and c.is_active;
    if org is null then raise exception 'Company unavailable for Remote devices' using errcode = '42501'; end if;
    perform 1 from public.users u where u.id = grant_row.created_by and u.is_active and u.deleted_at is null;
    if not found then raise exception 'Support code owner is no longer active' using errcode = '42501'; end if;
    if grant_row.used_at is not null then
        -- The same phone asking again (app restarted) gets its registration back; anyone else is refused.
        select e.* into enrolled from public.remote_device_enrollments e
            where e.device_id = grant_row.device_id and e.public_key = key_material and e.revoked_at is null;
        if not found then raise exception 'Support code invalid or expired' using errcode = '42501'; end if;
        return query select enrolled.device_id, enrolled.connection_id, enrolled.generation, enrolled.enrolled_by, org;
        return;
    end if;
    -- The same phone with a new code (helped before): its registration is renewed, so it keeps
    -- one device record and its number instead of being refused or piling up records.
    select e.* into enrolled from public.remote_device_enrollments e where e.public_key = key_material for update;
    if found then
        if not exists (select 1 from remote_private.support_codes s where s.device_id = enrolled.device_id) then
            raise exception 'Support code invalid or expired' using errcode = '42501';
        end if;
        created := enrolled.device_id;
        name_try := clean_name;
        for attempt in 1..20 loop
            exit when not exists (select 1 from public.devices d where d.client_id = grant_row.client_id and lower(d.name) = lower(name_try) and d.id <> created);
            name_try := clean_name || ' (' || (attempt + 1) || ')';
        end loop;
        update remote_private.support_codes s set device_id = null where s.device_id = created;
        update public.devices d set client_id = grant_row.client_id, name = name_try where d.id = created;
        update public.remote_device_enrollments e
            set revoked_at = null, generation = e.generation + 1, enrolled_by = grant_row.created_by, enrolled_at = now()
            where e.device_id = created returning e.* into enrolled;
        insert into remote_private.enrollment_audit(device_id, actor_id, action, next_key, generation)
            values (created, grant_row.created_by, 'enroll', key_material, enrolled.generation);
        update remote_private.support_codes set used_at = now(), device_id = created where id = grant_row.id;
        return query select enrolled.device_id, enrolled.connection_id, enrolled.generation, enrolled.enrolled_by, org;
        return;
    end if;
    name_try := clean_name;
    for attempt in 1..20 loop
        exit when not exists (select 1 from public.devices d where d.client_id = grant_row.client_id and lower(d.name) = lower(name_try));
        name_try := clean_name || ' (' || (attempt + 1) || ')';
    end loop;
    insert into public.devices(client_id, name, type, os)
        values (grant_row.client_id, name_try, 'phone', 'other'::public.ism_device_os) returning id into created;
    for attempt in 1..128 loop
        candidate := (('x' || pg_catalog.encode(pg_catalog.substring(pg_catalog.uuid_send(pg_catalog.gen_random_uuid()),1,4),'hex'))::bit(32)::bigint);
        if candidate >= 3600000000 then continue; end if;
        candidate := 100000000 + candidate % 900000000;
        begin
            insert into public.remote_device_enrollments(device_id, connection_id, public_key, enrolled_by)
                values (created, candidate, key_material, grant_row.created_by) returning * into enrolled;
        exception when unique_violation then
            if exists(select 1 from public.remote_device_enrollments e where e.public_key = key_material) then
                raise exception 'Support code invalid or expired' using errcode = '42501';
            end if;
            continue;
        end;
        insert into remote_private.enrollment_audit(device_id, actor_id, action, next_key, generation)
            values (created, grant_row.created_by, 'enroll', key_material, enrolled.generation);
        update remote_private.support_codes set used_at = now(), device_id = created where id = grant_row.id;
        return query select enrolled.device_id, enrolled.connection_id, enrolled.generation, enrolled.enrolled_by, org;
        return;
    end loop;
    raise exception 'Connection identifier allocation exhausted' using errcode = '54000';
end;
$$;

revoke all on function public.remote_enroll_with_support_code(text,text,bytea) from public;
grant execute on function public.remote_enroll_with_support_code(text,text,bytea) to anon, authenticated;
commit;
