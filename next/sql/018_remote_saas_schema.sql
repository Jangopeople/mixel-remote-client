-- Mixel Remote as SaaS, stage 1 (schema). A SaaS customer company is a tenant in schema
-- remote_saas with its own ISM organization (kind 'remote_saas') and one ISM client "Devices", so
-- devices, enrollments and the connection broker keep working unchanged. Tenant users are
-- public.users rows with that organization_id; their Remote role lives in remote_saas.members.
-- Every remote_saas table is reached only through SECURITY DEFINER functions that check membership
-- and role. There are no grants on these tables to anon or authenticated.
begin;
set local lock_timeout = '5s';

-- ISM organizations: ordinary ISM customers ('ism') or Remote SaaS tenants ('remote_saas').
alter table public.organizations add column if not exists kind text not null default 'ism';
do $$ begin
    alter table public.organizations add constraint organizations_kind_check
        check (kind in ('ism', 'remote_saas'));
exception when duplicate_object then null; end $$;

create schema remote_saas;
revoke all on schema remote_saas from public, anon, authenticated;

create table remote_saas.plans (
    code text primary key check (code in ('personal', 'solo', 'team', 'business', 'enterprise')),
    name text not null,
    -- CHF per month with yearly billing; monthly billing is +20 %. Null: on request.
    price_chf_month numeric(8, 2),
    extra_technician_chf_month numeric(8, 2),
    -- Null: set per tenant (Enterprise). A tenant without an override then gets none.
    max_technicians integer check (max_technicians >= 0),
    max_devices integer check (max_devices >= 0),
    max_sessions integer check (max_sessions >= 0),
    features jsonb not null,
    sort smallint not null unique
);
insert into remote_saas.plans values
    ('personal', 'Personal', 0, null, 1, 10, 1,
     '{"phone_support":true,"browser_viewer":true,"files":true,"recording":true,"meetings":true,"private_use_only":true,"groups":false,"access_rights":false,"sso":false,"audit_log":false,"policies":false,"require_mfa":false}', 1),
    ('solo', 'Solo', 12, 8, 1, 100, 1,
     '{"phone_support":true,"browser_viewer":true,"files":true,"recording":true,"meetings":true,"private_use_only":false,"groups":false,"access_rights":false,"sso":false,"audit_log":false,"policies":false,"require_mfa":false}', 2),
    ('team', 'Team', 39, 8, 5, 500, 3,
     '{"phone_support":true,"browser_viewer":true,"files":true,"recording":true,"meetings":true,"private_use_only":false,"groups":true,"access_rights":true,"sso":false,"audit_log":false,"policies":false,"require_mfa":false}', 3),
    ('business', 'Business', 89, 8, 15, 2000, 10,
     '{"phone_support":true,"browser_viewer":true,"files":true,"recording":true,"meetings":true,"private_use_only":false,"groups":true,"access_rights":true,"sso":true,"audit_log":true,"policies":true,"require_mfa":true}', 4),
    ('enterprise', 'Enterprise', null, 8, null, null, null,
     '{"phone_support":true,"browser_viewer":true,"files":true,"recording":true,"meetings":true,"private_use_only":false,"groups":true,"access_rights":true,"sso":true,"audit_log":true,"policies":true,"require_mfa":true}', 5);

create table remote_saas.tenants (
    id uuid primary key default gen_random_uuid(),
    name text not null check (char_length(name) between 2 and 80),
    slug text not null unique check (slug ~ '^[a-z0-9]([a-z0-9-]{0,46}[a-z0-9])?$'),
    organization_id uuid not null unique references public.organizations(id) on delete restrict,
    client_id uuid not null unique references public.clients(id) on delete restrict,
    -- The free Personal plan is the existing personal account (remote_private.personal_accounts).
    plan text not null references remote_saas.plans(code) check (plan <> 'personal'),
    status text not null check (status in ('active', 'trial', 'past_due', 'read_only', 'closed')),
    trial_ends_at timestamptz,
    extra_technicians integer not null default 0 check (extra_technicians between 0 and 10000),
    max_technicians_override integer check (max_technicians_override >= 0),
    max_devices_override integer check (max_devices_override >= 0),
    max_sessions_override integer check (max_sessions_override >= 0),
    created_by uuid not null references public.users(id) on delete restrict,
    created_at timestamptz not null default now(),
    status_changed_at timestamptz not null default now(),
    check (status <> 'trial' or trial_ends_at is not null)
);

-- One profile belongs to one organization (public.users.organization_id), so to one tenant.
create table remote_saas.members (
    tenant_id uuid not null references remote_saas.tenants(id) on delete cascade,
    user_id uuid not null unique references public.users(id) on delete cascade,
    role text not null check (role in ('owner', 'admin', 'technician', 'member')),
    created_at timestamptz not null default now(),
    primary key (tenant_id, user_id)
);

create table remote_saas.invites (
    id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references remote_saas.tenants(id) on delete cascade,
    email text not null check (char_length(email) between 3 and 254 and email = lower(btrim(email))
        and email ~ '^[^@[:space:][:cntrl:]]+@[^@[:space:][:cntrl:]]+\.[^@[:space:][:cntrl:]]+$'),
    role text not null check (role in ('admin', 'technician', 'member')),
    token_hash bytea not null unique check (octet_length(token_hash) = 32),
    invited_by uuid not null references public.users(id) on delete restrict,
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    accepted_at timestamptz,
    accepted_by uuid references public.users(id) on delete set null,
    revoked_at timestamptz
);
create unique index invites_one_open_per_email on remote_saas.invites(tenant_id, email)
    where accepted_at is null and revoked_at is null;

create table remote_saas.auth_settings (
    tenant_id uuid primary key references remote_saas.tenants(id) on delete cascade,
    methods text[] not null default '{password,magic_link,google,azure,apple,mixel_id}'
        check (cardinality(methods) between 1 and 7
            and methods <@ '{password,magic_link,google,azure,apple,mixel_id,sso}'::text[]),
    require_mfa boolean not null default false,
    -- GoTrue SSO provider (SAML 2.0) for 'sso'.
    sso_provider_id uuid,
    updated_by uuid references public.users(id) on delete set null,
    updated_at timestamptz not null default now(),
    check (not ('sso' = any(methods)) or sso_provider_id is not null)
);

-- Email domains of a tenant. Only a verified domain is used to pick sign-in methods for an
-- email address, so a tenant cannot steer other companies' users to its own sign-in.
create table remote_saas.domains (
    tenant_id uuid not null references remote_saas.tenants(id) on delete cascade,
    domain text not null check (char_length(domain) <= 253
        and domain ~ '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$'),
    verification_token text not null,
    created_at timestamptz not null default now(),
    verified_at timestamptz,
    primary key (tenant_id, domain)
);
create unique index domains_verified_once on remote_saas.domains(domain) where verified_at is not null;

create table remote_saas.device_groups (
    id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references remote_saas.tenants(id) on delete cascade,
    name text not null check (char_length(name) between 1 and 60 and name !~ '[[:cntrl:]]'
        and name = btrim(name)),
    created_at timestamptz not null default now(),
    unique (id, tenant_id)
);
create unique index device_groups_name on remote_saas.device_groups(tenant_id, lower(name));

-- Technicians reach the devices of the groups they are granted, plus devices in no group.
-- Owners and admins reach every device of their tenant.
create table remote_saas.group_access (
    group_id uuid not null,
    tenant_id uuid not null,
    user_id uuid not null,
    granted_by uuid references public.users(id) on delete set null,
    granted_at timestamptz not null default now(),
    primary key (group_id, user_id),
    foreign key (group_id, tenant_id) references remote_saas.device_groups(id, tenant_id) on delete cascade,
    foreign key (tenant_id, user_id) references remote_saas.members(tenant_id, user_id) on delete cascade
);

create table remote_saas.device_meta (
    device_id uuid primary key references public.devices(id) on delete cascade,
    tenant_id uuid not null references remote_saas.tenants(id) on delete cascade,
    group_id uuid,
    display_name text check (display_name is null or (char_length(display_name) between 1 and 80
        and display_name !~ '[[:cntrl:]]')),
    added_by uuid references public.users(id) on delete set null,
    added_via text not null check (added_via in ('this_device', 'deploy_link', 'staff')),
    created_at timestamptz not null default now(),
    foreign key (group_id, tenant_id) references remote_saas.device_groups(id, tenant_id)
        on delete set null (group_id)
);
create index device_meta_tenant on remote_saas.device_meta(tenant_id, group_id);

-- Tenant audit log (member, settings, group and device actions). Device enrolment actions are
-- recorded in remote_private.enrollment_audit as well.
create table remote_saas.audit_log (
    id bigint generated always as identity primary key,
    tenant_id uuid not null references remote_saas.tenants(id) on delete cascade,
    actor_id uuid references public.users(id) on delete set null,
    action text not null check (char_length(action) between 1 and 60),
    target text check (char_length(target) <= 200),
    detail jsonb not null default '{}'::jsonb,
    occurred_at timestamptz not null default now()
);
create index audit_log_tenant on remote_saas.audit_log(tenant_id, id desc);

-- Deploy links reuse rollout tokens; a SaaS link may place new devices in a group.
alter table remote_private.rollout_tokens add column if not exists saas_group_id uuid
    references remote_saas.device_groups(id) on delete set null;

-- The audit trail outlives a removed device: drop the restrict FK so a device can be deleted, and
-- record the new device-management actions.
alter table remote_private.enrollment_audit drop constraint if exists enrollment_audit_device_id_fkey;
alter table remote_private.enrollment_audit drop constraint if exists enrollment_audit_action_check;
alter table remote_private.enrollment_audit add constraint enrollment_audit_action_check
    check (action in ('enroll', 'revoke', 'rotate', 'rename', 'move', 'remove', 'self_remove'));

do $$ declare t text; begin
    foreach t in array array['plans','tenants','members','invites','auth_settings','domains',
                             'device_groups','group_access','device_meta','audit_log'] loop
        execute format('alter table remote_saas.%I enable row level security', t);
        execute format('revoke all on remote_saas.%I from public, anon, authenticated', t);
    end loop;
end $$;

---------------------------------------------------------------------------------------------------
-- Internal helpers (not exposed: schema remote_saas is not an API schema and has no grants).

-- A trial that ran out without payment is read-only until billing changes the status.
create function remote_saas.effective_status(t remote_saas.tenants) returns text
language sql stable set search_path = '' as $$
    select case when t.status = 'trial' and t.trial_ends_at <= now() then 'read_only' else t.status end;
$$;

create function remote_saas.limits(target uuid)
returns table (plan text, status text, max_technicians integer, max_devices integer,
               max_sessions integer, features jsonb)
language sql stable security definer set search_path = '' as $$
    select p.code, remote_saas.effective_status(t),
        coalesce(t.max_technicians_override, p.max_technicians + t.extra_technicians, 0),
        coalesce(t.max_devices_override, p.max_devices, 0),
        coalesce(t.max_sessions_override, p.max_sessions, 0),
        p.features
    from remote_saas.tenants t join remote_saas.plans p on p.code = t.plan
    where t.id = target;
$$;

create function remote_saas.technician_count(target uuid) returns integer
language sql stable security definer set search_path = '' as $$
    select count(*)::integer from remote_saas.members m
    where m.tenant_id = target and m.role in ('owner', 'admin', 'technician');
$$;

create function remote_saas.device_count(target uuid) returns integer
language sql stable security definer set search_path = '' as $$
    select count(*)::integer from public.devices d
    join remote_saas.tenants t on t.client_id = d.client_id where t.id = target;
$$;

-- The sign-in methods of the current session, from the GoTrue "amr" claim. OAuth sign-ins are
-- mapped through the account's linked identities (GoTrue does not name the OAuth provider in amr).
create function remote_saas.session_methods() returns text[]
language sql stable security definer set search_path = '' as $$
    with amr as (
        select e->>'method' as method
        from jsonb_array_elements(case when jsonb_typeof(auth.jwt()->'amr') = 'array'
            then auth.jwt()->'amr' else '[]'::jsonb end) e
    ), mapped as (
        select case
            when method = 'password' then 'password'
            when method in ('otp', 'magiclink', 'email/signup', 'invite', 'recovery') then 'magic_link'
            when method = 'sso/saml' then 'sso'
        end as m from amr
        union all
        select case i.provider when 'google' then 'google' when 'azure' then 'azure'
            when 'apple' then 'apple' when 'keycloak' then 'mixel_id' end
        from auth.identities i
        where i.user_id = auth.uid() and exists (select 1 from amr where method = 'oauth')
    )
    select coalesce(array_agg(distinct m) filter (where m is not null), '{}'::text[]) from mapped;
$$;

create function remote_saas.method_allowed(allowed text[]) returns boolean
language sql stable security definer set search_path = '' as $$
    select case
        -- A session without a recognisable method passes only an unrestricted tenant.
        when cardinality(remote_saas.session_methods()) = 0
            then allowed @> '{password,magic_link,google,azure,apple,mixel_id}'::text[]
        else remote_saas.session_methods() && allowed
    end;
$$;

-- The calling member of a tenant with one of the given roles, or an exception.
-- need = 'read': any status but closed (lists, removals); 'write': active, trial or past_due.
create function remote_saas.actor(target uuid, roles text[], need text default 'write')
returns remote_saas.members
language plpgsql stable security definer set search_path = '' as $$
declare t remote_saas.tenants; m remote_saas.members; s remote_saas.auth_settings; st text;
begin
    select * into t from remote_saas.tenants where id = target;
    if not found then raise exception 'Not allowed for this company' using errcode = '42501'; end if;
    select mm.* into m from remote_saas.members mm
        join public.users u on u.id = mm.user_id
        join public.organizations o on o.id = t.organization_id and o.is_active
        where mm.tenant_id = target and u.auth_user_id = auth.uid() and auth.uid() is not null
          and u.is_active and u.deleted_at is null and u.organization_id = t.organization_id;
    if not found or not (m.role = any(roles)) then
        raise exception 'Not allowed for this company' using errcode = '42501';
    end if;
    st := remote_saas.effective_status(t);
    if st = 'closed' or (need = 'write' and st not in ('active', 'trial', 'past_due')) then
        raise exception 'This Mixel Remote account is read-only' using errcode = '25006';
    end if;
    select * into s from remote_saas.auth_settings where tenant_id = target;
    if found then
        if s.require_mfa and coalesce(auth.jwt()->>'aal', '') <> 'aal2' then
            raise exception 'Two-step sign-in is required by this company' using errcode = '42501';
        end if;
        if not remote_saas.method_allowed(s.methods) then
            raise exception 'This sign-in method is not allowed by this company' using errcode = '42501';
        end if;
    end if;
    return m;
end;
$$;

-- May this tenant member reach this device? Owners and admins: every device of the tenant.
-- Technicians: devices in no group and in groups they are granted. Members: the devices they added.
create function remote_saas.can_access_device(profile uuid, target uuid) returns boolean
language sql stable security definer set search_path = '' as $$
    select exists (
        select 1 from remote_saas.members m
        join remote_saas.tenants t on t.id = m.tenant_id
        join public.devices d on d.id = target and d.client_id = t.client_id
        left join remote_saas.device_meta dm on dm.device_id = d.id
        left join public.remote_device_enrollments e on e.device_id = d.id
        where m.user_id = profile
          and (m.role in ('owner', 'admin')
            or (m.role = 'technician' and (dm.group_id is null or exists (
                select 1 from remote_saas.group_access ga
                where ga.group_id = dm.group_id and ga.user_id = profile)))
            or (m.role = 'member' and (dm.added_by = profile or e.enrolled_by = profile)))
    );
$$;

create function remote_saas.audit(target uuid, actor uuid, what text, about text, detail jsonb default '{}'::jsonb)
returns void language sql security definer set search_path = '' as $$
    insert into remote_saas.audit_log(tenant_id, actor_id, action, target, detail)
    values (target, actor, what, about, coalesce(detail, '{}'::jsonb));
$$;

-- public.guard_users_privilege_escalation lets only Mixel staff move a profile between
-- organizations and trusts a statement without a JWT subject (migrations, provisioning). The
-- callers of this helper have already checked that the move is the caller's own sign-up or
-- invitation, or a removal by an authorized tenant owner/admin; the subject is hidden for this one
-- update and restored right after it.
create function remote_saas.set_profile_org(profile uuid, org uuid) returns void
language plpgsql security definer set search_path = '' as $$
declare claims text := current_setting('request.jwt.claims', true);
        subject text := current_setting('request.jwt.claim.sub', true);
begin
    perform set_config('request.jwt.claims', '', true);
    perform set_config('request.jwt.claim.sub', '', true);
    update public.users set organization_id = org, updated_at = now() where id = profile;
    perform set_config('request.jwt.claims', coalesce(claims, ''), true);
    perform set_config('request.jwt.claim.sub', coalesce(subject, ''), true);
end;
$$;

-- Connection IDs: same unbiased allocation as the other enrollment paths.
create function remote_saas.enroll_key(target uuid, key_material bytea, actor uuid)
returns public.remote_device_enrollments
language plpgsql security definer set search_path = '' as $$
declare enrolled public.remote_device_enrollments; candidate bigint; reserved remote_private.connection_reservations;
begin
    -- A computer that was removed earlier keeps its key and connection ID reserved. Adding it again
    -- reuses that reservation, but only when the reserved device record is gone (never another
    -- live device, a personal device or a phone).
    select * into reserved from remote_private.connection_reservations r where r.public_key = key_material for update;
    if found then
        if exists (select 1 from public.remote_device_enrollments e where e.connection_id = reserved.connection_id or e.public_key = key_material)
            or exists (select 1 from public.devices d where d.id = reserved.device_id)
            or exists (select 1 from remote_private.personal_devices p where p.connection_id = reserved.connection_id)
            or exists (select 1 from remote_private.quick_phones q where q.public_key = key_material or q.connection_id = reserved.connection_id) then
            raise exception 'Device key already registered' using errcode = '42501';
        end if;
        delete from remote_private.connection_reservations where connection_id = reserved.connection_id;
        insert into public.remote_device_enrollments(device_id, connection_id, public_key, enrolled_by)
        values (target, reserved.connection_id, key_material, actor) returning * into enrolled;
        insert into remote_private.enrollment_audit(device_id, actor_id, action, next_key, generation)
        values (target, actor, 'enroll', key_material, enrolled.generation);
        return enrolled;
    end if;
    for attempt in 1..128 loop
        candidate := (('x' || pg_catalog.encode(pg_catalog.substring(pg_catalog.uuid_send(pg_catalog.gen_random_uuid()), 1, 4), 'hex'))::bit(32)::bigint);
        if candidate >= 3600000000 then continue; end if;
        candidate := 100000000 + candidate % 900000000;
        continue when exists (select 1 from remote_private.quick_phones q where q.connection_id = candidate);
        begin
            insert into public.remote_device_enrollments(device_id, connection_id, public_key, enrolled_by)
            values (target, candidate, key_material, actor) returning * into enrolled;
        exception when unique_violation then
            if exists (select 1 from public.remote_device_enrollments e where e.public_key = key_material)
                or exists (select 1 from remote_private.connection_reservations r where r.public_key = key_material) then
                raise exception 'Device key already registered' using errcode = '42501';
            end if;
            continue;
        end;
        insert into remote_private.enrollment_audit(device_id, actor_id, action, next_key, generation)
        values (target, actor, 'enroll', key_material, enrolled.generation);
        return enrolled;
    end loop;
    raise exception 'Connection identifier allocation exhausted' using errcode = '54000';
end;
$$;

-- Revoke and delete a device of a tenant (enrollment, metadata, ISM device record). The connection
-- ID and key stay reserved in remote_private.connection_reservations and are never reassigned.
-- Running sessions end at the broker's next policy check, which fails for a missing enrollment.
create function remote_saas.remove_device(target uuid, actor uuid, what text) returns void
language plpgsql security definer set search_path = '' as $$
declare existing public.remote_device_enrollments;
begin
    perform 1 from public.devices where id = target for update;
    if not found then return; end if;
    select * into existing from public.remote_device_enrollments where device_id = target for update;
    if found then
        if existing.revoked_at is null then
            update public.remote_device_enrollments set revoked_at = now(), generation = generation + 1
                where device_id = target;
            insert into remote_private.enrollment_audit(device_id, actor_id, action, previous_key, generation)
            values (target, actor, 'revoke', existing.public_key, existing.generation + 1);
            existing.generation := existing.generation + 1;
        end if;
        insert into remote_private.enrollment_audit(device_id, actor_id, action, previous_key, generation)
        values (target, actor, what, existing.public_key, existing.generation);
        delete from public.remote_device_enrollments where device_id = target;
    end if;
    delete from public.devices where id = target;
end;
$$;

do $$ declare f text; begin
    for f in select p.oid::regprocedure::text from pg_proc p
             where p.pronamespace = 'remote_saas'::regnamespace loop
        execute format('revoke all on function %s from public, anon, authenticated', f);
    end loop;
end $$;

commit;
