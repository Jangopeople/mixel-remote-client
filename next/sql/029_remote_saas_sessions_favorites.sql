-- Mixel Remote as SaaS: session reports, favorite devices and recently used devices.
--
-- Sessions: the connection broker records every SaaS session (a session whose support policy grant
-- named a tenant) once it is connected, and its end, and sends them every few seconds to
-- public.remote_session_report through PostgREST with the presence token (role
-- mixel_remote_presence, crates/signaling_server/src/sessions.rs). The report also carries the
-- complete list of the broker's running sessions at least once a minute, so sessions a broker
-- restart lost the end of are closed at the time they were last seen running. A transport restart
-- keeps the record; switching sides starts a new one. Owners and admins read every session of their
-- company, technicians and members their own (remote_saas_session_report, plan feature "reports");
-- the technician (or an owner/admin) adds a note to a session (remote_saas_session_note).
--
-- Favorites: each member keeps up to 200 favorite devices; recently used devices come from the
-- member's own sessions. Both only list devices the member may still reach.
--
-- Deletion (027): deleting a company deletes its sessions and favorites with the tenant. Deleting
-- an account or a profile keeps the company's session records (who reached which company computer
-- is the company's record) but removes the person from them (technician and note author become
-- unknown); the person's favorites go with the membership. The owner's export lists the sessions.
begin;
set local lock_timeout = '5s';

---------------------------------------------------------------------------------------------------
-- Tables

create table remote_saas.sessions (
    -- The broker's record id (stable across transport restarts).
    id uuid primary key,
    tenant_id uuid not null references remote_saas.tenants(id) on delete cascade,
    technician_id uuid references public.users(id) on delete set null,
    controller_device_id uuid not null,
    controller_connection integer not null check (controller_connection between 100000000 and 999999999),
    -- The helped computer. No foreign key: the record outlives a removed device.
    device_id uuid not null,
    host_connection integer not null check (host_connection between 100000000 and 999999999),
    -- The device's name when the session started (shown once the device is gone).
    device_name text not null check (char_length(device_name) <= 200),
    kind text not null check (kind in ('attended', 'unattended')),
    -- A colleague who joined a running session of another technician.
    colleague boolean not null default false,
    started_at timestamptz not null,
    ended_at timestamptz check (ended_at is null or ended_at >= started_at),
    -- Last time the broker listed the session as running.
    seen_at timestamptz,
    note text check (note is null or (char_length(note) between 1 and 4000)),
    note_by uuid references public.users(id) on delete set null,
    note_at timestamptz,
    recorded_at timestamptz not null default now()
);
create index sessions_tenant_started on remote_saas.sessions(tenant_id, started_at desc);
create index sessions_technician_started on remote_saas.sessions(technician_id, started_at desc);
create index sessions_open on remote_saas.sessions(started_at) where ended_at is null;

create table remote_saas.favorites (
    user_id uuid not null references public.users(id) on delete cascade,
    device_id uuid not null references public.devices(id) on delete cascade,
    tenant_id uuid not null references remote_saas.tenants(id) on delete cascade,
    created_at timestamptz not null default now(),
    primary key (user_id, device_id),
    -- Leaving the company (or being removed) drops the person's favorites.
    foreign key (tenant_id, user_id) references remote_saas.members(tenant_id, user_id) on delete cascade
);
create index favorites_tenant on remote_saas.favorites(tenant_id);

do $$ declare t text; begin
    foreach t in array array['sessions', 'favorites'] loop
        execute format('alter table remote_saas.%I enable row level security', t);
        execute format('revoke all on remote_saas.%I from public, anon, authenticated', t);
    end loop;
end $$;

-- Session reports are part of every paid plan (trial companies run on Business).
update remote_saas.plans set features = features || jsonb_build_object('reports', code <> 'personal');

---------------------------------------------------------------------------------------------------
-- Helpers

create function remote_saas.json_uuid(v jsonb) returns uuid
language sql immutable set search_path = '' as $$
    select case when jsonb_typeof(v) = 'string'
        and (v #>> '{}') ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        then (v #>> '{}')::uuid end;
$$;

-- A Remote ID (connection id) or null.
create function remote_saas.json_connection(v jsonb) returns integer
language sql immutable set search_path = '' as $$
    select case when jsonb_typeof(v) = 'number' and (v #>> '{}') ~ '^[1-9][0-9]{8}$'
        then (v #>> '{}')::integer end;
$$;

-- Unix seconds from the broker, clamped to the last day (a broker clock or a long retry never
-- writes times in the future or far in the past). Null when not a number.
create function remote_saas.json_time(v jsonb) returns timestamptz
language sql stable set search_path = '' as $$
    select case when jsonb_typeof(v) = 'number' and (v #>> '{}') ~ '^[0-9]{1,12}(\.[0-9]+)?$'
        then greatest(least(pg_catalog.to_timestamp((v #>> '{}')::double precision), now()),
                      now() - interval '1 day') end;
$$;

create function remote_saas.json_bool(v jsonb) returns boolean
language sql immutable set search_path = '' as $$
    select case when jsonb_typeof(v) = 'boolean' then (v #>> '{}')::boolean end;
$$;

-- A session note: trimmed, CR LF as LF, empty clears it (null). At most 4000 characters, no control
-- characters other than newline and tab.
create function remote_saas.clean_note(requested text) returns text
language plpgsql immutable set search_path = '' as $$
declare clean text := btrim(replace(coalesce(requested, ''), E'\r\n', E'\n'), E' \n\t');
begin
    if clean = '' then return null; end if;
    if char_length(clean) > 4000 or translate(clean, E'\n\t', '') ~ '[[:cntrl:]]' then
        raise exception 'Enter a note of at most 4000 characters' using errcode = '22023';
    end if;
    return clean;
end;
$$;

do $$ declare f text; begin
    for f in select p.oid::regprocedure::text from pg_proc p
             where p.pronamespace = 'remote_saas'::regnamespace
               and p.proname in ('json_uuid', 'json_connection', 'json_time', 'json_bool', 'clean_note') loop
        execute format('revoke all on function %s from public, anon, authenticated', f);
    end loop;
end $$;

---------------------------------------------------------------------------------------------------
-- Broker: session events (role mixel_remote_presence)

-- {"events": [{"type": "start", "id", "tenant", "technician", "controller_device",
--              "controller_connection", "device", "connection", "unattended", "colleague", "at"},
--             {"type": "end", "id", "at"}],
--  "open": [record ids]}   -- optional: every record the broker is running right now.
-- A malformed report is refused as a whole (22023). Start events of an unknown tenant, a device
-- outside the tenant or a technician who is not a member are skipped. Repeated events change
-- nothing; an end never moves a session's end backwards. Returns the number of events applied.
create function public.remote_session_report(report jsonb)
returns integer language plpgsql security definer set search_path = '' as $$
declare e jsonb; v_type text; v_id uuid; v_at timestamptz; applied integer := 0; changed integer;
        v_tenant uuid; v_technician uuid; v_controller_device uuid; v_controller_connection integer;
        v_device uuid; v_host_connection integer; v_unattended boolean; v_colleague boolean;
        t remote_saas.tenants; device_label text; open_ids uuid[];
begin
    if report is null or jsonb_typeof(report) <> 'object'
        or exists (select 1 from jsonb_object_keys(report) k where k not in ('events', 'open'))
        or jsonb_typeof(coalesce(report->'events', '[]'::jsonb)) <> 'array'
        or jsonb_array_length(coalesce(report->'events', '[]'::jsonb)) > 1000
        or (report ? 'open' and (jsonb_typeof(report->'open') <> 'array' or jsonb_array_length(report->'open') > 20000)) then
        raise exception 'Invalid session report' using errcode = '22023';
    end if;
    if report ? 'open' then
        select array_agg(remote_saas.json_uuid(x)) into open_ids from jsonb_array_elements(report->'open') x;
        open_ids := coalesce(open_ids, '{}');
        if array_position(open_ids, null) is not null then
            raise exception 'Invalid session report' using errcode = '22023';
        end if;
    end if;
    -- Validate every event before applying any.
    for e in select x from jsonb_array_elements(coalesce(report->'events', '[]'::jsonb)) x loop
        v_type := case when jsonb_typeof(e) = 'object' then e->>'type' end;
        if v_type is null or remote_saas.json_uuid(e->'id') is null or remote_saas.json_time(e->'at') is null
            or (v_type = 'end' and exists (select 1 from jsonb_object_keys(e) k where k not in ('type', 'id', 'at')))
            or (v_type = 'start' and (
                exists (select 1 from jsonb_object_keys(e) k where k not in ('type', 'id', 'tenant', 'technician',
                    'controller_device', 'controller_connection', 'device', 'connection', 'unattended', 'colleague', 'at'))
                or remote_saas.json_uuid(e->'tenant') is null or remote_saas.json_uuid(e->'technician') is null
                or remote_saas.json_uuid(e->'controller_device') is null or remote_saas.json_uuid(e->'device') is null
                or remote_saas.json_connection(e->'controller_connection') is null
                or remote_saas.json_connection(e->'connection') is null
                or remote_saas.json_bool(e->'unattended') is null or remote_saas.json_bool(e->'colleague') is null))
            or v_type not in ('start', 'end') then
            raise exception 'Invalid session report' using errcode = '22023';
        end if;
    end loop;
    for e in select x from jsonb_array_elements(coalesce(report->'events', '[]'::jsonb)) x loop
        v_id := remote_saas.json_uuid(e->'id');
        v_at := remote_saas.json_time(e->'at');
        if e->>'type' = 'start' then
            v_tenant := remote_saas.json_uuid(e->'tenant');
            v_technician := remote_saas.json_uuid(e->'technician');
            v_controller_device := remote_saas.json_uuid(e->'controller_device');
            v_controller_connection := remote_saas.json_connection(e->'controller_connection');
            v_device := remote_saas.json_uuid(e->'device');
            v_host_connection := remote_saas.json_connection(e->'connection');
            v_unattended := remote_saas.json_bool(e->'unattended');
            v_colleague := remote_saas.json_bool(e->'colleague');
            select * into t from remote_saas.tenants x where x.id = v_tenant;
            continue when not found;
            select left(coalesce(dm.display_name, d.name), 200) into device_label
                from public.devices d left join remote_saas.device_meta dm on dm.device_id = d.id
                where d.id = v_device and d.client_id = t.client_id;
            continue when not found;
            continue when not exists (select 1 from remote_saas.members m where m.tenant_id = t.id and m.user_id = v_technician);
            insert into remote_saas.sessions(id, tenant_id, technician_id, controller_device_id, controller_connection,
                    device_id, host_connection, device_name, kind, colleague, started_at)
            values (v_id, t.id, v_technician, v_controller_device, v_controller_connection, v_device, v_host_connection,
                    coalesce(device_label, ''), case when v_unattended then 'unattended' else 'attended' end, v_colleague, v_at)
            on conflict (id) do nothing;
            get diagnostics changed = row_count;
            applied := applied + changed;
        else
            update remote_saas.sessions s set ended_at = greatest(s.started_at, v_at)
                where s.id = v_id and (s.ended_at is null or s.ended_at < greatest(s.started_at, v_at));
            get diagnostics changed = row_count;
            applied := applied + changed;
        end if;
    end loop;
    if open_ids is not null then
        update remote_saas.sessions s set seen_at = now() where s.id = any(open_ids) and s.ended_at is null;
        -- Running here but not at the broker any more (its end was lost, e.g. a broker restart).
        update remote_saas.sessions s set ended_at = greatest(s.started_at, coalesce(s.seen_at, s.started_at))
            where s.ended_at is null and not (s.id = any(open_ids)) and s.started_at < now() - interval '30 seconds';
    end if;
    return applied;
end;
$$;
revoke all on function public.remote_session_report(jsonb) from public, anon, authenticated;
grant execute on function public.remote_session_report(jsonb) to mixel_remote_presence;

---------------------------------------------------------------------------------------------------
-- Session reports (members)

-- Sessions started in [from_at, to_at) (default: the last 30 days up to now, at most 366 days), newest first,
-- at most 5000. Owners and admins: every session of the company; technicians and members: their own
-- (asking for another technician's sessions is refused). Running sessions have no end; their
-- duration counts up to now. Read-only companies can still read their reports.
create function public.remote_saas_session_report(tenant uuid, from_at timestamptz default null,
        to_at timestamptz default null, technician uuid default null, device uuid default null)
returns table (session_id uuid, technician_id uuid, technician_name text, technician_email text, device_id uuid,
               device_name text, connection_id integer, started_at timestamptz, ended_at timestamptz,
               duration_seconds integer, kind text, colleague boolean, note text)
language plpgsql stable security definer set search_path = '' as $$
declare me remote_saas.members; t remote_saas.tenants; range_to timestamptz := coalesce(to_at, now());
        range_from timestamptz; who uuid := technician;
begin
    me := remote_saas.actor(tenant, '{owner,admin,technician,member}', 'read');
    perform remote_saas.require_feature(tenant, 'reports', 'Session reports need a paid plan');
    range_from := coalesce(from_at, range_to - interval '30 days');
    if range_from > range_to or range_to - range_from > interval '366 days' then
        raise exception 'Choose a period of at most 366 days' using errcode = '22023';
    end if;
    if me.role not in ('owner', 'admin') then
        if who is not null and who <> me.user_id then
            raise exception 'Not allowed for this company' using errcode = '42501';
        end if;
        who := me.user_id;
    end if;
    select * into t from remote_saas.tenants x where x.id = tenant;
    return query
        select s.id, s.technician_id, u.name, u.email, s.device_id,
               coalesce(dm.display_name, d.name, s.device_name), s.host_connection, s.started_at, s.ended_at,
               least(extract(epoch from coalesce(s.ended_at, now()) - s.started_at), 2147483647)::integer,
               s.kind, s.colleague, s.note
        from remote_saas.sessions s
        left join public.users u on u.id = s.technician_id
        left join public.devices d on d.id = s.device_id and d.client_id = t.client_id
        left join remote_saas.device_meta dm on dm.device_id = d.id
        where s.tenant_id = tenant and s.started_at >= range_from and (to_at is null or s.started_at < to_at)
          and (who is null or s.technician_id = who)
          and (device is null or s.device_id = device)
        order by s.started_at desc, s.id
        limit 5000;
end;
$$;

-- Note on a session: by its record id, or (after a session ended in the app, which knows only the
-- computer's Remote ID) the caller's newest session to that Remote ID in the last 24 hours. The
-- session's technician may write it; owners and admins any session's. An empty note clears it.
create function public.remote_saas_session_note(tenant uuid, note text, session uuid default null,
                                               connection integer default null)
returns uuid language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; s remote_saas.sessions; clean text := remote_saas.clean_note(note);
begin
    me := remote_saas.actor(tenant, '{owner,admin,technician,member}');
    perform remote_saas.require_feature(tenant, 'reports', 'Session reports need a paid plan');
    if (session is null) = (connection is null) then
        raise exception 'Choose one session' using errcode = '22023';
    end if;
    if session is not null then
        select * into s from remote_saas.sessions x where x.id = session and x.tenant_id = tenant for update;
        if not found then raise exception 'Session not found' using errcode = 'P0002'; end if;
        if s.technician_id is distinct from me.user_id and me.role not in ('owner', 'admin') then
            raise exception 'Not allowed for this session' using errcode = '42501';
        end if;
    else
        select * into s from remote_saas.sessions x
            where x.tenant_id = tenant and x.technician_id = me.user_id and x.host_connection = connection
              and x.started_at > now() - interval '24 hours'
            order by x.started_at desc, x.id limit 1 for update;
        if not found then raise exception 'No recent session to this computer' using errcode = 'P0002'; end if;
    end if;
    update remote_saas.sessions x set note = clean, note_by = case when clean is null then null else me.user_id end,
            note_at = case when clean is null then null else now() end
        where x.id = s.id;
    perform remote_saas.audit(tenant, me.user_id, 'session.note', s.id::text,
        jsonb_build_object('cleared', clean is null, 'length', coalesce(char_length(clean), 0)));
    return s.id;
end;
$$;

---------------------------------------------------------------------------------------------------
-- Favorites and recently used devices (every role, every plan)

create function public.remote_saas_set_favorite(tenant uuid, device uuid, favorite boolean)
returns boolean language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members;
begin
    me := remote_saas.actor(tenant, '{owner,admin,technician,member}', 'read');
    if favorite is null then raise exception 'Choose favorite or not' using errcode = '22023'; end if;
    if not favorite then
        delete from remote_saas.favorites f where f.user_id = me.user_id and f.tenant_id = tenant and f.device_id = device;
        return false;
    end if;
    perform remote_saas.tenant_device(tenant, device);
    if not remote_saas.can_access_device(me.user_id, device) then
        raise exception 'Device unavailable' using errcode = '42501';
    end if;
    -- Serializes one person's concurrent additions so the limit holds.
    perform 1 from remote_saas.members m where m.tenant_id = tenant and m.user_id = me.user_id for update;
    if exists (select 1 from remote_saas.favorites f where f.user_id = me.user_id and f.device_id = device) then
        return true;
    end if;
    if (select count(*) from remote_saas.favorites f where f.user_id = me.user_id) >= 200 then
        raise exception 'At most 200 favorites' using errcode = '54000';
    end if;
    insert into remote_saas.favorites(user_id, device_id, tenant_id) values (me.user_id, device, tenant)
    on conflict (user_id, device_id) do nothing;
    return true;
end;
$$;

create function public.remote_saas_favorites(tenant uuid)
returns table (device_id uuid, created_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
declare me remote_saas.members;
begin
    me := remote_saas.actor(tenant, '{owner,admin,technician,member}', 'read');
    return query select f.device_id, f.created_at from remote_saas.favorites f
        where f.user_id = me.user_id and f.tenant_id = tenant and remote_saas.can_access_device(me.user_id, f.device_id)
        order by f.created_at desc, f.device_id;
end;
$$;

-- The devices of the caller's own most recent sessions (one row per device), newest first.
create function public.remote_saas_recent_devices(tenant uuid, max_count integer default 10)
returns table (device_id uuid, last_connected_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
declare me remote_saas.members; wanted integer := coalesce(max_count, 10);
begin
    me := remote_saas.actor(tenant, '{owner,admin,technician,member}', 'read');
    if wanted not between 1 and 50 then
        raise exception 'Choose 1 to 50 devices' using errcode = '22023';
    end if;
    return query
        select r.device_id, r.last_at from (
            select s.device_id, max(s.started_at) as last_at from remote_saas.sessions s
            where s.tenant_id = tenant and s.technician_id = me.user_id
              and s.started_at > now() - interval '366 days'
            group by s.device_id) r
        where remote_saas.can_access_device(me.user_id, r.device_id)
        order by r.last_at desc, r.device_id
        limit wanted;
end;
$$;

---------------------------------------------------------------------------------------------------
-- Owner export (027) with the session records

alter function public.remote_saas_export(uuid) set schema remote_saas;
alter function remote_saas.remote_saas_export(uuid) rename to export_core;
revoke all on function remote_saas.export_core(uuid) from public, anon, authenticated;

create function public.remote_saas_export(tenant uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare t remote_saas.tenants; core jsonb;
begin
    -- Checks the caller (owner) and audits the export.
    core := remote_saas.export_core(tenant);
    select * into t from remote_saas.tenants x where x.id = tenant;
    return core || jsonb_build_object('sessions', coalesce((select jsonb_agg(jsonb_build_object(
            'session_id', s.id, 'technician', u.email, 'device_id', s.device_id,
            'device_name', coalesce(dm.display_name, d.name, s.device_name), 'remote_id', s.host_connection,
            'technician_remote_id', s.controller_connection, 'kind', s.kind, 'colleague', s.colleague,
            'started_at', s.started_at, 'ended_at', s.ended_at, 'note', s.note, 'note_by', nu.email,
            'note_at', s.note_at) order by s.started_at, s.id)
        from remote_saas.sessions s
        left join public.users u on u.id = s.technician_id
        left join public.users nu on nu.id = s.note_by
        left join public.devices d on d.id = s.device_id and d.client_id = t.client_id
        left join remote_saas.device_meta dm on dm.device_id = d.id
        where s.tenant_id = tenant), '[]'::jsonb));
end;
$$;

---------------------------------------------------------------------------------------------------
-- Grants

revoke all on function public.remote_saas_session_report(uuid, timestamptz, timestamptz, uuid, uuid),
    public.remote_saas_session_note(uuid, text, uuid, integer), public.remote_saas_set_favorite(uuid, uuid, boolean),
    public.remote_saas_favorites(uuid), public.remote_saas_recent_devices(uuid, integer),
    public.remote_saas_export(uuid) from public, anon, authenticated;
grant execute on function public.remote_saas_session_report(uuid, timestamptz, timestamptz, uuid, uuid),
    public.remote_saas_session_note(uuid, text, uuid, integer), public.remote_saas_set_favorite(uuid, uuid, boolean),
    public.remote_saas_favorites(uuid), public.remote_saas_recent_devices(uuid, integer),
    public.remote_saas_export(uuid) to authenticated;

commit;
notify pgrst, 'reload schema';
