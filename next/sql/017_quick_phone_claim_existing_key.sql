-- Quick Support for phones: a phone whose key already has a registration (for example from the
-- earlier support-code version) is taken over when a team member claims its ID, instead of
-- failing on the unique key. The registration then carries the ID the phone shows.
begin;
create or replace function public.remote_quick_phone_claim(connection integer)
returns boolean
language plpgsql security definer set search_path = '' as $$
declare actor uuid; phone remote_private.quick_phones; client uuid; created uuid; name_try text; enrolled public.remote_device_enrollments; existing public.remote_device_enrollments;
begin
    actor := remote_private.staff_actor();
    select * into phone from remote_private.quick_phones q where q.connection_id = connection for update;
    if not found then return false; end if;
    client := remote_private.quick_phone_client();
    if client is null then raise exception 'Company unavailable for Remote devices' using errcode = '42501'; end if;
    created := phone.device_id;
    -- The same key registered before (another device record): take that registration over.
    select e.* into existing from public.remote_device_enrollments e where e.public_key = phone.public_key for update;
    if found and (created is null or existing.device_id <> created) then
        created := existing.device_id;
        update public.remote_device_enrollments e
            set connection_id = phone.connection_id, revoked_at = null, enrolled_by = actor, generation = e.generation + 1
            where e.device_id = created returning * into enrolled;
        update public.devices d set client_id = client where d.id = created;
        update remote_private.support_codes s set device_id = null where s.device_id = created;
        insert into remote_private.enrollment_audit(device_id, actor_id, action, next_key, generation)
            values (created, actor, 'enroll', phone.public_key, enrolled.generation);
        update remote_private.quick_phones q set device_id = created, claimed_by = actor, claimed_at = now() where q.public_key = phone.public_key;
        return true;
    end if;
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
        update public.remote_device_enrollments e set revoked_at = null, enrolled_by = actor, connection_id = phone.connection_id,
            generation = case when e.revoked_at is null and e.connection_id = phone.connection_id then e.generation else e.generation + 1 end
            where e.device_id = created returning * into enrolled;
    end if;
    insert into remote_private.enrollment_audit(device_id, actor_id, action, next_key, generation)
        values (created, actor, 'enroll', phone.public_key, enrolled.generation);
    update remote_private.quick_phones q set device_id = created, claimed_by = actor, claimed_at = now() where q.public_key = phone.public_key;
    return true;
end;
$$;
revoke all on function public.remote_quick_phone_claim(integer) from public;
grant execute on function public.remote_quick_phone_claim(integer) to authenticated;
commit;
