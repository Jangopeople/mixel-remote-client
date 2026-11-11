-- Mass rollout: a team member creates a company enrolment token; computers install with it and
-- enrol themselves (always-on access) without anyone signing in on them. The token is a secret
-- (only its SHA-256 is stored), limited in time and number of computers, and can be revoked.
begin;

create table if not exists remote_private.rollout_tokens (
    id uuid primary key default gen_random_uuid(),
    token_hash bytea not null unique check (octet_length(token_hash) = 32),
    client_id uuid not null references public.clients(id) on delete cascade,
    created_by uuid not null references public.users(id) on delete restrict,
    label text not null check (char_length(label) between 1 and 80),
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    max_uses integer not null check (max_uses between 1 and 10000),
    uses integer not null default 0,
    revoked_at timestamptz
);
revoke all on remote_private.rollout_tokens from public, anon, authenticated;

-- Staff: create a token for a linked company; returns the secret once.
create or replace function public.remote_create_rollout_token(target_client uuid, token_label text, valid_days integer, computers integer)
returns text
language plpgsql security definer set search_path = '' as $$
declare actor uuid; secret text;
begin
    actor := remote_private.staff_actor();
    if valid_days not between 1 and 365 or computers not between 1 and 10000
        or token_label is null or char_length(btrim(token_label)) not between 1 and 80 then
        raise exception 'Invalid rollout token' using errcode = '22023';
    end if;
    perform 1 from public.clients c
        join public.organizations o on o.id = c.organization_id and o.is_active and o.mixel_org_id is not null
        where c.id = target_client and c.is_active;
    if not found then raise exception 'Company unavailable for Remote devices' using errcode = '42501'; end if;
    secret := 'mrt_' || translate(pg_catalog.encode(public.gen_random_bytes(32), 'base64'), '+/=', '-_');
    insert into remote_private.rollout_tokens(token_hash, client_id, created_by, label, expires_at, max_uses)
    values (pg_catalog.sha256(convert_to(secret, 'UTF8')), target_client, actor, btrim(token_label),
            now() + make_interval(days => valid_days), computers);
    return secret;
end;
$$;

-- Staff: the tokens of a company (no secrets).
create or replace function public.remote_rollout_tokens(target_client uuid)
returns table(id uuid, label text, created_at timestamptz, expires_at timestamptz, max_uses integer, uses integer, revoked_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
begin
    perform remote_private.staff_actor();
    return query select t.id, t.label, t.created_at, t.expires_at, t.max_uses, t.uses, t.revoked_at
        from remote_private.rollout_tokens t where t.client_id = target_client order by t.created_at desc limit 100;
end;
$$;

create or replace function public.remote_revoke_rollout_token(token_id uuid)
returns boolean
language plpgsql security definer set search_path = '' as $$
begin
    perform remote_private.staff_actor();
    update remote_private.rollout_tokens set revoked_at = now() where id = token_id and revoked_at is null;
    return found;
end;
$$;

-- Computer (anonymous, holds the token): add itself to the token's company and enrol its key.
create or replace function public.remote_enroll_with_token(token text, device_name text, device_os text, key_material bytea)
returns table(device_id uuid, connection_id integer, generation bigint, profile uuid, tenant uuid)
language plpgsql security definer set search_path = '' as $$
declare grant_row remote_private.rollout_tokens; clean_name text := btrim(device_name); name_try text;
        created uuid; enrolled public.remote_device_enrollments; org uuid; candidate bigint;
begin
    if token is null or char_length(token) not between 20 and 100 or octet_length(key_material) is distinct from 32
        or clean_name is null or char_length(clean_name) not between 1 and 60 or clean_name ~ '[[:cntrl:]]'
        or device_os not in ('windows', 'macos', 'linux') then
        raise exception 'Invalid rollout request' using errcode = '22023';
    end if;
    select * into grant_row from remote_private.rollout_tokens t
        where t.token_hash = pg_catalog.sha256(convert_to(token, 'UTF8')) for update;
    if not found or grant_row.revoked_at is not null or grant_row.expires_at <= now() then
        raise exception 'Rollout token invalid, expired or used up' using errcode = '42501';
    end if;
    select o.id into org from public.clients c
        join public.organizations o on o.id = c.organization_id and o.is_active and o.mixel_org_id is not null
        where c.id = grant_row.client_id and c.is_active;
    if org is null then raise exception 'Company unavailable for Remote devices' using errcode = '42501'; end if;
    perform 1 from public.users u where u.id = grant_row.created_by and u.is_active and u.deleted_at is null;
    if not found then raise exception 'Rollout token owner is no longer active' using errcode = '42501'; end if;
    -- A key that is already enrolled returns its record (re-running the installer is harmless).
    select e.* into enrolled from public.remote_device_enrollments e
        join public.devices d on d.id = e.device_id and d.client_id = grant_row.client_id
        where e.public_key = key_material and e.revoked_at is null;
    if found then
        return query select enrolled.device_id, enrolled.connection_id, enrolled.generation, enrolled.enrolled_by, org;
        return;
    end if;
    if grant_row.uses >= grant_row.max_uses then
        raise exception 'Rollout token invalid, expired or used up' using errcode = '42501';
    end if;
    name_try := clean_name;
    for attempt in 1..20 loop
        exit when not exists (select 1 from public.devices d where d.client_id = grant_row.client_id and lower(d.name) = lower(name_try));
        name_try := clean_name || ' (' || (attempt + 1) || ')';
    end loop;
    insert into public.devices(client_id, name, type, os)
        values (grant_row.client_id, name_try, 'desktop', device_os::public.ism_device_os) returning id into created;
    for attempt in 1..128 loop
        candidate := (('x' || pg_catalog.encode(pg_catalog.substring(pg_catalog.uuid_send(pg_catalog.gen_random_uuid()),1,4),'hex'))::bit(32)::bigint);
        if candidate >= 3600000000 then continue; end if;
        candidate := 100000000 + candidate % 900000000;
        begin
            insert into public.remote_device_enrollments(device_id, connection_id, public_key, enrolled_by)
                values (created, candidate, key_material, grant_row.created_by) returning * into enrolled;
        exception when unique_violation then
            continue;
        end;
        insert into remote_private.enrollment_audit(device_id, actor_id, action, next_key, generation)
            values (created, grant_row.created_by, 'enroll', key_material, enrolled.generation);
        update remote_private.rollout_tokens set uses = uses + 1 where id = grant_row.id;
        return query select enrolled.device_id, enrolled.connection_id, enrolled.generation, enrolled.enrolled_by, org;
        return;
    end loop;
    raise exception 'Connection identifier allocation exhausted' using errcode = '54000';
end;
$$;

revoke all on function public.remote_create_rollout_token(uuid,text,integer,integer), public.remote_rollout_tokens(uuid),
    public.remote_revoke_rollout_token(uuid), public.remote_enroll_with_token(text,text,text,bytea) from public;
grant execute on function public.remote_create_rollout_token(uuid,text,integer,integer), public.remote_rollout_tokens(uuid),
    public.remote_revoke_rollout_token(uuid) to authenticated;
grant execute on function public.remote_enroll_with_token(text,text,text,bytea) to anon, authenticated;
commit;
