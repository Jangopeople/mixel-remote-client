-- Run after enrollment.sql in the isolated fixture. The first client is
-- active at the organization level and already has an enrolled device.
\set ON_ERROR_STOP on
begin;
update public.users set is_active = true
where auth_user_id = '00000000-0000-0000-0000-000000000042';
insert into public.devices values
    ('00000000-0000-0000-0000-000000000023','00000000-0000-0000-0000-000000000011');
create temporary table original_remote_id as
select connection_id from public.remote_device_enrollments
where device_id = '00000000-0000-0000-0000-000000000021';
grant select on original_remote_id to authenticated;
update public.clients set is_active = false
where id = '00000000-0000-0000-0000-000000000011';

set local role authenticated;
set local request.jwt.claims = '{"aal":"aal2"}';
set local request.jwt.claim.sub = '00000000-0000-0000-0000-000000000041';
do $$
begin
    if (select count(*) from public.remote_device_enrollments) <> 1
       or exists (select 1 from public.remote_device_enrollments
           where device_id = '00000000-0000-0000-0000-000000000021') then
        raise exception 'Inactive customer device remains available to staff';
    end if;
    begin
        perform public.remote_enroll_device('00000000-0000-0000-0000-000000000023',decode(repeat('04',32),'hex'));
        raise exception 'Enrollment accepted for an inactive client';
    exception when insufficient_privilege then null;
    end;
    begin
        perform public.remote_enroll_device('00000000-0000-0000-0000-000000000021',decode(repeat('03',32),'hex'));
        raise exception 'Existing enrollment returned for an inactive client';
    exception when insufficient_privilege then null;
    end;
    begin
        perform public.remote_rotate_device('00000000-0000-0000-0000-000000000021',3,decode(repeat('05',32),'hex'));
        raise exception 'Key rotation accepted for an inactive client';
    exception when insufficient_privilege then null;
    end;
    raise notice 'PASS: inactive client denied staff device lookup, enrollment, enrollment retry, and key rotation';
end $$;

set local request.jwt.claim.sub = '00000000-0000-0000-0000-000000000042';
do $$
begin
    if exists (select 1 from public.remote_device_enrollments) then
        raise exception 'Inactive client device remains available to tenant';
    end if;
    raise notice 'PASS: inactive client denied tenant device lookup';
end $$;

reset role;
update public.clients set is_active = true
where id = '00000000-0000-0000-0000-000000000011';
set local role authenticated;
do $$
begin
    if not exists (select 1 from public.remote_device_enrollments e
        join original_remote_id saved using (connection_id)
        where e.device_id = '00000000-0000-0000-0000-000000000021' and e.generation = 3) then
        raise exception 'Reactivation did not restore the same ID and enrollment generation';
    end if;
    raise notice 'PASS: reactivation retained the original Remote ID and device key generation';
end $$;
rollback;
