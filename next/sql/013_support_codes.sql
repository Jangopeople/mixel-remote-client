-- Quick Support for phones: a team member creates a one-time support code for a linked company.
-- A phone that enters it gets a temporary device registration without any account and can host
-- one attended session. Only the SHA-256 of the code is stored; a code is valid for 30 minutes and
-- for one device. Registrations made with a support code are removed automatically after 8 hours.
begin;

create table if not exists remote_private.support_codes (
    id uuid primary key default gen_random_uuid(),
    code_hash bytea not null unique check (octet_length(code_hash) = 32),
    client_id uuid not null references public.clients(id) on delete cascade,
    created_by uuid not null references public.users(id) on delete restrict,
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    used_at timestamptz,
    device_id uuid references public.devices(id) on delete set null
);
revoke all on remote_private.support_codes from public, anon, authenticated;
create index if not exists support_codes_device on remote_private.support_codes(device_id) where device_id is not null;

-- Removes what support codes left behind: registrations older than 8 hours and codes older than a day.
create or replace function remote_private.sweep_support_codes() returns void
language plpgsql security definer set search_path = '' as $$
declare stale record;
begin
    for stale in
        select e.device_id, e.public_key, e.generation, s.created_by
        from remote_private.support_codes s
        join public.remote_device_enrollments e on e.device_id = s.device_id and e.revoked_at is null
        where s.used_at < now() - interval '8 hours'
        for update of e
    loop
        update public.remote_device_enrollments set revoked_at = now(), generation = generation + 1 where device_id = stale.device_id;
        insert into remote_private.enrollment_audit(device_id, actor_id, action, previous_key, generation)
            values (stale.device_id, stale.created_by, 'revoke', stale.public_key, stale.generation + 1);
    end loop;
    delete from remote_private.support_codes where created_at < now() - interval '1 day' and device_id is null;
end;
$$;
revoke all on function remote_private.sweep_support_codes() from public, anon, authenticated;

-- Staff: a new support code for a linked company. Returned once, as XXXX-XXXX-XXXX.
create or replace function public.remote_create_support_code(target_client uuid)
returns text
language plpgsql security definer set search_path = '' as $$
declare actor uuid; letters constant text := 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'; raw bytea; code text := '';
begin
    actor := remote_private.staff_actor();
    perform 1 from public.clients c
        join public.organizations o on o.id = c.organization_id and o.is_active and o.mixel_org_id is not null
        where c.id = target_client and c.is_active;
    if not found then raise exception 'Company unavailable for Remote devices' using errcode = '42501'; end if;
    perform remote_private.sweep_support_codes();
    if (select count(*) from remote_private.support_codes s where s.created_by = actor and s.created_at > now() - interval '1 hour') >= 30 then
        raise exception 'Too many support codes' using errcode = '54000';
    end if;
    -- 12 characters from 31 letters and digits (no 0, O, 1, I, L); 256 mod 31 bias is irrelevant at this length.
    raw := public.gen_random_bytes(12);
    for position in 0..11 loop
        code := code || substr(letters, 1 + get_byte(raw, position) % 31, 1);
    end loop;
    insert into remote_private.support_codes(code_hash, client_id, created_by, expires_at)
        values (pg_catalog.sha256(convert_to(code, 'UTF8')), target_client, actor, now() + interval '30 minutes');
    return substr(code, 1, 4) || '-' || substr(code, 5, 4) || '-' || substr(code, 9, 4);
end;
$$;

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

revoke all on function public.remote_create_support_code(uuid), public.remote_enroll_with_support_code(text,text,bytea) from public;
grant execute on function public.remote_create_support_code(uuid) to authenticated;
grant execute on function public.remote_enroll_with_support_code(text,text,bytea) to anon, authenticated;
commit;
