begin;

create table remote_private.personal_accounts (
    id uuid primary key default gen_random_uuid(),
    auth_user_id uuid not null unique references auth.users(id) on delete cascade,
    mixel_subject text not null unique check (char_length(mixel_subject) between 1 and 255),
    display_name text not null check (char_length(display_name) between 1 and 80),
    created_at timestamptz not null default now()
);

-- Both enrollment paths reserve IDs and keys in the same unique index. Keep
-- reservations after revocation so an old connection ID cannot be reassigned.
create table remote_private.connection_reservations (
    connection_id integer primary key check (connection_id between 100000000 and 999999999),
    public_key bytea not null unique check (octet_length(public_key) = 32),
    device_id uuid not null
);
lock table public.remote_device_enrollments in share row exclusive mode;
insert into remote_private.connection_reservations
select connection_id, public_key, device_id from public.remote_device_enrollments;

create function remote_private.reserve_legacy_connection()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
    if tg_op = 'INSERT' then
        insert into remote_private.connection_reservations values (new.connection_id, new.public_key, new.device_id);
    elsif new.public_key <> old.public_key then
        update remote_private.connection_reservations
           set public_key = new.public_key
         where connection_id = old.connection_id and device_id = old.device_id;
        if not found then raise exception 'Missing Remote connection reservation'; end if;
    end if;
    return new;
end
$$;
create trigger remote_reserve_legacy_connection
after insert or update of public_key on public.remote_device_enrollments
for each row execute function remote_private.reserve_legacy_connection();

create table remote_private.personal_devices (
    device_id uuid primary key,
    account_id uuid not null references remote_private.personal_accounts(id) on delete cascade,
    connection_id integer not null unique references remote_private.connection_reservations(connection_id),
    public_key bytea not null unique check (octet_length(public_key) = 32),
    name text not null check (char_length(name) between 1 and 80),
    os text not null check (os in ('macos','windows','linux')),
    generation bigint not null default 1 check (generation > 0),
    created_at timestamptz not null default now(),
    revoked_at timestamptz
);
create index personal_devices_account on remote_private.personal_devices(account_id, connection_id);
alter table remote_private.personal_accounts enable row level security;
alter table remote_private.connection_reservations enable row level security;
alter table remote_private.personal_devices enable row level security;
revoke all on remote_private.personal_accounts, remote_private.connection_reservations,
    remote_private.personal_devices from public, anon, authenticated;

-- Use the identity GoTrue obtained from the configured Mixel ID issuer.
-- An email match, user-editable metadata or an ISM role grants no access here.
create function remote_private.verified_mixel_subject(who uuid)
returns text language sql stable security definer set search_path = '' as $$
    select case when count(*) = 1 then min(i.provider_id) end
    from auth.identities i
    join auth.users u on u.id = i.user_id and u.deleted_at is null
        and (u.banned_until is null or u.banned_until <= now())
    where i.user_id = who
      and i.provider = 'keycloak'
      and i.identity_data->>'iss' in (
          'https://auth.mixel.ch/application/o/mixel-ism-user',
          'https://auth.mixel.ch/application/o/mixel-ism-user/'
      )
      and i.provider_id = i.identity_data->>'sub'
      and char_length(i.provider_id) between 1 and 255
      and i.identity_data->'email_verified' = 'true'::jsonb;
$$;

create function public.remote_personal_account()
returns table (profile_id uuid, display_name text)
language sql stable security definer set search_path = '' as $$
    select a.id, a.display_name
    from (select remote_private.verified_mixel_subject(auth.uid()) as subject) i
    left join remote_private.personal_accounts a
      on a.auth_user_id = auth.uid() and a.mixel_subject = i.subject
    where i.subject is not null;
$$;

create function public.remote_create_personal_account(requested_name text)
returns uuid language plpgsql security definer set search_path = '' as $$
declare
    subject text := remote_private.verified_mixel_subject(auth.uid());
    clean_name text := btrim(requested_name);
    account uuid;
begin
    if subject is null then raise exception 'Verified Mixel ID required' using errcode = '42501'; end if;
    if clean_name is null or char_length(clean_name) not between 1 and 80 or clean_name ~ '[[:cntrl:]]'
    then raise exception 'Invalid display name' using errcode = '22023'; end if;
    insert into remote_private.personal_accounts(auth_user_id, mixel_subject, display_name)
    values (auth.uid(), subject, clean_name)
    on conflict (auth_user_id) do nothing;
    select id into account from remote_private.personal_accounts
    where auth_user_id = auth.uid() and mixel_subject = subject;
    if account is null then raise exception 'Mixel identity changed' using errcode = '42501'; end if;
    return account;
end
$$;

create function public.remote_personal_devices()
returns table (device_id uuid, connection_id integer, generation bigint, public_key bytea,
    revoked_at timestamptz, name text, os text)
language sql stable security definer set search_path = '' as $$
    select d.device_id, d.connection_id, d.generation, d.public_key, d.revoked_at, d.name, d.os
    from public.remote_personal_account() a
    join remote_private.personal_devices d on d.account_id = a.profile_id
    where d.revoked_at is null order by d.connection_id;
$$;

create function public.remote_register_personal_device(device_name text, device_os text, key_material bytea)
returns integer language plpgsql security definer set search_path = '' as $$
declare
    account uuid;
    known remote_private.personal_devices;
    device uuid := gen_random_uuid();
    sample bigint;
    candidate integer;
    clean_name text := btrim(device_name);
begin
    select profile_id into account from public.remote_personal_account();
    if account is null then raise exception 'Set up a Remote account first' using errcode = '42501'; end if;
    if clean_name is null or char_length(clean_name) not between 1 and 80 or clean_name ~ '[[:cntrl:]]'
        or device_os is null or device_os not in ('macos','windows','linux')
        or octet_length(key_material) is distinct from 32
    then raise exception 'Invalid Remote device' using errcode = '22023'; end if;
    perform 1 from remote_private.personal_accounts where id = account for update;
    select * into known from remote_private.personal_devices where public_key = key_material;
    if found then
        if known.account_id <> account or known.revoked_at is not null
        then raise exception 'Device key already registered' using errcode = '42501'; end if;
        return known.connection_id;
    end if;
    if (select count(*) from remote_private.personal_devices where account_id = account) >= 32
    then raise exception 'Device limit reached' using errcode = '54000'; end if;
    for attempt in 1..128 loop
        sample := ('x' || encode(substring(uuid_send(gen_random_uuid()),1,4),'hex'))::bit(32)::bigint;
        if sample >= 3600000000 then continue; end if;
        candidate := 100000000 + (sample % 900000000)::integer;
        insert into remote_private.connection_reservations values (candidate, key_material, device)
        on conflict (connection_id) do nothing;
        if found then
            insert into remote_private.personal_devices(device_id, account_id, connection_id, public_key, name, os)
            values (device, account, candidate, key_material, clean_name, device_os);
            return candidate;
        end if;
    end loop;
    raise exception 'Connection ID allocation exhausted' using errcode = '54000';
end
$$;

create function remote_private.personal_support_pair(
    controller_profile uuid, controller_device uuid, controller_generation bigint, controller_key bytea,
    host_profile uuid, host_device uuid, host_generation bigint, host_key bytea
) returns boolean language sql stable security definer set search_path = '' as $$
    select controller_profile = host_profile and controller_device <> host_device and exists (
        select 1 from remote_private.personal_accounts a
        join remote_private.personal_devices c on c.account_id = a.id
        join remote_private.personal_devices h on h.account_id = a.id
        where a.id = controller_profile
          and remote_private.verified_mixel_subject(a.auth_user_id) = a.mixel_subject
          and c.device_id = controller_device and c.generation = controller_generation
          and c.public_key = controller_key and c.revoked_at is null
          and h.device_id = host_device and h.generation = host_generation
          and h.public_key = host_key and h.revoked_at is null
    );
$$;
revoke all on function remote_private.verified_mixel_subject(uuid),
    remote_private.reserve_legacy_connection(),
    remote_private.personal_support_pair(uuid,uuid,bigint,bytea,uuid,uuid,bigint,bytea)
from public, anon, authenticated;
grant execute on function remote_private.personal_support_pair(uuid,uuid,bigint,bytea,uuid,uuid,bigint,bytea)
to mixel_remote_policy_reader;
revoke all on function public.remote_personal_account(), public.remote_create_personal_account(text),
    public.remote_personal_devices(), public.remote_register_personal_device(text,text,bytea)
from public, anon, authenticated;
grant execute on function public.remote_personal_account(), public.remote_create_personal_account(text),
    public.remote_personal_devices(), public.remote_register_personal_device(text,text,bytea)
to authenticated;
commit;
