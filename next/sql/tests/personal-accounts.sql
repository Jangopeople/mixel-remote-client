begin;
insert into auth.users (id) values
    ('00000000-0000-0000-0000-000000000071'),
    ('00000000-0000-0000-0000-000000000072'),
    ('00000000-0000-0000-0000-000000000073'),
    ('00000000-0000-0000-0000-000000000074');
insert into auth.identities
select id, 'keycloak', id::text,
    jsonb_build_object('iss', case when id::text like '%74' then 'https://example.test'
        else 'https://auth.mixel.ch/application/o/mixel-ism-user/' end,
        'sub', id::text, 'email_verified', id::text not like '%73')
from auth.users;

set local role authenticated;
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000071', true);
select set_config('request.jwt.claims', '{"aal":"aal1"}', true);
do $$
declare
    profile uuid;
    first_id integer;
    second_id integer;
    windows_id integer;
    linux_id integer;
begin
    if (select count(*) from public.remote_personal_account() where profile_id is null) <> 1
    then raise exception 'Missing verified first-time Remote account'; end if;
    profile := public.remote_create_personal_account('  Michael Example  ');
    if profile is distinct from public.remote_create_personal_account('Other name')
       or (select display_name from public.remote_personal_account()) <> 'Michael Example'
    then raise exception 'Account setup is not idempotent'; end if;
    first_id := public.remote_register_personal_device('My Mac', 'macos', decode(repeat('ab',32),'hex'));
    second_id := public.remote_register_personal_device('Other Mac', 'macos', decode(repeat('cd',32),'hex'));
    windows_id := public.remote_register_personal_device('My Windows PC', 'windows', decode(repeat('ef',32),'hex'));
    linux_id := public.remote_register_personal_device('My Linux PC', 'linux', decode(repeat('23',32),'hex'));
    if first_id = second_id
        or first_id <> public.remote_register_personal_device('My Mac', 'macos', decode(repeat('ab',32),'hex'))
        or windows_id <> public.remote_register_personal_device('My Windows PC', 'windows', decode(repeat('ef',32),'hex'))
        or linux_id <> public.remote_register_personal_device('My Linux PC', 'linux', decode(repeat('23',32),'hex'))
        or (select count(distinct connection_id) from public.remote_personal_devices()) <> 4
        or not exists (select from public.remote_personal_devices() where connection_id = windows_id and os = 'windows')
        or not exists (select from public.remote_personal_devices() where connection_id = linux_id and os = 'linux')
    then raise exception 'Registration did not keep permanent, distinct IDs'; end if;
    begin
        perform count(*) from remote_private.personal_accounts;
        raise exception 'Authenticated client can query private profiles';
    exception when insufficient_privilege then null; end;
end $$;

select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000072', true);
select public.remote_create_personal_account('Second Remote user');
do $$
begin
    if exists(select from public.remote_personal_devices())
    then raise exception 'Personal directory leaked between accounts'; end if;
    begin
        perform public.remote_register_personal_device('Other account Mac','macos',decode(repeat('ab',32),'hex'));
        raise exception 'Another account registered the first device key';
    exception when insufficient_privilege then null; end;
end $$;
do $$
declare who text;
begin
    foreach who in array array['00000000-0000-0000-0000-000000000073','00000000-0000-0000-0000-000000000074'] loop
        perform set_config('request.jwt.claim.sub',who,true);
        if exists(select from public.remote_personal_account()) then raise exception 'Unverified identity reached account setup'; end if;
        begin
            perform public.remote_create_personal_account('Unverified');
            raise exception 'Unverified identity created an account';
        exception when insufficient_privilege then null; end;
    end loop;
end $$;

reset role;
do $$
declare first_device record; second_device record; other_profile uuid;
begin
    select * into strict first_device from remote_private.personal_devices where name = 'My Mac';
    select * into strict second_device from remote_private.personal_devices where name = 'Other Mac';
    select id into strict other_profile from remote_private.personal_accounts where display_name = 'Second Remote user';
    if not remote_private.personal_support_pair(first_device.account_id,first_device.device_id,1,first_device.public_key,
        second_device.account_id,second_device.device_id,1,second_device.public_key)
        or remote_private.personal_support_pair(other_profile,first_device.device_id,1,first_device.public_key,
        second_device.account_id,second_device.device_id,1,second_device.public_key)
        or remote_private.personal_support_pair(first_device.account_id,first_device.device_id,2,first_device.public_key,
        second_device.account_id,second_device.device_id,1,second_device.public_key)
    then raise exception 'Personal broker policy failed its device binding checks'; end if;
    update remote_private.personal_devices set revoked_at=now() where device_id=second_device.device_id;
    if remote_private.personal_support_pair(first_device.account_id,first_device.device_id,1,first_device.public_key,
        second_device.account_id,second_device.device_id,1,second_device.public_key)
    then raise exception 'Revoked personal device remained authorized'; end if;
end $$;
select 'PASS: verified account setup, permanent Mac, Windows and Linux IDs, cross-account isolation and broker revocation';
rollback;
