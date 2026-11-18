-- Mixel Remote 2.2.0 server work (contract: mixel-craft/docs/mixel-remote-2.2-server-contract.md).
--
-- 1. Company switches for terminal, elevated terminal, task manager, scripts (default OFF; tasks_unattended and
--    scripts_four_eyes default ON as the owner decided), effective only with a paid plan.
-- 2. Script library: scripts, immutable versions, four-eyes approval (the author never approves their own while the
--    company switch is on), revoke, archive.
-- 3. Script runs: "session" tickets (signed by mixel-admin) and fleet runs (queued per device, signed at delivery,
--    per-device status, timeouts, cancel, 4 kB of output).
-- 4. One audit table for terminal, task manager, scripts, runs and policy changes, with list and detail RPCs.
-- Admin rights and tenant scoping are decided here, in SQL (security definer, search_path = ''); the apps never send
-- a tenant id that is trusted. Error hints are stable keys for the apps.
begin;
set local lock_timeout = '5s';

---------------------------------------------------------------------------------------------------
-- Tables

create table remote_private.tool_policies (
    client_id uuid primary key references public.clients(id) on delete cascade,
    terminal boolean not null default false,
    terminal_elevate text not null default 'none' check (terminal_elevate in ('none', 'admins', 'technicians')),
    tasks boolean not null default false,
    tasks_unattended boolean not null default true,
    scripts boolean not null default false,
    scripts_four_eyes boolean not null default true,
    updated_by uuid references public.users(id) on delete set null,
    updated_by_label text not null check (char_length(updated_by_label) between 1 and 200),
    updated_at timestamptz not null default now()
);

create table remote_private.tool_scripts (
    id uuid primary key default gen_random_uuid(),
    client_id uuid not null references public.clients(id) on delete cascade,
    name text not null check (char_length(name) between 1 and 80 and name = btrim(name) and name !~ '[[:cntrl:]]'),
    description text not null default '' check (char_length(description) <= 500 and description !~ '[\x00-\x08\x0b-\x1f\x7f]'),
    created_by uuid references public.users(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    archived_at timestamptz,
    latest_version integer not null default 0
);
create unique index tool_scripts_name on remote_private.tool_scripts(client_id, lower(name)) where archived_at is null;
create index tool_scripts_client on remote_private.tool_scripts(client_id, updated_at desc);

create table remote_private.tool_script_versions (
    id uuid primary key default gen_random_uuid(),
    script_id uuid not null references remote_private.tool_scripts(id) on delete cascade,
    version integer not null check (version >= 1),
    os text[] not null check (cardinality(os) between 1 and 3 and os <@ array['windows', 'macos', 'linux']),
    interpreter text not null check (interpreter in ('powershell', 'sh')),
    params jsonb not null default '[]'::jsonb check (jsonb_typeof(params) = 'array'),
    body text not null check (char_length(body) between 1 and 16000),
    sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
    timeout_seconds integer not null default 300 check (timeout_seconds between 1 and 3600),
    author_id uuid references public.users(id) on delete set null,
    author_name text not null,
    created_at timestamptz not null default now(),
    approved_by uuid references public.users(id) on delete set null,
    approved_by_name text,
    approved_at timestamptz,
    approval_mode text check (approval_mode in ('second_admin', 'four_eyes_off')),
    revoked_at timestamptz,
    revoked_by_name text,
    revoke_reason text check (char_length(revoke_reason) <= 300),
    unique (script_id, version),
    check ((approved_at is null) = (approval_mode is null))
);

-- Content never changes; approval and revocation only move forward.
create function remote_private.tool_version_guard() returns trigger
language plpgsql set search_path = '' as $$
begin
    if (new.script_id, new.version, new.os, new.interpreter, new.params, new.body, new.sha256, new.timeout_seconds, new.author_id, new.created_at)
        is distinct from (old.script_id, old.version, old.os, old.interpreter, old.params, old.body, old.sha256, old.timeout_seconds, old.author_id, old.created_at) then
        raise exception 'Script versions cannot be edited; save a new version' using errcode = '55000';
    end if;
    if old.approved_at is not null and (new.approved_at is distinct from old.approved_at or new.approved_by is distinct from old.approved_by) then
        raise exception 'An approval cannot be changed' using errcode = '55000';
    end if;
    if old.revoked_at is not null and new.revoked_at is distinct from old.revoked_at then
        raise exception 'A revocation cannot be changed' using errcode = '55000';
    end if;
    return new;
end;
$$;
create trigger tool_version_guard before update on remote_private.tool_script_versions
    for each row execute function remote_private.tool_version_guard();

create table remote_private.tool_runs (
    id uuid primary key default gen_random_uuid(),
    seq bigint generated always as identity,
    client_id uuid not null references public.clients(id) on delete cascade,
    script_id uuid not null references remote_private.tool_scripts(id) on delete cascade,
    version_id uuid not null references remote_private.tool_script_versions(id) on delete cascade,
    mode text not null check (mode in ('session', 'fleet')),
    requested_by uuid references public.users(id) on delete set null,
    requested_by_name text not null,
    reason text check (char_length(reason) <= 300),
    -- Ordered [{name, value}] exactly as signed. Secret values are removed once every command was delivered.
    params jsonb not null default '[]'::jsonb,
    ticket_taken_at timestamptz,
    cancelled_at timestamptz,
    created_at timestamptz not null default now()
);
create index tool_runs_client on remote_private.tool_runs(client_id, seq desc);
create index tool_runs_user on remote_private.tool_runs(requested_by, created_at desc);

create table remote_private.tool_run_devices (
    run_id uuid not null references remote_private.tool_runs(id) on delete cascade,
    -- No foreign key: the record outlives a removed device.
    device_id uuid not null,
    device_name text not null,
    os text not null,
    connection_id integer,
    status text not null check (status in ('queued', 'running', 'succeeded', 'failed', 'timeout', 'skipped', 'cancelled')),
    reason text check (char_length(reason) <= 60),
    command_id uuid,
    deadline timestamptz,
    started_at timestamptz,
    finished_at timestamptz,
    exit_code integer,
    duration_ms integer,
    output_head text check (char_length(output_head) <= 4096),
    output_truncated boolean not null default false,
    message text check (char_length(message) <= 200),
    primary key (run_id, device_id)
);
create index tool_run_devices_device on remote_private.tool_run_devices(device_id, status);

-- Fleet commands waiting for their device. Signed at delivery (mixel-admin), never stored signed.
create table remote_private.tool_commands (
    id uuid primary key default gen_random_uuid(),
    run_id uuid not null references remote_private.tool_runs(id) on delete cascade,
    device_id uuid not null,
    connection_id integer not null,
    payload jsonb not null,
    status text not null default 'queued' check (status in ('queued', 'sent', 'done', 'expired', 'cancelled')),
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    sent_at timestamptz
);
create index tool_commands_device on remote_private.tool_commands(device_id, status, created_at);

create table remote_private.tool_audit (
    id bigint generated always as identity primary key,
    client_id uuid not null,
    occurred_at timestamptz not null default now(),
    kind text not null check (char_length(kind) between 1 and 60),
    device_id uuid,
    device_name text check (char_length(device_name) <= 200),
    actor_id uuid references public.users(id) on delete set null,
    actor_name text check (char_length(actor_name) <= 200),
    session_id uuid,
    source text not null check (source in ('app', 'host', 'server')),
    summary text not null default '' check (char_length(summary) <= 500),
    result text check (char_length(result) <= 60),
    detail jsonb not null default '{}'::jsonb
);
create index tool_audit_client on remote_private.tool_audit(client_id, id desc);
create index tool_audit_actor on remote_private.tool_audit(actor_id, id desc);
create index tool_audit_device on remote_private.tool_audit(device_id, id desc);

do $$ declare t text; begin
    foreach t in array array['tool_policies', 'tool_scripts', 'tool_script_versions', 'tool_runs', 'tool_run_devices',
                             'tool_commands', 'tool_audit'] loop
        execute format('alter table remote_private.%I enable row level security', t);
        execute format('revoke all on remote_private.%I from public, anon, authenticated', t);
    end loop;
end $$;

---------------------------------------------------------------------------------------------------
-- Helpers: who is calling, which company, which rights

-- The caller's role in a company: 'admin', 'technician' or null. Staff and company administrators need MFA.
create function remote_private.tools_role(target_client uuid) returns text
language plpgsql stable security definer set search_path = '' as $$
declare t remote_saas.tenants; c public.clients; r text;
begin
    select * into c from public.clients where id = target_client and is_active;
    if c.id is null or auth.uid() is null then return null; end if;
    select * into t from remote_saas.tenants where client_id = c.id;
    if t.id is not null then
        select case m.role when 'owner' then 'admin' when 'admin' then 'admin' when 'technician' then 'technician' end into r
        from remote_saas.members m join public.users u on u.id = m.user_id
        where m.tenant_id = t.id and u.auth_user_id = auth.uid() and u.is_active and u.deleted_at is null
          and u.organization_id = t.organization_id;
        return r;
    end if;
    if coalesce(auth.jwt()->>'aal', '') <> 'aal2' then return null; end if;
    select case u.role::text when 'admin' then 'admin' when 'agent' then 'technician' end into r
    from public.users u
    where u.auth_user_id = auth.uid() and u.is_active and u.deleted_at is null and u.organization_id is null
      and u.role::text in ('admin', 'agent');
    if r is not null then return r; end if;
    if exists (
        select 1 from remote_private.company_administrators ca
        join public.users u on u.id = ca.profile_id
        join public.organizations o on o.id = ca.organization_id and o.is_active
        join auth.identities i on i.user_id = u.auth_user_id and i.provider = 'keycloak'
            and i.identity_data->>'iss' in ('https://auth.mixel.ch/application/o/mixel-ism-user',
                                            'https://auth.mixel.ch/application/o/mixel-ism-user/')
            and i.identity_data->>'sub' = ca.mixel_subject and i.provider_id = ca.mixel_subject
        where u.auth_user_id = auth.uid() and u.is_active and u.deleted_at is null
          and ca.organization_id = c.organization_id) then
        return 'admin';
    end if;
    return null;
end;
$$;

-- Paid plan: a Remote SaaS company needs a status in good standing (a trial runs on Business). ISM companies are managed.
create function remote_private.tools_paid(target_client uuid) returns boolean
language sql stable security definer set search_path = '' as $$
    select coalesce((select remote_saas.effective_status(t) in ('active', 'trial', 'past_due')
                     from remote_saas.tenants t where t.client_id = target_client), true);
$$;

create type remote_private.tools_ctx as (
    client_id uuid, tenant_id uuid, client_name text, role text, actor_id uuid, actor_name text, paid boolean
);

-- Resolves the company for the caller. `company` null: the caller's only company (company_required when several).
-- need_admin: not_admin for a technician. Unknown company or no role: not_found (never "forbidden").
create function remote_private.tools_context(company uuid, need_admin boolean default false)
returns remote_private.tools_ctx
language plpgsql stable security definer set search_path = '' as $$
declare me public.users; ctx remote_private.tools_ctx; cands uuid[]; role text;
begin
    me := remote_private.me();
    if me.id is null then
        perform remote_private.raise_hint('PT404', 'Company not found', 'not_found');
    end if;
    if company is null then
        select array_agg(distinct x) into cands from (
            select t.client_id x from remote_saas.members m join remote_saas.tenants t on t.id = m.tenant_id
                where m.user_id = me.id and m.role in ('owner', 'admin', 'technician')
            union
            select c.id from public.clients c where me.organization_id is not null and c.organization_id = me.organization_id
        ) s where remote_private.tools_role(x) is not null;
        if cands is null or cardinality(cands) = 0 then
            perform remote_private.raise_hint('PT404', 'Company not found', 'not_found');
        elsif cardinality(cands) > 1 then
            perform remote_private.raise_hint('22023', 'Say which company', 'company_required');
        end if;
        company := cands[1];
    end if;
    role := remote_private.tools_role(company);
    if role is null then
        perform remote_private.raise_hint('PT404', 'Company not found', 'not_found');
    end if;
    if need_admin and role <> 'admin' then
        perform remote_private.raise_hint('42501', 'Only an administrator can do this', 'not_admin');
    end if;
    ctx.client_id := company;
    select t.id into ctx.tenant_id from remote_saas.tenants t where t.client_id = company;
    select c.name into ctx.client_name from public.clients c where c.id = company;
    ctx.role := role; ctx.actor_id := me.id; ctx.actor_name := coalesce(nullif(me.name, ''), me.email, 'user');
    ctx.paid := remote_private.tools_paid(company);
    return ctx;
end;
$$;

-- Switches of a company (defaults when it never set any).
create function remote_private.tools_switches(target_client uuid)
returns table (terminal boolean, terminal_elevate text, tasks boolean, tasks_unattended boolean, scripts boolean,
               scripts_four_eyes boolean, updated_by_label text, updated_at timestamptz)
language sql stable security definer set search_path = '' as $$
    select coalesce(p.terminal, false), coalesce(p.terminal_elevate, 'none'), coalesce(p.tasks, false),
           coalesce(p.tasks_unattended, true), coalesce(p.scripts, false), coalesce(p.scripts_four_eyes, true),
           p.updated_by_label, p.updated_at
    from (select 1) x left join remote_private.tool_policies p on p.client_id = target_client;
$$;

-- Effective company-level flags (switch AND paid), the five fields of the broker's ToolsPolicy.
create function remote_private.tools_effective(target_client uuid)
returns table (terminal boolean, terminal_elevate boolean, tasks boolean, tasks_unattended boolean, scripts boolean)
language sql stable security definer set search_path = '' as $$
    select paid and s.terminal, paid and s.terminal and s.terminal_elevate <> 'none', paid and s.tasks,
           paid and s.tasks and s.tasks_unattended, paid and s.scripts
    from remote_private.tools_switches(target_client) s
    cross join lateral (select remote_private.tools_paid(target_client) paid) pd;
$$;

create function remote_private.tools_policy_json(ctx remote_private.tools_ctx) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare s record; e record; can_elev boolean;
begin
    select * into s from remote_private.tools_switches(ctx.client_id);
    select * into e from remote_private.tools_effective(ctx.client_id);
    can_elev := e.terminal_elevate and ((s.terminal_elevate = 'admins' and ctx.role = 'admin')
                or (s.terminal_elevate = 'technicians' and ctx.role in ('admin', 'technician')));
    return jsonb_build_object(
        'company_id', ctx.client_id, 'company_name', ctx.client_name, 'role', ctx.role, 'paid', ctx.paid,
        'switches', jsonb_build_object('terminal', s.terminal, 'terminal_elevate', s.terminal_elevate, 'tasks', s.tasks,
            'tasks_unattended', s.tasks_unattended, 'scripts', s.scripts, 'scripts_four_eyes', s.scripts_four_eyes),
        'effective', jsonb_build_object('terminal', e.terminal, 'terminal_elevate', e.terminal_elevate, 'tasks', e.tasks,
            'tasks_unattended', e.tasks_unattended, 'scripts', e.scripts, 'scripts_four_eyes', s.scripts_four_eyes,
            'can_elevate', can_elev, 'can_manage_scripts', e.scripts and ctx.role = 'admin',
            'can_run_scripts', e.scripts and ctx.role in ('admin', 'technician')),
        'limits', jsonb_build_object('max_devices_per_run', 100, 'min_reason_length', 8),
        'updated_at', s.updated_at, 'updated_by', s.updated_by_label);
end;
$$;

-- Audit row (also in the tenant audit log of a Remote SaaS company).
create function remote_private.tools_audit_add(ctx remote_private.tools_ctx, what text, dev uuid, dev_name text,
    session_id uuid, src text, about text, outcome text, detail jsonb) returns bigint
language plpgsql security definer set search_path = '' as $$
declare new_id bigint;
begin
    insert into remote_private.tool_audit(client_id, kind, device_id, device_name, actor_id, actor_name, session_id, source,
                                          summary, result, detail)
    values (ctx.client_id, what, dev, left(dev_name, 200), ctx.actor_id, left(ctx.actor_name, 200), session_id, src,
            left(coalesce(about, ''), 500), left(outcome, 60), coalesce(detail, '{}'::jsonb))
    returning id into new_id;
    if ctx.tenant_id is not null then
        perform remote_saas.audit(ctx.tenant_id, ctx.actor_id, what, dev::text,
            jsonb_build_object('audit_id', new_id, 'summary', left(coalesce(about, ''), 200), 'result', outcome));
    end if;
    return new_id;
end;
$$;

-- Parameter schema of a script version: raises invalid_script with the problem in the message.
create function remote_private.tools_check_param_schema(p jsonb) returns jsonb
language plpgsql immutable set search_path = '' as $$
declare e jsonb; n text; seen text[] := '{}'; o jsonb; i integer := 0;
begin
    if p is null then return '[]'::jsonb; end if;
    if jsonb_typeof(p) <> 'array' or jsonb_array_length(p) > 16 then
        perform remote_private.raise_hint('22023', 'Parameters must be a list of at most 16', 'invalid_script');
    end if;
    for e in select x from jsonb_array_elements(p) x loop
        if jsonb_typeof(e) <> 'object' or exists (select 1 from jsonb_object_keys(e) k
              where k not in ('name', 'label', 'type', 'required', 'default', 'options', 'max_length')) then
            perform remote_private.raise_hint('22023', 'Parameter has an unknown field', 'invalid_script');
        end if;
        n := e->>'name';
        if n is null or n !~ '^[A-Za-z_][A-Za-z0-9_]{0,31}$' or n = any(seen) then
            perform remote_private.raise_hint('22023', 'Parameter name is invalid or used twice', 'invalid_script');
        end if;
        seen := seen || n;
        if coalesce(e->>'type', '') not in ('text', 'number', 'choice') then
            perform remote_private.raise_hint('22023', 'Parameter ' || n || ' needs type text, number or choice', 'invalid_script');
        end if;
        if e ? 'label' and (jsonb_typeof(e->'label') <> 'string' or char_length(e->>'label') > 80) then
            perform remote_private.raise_hint('22023', 'Parameter label too long', 'invalid_script');
        end if;
        if e ? 'required' and jsonb_typeof(e->'required') <> 'boolean' then
            perform remote_private.raise_hint('22023', 'required must be true or false', 'invalid_script');
        end if;
        if e ? 'default' and (jsonb_typeof(e->'default') <> 'string' or octet_length(e->>'default') > 1024) then
            perform remote_private.raise_hint('22023', 'default must be a string', 'invalid_script');
        end if;
        if e ? 'max_length' and (jsonb_typeof(e->'max_length') <> 'number' or (e->>'max_length') !~ '^[0-9]{1,4}$'
              or (e->>'max_length')::integer not between 1 and 1024) then
            perform remote_private.raise_hint('22023', 'max_length must be 1 to 1024', 'invalid_script');
        end if;
        if e->>'type' = 'choice' then
            if jsonb_typeof(e->'options') <> 'array' or jsonb_array_length(e->'options') not between 1 and 50 then
                perform remote_private.raise_hint('22023', 'Choice parameter ' || n || ' needs 1 to 50 options', 'invalid_script');
            end if;
            for o in select x from jsonb_array_elements(e->'options') x loop
                if jsonb_typeof(o) <> 'string' or o #>> '{}' !~ '^[^[:cntrl:]]{1,200}$' then
                    perform remote_private.raise_hint('22023', 'Options must be short plain strings', 'invalid_script');
                end if;
            end loop;
        elsif e ? 'options' then
            perform remote_private.raise_hint('22023', 'Only choice parameters have options', 'invalid_script');
        end if;
        i := i + 1;
    end loop;
    return p;
end;
$$;

-- Values for a run: ordered [{name, value}] following the schema. invalid_params names the problems.
create function remote_private.tools_bind_params(schema jsonb, given jsonb) returns jsonb
language plpgsql immutable set search_path = '' as $$
declare e jsonb; n text; v text; result jsonb := '[]'::jsonb; bad text[] := '{}'; maxlen integer;
begin
    given := coalesce(given, '{}'::jsonb);
    if jsonb_typeof(given) <> 'object' then
        perform remote_private.raise_hint('22023', 'Parameters must be an object', 'invalid_params');
    end if;
    select coalesce(array_agg(k), '{}') into bad from jsonb_object_keys(given) k
        where not exists (select 1 from jsonb_array_elements(schema) s where s->>'name' = k);
    if cardinality(bad) > 0 then
        perform remote_private.raise_hint('22023', 'Unknown parameters: ' || array_to_string(bad, ', '), 'invalid_params');
    end if;
    for e in select x from jsonb_array_elements(schema) x loop
        n := e->>'name';
        if given ? n then
            if jsonb_typeof(given->n) <> 'string' then bad := bad || n; continue; end if;
            v := given->>n;
        else
            v := e->>'default';
        end if;
        if v is null or v = '' then
            if coalesce((e->>'required')::boolean, false) then bad := bad || n; continue; end if;
            if v is null then continue; end if;
        end if;
        maxlen := coalesce((e->>'max_length')::integer, 256);
        if octet_length(v) > 1024 or char_length(v) > maxlen or v ~ '[[:cntrl:]]' then bad := bad || n; continue; end if;
        if e->>'type' = 'number' and v <> '' and v !~ '^-?[0-9]{1,15}(\.[0-9]{1,6})?$' then bad := bad || n; continue; end if;
        if e->>'type' = 'choice' and not exists (select 1 from jsonb_array_elements_text(e->'options') o where o = v) then
            bad := bad || n; continue;
        end if;
        result := result || jsonb_build_array(jsonb_build_object('name', n, 'value', v));
    end loop;
    if cardinality(bad) > 0 then
        perform remote_private.raise_hint('22023', 'Invalid parameters: ' || array_to_string(bad, ', '), 'invalid_params');
    end if;
    return result;
end;
$$;

-- Parameters for logs and the audit: values of SECRET_ parameters are masked.
create function remote_private.tools_mask_params(params jsonb) returns jsonb
language sql immutable set search_path = '' as $$
    select coalesce(jsonb_agg(case when p->>'name' like 'SECRET\_%' then jsonb_build_object('name', p->>'name', 'value', '***')
                                   else p end), '[]'::jsonb)
    from jsonb_array_elements(coalesce(params, '[]'::jsonb)) p;
$$;

-- Output of a script for the run record: first 4 kB, secret values replaced by ***.
create function remote_private.tools_clean_output(output text, params jsonb) returns text
language plpgsql immutable set search_path = '' as $$
declare p jsonb; res text := coalesce(output, '');
begin
    res := replace(res, E'\\u0000', '');
    for p in select x from jsonb_array_elements(coalesce(params, '[]'::jsonb)) x where x->>'name' like 'SECRET\_%' loop
        if char_length(p->>'value') >= 3 then res := replace(res, p->>'value', '***'); end if;
    end loop;
    return left(res, 4096);
end;
$$;

-- Device context for authorize, log and run: visible, enrolled, the caller has a role in its company.
create type remote_private.tools_device as (
    device_id uuid, client_id uuid, name text, os text, connection_id integer, app_version text
);
create function remote_private.tools_device_ctx(target uuid) returns remote_private.tools_ctx
language plpgsql stable security definer set search_path = '' as $$
declare base remote_private.device_admin_ctx; t uuid;
begin
    base := remote_private.admin_context(target, false, 'read');
    select tn.id into t from remote_saas.tenants tn where tn.client_id = base.client_id;
    if t is not null and not remote_saas.can_access_device((remote_private.me()).id, target) then
        perform remote_private.raise_hint('PT404', 'Device not found', 'device_not_found');
    end if;
    return remote_private.tools_context(base.client_id, false);
end;
$$;
create function remote_private.tools_device_row(target uuid) returns remote_private.tools_device
language sql stable security definer set search_path = '' as $$
    select d.id, d.client_id, coalesce(dm.display_name, d.name), d.os::text, e.connection_id, p.app_version
    from public.devices d
    join public.remote_device_enrollments e on e.device_id = d.id and e.revoked_at is null
    left join remote_saas.device_meta dm on dm.device_id = d.id
    left join remote_private.device_presence p on p.connection_id = e.connection_id
    where d.id = target;
$$;

---------------------------------------------------------------------------------------------------
-- 1. Policy

create function public.remote_tools_policy(company uuid default null) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare ctx remote_private.tools_ctx;
begin
    ctx := remote_private.tools_context(company, false);
    return remote_private.tools_policy_json(ctx);
end;
$$;

create function public.remote_tools_set_policy(company uuid, changes jsonb, reason text default null) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.tools_ctx; old record; k text; v jsonb; n record;
begin
    ctx := remote_private.tools_context(company, true);
    if changes is null or jsonb_typeof(changes) <> 'object' or changes = '{}'::jsonb then
        perform remote_private.raise_hint('22023', 'Nothing to change', 'invalid_policy');
    end if;
    for k, v in select * from jsonb_each(changes) loop
        if k in ('terminal', 'tasks', 'tasks_unattended', 'scripts', 'scripts_four_eyes') then
            if jsonb_typeof(v) <> 'boolean' then
                perform remote_private.raise_hint('22023', k || ' must be true or false', 'invalid_policy');
            end if;
        elsif k = 'terminal_elevate' then
            if jsonb_typeof(v) <> 'string' or (v #>> '{}') not in ('none', 'admins', 'technicians') then
                perform remote_private.raise_hint('22023', 'terminal_elevate must be none, admins or technicians', 'invalid_policy');
            end if;
        else
            perform remote_private.raise_hint('22023', 'Unknown setting ' || left(k, 40), 'invalid_policy');
        end if;
    end loop;
    select * into old from remote_private.tools_switches(ctx.client_id);
    insert into remote_private.tool_policies(client_id, terminal, terminal_elevate, tasks, tasks_unattended, scripts,
                                             scripts_four_eyes, updated_by, updated_by_label)
    values (ctx.client_id,
            coalesce((changes->>'terminal')::boolean, old.terminal), coalesce(changes->>'terminal_elevate', old.terminal_elevate),
            coalesce((changes->>'tasks')::boolean, old.tasks), coalesce((changes->>'tasks_unattended')::boolean, old.tasks_unattended),
            coalesce((changes->>'scripts')::boolean, old.scripts), coalesce((changes->>'scripts_four_eyes')::boolean, old.scripts_four_eyes),
            ctx.actor_id, ctx.actor_name)
    on conflict (client_id) do update set terminal = excluded.terminal, terminal_elevate = excluded.terminal_elevate,
        tasks = excluded.tasks, tasks_unattended = excluded.tasks_unattended, scripts = excluded.scripts,
        scripts_four_eyes = excluded.scripts_four_eyes, updated_by = excluded.updated_by,
        updated_by_label = excluded.updated_by_label, updated_at = now();
    select * into n from remote_private.tools_switches(ctx.client_id);
    perform remote_private.tools_audit_add(ctx, 'policy.set', null, null, null, 'server',
        'Remote tools policy changed', 'ok', jsonb_build_object('reason', left(reason, 300),
            'before', to_jsonb(old) - 'updated_by_label' - 'updated_at', 'after', to_jsonb(n) - 'updated_by_label' - 'updated_at'));
    return remote_private.tools_policy_json(ctx);
end;
$$;

-- For the broker (via mixel-admin): the effective flags of a connected device's company, checked against its binding.
create function remote_private.tools_policy_for(connection integer, device uuid, profile uuid, key_generation bigint, device_key bytea)
returns table (terminal boolean, terminal_elevate boolean, tasks boolean, tasks_unattended boolean, scripts boolean)
language sql stable security definer set search_path = '' as $$
    select p.terminal, p.terminal_elevate, p.tasks, p.tasks_unattended, p.scripts
    from public.remote_device_enrollments e
    join public.users u on u.id = profile and u.is_active and u.deleted_at is null
    join public.devices d on d.id = e.device_id
    cross join lateral remote_private.tools_effective(d.client_id) p
    where e.connection_id = connection and e.device_id = device and e.generation = key_generation
      and e.public_key = device_key and e.revoked_at is null;
$$;

---------------------------------------------------------------------------------------------------
-- 2. Authorizing and logging actions (terminal, task manager)

create function public.remote_tools_authorize(device uuid, action text, reason text default null, session uuid default null,
                                              detail jsonb default '{}'::jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.tools_ctx; dev remote_private.tools_device; s record; e record; unattended boolean; kind text;
        refused text; audit bigint; needs_reason boolean := false;
begin
    if action not in ('terminal_open', 'terminal_elevate', 'tasks_open', 'process_end', 'service_action') then
        perform remote_private.raise_hint('22023', 'Unknown action', 'invalid_action');
    end if;
    if detail is null or jsonb_typeof(detail) <> 'object' or octet_length(detail::text) > 2048 then
        perform remote_private.raise_hint('22023', 'detail must be a small object', 'invalid_action');
    end if;
    ctx := remote_private.tools_device_ctx(device);
    dev := remote_private.tools_device_row(device);
    select * into s from remote_private.tools_switches(ctx.client_id);
    select * into e from remote_private.tools_effective(ctx.client_id);
    unattended := coalesce((detail->>'unattended')::boolean, false);
    kind := case action when 'terminal_open' then 'terminal.open' when 'terminal_elevate' then 'terminal.elevate'
                        when 'tasks_open' then 'tasks.open' when 'process_end' then 'tasks.end_process' else 'tasks.service' end;
    if not ctx.paid then
        refused := 'not_paid';
    elsif action in ('terminal_open', 'terminal_elevate') and not e.terminal then
        refused := 'policy_off';
    elsif action in ('tasks_open', 'process_end', 'service_action') and not e.tasks then
        refused := 'policy_off';
    elsif action = 'terminal_elevate' and not e.terminal_elevate then
        refused := 'elevation_policy';
    elsif action = 'terminal_elevate' and not ((s.terminal_elevate = 'admins' and ctx.role = 'admin')
                or (s.terminal_elevate = 'technicians' and ctx.role in ('admin', 'technician'))) then
        refused := 'elevation_policy';
    elsif action in ('process_end', 'service_action') and unattended and not e.tasks_unattended then
        refused := 'unattended_policy';
    end if;
    needs_reason := action = 'terminal_elevate' or (action in ('process_end', 'service_action') and unattended);
    if refused is null and needs_reason and char_length(btrim(coalesce(reason, ''))) < 8 then
        refused := 'reason_required';
    end if;
    audit := remote_private.tools_audit_add(ctx, case when refused is null then kind else 'terminal.refused' end, dev.device_id, dev.name,
        session, 'app', case when refused is null then initcap(replace(kind, '.', ' ')) else 'Refused: ' || refused || ' (' || action || ')' end,
        coalesce(refused, 'ok'), jsonb_build_object('action', action, 'reason', left(btrim(coalesce(reason, '')), 300)) || detail);
    if refused is not null then
        return jsonb_build_object('allowed', false, 'reason', refused, 'audit_id', audit);
    end if;
    return jsonb_build_object('allowed', true, 'audit_id', audit);
end;
$$;

-- A refusal is an ordinary result ({allowed:false, reason}), not an error, so that its audit row is kept.

create function public.remote_tools_log(device uuid, kind text, detail jsonb, session uuid default null) returns bigint
language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.tools_ctx; dev remote_private.tools_device; e record; recent integer; fam text;
begin
    if kind not in ('terminal.shell', 'terminal.command', 'terminal.exit', 'tasks.end_process', 'tasks.service', 'tasks.view', 'quality.set') then
        perform remote_private.raise_hint('22023', 'Unknown record kind', 'invalid_action');
    end if;
    if detail is null or jsonb_typeof(detail) <> 'object' or octet_length(detail::text) > 8192 then
        perform remote_private.raise_hint('22023', 'detail must be an object of at most 8 kB', 'invalid_action');
    end if;
    ctx := remote_private.tools_device_ctx(device);
    dev := remote_private.tools_device_row(device);
    select * into e from remote_private.tools_effective(ctx.client_id);
    fam := split_part(kind, '.', 1);
    if not ctx.paid then perform remote_private.raise_hint('42501', 'This company has no active paid plan', 'not_paid'); end if;
    if (fam = 'terminal' and not e.terminal) or (fam = 'tasks' and not e.tasks) then
        perform remote_private.raise_hint('42501', 'Not allowed by company policy', 'policy_off');
    end if;
    select count(*) into recent from remote_private.tool_audit a
        where a.actor_id = ctx.actor_id and a.source = 'app' and a.occurred_at > now() - interval '1 hour';
    if recent >= 600 then perform remote_private.raise_hint('53400', 'Too many records', 'rate_limited'); end if;
    return remote_private.tools_audit_add(ctx, kind, dev.device_id, dev.name, session, 'app',
        left(coalesce(detail->>'line', detail->>'summary', initcap(replace(kind, '.', ' '))), 300), detail->>'result', detail);
end;
$$;

---------------------------------------------------------------------------------------------------
-- 3. Script library

create function remote_private.tools_script_status(s remote_private.tool_scripts, v remote_private.tool_script_versions)
returns text language sql stable security definer set search_path = '' as $$
    select case when s.archived_at is not null then 'archived'
                when v.revoked_at is not null then 'revoked'
                when v.approved_at is not null then 'approved'
                when exists (select 1 from remote_private.tool_script_versions pv
                             where pv.script_id = s.id and pv.version < v.version and pv.approved_at is not null) then 'edited'
                else 'waiting' end;
$$;

-- Everything one version tells the apps. Technicians see approved versions only.
create function remote_private.tools_script_json(ctx remote_private.tools_ctx, sid uuid, ver integer default null) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare s remote_private.tool_scripts; v remote_private.tool_script_versions; sw record; status text; can_app boolean := false;
        why text; vers jsonb;
begin
    select * into s from remote_private.tool_scripts where id = sid and client_id = ctx.client_id;
    if s.id is null then perform remote_private.raise_hint('PT404', 'Script not found', 'not_found'); end if;
    select * into v from remote_private.tool_script_versions where script_id = s.id and version = coalesce(ver, s.latest_version);
    if v.id is null then perform remote_private.raise_hint('PT404', 'Script not found', 'not_found'); end if;
    status := remote_private.tools_script_status(s, (select lv from remote_private.tool_script_versions lv
                                                    where lv.script_id = s.id and lv.version = s.latest_version));
    if ctx.role <> 'admin' and (v.approved_at is null or v.revoked_at is not null or s.archived_at is not null) then
        perform remote_private.raise_hint('PT404', 'Script not found', 'not_found');
    end if;
    select * into sw from remote_private.tools_switches(ctx.client_id);
    if ctx.role <> 'admin' then why := 'not_admin';
    elsif v.version <> s.latest_version or v.approved_at is not null or v.revoked_at is not null then why := 'not_waiting';
    elsif sw.scripts_four_eyes and v.author_id = ctx.actor_id then why := 'author_cannot_approve';
    else can_app := true; end if;
    select coalesce(jsonb_agg(jsonb_build_object('version', x.version, 'status', case when x.revoked_at is not null then 'revoked'
            when x.approved_at is not null then 'approved' else 'waiting' end, 'author', x.author_name, 'created_at', x.created_at,
            'approved_by', x.approved_by_name, 'approved_at', x.approved_at, 'sha256', x.sha256) order by x.version desc), '[]'::jsonb)
        into vers from remote_private.tool_script_versions x
        where x.script_id = s.id and (ctx.role = 'admin' or (x.approved_at is not null and x.revoked_at is null));
    return jsonb_build_object('script_id', s.id, 'name', s.name, 'description', s.description, 'status', status,
        'archived', s.archived_at is not null, 'version', v.version, 'latest_version', s.latest_version, 'versions', vers,
        'os', to_jsonb(v.os), 'interpreter', v.interpreter, 'params', v.params, 'timeout_seconds', v.timeout_seconds,
        'body', v.body, 'sha256', v.sha256, 'author', v.author_name, 'created_at', v.created_at,
        'can_approve', can_app, 'approve_blocked_reason', why);
end;
$$;

create function public.remote_tools_scripts(company uuid default null, search text default null,
                                            include_archived boolean default false)
returns table (script_id uuid, name text, description text, os text[], interpreter text, status text, latest_version integer,
               approved_version integer, approved_by text, approved_at timestamptz, author text, updated_at timestamptz,
               last_run_at timestamptz, last_run_status text, can_run boolean, run_blocked_reason text)
language plpgsql stable security definer set search_path = '' as $$
declare ctx remote_private.tools_ctx; pattern text; e record;
begin
    ctx := remote_private.tools_context(company, false);
    if search is not null and char_length(search) > 80 then
        perform remote_private.raise_hint('22023', 'Search text too long', 'invalid_search');
    end if;
    pattern := '%' || replace(replace(replace(lower(btrim(coalesce(search, ''))), '\', '\\'), '%', '\%'), '_', '\_') || '%';
    select * into e from remote_private.tools_effective(ctx.client_id);
    return query
        select s.id, s.name, s.description, v.os, v.interpreter, remote_private.tools_script_status(s, v), s.latest_version,
               (select max(a.version) from remote_private.tool_script_versions a where a.script_id = s.id and a.approved_at is not null
                   and a.revoked_at is null),
               (select a.approved_by_name from remote_private.tool_script_versions a where a.script_id = s.id and a.approved_at is not null
                   order by a.version desc limit 1),
               (select a.approved_at from remote_private.tool_script_versions a where a.script_id = s.id and a.approved_at is not null
                   order by a.version desc limit 1),
               v.author_name, s.updated_at,
               lr.created_at, lr.st,
               e.scripts and ctx.paid and v.approved_at is not null and v.revoked_at is null and s.archived_at is null,
               case when not ctx.paid then 'not_paid' when not e.scripts then 'policy_off' when s.archived_at is not null then 'archived'
                    when v.revoked_at is not null then 'script_revoked' when v.approved_at is null then 'script_not_approved' end
        from remote_private.tool_scripts s
        join remote_private.tool_script_versions v on v.script_id = s.id and v.version = s.latest_version
        left join lateral (
            select r.created_at, (select case when count(*) filter (where d.status in ('queued', 'running')) > 0 then 'running'
                     when count(*) filter (where d.status in ('failed', 'timeout')) > 0 then 'failed'
                     when count(*) filter (where d.status = 'succeeded') > 0 then 'succeeded' else 'skipped' end
                     from remote_private.tool_run_devices d where d.run_id = r.id) st
            from remote_private.tool_runs r where r.script_id = s.id order by r.seq desc limit 1) lr on true
        where s.client_id = ctx.client_id
          and (include_archived or s.archived_at is null)
          and (ctx.role = 'admin' or (v.approved_at is not null and v.revoked_at is null and s.archived_at is null))
          and (lower(s.name) like pattern or lower(s.description) like pattern)
        order by lower(s.name), s.id
        limit 1000;
end;
$$;

create function public.remote_tools_script_get(script uuid, version integer default null) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare ctx remote_private.tools_ctx; cid uuid;
begin
    select s.client_id into cid from remote_private.tool_scripts s where s.id = script;
    if cid is null then perform remote_private.raise_hint('PT404', 'Script not found', 'not_found'); end if;
    ctx := remote_private.tools_context(cid, false);
    return remote_private.tools_script_json(ctx, script, version);
end;
$$;

create function public.remote_tools_script_save(company uuid, script uuid default null, name text default null,
    description text default null, os text[] default null, interpreter text default null, params jsonb default null,
    body text default null, timeout_seconds integer default null) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.tools_ctx; s remote_private.tool_scripts; prev remote_private.tool_script_versions; sw record;
        n_name text; n_desc text; n_os text[]; n_interp text; n_params jsonb; n_body text; n_timeout integer; hash text;
        new_version integer; auto boolean; e record; vid uuid;
begin
    ctx := remote_private.tools_context(company, true);
    select * into e from remote_private.tools_effective(ctx.client_id);
    if not ctx.paid then perform remote_private.raise_hint('42501', 'This company has no active paid plan', 'not_paid'); end if;
    if not e.scripts then perform remote_private.raise_hint('42501', 'Not allowed by company policy', 'policy_off'); end if;
    select * into sw from remote_private.tools_switches(ctx.client_id);
    if script is not null then
        select * into s from remote_private.tool_scripts x where x.id = script and x.client_id = ctx.client_id for update;
        if s.id is null then perform remote_private.raise_hint('PT404', 'Script not found', 'not_found'); end if;
        select * into prev from remote_private.tool_script_versions v where v.script_id = s.id and v.version = s.latest_version;
    end if;
    n_name := btrim(coalesce(name, s.name));
    n_desc := coalesce(description, s.description, '');
    n_os := coalesce(os, prev.os);
    n_interp := coalesce(interpreter, prev.interpreter);
    n_params := remote_private.tools_check_param_schema(coalesce(params, prev.params, '[]'::jsonb));
    n_body := coalesce(body, prev.body);
    n_timeout := coalesce(timeout_seconds, prev.timeout_seconds, 300);
    if n_name is null or char_length(n_name) not between 1 and 80 or n_name ~ '[[:cntrl:]]' then
        perform remote_private.raise_hint('22023', 'The name needs 1 to 80 characters', 'invalid_script');
    end if;
    if char_length(n_desc) > 500 or n_desc ~ '[\x00-\x08\x0b-\x1f\x7f]' then
        perform remote_private.raise_hint('22023', 'The description is too long', 'invalid_script');
    end if;
    if n_os is null or cardinality(n_os) not between 1 and 3 or not (n_os <@ array['windows', 'macos', 'linux']) then
        perform remote_private.raise_hint('22023', 'os must be a list of windows, macos, linux', 'invalid_script');
    end if;
    if n_interp not in ('powershell', 'sh') or (n_interp = 'powershell' and n_os <> array['windows'])
       or (n_interp = 'sh' and 'windows' = any(n_os)) then
        perform remote_private.raise_hint('22023', 'powershell is for windows only, sh for macos and linux', 'invalid_script');
    end if;
    if n_body is null or btrim(n_body) = '' then
        perform remote_private.raise_hint('22023', 'The script text is empty', 'invalid_script');
    end if;
    if char_length(n_body) > 16000 or octet_length(n_body) > 16000 * 4 then
        perform remote_private.raise_hint('22023', 'The script text is over 16 000 characters', 'script_too_large');
    end if;
    if octet_length(n_body) > 48000 or n_body ~ E'\\x00' then
        perform remote_private.raise_hint('22023', 'The script text is too large or contains a NUL character', 'script_too_large');
    end if;
    if n_timeout not between 1 and 3600 then
        perform remote_private.raise_hint('22023', 'The time limit is 1 to 3600 seconds', 'invalid_script');
    end if;
    hash := pg_catalog.encode(extensions.digest(pg_catalog.convert_to(n_body, 'UTF8'), 'sha256'), 'hex');
    if s.id is null then
        if exists (select 1 from remote_private.tool_scripts x where x.client_id = ctx.client_id and lower(x.name) = lower(n_name)
                   and x.archived_at is null) then
            perform remote_private.raise_hint('23505', 'A script with this name exists', 'name_taken');
        end if;
        insert into remote_private.tool_scripts(client_id, name, description, created_by) values (ctx.client_id, n_name, n_desc, ctx.actor_id)
            returning * into s;
    else
        if s.archived_at is not null then perform remote_private.raise_hint('55000', 'The script is archived', 'script_not_approved'); end if;
        if n_name <> s.name and exists (select 1 from remote_private.tool_scripts x where x.client_id = ctx.client_id
               and lower(x.name) = lower(n_name) and x.archived_at is null and x.id <> s.id) then
            perform remote_private.raise_hint('23505', 'A script with this name exists', 'name_taken');
        end if;
        if prev.id is not null and prev.revoked_at is null and n_name = s.name and n_desc = s.description and n_os = prev.os
           and n_interp = prev.interpreter and n_params = prev.params and hash = prev.sha256 and n_timeout = prev.timeout_seconds then
            perform remote_private.raise_hint('22023', 'Nothing changed', 'no_change');
        end if;
        update remote_private.tool_scripts set name = n_name, description = n_desc, updated_at = now() where id = s.id;
    end if;
    new_version := s.latest_version + 1;
    auto := not sw.scripts_four_eyes;
    insert into remote_private.tool_script_versions(script_id, version, os, interpreter, params, body, sha256, timeout_seconds,
        author_id, author_name, approved_by, approved_by_name, approved_at, approval_mode)
    values (s.id, new_version, n_os, n_interp, n_params, n_body, hash, n_timeout, ctx.actor_id, ctx.actor_name,
        case when auto then ctx.actor_id end, case when auto then ctx.actor_name end, case when auto then now() end,
        case when auto then 'four_eyes_off' end)
    returning id into vid;
    update remote_private.tool_scripts set latest_version = new_version where id = s.id;
    perform remote_private.tools_audit_add(ctx, 'script.save', null, null, null, 'server',
        'Script "' || n_name || '" saved as version ' || new_version, 'ok',
        jsonb_build_object('script_id', s.id, 'version', new_version, 'sha256', hash, 'author', ctx.actor_name));
    if auto then
        perform remote_private.tools_audit_add(ctx, 'script.approve', null, null, null, 'server',
            'Script "' || n_name || '" version ' || new_version || ' approved by its author (four-eyes is off)', 'ok',
            jsonb_build_object('script_id', s.id, 'version', new_version, 'sha256', hash, 'author', ctx.actor_name,
                               'approver', ctx.actor_name, 'four_eyes', false));
    end if;
    return remote_private.tools_script_json(ctx, s.id, new_version);
end;
$$;


create function public.remote_tools_script_approve(script uuid, version integer, i_read_the_script boolean) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.tools_ctx; s remote_private.tool_scripts; v remote_private.tool_script_versions; sw record; e record;
begin
    select * into s from remote_private.tool_scripts x where x.id = script;
    if s.id is null then perform remote_private.raise_hint('PT404', 'Script not found', 'not_found'); end if;
    ctx := remote_private.tools_context(s.client_id, true);
    if not coalesce(i_read_the_script, false) then
        perform remote_private.raise_hint('22023', 'Confirm that you read the script text', 'ack_required');
    end if;
    select * into e from remote_private.tools_effective(ctx.client_id);
    if not ctx.paid then perform remote_private.raise_hint('42501', 'This company has no active paid plan', 'not_paid'); end if;
    if not e.scripts then perform remote_private.raise_hint('42501', 'Not allowed by company policy', 'policy_off'); end if;
    select * into s from remote_private.tool_scripts x where x.id = script for update;
    select * into v from remote_private.tool_script_versions x where x.script_id = s.id and x.version = remote_tools_script_approve.version for update;
    if v.id is null then perform remote_private.raise_hint('PT404', 'Script not found', 'not_found'); end if;
    if s.archived_at is not null or v.version <> s.latest_version or v.approved_at is not null or v.revoked_at is not null then
        perform remote_private.raise_hint('55000', 'This version is not waiting for approval', 'not_waiting');
    end if;
    select * into sw from remote_private.tools_switches(ctx.client_id);
    if sw.scripts_four_eyes and v.author_id = ctx.actor_id then
        perform remote_private.raise_hint('42501', 'The author cannot approve their own script', 'author_cannot_approve');
    end if;
    if v.sha256 <> pg_catalog.encode(extensions.digest(pg_catalog.convert_to(v.body, 'UTF8'), 'sha256'), 'hex') then
        perform remote_private.raise_hint('55000', 'The script text does not match its hash', 'hash_mismatch');
    end if;
    update remote_private.tool_script_versions set approved_by = ctx.actor_id, approved_by_name = ctx.actor_name, approved_at = now(),
        approval_mode = case when v.author_id = ctx.actor_id then 'four_eyes_off' else 'second_admin' end where id = v.id;
    update remote_private.tool_scripts set updated_at = now() where id = s.id;
    perform remote_private.tools_audit_add(ctx, 'script.approve', null, null, null, 'server',
        'Script "' || s.name || '" version ' || v.version || ' approved by ' || ctx.actor_name, 'ok',
        jsonb_build_object('script_id', s.id, 'version', v.version, 'sha256', v.sha256, 'author', v.author_name,
                           'approver', ctx.actor_name, 'four_eyes', sw.scripts_four_eyes));
    return remote_private.tools_script_json(ctx, s.id, v.version);
end;
$$;

create function public.remote_tools_script_revoke(script uuid, version integer default null, reason text default null) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.tools_ctx; s remote_private.tool_scripts; v remote_private.tool_script_versions;
begin
    select * into s from remote_private.tool_scripts x where x.id = script;
    if s.id is null then perform remote_private.raise_hint('PT404', 'Script not found', 'not_found'); end if;
    ctx := remote_private.tools_context(s.client_id, true);
    select * into v from remote_private.tool_script_versions x
        where x.script_id = s.id and x.version = coalesce(remote_tools_script_revoke.version, s.latest_version) for update;
    if v.id is null then perform remote_private.raise_hint('PT404', 'Script not found', 'not_found'); end if;
    if v.revoked_at is not null then perform remote_private.raise_hint('55000', 'Already revoked', 'already_done'); end if;
    update remote_private.tool_script_versions set revoked_at = now(), revoked_by_name = ctx.actor_name,
        revoke_reason = left(btrim(reason), 300) where id = v.id;
    update remote_private.tool_scripts set updated_at = now() where id = s.id;
    perform remote_private.tools_audit_add(ctx, 'script.revoke', null, null, null, 'server',
        'Script "' || s.name || '" version ' || v.version || ' revoked', 'ok',
        jsonb_build_object('script_id', s.id, 'version', v.version, 'sha256', v.sha256, 'reason', left(btrim(reason), 300)));
    return remote_private.tools_script_json(ctx, s.id, v.version);
end;
$$;

create function public.remote_tools_script_archive(script uuid, archived boolean default true) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.tools_ctx; s remote_private.tool_scripts;
begin
    select * into s from remote_private.tool_scripts x where x.id = script;
    if s.id is null then perform remote_private.raise_hint('PT404', 'Script not found', 'not_found'); end if;
    ctx := remote_private.tools_context(s.client_id, true);
    if archived then
        update remote_private.tool_scripts set archived_at = coalesce(archived_at, now()), updated_at = now() where id = s.id;
    else
        if exists (select 1 from remote_private.tool_scripts x where x.client_id = s.client_id and lower(x.name) = lower(s.name)
                   and x.archived_at is null and x.id <> s.id) then
            perform remote_private.raise_hint('23505', 'A script with this name exists', 'name_taken');
        end if;
        update remote_private.tool_scripts set archived_at = null, updated_at = now() where id = s.id;
    end if;
    perform remote_private.tools_audit_add(ctx, 'script.archive', null, null, null, 'server',
        'Script "' || s.name || '" ' || case when archived then 'archived' else 'restored' end, 'ok', jsonb_build_object('script_id', s.id));
    return remote_private.tools_script_json(ctx, s.id, null);
end;
$$;

---------------------------------------------------------------------------------------------------
-- 4. Runs

-- One run with its devices.
create function remote_private.tools_run_json(rid uuid) returns jsonb
language sql stable security definer set search_path = '' as $$
    select jsonb_build_object(
        'run_id', r.id, 'seq', r.seq, 'script_id', r.script_id, 'script_name', s.name, 'version', v.version, 'sha256', v.sha256,
        'mode', r.mode, 'requested_by', r.requested_by_name, 'reason', r.reason, 'created_at', r.created_at,
        'cancelled_at', r.cancelled_at,
        'status', case when t.active = 0 then 'finished' when t.running > 0 then 'running' else 'queued' end,
        'totals', jsonb_build_object('queued', t.queued, 'running', t.running, 'succeeded', t.succeeded, 'failed', t.failed,
                                     'timeout', t.timeout, 'skipped', t.skipped, 'cancelled', t.cancelled),
        'devices', coalesce(t.devices, '[]'::jsonb))
    from remote_private.tool_runs r
    join remote_private.tool_scripts s on s.id = r.script_id
    join remote_private.tool_script_versions v on v.id = r.version_id
    cross join lateral (
        select count(*) filter (where d.status = 'queued')::int queued, count(*) filter (where d.status = 'running')::int running,
               count(*) filter (where d.status = 'succeeded')::int succeeded, count(*) filter (where d.status = 'failed')::int failed,
               count(*) filter (where d.status = 'timeout')::int timeout, count(*) filter (where d.status = 'skipped')::int skipped,
               count(*) filter (where d.status = 'cancelled')::int cancelled,
               count(*) filter (where d.status in ('queued', 'running'))::int active,
               jsonb_agg(jsonb_build_object('device_id', d.device_id, 'name', d.device_name, 'os', d.os, 'status', d.status,
                   'reason', d.reason, 'exit_code', d.exit_code, 'duration_ms', d.duration_ms, 'started_at', d.started_at,
                   'finished_at', d.finished_at, 'output_head', d.output_head, 'output_truncated', d.output_truncated,
                   'message', d.message) order by lower(d.device_name), d.device_id) devices
        from remote_private.tool_run_devices d where d.run_id = r.id) t
    where r.id = rid;
$$;

-- A run that no longer has work to do no longer needs its parameter values.
create function remote_private.tools_run_settle(rid uuid) returns void
language sql security definer set search_path = '' as $$
    update remote_private.tool_runs r set params = remote_private.tools_mask_params(r.params)
    where r.id = rid and r.params <> remote_private.tools_mask_params(r.params)
      and not exists (select 1 from remote_private.tool_run_devices d where d.run_id = r.id and d.status in ('queued', 'running'));
    update remote_private.tool_commands c set payload = (c.payload - 'script') || jsonb_build_object('params', remote_private.tools_mask_params(c.payload->'params'))
    where c.run_id = rid and c.status in ('done', 'expired', 'cancelled') and c.payload ? 'script';
$$;

-- Offline devices and missing results time out (called when a run is read and when a device checks in).
create function remote_private.tools_expire(rid uuid default null, dev uuid default null) returns void
language plpgsql security definer set search_path = '' as $$
declare r record;
begin
    for r in select d.run_id from remote_private.tool_run_devices d
             join remote_private.tool_runs ru on ru.id = d.run_id
             join remote_private.tool_script_versions v on v.id = ru.version_id
             where (rid is null or d.run_id = rid) and (dev is null or d.device_id = dev)
               and ((d.status = 'queued' and d.deadline < now())
                 or (d.status = 'running' and d.started_at + make_interval(secs => v.timeout_seconds + 180) < now()))
             group by d.run_id loop
        update remote_private.tool_run_devices d set status = 'timeout', finished_at = now(),
            message = case when d.status = 'queued' then (case when ru.mode = 'fleet' then 'device_offline' else 'ticket_expired' end)
                           else 'no_result' end
            from remote_private.tool_runs ru join remote_private.tool_script_versions v on v.id = ru.version_id
            where d.run_id = r.run_id and ru.id = d.run_id and (dev is null or d.device_id = dev)
              and ((d.status = 'queued' and d.deadline < now())
                or (d.status = 'running' and d.started_at + make_interval(secs => v.timeout_seconds + 180) < now()));
        update remote_private.tool_commands c set status = 'expired'
            where c.run_id = r.run_id and c.status = 'queued' and (c.expires_at < now()
              or exists (select 1 from remote_private.tool_run_devices d where d.run_id = c.run_id and d.device_id = c.device_id
                         and d.status = 'timeout'));
        perform remote_private.tools_run_settle(r.run_id);
    end loop;
end;
$$;

create function public.remote_tools_run_script(script uuid, devices uuid[], params jsonb default '{}'::jsonb,
    reason text default null, mode text default 'fleet', unattended boolean default false) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.tools_ctx; dctx remote_private.tools_ctx; s remote_private.tool_scripts; v remote_private.tool_script_versions;
        e record; bound jsonb; rid uuid; d uuid; dev remote_private.tools_device; st text; why text; ids uuid[]; queued integer := 0;
        summary text := ''; cmd uuid; need_reason boolean; recent integer; ver_ok boolean; pol record; skipped_list text := '';
        out_devices jsonb;
begin
    if mode not in ('session', 'fleet') then perform remote_private.raise_hint('22023', 'mode is session or fleet', 'invalid_params'); end if;
    if devices is null or cardinality(devices) = 0 then perform remote_private.raise_hint('22023', 'No devices given', 'nothing_to_run'); end if;
    select array_agg(distinct x) into ids from unnest(devices) x where x is not null;
    if cardinality(ids) > 100 then perform remote_private.raise_hint('22023', 'At most 100 devices per run', 'too_many_devices'); end if;
    if mode = 'session' and cardinality(ids) <> 1 then
        perform remote_private.raise_hint('22023', 'A session run takes exactly one device', 'too_many_devices');
    end if;
    select * into s from remote_private.tool_scripts x where x.id = script;
    if s.id is null then perform remote_private.raise_hint('PT404', 'Script not found', 'not_found'); end if;
    ctx := remote_private.tools_context(s.client_id, false);
    select * into e from remote_private.tools_effective(ctx.client_id);
    if not ctx.paid then perform remote_private.raise_hint('42501', 'This company has no active paid plan', 'not_paid'); end if;
    if not e.scripts then perform remote_private.raise_hint('42501', 'Not allowed by company policy', 'policy_off'); end if;
    select * into v from remote_private.tool_script_versions x where x.script_id = s.id and x.version = s.latest_version;
    if s.archived_at is not null or v.id is null or v.approved_at is null or v.revoked_at is not null then
        perform remote_private.raise_hint('55000', 'The script is not approved', 'script_not_approved');
    end if;
    if v.sha256 <> pg_catalog.encode(extensions.digest(pg_catalog.convert_to(v.body, 'UTF8'), 'sha256'), 'hex') then
        perform remote_private.raise_hint('55000', 'The script text does not match its approval', 'hash_mismatch');
    end if;
    need_reason := mode = 'fleet' or coalesce(unattended, false);
    if need_reason and char_length(btrim(coalesce(reason, ''))) < 8 then
        perform remote_private.raise_hint('22023', 'A reason of at least 8 characters is required', 'reason_required');
    end if;
    select count(*) into recent from remote_private.tool_runs r where r.requested_by = ctx.actor_id and r.created_at > now() - interval '1 hour';
    if recent >= 20 then perform remote_private.raise_hint('53400', 'Too many runs this hour', 'rate_limited'); end if;
    if mode = 'fleet' then
        select count(*) into recent from remote_private.tool_runs r
            where r.client_id = ctx.client_id and r.mode = 'fleet' and r.created_at > now() - interval '1 hour';
        if recent >= 100 then perform remote_private.raise_hint('53400', 'Too many fleet runs this hour', 'rate_limited'); end if;
    end if;
    bound := remote_private.tools_bind_params(v.params, params);
    insert into remote_private.tool_runs(client_id, script_id, version_id, mode, requested_by, requested_by_name, reason, params)
        values (ctx.client_id, s.id, v.id, mode, ctx.actor_id, ctx.actor_name, left(btrim(reason), 300), bound) returning id into rid;
    foreach d in array ids loop
        st := 'queued'; why := null; dev := null;
        begin
            dctx := remote_private.tools_device_ctx(d);
            dev := remote_private.tools_device_row(d);
        exception when others then
            dev := null;
        end;
        if dev.device_id is null or dctx.client_id is distinct from ctx.client_id then
            st := 'skipped'; why := 'no_access';
            insert into remote_private.tool_run_devices(run_id, device_id, device_name, os, status, reason)
                values (rid, d, '(unknown device)', 'other', st, why);
            continue;
        end if;
        if dev.connection_id is null then st := 'skipped'; why := 'not_enrolled';
        elsif not (dev.os = any(v.os)) then st := 'skipped'; why := 'wrong_os';
        elsif dev.app_version is null or dev.app_version !~ '^[0-9]+\.[0-9]+' or
              not ((split_part(dev.app_version, '.', 1))::integer > 2
                   or ((split_part(dev.app_version, '.', 1))::integer = 2 and (split_part(dev.app_version, '.', 2))::integer >= 2)) then
            st := 'skipped'; why := 'needs_2_2';
        elsif mode = 'fleet' then
            select * into pol from remote_private.device_unattended_policy(d);
            if not coalesce(pol.unattended_allowed, false) then st := 'skipped'; why := 'unattended_off';
            elsif not (exists (select 1 from remote_saas.unattended_grants g where g.device_id = d and g.user_id = ctx.actor_id)
                       or remote_private.has_auto_access(ctx.actor_id, d)) then
                st := 'skipped'; why := 'no_unattended_access';
            end if;
        end if;
        if st = 'queued' and (select count(*) from remote_private.tool_commands c where c.device_id = d and c.status in ('queued', 'sent')) >= 5 then
            st := 'skipped'; why := 'device_busy';
        end if;
        cmd := null;
        if st = 'queued' then
            queued := queued + 1;
            cmd := gen_random_uuid();
            if mode = 'fleet' then
                insert into remote_private.tool_commands(id, run_id, device_id, connection_id, payload, expires_at)
                values (cmd, rid, d, dev.connection_id, jsonb_build_object('id', cmd, 'shell', v.interpreter, 'script', v.body,
                    'timeout_seconds', v.timeout_seconds, 'params', bound, 'script_ref', s.id || '@' || v.version),
                    now() + interval '15 minutes');
            end if;
        else
            skipped_list := skipped_list || coalesce(dev.name, d::text) || ': ' || why || '; ';
        end if;
        insert into remote_private.tool_run_devices(run_id, device_id, device_name, os, connection_id, status, reason, command_id, deadline)
        values (rid, d, left(dev.name, 200), dev.os, dev.connection_id, st, why, cmd,
                case when st = 'queued' then now() + case when mode = 'fleet' then interval '15 minutes' else interval '5 minutes' end end);
    end loop;
    if queued = 0 then
        perform remote_private.raise_hint('22023', 'Nothing to run (' || left(skipped_list, 300) || ')', 'nothing_to_run');
    end if;
    -- Fleet commands carry their own parameter copy: the run keeps masked values.
    if mode = 'fleet' then
        update remote_private.tool_runs set params = remote_private.tools_mask_params(params) where id = rid;
    end if;
    select jsonb_agg(jsonb_build_object('device_id', x.device_id, 'name', x.device_name, 'status', x.status, 'reason', x.reason))
        into out_devices from remote_private.tool_run_devices x where x.run_id = rid;
    perform remote_private.tools_audit_add(ctx, 'script.run', null, null, null, 'server',
        'Script "' || s.name || '" v' || v.version || ' run on ' || queued || ' of ' || cardinality(ids) || ' devices (' || mode || ')', 'queued',
        jsonb_build_object('run_id', rid, 'script_id', s.id, 'version', v.version, 'sha256', v.sha256, 'mode', mode,
            'reason', left(btrim(reason), 300), 'params', remote_private.tools_mask_params(bound), 'devices', out_devices));
    return remote_private.tools_run_json(rid);
end;
$$;

-- Session mode: the app asks for the command to have it signed (mixel-admin calls this as the user).
create function public.remote_tools_take_ticket(run uuid, device uuid) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare r remote_private.tool_runs; me public.users; s remote_private.tool_scripts; v remote_private.tool_script_versions;
        d remote_private.tool_run_devices; e record; dev remote_private.tools_device; cmd uuid; role text;
begin
    me := remote_private.me();
    select * into r from remote_private.tool_runs x where x.id = run;
    if me.id is null or r.id is null or r.requested_by is distinct from me.id then
        perform remote_private.raise_hint('PT404', 'Run not found', 'not_found');
    end if;
    if r.mode <> 'session' then perform remote_private.raise_hint('22023', 'Only session runs need a ticket', 'invalid_params'); end if;
    role := remote_private.tools_role(r.client_id);
    if role is null then perform remote_private.raise_hint('PT404', 'Run not found', 'not_found'); end if;
    select * into d from remote_private.tool_run_devices x where x.run_id = r.id and x.device_id = device for update;
    if d.run_id is null then perform remote_private.raise_hint('PT404', 'Run not found', 'not_found'); end if;
    perform remote_private.tools_expire(r.id, null);
    select * into d from remote_private.tool_run_devices x where x.run_id = r.id and x.device_id = device;
    if d.status <> 'queued' or r.cancelled_at is not null then
        perform remote_private.raise_hint('55000', 'This run was already dispatched, cancelled or expired', 'already_done');
    end if;
    select * into s from remote_private.tool_scripts x where x.id = r.script_id;
    select * into v from remote_private.tool_script_versions x where x.id = r.version_id;
    select * into e from remote_private.tools_effective(r.client_id);
    if s.archived_at is not null or v.revoked_at is not null or v.approved_at is null then
        perform remote_private.raise_hint('55000', 'The script was revoked', 'script_not_approved');
    end if;
    if not remote_private.tools_paid(r.client_id) or not e.scripts then
        perform remote_private.raise_hint('42501', 'Not allowed by company policy', 'policy_off');
    end if;
    dev := remote_private.tools_device_row(device);
    if dev.connection_id is null then perform remote_private.raise_hint('PT404', 'Device not found', 'device_not_found'); end if;
    cmd := gen_random_uuid();
    update remote_private.tool_run_devices set status = 'running', started_at = now(), command_id = cmd, connection_id = dev.connection_id
        where run_id = r.id and device_id = device;
    update remote_private.tool_runs set ticket_taken_at = now() where id = r.id;
    return jsonb_build_object('run_id', r.id, 'device_id', device, 'connection_id', dev.connection_id,
        'command', jsonb_build_object('id', cmd, 'shell', v.interpreter, 'script', v.body, 'timeout_seconds', v.timeout_seconds,
            'params', r.params, 'script_ref', s.id || '@' || v.version));
end;
$$;

-- Finish one device of a run (fleet result from the device, session result from the technician's app).
create function remote_private.tools_finish(rid uuid, dev uuid, exit integer, output text, duration integer, timed_out boolean)
returns boolean language plpgsql security definer set search_path = '' as $$
declare r remote_private.tool_runs; d remote_private.tool_run_devices; st text; ctx remote_private.tools_ctx; v remote_private.tool_script_versions;
        raw text; cleaned text;
begin
    select * into r from remote_private.tool_runs where id = rid;
    select * into d from remote_private.tool_run_devices x where x.run_id = rid and x.device_id = dev and x.status = 'running' for update;
    if r.id is null or d.run_id is null then return false; end if;
    select * into v from remote_private.tool_script_versions where id = r.version_id;
    raw := coalesce(output, '');
    cleaned := remote_private.tools_clean_output(raw, r.params);
    st := case when coalesce(timed_out, false) or exit = -2 then 'timeout' when exit = 0 then 'succeeded' else 'failed' end;
    update remote_private.tool_run_devices set status = st, exit_code = exit, finished_at = now(),
        duration_ms = coalesce(duration, greatest(0, (extract(epoch from now() - d.started_at) * 1000)::integer)),
        output_head = cleaned, output_truncated = char_length(raw) > 4096,
        message = case when st = 'timeout' then 'script_time_limit' end
        where run_id = rid and device_id = dev;
    update remote_private.tool_commands set status = 'done' where id = d.command_id and status = 'sent';
    ctx.client_id := r.client_id; ctx.actor_id := r.requested_by; ctx.actor_name := r.requested_by_name;
    select t.id into ctx.tenant_id from remote_saas.tenants t where t.client_id = r.client_id;
    perform remote_private.tools_audit_add(ctx, 'script.result', dev, d.device_name, null, 'server',
        'Script run on ' || d.device_name || ': ' || st || ' (exit ' || coalesce(exit::text, '?') || ')', st,
        jsonb_build_object('run_id', rid, 'script_id', r.script_id, 'version', v.version, 'sha256', v.sha256, 'exit_code', exit,
            'duration_ms', duration, 'output_head', left(cleaned, 1024)));
    perform remote_private.tools_run_settle(rid);
    return true;
end;
$$;

create function public.remote_tools_report_result(run uuid, device uuid, exit_code integer, output text,
    duration_ms integer default null, timed_out boolean default false) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare r remote_private.tool_runs; me public.users;
begin
    me := remote_private.me();
    select * into r from remote_private.tool_runs x where x.id = run;
    if me.id is null or r.id is null or r.requested_by is distinct from me.id or r.mode <> 'session'
       or remote_private.tools_role(r.client_id) is null then
        perform remote_private.raise_hint('PT404', 'Run not found', 'not_found');
    end if;
    if not remote_private.tools_finish(r.id, device, exit_code, output, duration_ms, timed_out) then
        perform remote_private.raise_hint('55000', 'No running command for this device', 'already_done');
    end if;
    return remote_private.tools_run_json(r.id);
end;
$$;

create function public.remote_tools_run_get(run uuid) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare r remote_private.tool_runs; ctx remote_private.tools_ctx;
begin
    select * into r from remote_private.tool_runs x where x.id = run;
    if r.id is null then perform remote_private.raise_hint('PT404', 'Run not found', 'not_found'); end if;
    ctx := remote_private.tools_context(r.client_id, false);
    if ctx.role <> 'admin' and r.requested_by is distinct from ctx.actor_id then
        perform remote_private.raise_hint('PT404', 'Run not found', 'not_found');
    end if;
    perform remote_private.tools_expire(r.id, null);
    return remote_private.tools_run_json(r.id);
end;
$$;

create function public.remote_tools_runs(company uuid default null, script uuid default null, device uuid default null,
    with_status text default null, from_at timestamptz default null, to_at timestamptz default null, limit_rows integer default 50,
    before_id bigint default null)
returns table (run_id uuid, seq bigint, script_name text, version integer, sha256 text, mode text, status text, requested_by text,
               created_at timestamptz, devices_total integer, succeeded integer, failed integer, timeout integer, skipped integer)
language plpgsql security definer set search_path = '' as $$
declare ctx remote_private.tools_ctx; r record;
begin
    ctx := remote_private.tools_context(company, false);
    if with_status is not null and with_status not in ('queued', 'running', 'finished') then
        perform remote_private.raise_hint('22023', 'status is queued, running or finished', 'invalid_params');
    end if;
    for r in select x.id from remote_private.tool_runs x where x.client_id = ctx.client_id and x.created_at > now() - interval '2 days' loop
        perform remote_private.tools_expire(r.id, null);
    end loop;
    return query
        select j.run_id, j.seq, j.script_name, j.version, j.sha256, j.mode, j.status, j.requested_by, j.created_at,
               (j.t->>'queued')::int + (j.t->>'running')::int + (j.t->>'succeeded')::int + (j.t->>'failed')::int
                   + (j.t->>'timeout')::int + (j.t->>'skipped')::int + (j.t->>'cancelled')::int,
               (j.t->>'succeeded')::int, (j.t->>'failed')::int, (j.t->>'timeout')::int, (j.t->>'skipped')::int
        from (select (x.js->>'run_id')::uuid run_id, (x.js->>'seq')::bigint seq, x.js->>'script_name' script_name,
                     (x.js->>'version')::int version, x.js->>'sha256' sha256, x.js->>'mode' mode, x.js->>'status' status,
                     x.js->>'requested_by' requested_by, (x.js->>'created_at')::timestamptz created_at, x.js->'totals' t
              from (select remote_private.tools_run_json(ru.id) js from remote_private.tool_runs ru
                    where ru.client_id = ctx.client_id
                      and (ctx.role = 'admin' or ru.requested_by = ctx.actor_id)
                      and (script is null or ru.script_id = script)
                      and (device is null or exists (select 1 from remote_private.tool_run_devices d where d.run_id = ru.id and d.device_id = device))
                      and (from_at is null or ru.created_at >= from_at) and (to_at is null or ru.created_at < to_at)
                      and (before_id is null or ru.seq < before_id)
                    order by ru.seq desc limit least(greatest(coalesce(limit_rows, 50), 1), 200)) x) j
        where with_status is null or j.status = with_status
        order by j.seq desc;
end;
$$;

create function public.remote_tools_cancel_run(run uuid) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare r remote_private.tool_runs; ctx remote_private.tools_ctx; n integer;
begin
    select * into r from remote_private.tool_runs x where x.id = run for update;
    if r.id is null then perform remote_private.raise_hint('PT404', 'Run not found', 'not_found'); end if;
    ctx := remote_private.tools_context(r.client_id, false);
    if ctx.role <> 'admin' and r.requested_by is distinct from ctx.actor_id then
        perform remote_private.raise_hint('PT404', 'Run not found', 'not_found');
    end if;
    update remote_private.tool_run_devices set status = 'cancelled', finished_at = now(), message = 'cancelled_by_user'
        where run_id = r.id and status = 'queued';
    get diagnostics n = row_count;
    update remote_private.tool_commands set status = 'cancelled' where run_id = r.id and status = 'queued';
    update remote_private.tool_runs set cancelled_at = now() where id = r.id and cancelled_at is null;
    perform remote_private.tools_run_settle(r.id);
    perform remote_private.tools_audit_add(ctx, 'run.cancel', null, null, null, 'server', 'Run cancelled; ' || n || ' devices will not run it', 'ok',
        jsonb_build_object('run_id', r.id, 'cancelled', n));
    return remote_private.tools_run_json(r.id);
end;
$$;

-- For the broker's agent check-in (mixel-admin signs the returned command): next fleet command of a device.
create function remote_private.tools_next_command(connection integer, device uuid) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare c remote_private.tool_commands; r remote_private.tool_runs; s remote_private.tool_scripts; v remote_private.tool_script_versions;
        e record; ok boolean; why text;
begin
    if not exists (select 1 from public.remote_device_enrollments en where en.device_id = device and en.connection_id = connection
                   and en.revoked_at is null) then
        return null;
    end if;
    perform remote_private.tools_expire(null, device);
    if exists (select 1 from remote_private.tool_commands x join remote_private.tool_run_devices d on d.command_id = x.id
               where x.device_id = device and x.status = 'sent' and d.status = 'running') then
        return null;
    end if;
    loop
        select * into c from remote_private.tool_commands x where x.device_id = device and x.status = 'queued' and x.expires_at > now()
            order by x.created_at limit 1 for update skip locked;
        exit when c.id is null;
        select * into r from remote_private.tool_runs where id = c.run_id;
        select * into v from remote_private.tool_script_versions where id = r.version_id;
        select * into s from remote_private.tool_scripts where id = r.script_id;
        select * into e from remote_private.tools_effective(r.client_id);
        ok := r.cancelled_at is null and s.archived_at is null and v.revoked_at is null and v.approved_at is not null
              and e.scripts and connection = c.connection_id;
        if ok then
            update remote_private.tool_commands set status = 'sent', sent_at = now() where id = c.id;
            update remote_private.tool_run_devices set status = 'running', started_at = now()
                where run_id = c.run_id and device_id = device and command_id = c.id and status = 'queued';
            return c.payload || jsonb_build_object('device', device, 'connection_id', connection);
        end if;
        why := case when r.cancelled_at is not null then 'cancelled_by_user' when not e.scripts then 'policy_off' else 'script_revoked' end;
        update remote_private.tool_commands set status = 'cancelled' where id = c.id;
        update remote_private.tool_run_devices set status = 'cancelled', finished_at = now(), message = why
            where run_id = c.run_id and device_id = device and command_id = c.id and status = 'queued';
        perform remote_private.tools_run_settle(c.run_id);
    end loop;
    return null;
end;
$$;

create function remote_private.tools_command_result(device uuid, command uuid, exit integer, output text) returns boolean
language plpgsql security definer set search_path = '' as $$
declare c remote_private.tool_commands;
begin
    select * into c from remote_private.tool_commands x where x.id = command and x.device_id = device and x.status = 'sent';
    if c.id is null then return false; end if;
    return remote_private.tools_finish(c.run_id, device, exit, output, null, false);
end;
$$;

---------------------------------------------------------------------------------------------------
-- 5. Audit and reports

create function public.remote_tools_audit(company uuid default null, from_at timestamptz default null, to_at timestamptz default null,
    device uuid default null, actor uuid default null, kinds text[] default null, search text default null,
    limit_rows integer default 50, before_id bigint default null)
returns table (id bigint, occurred_at timestamptz, kind text, device_id uuid, device_name text, actor_id uuid, actor_name text,
               session_id uuid, source text, summary text, result text)
language plpgsql stable security definer set search_path = '' as $$
declare ctx remote_private.tools_ctx; pattern text;
begin
    ctx := remote_private.tools_context(company, false);
    if search is not null and char_length(search) > 80 then
        perform remote_private.raise_hint('22023', 'Search text too long', 'invalid_search');
    end if;
    pattern := '%' || replace(replace(replace(lower(btrim(coalesce(search, ''))), '\', '\\'), '%', '\%'), '_', '\_') || '%';
    return query
        select a.id, a.occurred_at, a.kind, a.device_id, a.device_name, a.actor_id, a.actor_name, a.session_id, a.source, a.summary, a.result
        from remote_private.tool_audit a
        where a.client_id = ctx.client_id
          and (ctx.role = 'admin' or a.actor_id = ctx.actor_id)
          and (from_at is null or a.occurred_at >= from_at) and (to_at is null or a.occurred_at < to_at)
          and (device is null or a.device_id = device) and (actor is null or a.actor_id = actor)
          and (kinds is null or a.kind = any(kinds))
          and (before_id is null or a.id < before_id)
          and (search is null or lower(a.summary) like pattern or lower(coalesce(a.device_name, '')) like pattern)
        order by a.id desc
        limit least(greatest(coalesce(limit_rows, 50), 1), 200);
end;
$$;

create function public.remote_tools_audit_get(id bigint) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare a remote_private.tool_audit; ctx remote_private.tools_ctx;
begin
    select * into a from remote_private.tool_audit x where x.id = remote_tools_audit_get.id;
    if a.id is null then perform remote_private.raise_hint('PT404', 'Entry not found', 'not_found'); end if;
    ctx := remote_private.tools_context(a.client_id, false);
    if ctx.role <> 'admin' and a.actor_id is distinct from ctx.actor_id then
        perform remote_private.raise_hint('PT404', 'Entry not found', 'not_found');
    end if;
    return jsonb_build_object('id', a.id, 'occurred_at', a.occurred_at, 'kind', a.kind, 'device_id', a.device_id,
        'device_name', a.device_name, 'actor_id', a.actor_id, 'actor_name', a.actor_name, 'session_id', a.session_id,
        'source', a.source, 'summary', a.summary, 'result', a.result, 'detail', a.detail);
end;
$$;

-- Broker (role mixel_remote_presence): the host's ToolsAudit records.
-- Body {"report": {"records": [{"session", "device", "technician", "kind", "data", "at"}]}}.
create function public.remote_tools_audit_report(report jsonb) returns integer
language plpgsql security definer set search_path = '' as $$
declare r jsonb; n integer := 0; d record; kind text; who public.users; allowed text[] := array['terminal.shell', 'terminal.elevate',
    'terminal.command', 'terminal.exit', 'tasks.end_process', 'tasks.service', 'tasks.view', 'script.run', 'script.refused', 'quality.set'];
    tid uuid;
begin
    if report is null or jsonb_typeof(report) <> 'object' or jsonb_typeof(report->'records') <> 'array'
       or jsonb_array_length(report->'records') > 500 then
        raise exception 'Invalid tools audit report' using errcode = '22023';
    end if;
    for r in select x from jsonb_array_elements(report->'records') x loop
        kind := r->>'kind';
        if jsonb_typeof(r) <> 'object' or remote_saas.json_uuid(r->'device') is null or kind is null or not (kind = any(allowed))
           or jsonb_typeof(r->'data') <> 'object' or octet_length((r->'data')::text) > 8192 then
            continue;
        end if;
        select dv.id, dv.client_id, dv.name into d from public.devices dv where dv.id = remote_saas.json_uuid(r->'device');
        if d.id is null then continue; end if;
        who := null;
        if remote_saas.json_uuid(r->'technician') is not null then
            select * into who from public.users u where u.id = remote_saas.json_uuid(r->'technician');
        end if;
        select t.id into tid from remote_saas.tenants t where t.client_id = d.client_id;
        insert into remote_private.tool_audit(client_id, occurred_at, kind, device_id, device_name, actor_id, actor_name, session_id, source,
                                              summary, result, detail)
        values (d.client_id, coalesce(remote_saas.json_time(r->'at'), now()), kind, d.id, left(d.name, 200), who.id,
                left(coalesce(nullif(who.name, ''), who.email), 200), remote_saas.json_uuid(r->'session'), 'host',
                left(coalesce(r->'data'->>'line', r->'data'->>'script_ref', r->'data'->>'reason', initcap(replace(kind, '.', ' '))), 300),
                left(coalesce(r->'data'->>'result', r->'data'->>'exit_code'), 60), r->'data');
        if tid is not null then
            perform remote_saas.audit(tid, who.id, kind, d.id::text, jsonb_build_object('source', 'host'));
        end if;
        n := n + 1;
    end loop;
    return n;
end;
$$;

---------------------------------------------------------------------------------------------------
-- Grants

do $$ declare f text; begin
    for f in select p.oid::regprocedure::text from pg_proc p
             where p.pronamespace = 'remote_private'::regnamespace
               and (p.proname like 'tools\_%' or p.proname = 'tool_version_guard') loop
        execute format('revoke all on function %s from public, anon, authenticated', f);
    end loop;
    for f in select p.oid::regprocedure::text from pg_proc p
             where p.pronamespace = 'public'::regnamespace and p.proname like 'remote\_tools\_%'
               and p.proname <> 'remote_tools_audit_report' loop
        execute format('revoke all on function %s from public, anon', f);
        execute format('grant execute on function %s to authenticated', f);
    end loop;
end $$;
revoke all on function public.remote_tools_audit_report(jsonb) from public, anon, authenticated;
grant execute on function public.remote_tools_audit_report(jsonb) to mixel_remote_presence;
grant execute on function remote_private.tools_policy_for(integer, uuid, uuid, bigint, bytea),
    remote_private.tools_next_command(integer, uuid), remote_private.tools_command_result(uuid, uuid, integer, text)
    to mixel_remote_policy_reader;

commit;
notify pgrst, 'reload schema';
