-- Quick Support for phones: the team member who created a support code can ask which phone
-- registered with it, so their app can offer "Connect" without the person reading a number aloud.
begin;
create or replace function public.remote_support_code_device(code text)
returns table(connection_id integer, name text)
language plpgsql stable security definer set search_path = '' as $$
declare actor uuid; clean_code text;
begin
    actor := remote_private.staff_actor();
    clean_code := upper(regexp_replace(coalesce(code, ''), '[^A-Za-z0-9]', '', 'g'));
    if char_length(clean_code) <> 12 then
        raise exception 'Invalid support request' using errcode = '22023';
    end if;
    return query
        select e.connection_id, d.name::text
        from remote_private.support_codes s
        join public.remote_device_enrollments e on e.device_id = s.device_id and e.revoked_at is null
        join public.devices d on d.id = s.device_id
        where s.code_hash = pg_catalog.sha256(convert_to(clean_code, 'UTF8')) and s.created_by = actor;
end;
$$;
revoke all on function public.remote_support_code_device(text) from public;
grant execute on function public.remote_support_code_device(text) to authenticated;
commit;
