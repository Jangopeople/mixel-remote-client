-- Quick Support for phones, the simple way: a phone gets a fixed 9-digit Remote ID as soon as the app
-- opens (no account, no code). The ID alone can do nothing. When a Mixel team member types that ID into
-- Mixel Remote, the phone is "claimed" in that person's name and becomes a device of the Mixel
-- Remote-devices company for 8 hours, so it can go online and ask the person to allow the session.
begin;

create table if not exists remote_private.quick_phones (
    public_key bytea primary key check (octet_length(public_key) = 32),
    connection_id integer not null unique check (connection_id between 100000000 and 999999999),
    device_name text not null,
    device_id uuid references public.devices(id) on delete set null,
    claimed_by uuid references public.users(id) on delete set null,
    claimed_at timestamptz,
    created_at timestamptz not null default now(),
    last_seen timestamptz not null default now()
);
revoke all on remote_private.quick_phones from public, anon, authenticated;
create index if not exists quick_phones_created on remote_private.quick_phones(created_at);

-- The company phones belong to while a team member helps them.
create or replace function remote_private.quick_phone_client() returns uuid
language sql stable security definer set search_path = '' as $$
    select c.id from public.clients c
    join public.organizations o on o.id = c.organization_id and o.is_active and o.mixel_org_id is not null
    where c.name = 'Mixel IT and Corporate Services (Remote devices)' and c.is_active limit 1;
$$;
revoke all on function remote_private.quick_phone_client() from public, anon, authenticated;

-- Ends claims older than 8 hours and forgets phones not seen for 90 days.
create or replace function remote_private.sweep_quick_phones() returns void
language plpgsql security definer set search_path = '' as $$
declare stale record;
begin
    for stale in
        select e.device_id, e.public_key, e.generation, q.claimed_by
        from remote_private.quick_phones q
        join public.remote_device_enrollments e on e.device_id = q.device_id and e.revoked_at is null
        where q.claimed_at < now() - interval '8 hours'
        for update of e
    loop
        update public.remote_device_enrollments set revoked_at = now(), generation = generation + 1 where device_id = stale.device_id;
        insert into remote_private.enrollment_audit(device_id, actor_id, action, previous_key, generation)
            select stale.device_id, coalesce(stale.claimed_by, e.enrolled_by), 'revoke', stale.public_key, stale.generation + 1
            from public.remote_device_enrollments e where e.device_id = stale.device_id;
    end loop;
    update remote_private.quick_phones set claimed_by = null, claimed_at = null where claimed_at < now() - interval '8 hours';
    delete from remote_private.quick_phones where last_seen < now() - interval '90 days' and device_id is null;
end;
$$;
revoke all on function remote_private.sweep_quick_phones() from public, anon, authenticated;

-- Phone (anonymous): its fixed ID, and once claimed, the registration to go online with.
create or replace function public.remote_quick_phone_register(device_name text, key_material bytea)
returns table(connection_id integer, device_id uuid, generation bigint, profile uuid, tenant uuid)
language plpgsql security definer set search_path = '' as $$
declare clean_name text := btrim(device_name); row_found remote_private.quick_phones; candidate bigint; org uuid; enrolled public.remote_device_enrollments;
begin
    if octet_length(key_material) is distinct from 32 or clean_name is null
        or char_length(clean_name) not between 1 and 60 or clean_name ~ '[[:cntrl:]]' then
        raise exception 'Invalid request' using errcode = '22023';
    end if;
    select * into row_found from remote_private.quick_phones q where q.public_key = key_material for update;
    if not found then
        -- Keep the service usable: at most 120 new phones in 10 minutes.
        if (select count(*) from remote_private.quick_phones q where q.created_at > now() - interval '10 minutes') >= 120 then
            raise exception 'Too many requests' using errcode = '54000';
        end if;
        perform remote_private.sweep_quick_phones();
        for attempt in 1..128 loop
            candidate := (('x' || pg_catalog.encode(pg_catalog.substring(pg_catalog.uuid_send(pg_catalog.gen_random_uuid()),1,4),'hex'))::bit(32)::bigint);
            if candidate >= 3600000000 then continue; end if;
            candidate := 100000000 + candidate % 900000000;
            continue when exists (select 1 from public.remote_device_enrollments e where e.connection_id = candidate)
                or exists (select 1 from remote_private.quick_phones q where q.connection_id = candidate);
            insert into remote_private.quick_phones(public_key, connection_id, device_name)
                values (key_material, candidate, clean_name) returning * into row_found;
            exit;
        end loop;
        if row_found.public_key is null then raise exception 'Connection identifier allocation exhausted' using errcode = '54000'; end if;
    else
        update remote_private.quick_phones q set last_seen = now(), device_name = clean_name where q.public_key = key_material;
    end if;
    select e.* into enrolled from public.remote_device_enrollments e
        where e.device_id = row_found.device_id and e.public_key = key_material and e.revoked_at is null;
    if row_found.claimed_by is not null and enrolled.device_id is not null and row_found.claimed_at > now() - interval '8 hours' then
        select o.id into org from public.devices d join public.clients c on c.id = d.client_id
            join public.organizations o on o.id = c.organization_id where d.id = enrolled.device_id;
        return query select row_found.connection_id, enrolled.device_id, enrolled.generation, row_found.claimed_by, org;
    else
        return query select row_found.connection_id, null::uuid, null::bigint, null::uuid, null::uuid;
    end if;
end;
$$;

-- Team member: claim the phone with this ID. Returns false when the ID is not a phone (an ordinary computer).
create or replace function public.remote_quick_phone_claim(connection integer)
returns boolean
language plpgsql security definer set search_path = '' as $$
declare actor uuid; phone remote_private.quick_phones; client uuid; created uuid; name_try text; enrolled public.remote_device_enrollments;
begin
    actor := remote_private.staff_actor();
    select * into phone from remote_private.quick_phones q where q.connection_id = connection for update;
    if not found then return false; end if;
    client := remote_private.quick_phone_client();
    if client is null then raise exception 'Company unavailable for Remote devices' using errcode = '42501'; end if;
    created := phone.device_id;
    if created is null or not exists (select 1 from public.devices d where d.id = created) then
        name_try := phone.device_name;
        for attempt in 1..50 loop
            exit when not exists (select 1 from public.devices d where d.client_id = client and lower(d.name) = lower(name_try));
            name_try := phone.device_name || ' (' || (attempt + 1) || ')';
        end loop;
        insert into public.devices(client_id, name, type, os) values (client, name_try, 'phone', 'other'::public.ism_device_os) returning id into created;
    end if;
    select e.* into enrolled from public.remote_device_enrollments e where e.device_id = created for update;
    if not found then
        insert into public.remote_device_enrollments(device_id, connection_id, public_key, enrolled_by)
            values (created, phone.connection_id, phone.public_key, actor) returning * into enrolled;
    else
        update public.remote_device_enrollments e set revoked_at = null, enrolled_by = actor,
            generation = case when e.revoked_at is null then e.generation else e.generation + 1 end
            where e.device_id = created returning * into enrolled;
    end if;
    insert into remote_private.enrollment_audit(device_id, actor_id, action, next_key, generation)
        values (created, actor, 'enroll', phone.public_key, enrolled.generation);
    update remote_private.quick_phones q set device_id = created, claimed_by = actor, claimed_at = now() where q.public_key = phone.public_key;
    return true;
end;
$$;

revoke all on function public.remote_quick_phone_register(text,bytea), public.remote_quick_phone_claim(integer) from public;
grant execute on function public.remote_quick_phone_register(text,bytea) to anon, authenticated;
grant execute on function public.remote_quick_phone_claim(integer) to authenticated;
commit;
