-- Mixel Remote as SaaS: online status / last seen of devices, and renaming a company.
--
-- Presence: the connection broker reports the connection IDs of the enrolled devices connected
-- right now, every 30 s (crates/signaling_server/src/presence.rs), through PostgREST with a token of
-- role mixel_remote_presence, which can execute public.remote_presence_report and nothing else.
-- Tenant members read the state of the devices they may reach with remote_saas_device_presence.
-- A device is "online" when the latest report contained it; "unknown" while the broker has not
-- reported for 3 minutes (so a broker outage never shows every device as offline).
begin;
set local lock_timeout = '5s';

create table remote_private.device_presence (
    connection_id integer primary key check (connection_id between 100000000 and 999999999),
    last_seen timestamptz not null,
    online_since timestamptz not null
);
create table remote_private.presence_reports (
    reporter text primary key check (reporter = 'broker'),
    reported_at timestamptz not null,
    online integer not null
);
alter table remote_private.device_presence enable row level security;
alter table remote_private.presence_reports enable row level security;
revoke all on remote_private.device_presence, remote_private.presence_reports from public, anon, authenticated;

do $$ begin
    create role mixel_remote_presence nologin;
exception when duplicate_object then null; end $$;
grant mixel_remote_presence to authenticator;
-- PostgREST runs public.enforce_staff_mfa_request before every request (db-pre-request).
grant execute on function public.enforce_staff_mfa_request() to mixel_remote_presence;

-- The broker's report. Throttled: a report within 10 s of the previous one is ignored.
create function public.remote_presence_report(online integer[])
returns integer language plpgsql security definer set search_path = '' as $$
declare previous timestamptz; ids integer[];
begin
    if online is null or cardinality(online) > 20000 then
        raise exception 'Invalid presence report' using errcode = '22023';
    end if;
    select reported_at into previous from remote_private.presence_reports where reporter = 'broker' for update;
    if previous is not null and previous > now() - interval '10 seconds' then
        return 0;
    end if;
    select coalesce(array_agg(distinct x), '{}') into ids from unnest(online) x where x between 100000000 and 999999999;
    -- Devices that were in the previous report keep their online_since; the others start now.
    insert into remote_private.device_presence as p (connection_id, last_seen, online_since)
    select x, now(), now() from unnest(ids) x
    on conflict (connection_id) do update set
        last_seen = excluded.last_seen,
        online_since = case when previous is not null and p.last_seen >= previous - interval '1 second'
                            then p.online_since else excluded.online_since end;
    insert into remote_private.presence_reports(reporter, reported_at, online) values ('broker', now(), cardinality(ids))
    on conflict (reporter) do update set reported_at = excluded.reported_at, online = excluded.online;
    return cardinality(ids);
end;
$$;
revoke all on function public.remote_presence_report(integer[]) from public, anon, authenticated;
grant execute on function public.remote_presence_report(integer[]) to mixel_remote_presence;

-- 'online' | 'offline' (seen before) | 'never' (not seen since presence is recorded) | 'unknown'.
create function remote_saas.presence_status(seen timestamptz, reported timestamptz) returns text
language sql stable set search_path = '' as $$
    select case
        when reported is null or reported < now() - interval '3 minutes' then 'unknown'
        when seen is not null and seen >= reported - interval '1 second' then 'online'
        when seen is not null then 'offline'
        else 'never' end;
$$;
revoke all on function remote_saas.presence_status(timestamptz, timestamptz) from public, anon, authenticated;

-- Status of the devices of a tenant that the caller may reach (same rules as remote_saas_devices).
create function public.remote_saas_device_presence(tenant uuid)
returns table (device_id uuid, connection_id integer, status text, last_seen timestamptz, online_since timestamptz,
               checked_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
declare me remote_saas.members; t remote_saas.tenants; reported timestamptz;
begin
    me := remote_saas.actor(tenant, '{owner,admin,technician,member}', 'read');
    select * into t from remote_saas.tenants where id = tenant;
    select r.reported_at into reported from remote_private.presence_reports r where r.reporter = 'broker';
    return query
        select d.id, e.connection_id,
               case when e.connection_id is null or e.revoked_at is not null then 'not_enrolled'
                    else remote_saas.presence_status(p.last_seen, reported) end,
               p.last_seen,
               case when remote_saas.presence_status(p.last_seen, reported) = 'online' then p.online_since end,
               reported
        from public.devices d
        left join public.remote_device_enrollments e on e.device_id = d.id
        left join remote_private.device_presence p on p.connection_id = e.connection_id
        where d.client_id = t.client_id and remote_saas.can_access_device(me.user_id, d.id)
        order by d.id
        limit 2000;
end;
$$;
revoke all on function public.remote_saas_device_presence(uuid) from public, anon, authenticated;
grant execute on function public.remote_saas_device_presence(uuid) to authenticated;

-- Rename a company (owner/admin). The URL name (slug) stays, so links keep working.
create function public.remote_saas_rename_tenant(tenant uuid, company_name text)
returns text language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; t remote_saas.tenants; clean text := btrim(company_name);
begin
    me := remote_saas.actor(tenant, '{owner,admin}');
    if clean is null or char_length(clean) not between 2 and 80 or clean ~ '[[:cntrl:]]' then
        raise exception 'Enter a company name of 2 to 80 characters' using errcode = '22023';
    end if;
    select * into t from remote_saas.tenants where id = tenant for update;
    if t.name = clean then return clean; end if;
    update remote_saas.tenants set name = clean where id = tenant;
    update public.organizations set name = clean, updated_at = now() where id = t.organization_id;
    perform remote_saas.audit(tenant, me.user_id, 'tenant.rename', clean, jsonb_build_object('from', t.name, 'to', clean));
    return clean;
end;
$$;
revoke all on function public.remote_saas_rename_tenant(uuid, text) from public, anon, authenticated;
grant execute on function public.remote_saas_rename_tenant(uuid, text) to authenticated;

commit;
notify pgrst, 'reload schema';
