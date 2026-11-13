-- Mixel Remote as SaaS, stage 1: sign-up with a 14-day Business trial, members, invitations,
-- sign-in settings and the tenant audit log. All functions are SECURITY DEFINER with an empty
-- search_path; each checks the caller's membership and role through remote_saas.actor().
begin;

-- Self-service sign-up. The signed-in account (confirmed email, no company yet) becomes the owner
-- of a new tenant with its own ISM organization and ISM client "Devices".
create function public.remote_saas_signup(company_name text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    clean_name text := btrim(company_name);
    account auth.users;
    profile public.users;
    base text; candidate text;
    org uuid; client uuid; tenant uuid; owner_profile uuid;
    trial_end timestamptz := now() + interval '14 days';
begin
    select * into account from auth.users u where u.id = auth.uid();
    if auth.uid() is null or not found or account.deleted_at is not null
        or (account.banned_until is not null and account.banned_until > now()) then
        raise exception 'Sign in first' using errcode = '42501';
    end if;
    if account.email_confirmed_at is null or nullif(btrim(account.email), '') is null then
        raise exception 'Confirm your email address first' using errcode = '42501';
    end if;
    if clean_name is null or char_length(clean_name) not between 2 and 80 or clean_name ~ '[[:cntrl:]]' then
        raise exception 'Enter a company name of 2 to 80 characters' using errcode = '22023';
    end if;
    -- Serialize sign-ups of the same account.
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('remote_saas_signup:' || auth.uid()::text, 0));
    if (select count(*) from remote_saas.tenants t where t.created_at > now() - interval '1 hour') >= 50 then
        raise exception 'Too many sign-ups right now, please try again later' using errcode = '54000';
    end if;
    select * into profile from public.users u where u.auth_user_id = auth.uid();
    if found then
        if profile.organization_id is not null or profile.role::text in ('admin', 'agent')
            or not profile.is_active or profile.deleted_at is not null then
            raise exception 'This account already belongs to a company. Sign up with another email address.'
                using errcode = '42501';
        end if;
    elsif exists (select 1 from public.users u where lower(u.email) = lower(account.email)) then
        raise exception 'This email address is already used by another account' using errcode = '42501';
    end if;

    base := left(btrim(regexp_replace(lower(clean_name), '[^a-z0-9]+', '-', 'g'), '-'), 40);
    if base = '' then base := 'company'; end if;
    candidate := base;
    for attempt in 1..20 loop
        exit when not exists (select 1 from public.organizations o where o.slug = candidate)
              and not exists (select 1 from remote_saas.tenants t where t.slug = candidate);
        candidate := left(base, 40) || '-' || substr(md5(gen_random_uuid()::text), 1, 6);
    end loop;

    insert into public.organizations(name, slug, is_active, kind)
    values (clean_name, candidate, true, 'remote_saas') returning id into org;
    insert into public.clients(name, key, organization_id, is_active)
    values ('Devices', 'remote-saas-' || replace(org::text, '-', ''), org, true) returning id into client;
    if profile.id is null then
        insert into public.users(auth_user_id, name, email, role, organization_id, is_active, email_verified_at)
        values (auth.uid(),
                coalesce(nullif(btrim(left(account.raw_user_meta_data->>'name', 80)), ''), split_part(account.email, '@', 1)),
                lower(btrim(account.email)), 'user', org, true, account.email_confirmed_at)
        returning id into owner_profile;
    else
        owner_profile := profile.id;
        perform remote_saas.set_profile_org(owner_profile, org);
    end if;
    update public.organizations set owner_id = owner_profile where id = org;
    insert into remote_saas.tenants(name, slug, organization_id, client_id, plan, status, trial_ends_at, created_by)
    values (clean_name, candidate, org, client, 'business', 'trial', trial_end, owner_profile)
    returning id into tenant;
    insert into remote_saas.members(tenant_id, user_id, role) values (tenant, owner_profile, 'owner');
    insert into remote_saas.auth_settings(tenant_id, updated_by) values (tenant, owner_profile);
    perform remote_saas.audit(tenant, owner_profile, 'tenant.signup', clean_name,
        jsonb_build_object('plan', 'business', 'trial_ends_at', trial_end));
    return jsonb_build_object('tenant_id', tenant, 'organization_id', org, 'client_id', client,
        'slug', candidate, 'plan', 'business', 'status', 'trial', 'trial_ends_at', trial_end);
end;
$$;

-- The tenants of the signed-in account (at most one: a profile belongs to one organization).
create function public.remote_saas_my_tenants()
returns table (tenant_id uuid, organization_id uuid, name text, slug text, role text, plan text,
               status text, trial_ends_at timestamptz)
language sql stable security definer set search_path = '' as $$
    select t.id, t.organization_id, t.name, t.slug, m.role, t.plan, remote_saas.effective_status(t), t.trial_ends_at
    from public.users u
    join remote_saas.members m on m.user_id = u.id
    join remote_saas.tenants t on t.id = m.tenant_id and t.organization_id = u.organization_id
    join public.organizations o on o.id = t.organization_id and o.is_active
    where u.auth_user_id = auth.uid() and auth.uid() is not null and u.is_active and u.deleted_at is null;
$$;

create function public.remote_saas_tenant_overview(tenant uuid)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare me remote_saas.members; t remote_saas.tenants; l record;
begin
    me := remote_saas.actor(tenant, '{owner,admin,technician,member}', 'read');
    select * into t from remote_saas.tenants where id = tenant;
    select * into l from remote_saas.limits(tenant);
    return jsonb_build_object(
        'tenant_id', t.id, 'organization_id', t.organization_id, 'name', t.name, 'slug', t.slug,
        'role', me.role, 'plan', t.plan, 'status', l.status, 'trial_ends_at', t.trial_ends_at,
        'features', l.features,
        'limits', jsonb_build_object('technicians', l.max_technicians, 'devices', l.max_devices,
                                     'sessions', l.max_sessions),
        'usage', jsonb_build_object('technicians', remote_saas.technician_count(tenant),
            'devices', remote_saas.device_count(tenant),
            'members', (select count(*) from remote_saas.members m where m.tenant_id = tenant)));
end;
$$;

---------------------------------------------------------------------------------------------------
-- Members and invitations

create function public.remote_saas_members(tenant uuid)
returns table (user_id uuid, name text, email text, role text, created_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
begin
    perform remote_saas.actor(tenant, '{owner,admin,technician}', 'read');
    return query select u.id, u.name, u.email, m.role, m.created_at
        from remote_saas.members m join public.users u on u.id = m.user_id
        where m.tenant_id = tenant
        order by case m.role when 'owner' then 0 when 'admin' then 1 when 'technician' then 2 else 3 end, lower(u.name), u.id;
end;
$$;

-- Returns the invitation secret once (the portal emails it as a link). Only its SHA-256 is stored.
create function public.remote_saas_invite_member(tenant uuid, invitee_email text, member_role text)
returns text language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; clean_email text := lower(btrim(invitee_email)); secret text; l record;
begin
    me := remote_saas.actor(tenant, '{owner,admin}');
    if member_role is null or member_role not in ('admin', 'technician', 'member') then
        raise exception 'Invalid role' using errcode = '22023';
    end if;
    if clean_email is null or char_length(clean_email) not between 3 and 254
        or clean_email !~ '^[^@[:space:][:cntrl:]]+@[^@[:space:][:cntrl:]]+\.[^@[:space:][:cntrl:]]+$' then
        raise exception 'Invalid email address' using errcode = '22023';
    end if;
    perform 1 from remote_saas.tenants where id = tenant for update;
    if exists (select 1 from remote_saas.members m join public.users u on u.id = m.user_id
               where m.tenant_id = tenant and lower(u.email) = clean_email) then
        raise exception 'This person is already a member' using errcode = '23505';
    end if;
    if (select count(*) from remote_saas.invites i where i.tenant_id = tenant and i.created_at > now() - interval '1 day') >= 100 then
        raise exception 'Too many invitations today' using errcode = '54000';
    end if;
    update remote_saas.invites set revoked_at = now()
        where tenant_id = tenant and email = clean_email and accepted_at is null and revoked_at is null;
    if member_role in ('admin', 'technician') then
        select * into l from remote_saas.limits(tenant);
        if remote_saas.technician_count(tenant) + (select count(*) from remote_saas.invites i
                where i.tenant_id = tenant and i.role in ('admin', 'technician') and i.accepted_at is null
                  and i.revoked_at is null and i.expires_at > now()) >= l.max_technicians then
            raise exception 'Technician limit of your plan reached' using errcode = '54000';
        end if;
    end if;
    secret := 'mri_' || translate(pg_catalog.encode(public.gen_random_bytes(32), 'base64'), '+/=', '-_');
    insert into remote_saas.invites(tenant_id, email, role, token_hash, invited_by, expires_at)
    values (tenant, clean_email, member_role, pg_catalog.sha256(convert_to(secret, 'UTF8')), me.user_id,
            now() + interval '7 days');
    perform remote_saas.audit(tenant, me.user_id, 'member.invite', clean_email, jsonb_build_object('role', member_role));
    return secret;
end;
$$;

create function public.remote_saas_invites(tenant uuid)
returns table (id uuid, email text, role text, created_at timestamptz, expires_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
begin
    perform remote_saas.actor(tenant, '{owner,admin}', 'read');
    return query select i.id, i.email, i.role, i.created_at, i.expires_at from remote_saas.invites i
        where i.tenant_id = tenant and i.accepted_at is null and i.revoked_at is null and i.expires_at > now()
        order by i.created_at desc limit 200;
end;
$$;

create function public.remote_saas_revoke_invite(tenant uuid, invite uuid)
returns boolean language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members;
begin
    me := remote_saas.actor(tenant, '{owner,admin}', 'read');
    update remote_saas.invites set revoked_at = now()
        where id = invite and tenant_id = tenant and accepted_at is null and revoked_at is null;
    if found then perform remote_saas.audit(tenant, me.user_id, 'member.invite_revoke', invite::text); end if;
    return found;
end;
$$;

-- The invited person, signed in with the invited (confirmed) email address, joins the tenant.
create function public.remote_saas_accept_invite(invite_token text)
returns uuid language plpgsql security definer set search_path = '' as $$
declare grant_row remote_saas.invites; account auth.users; profile public.users; t remote_saas.tenants;
        joined uuid; l record;
begin
    if invite_token is null or char_length(invite_token) not between 20 and 100 then
        raise exception 'Invitation invalid or expired' using errcode = '42501';
    end if;
    select * into grant_row from remote_saas.invites i
        where i.token_hash = pg_catalog.sha256(convert_to(invite_token, 'UTF8')) for update;
    if not found or grant_row.accepted_at is not null or grant_row.revoked_at is not null
        or grant_row.expires_at <= now() then
        raise exception 'Invitation invalid or expired' using errcode = '42501';
    end if;
    select * into account from auth.users u where u.id = auth.uid();
    if auth.uid() is null or not found or account.email_confirmed_at is null or account.deleted_at is not null
        or lower(btrim(account.email)) <> grant_row.email then
        raise exception 'Sign in with the invited email address' using errcode = '42501';
    end if;
    select * into t from remote_saas.tenants where id = grant_row.tenant_id for update;
    if remote_saas.effective_status(t) not in ('active', 'trial', 'past_due')
        or not exists (select 1 from public.organizations o where o.id = t.organization_id and o.is_active) then
        raise exception 'This Mixel Remote account is read-only' using errcode = '25006';
    end if;
    if grant_row.role in ('admin', 'technician') then
        select * into l from remote_saas.limits(t.id);
        if remote_saas.technician_count(t.id) >= l.max_technicians then
            raise exception 'Technician limit of this plan reached' using errcode = '54000';
        end if;
    end if;
    select * into profile from public.users u where u.auth_user_id = auth.uid();
    if found then
        if profile.organization_id = t.organization_id then
            raise exception 'You are already a member' using errcode = '23505';
        end if;
        if profile.organization_id is not null or profile.role::text in ('admin', 'agent')
            or not profile.is_active or profile.deleted_at is not null then
            raise exception 'This account already belongs to another company' using errcode = '42501';
        end if;
        joined := profile.id;
        perform remote_saas.set_profile_org(joined, t.organization_id);
    else
        if exists (select 1 from public.users u where lower(u.email) = grant_row.email) then
            raise exception 'This email address is already used by another account' using errcode = '42501';
        end if;
        insert into public.users(auth_user_id, name, email, role, organization_id, is_active, email_verified_at)
        values (auth.uid(),
                coalesce(nullif(btrim(left(account.raw_user_meta_data->>'name', 80)), ''), split_part(account.email, '@', 1)),
                grant_row.email, 'user', t.organization_id, true, account.email_confirmed_at)
        returning id into joined;
    end if;
    insert into remote_saas.members(tenant_id, user_id, role) values (t.id, joined, grant_row.role);
    update remote_saas.invites set accepted_at = now(), accepted_by = joined where id = grant_row.id;
    perform remote_saas.audit(t.id, joined, 'member.join', grant_row.email, jsonb_build_object('role', grant_row.role));
    return t.id;
end;
$$;

-- Owners and admins remove members (admins cannot remove owners); anyone may leave. The last
-- owner stays. The profile leaves the organization; its sessions end at the next policy check.
create function public.remote_saas_remove_member(tenant uuid, member uuid)
returns boolean language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; target remote_saas.members;
begin
    me := remote_saas.actor(tenant, '{owner,admin,technician,member}', 'read');
    select * into target from remote_saas.members m where m.tenant_id = tenant and m.user_id = member for update;
    if not found then return false; end if;
    if member <> me.user_id and (me.role not in ('owner', 'admin') or (target.role = 'owner' and me.role <> 'owner')) then
        raise exception 'Not allowed for this company' using errcode = '42501';
    end if;
    perform 1 from remote_saas.members m where m.tenant_id = tenant and m.role = 'owner' for update;
    if target.role = 'owner' and (select count(*) from remote_saas.members m where m.tenant_id = tenant and m.role = 'owner') = 1 then
        raise exception 'A company needs at least one owner' using errcode = '42501';
    end if;
    delete from remote_saas.members where tenant_id = tenant and user_id = member;
    perform remote_saas.set_profile_org(member, null);
    perform remote_saas.audit(tenant, me.user_id, 'member.remove', member::text, jsonb_build_object('role', target.role));
    return true;
end;
$$;

-- Owners set any role; admins set admin, technician or member on non-owners. Promotions count
-- against the technician limit; the last owner cannot be demoted.
create function public.remote_saas_set_member_role(tenant uuid, member uuid, member_role text)
returns boolean language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; target remote_saas.members; l record;
begin
    me := remote_saas.actor(tenant, '{owner,admin}');
    if member_role is null or member_role not in ('owner', 'admin', 'technician', 'member') then
        raise exception 'Invalid role' using errcode = '22023';
    end if;
    perform 1 from remote_saas.tenants where id = tenant for update;
    select * into target from remote_saas.members m where m.tenant_id = tenant and m.user_id = member for update;
    if not found then return false; end if;
    if target.role = member_role then return true; end if;
    if me.role <> 'owner' and (target.role = 'owner' or member_role = 'owner') then
        raise exception 'Only an owner can change owners' using errcode = '42501';
    end if;
    if target.role = 'owner' and (select count(*) from remote_saas.members m where m.tenant_id = tenant and m.role = 'owner') = 1 then
        raise exception 'A company needs at least one owner' using errcode = '42501';
    end if;
    if target.role = 'member' and member_role <> 'member' then
        select * into l from remote_saas.limits(tenant);
        if remote_saas.technician_count(tenant) >= l.max_technicians then
            raise exception 'Technician limit of your plan reached' using errcode = '54000';
        end if;
    end if;
    update remote_saas.members set role = member_role where tenant_id = tenant and user_id = member;
    if member_role in ('owner', 'admin', 'member') then
        delete from remote_saas.group_access where tenant_id = tenant and user_id = member;
    end if;
    perform remote_saas.audit(tenant, me.user_id, 'member.role', member::text,
        jsonb_build_object('from', target.role, 'to', member_role));
    return true;
end;
$$;

---------------------------------------------------------------------------------------------------
-- Sign-in settings

create function public.remote_saas_get_auth_settings(tenant uuid)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare s remote_saas.auth_settings;
begin
    perform remote_saas.actor(tenant, '{owner,admin,technician,member}', 'read');
    select * into s from remote_saas.auth_settings where tenant_id = tenant;
    return jsonb_build_object('methods', to_jsonb(s.methods), 'require_mfa', s.require_mfa,
        'sso_provider_id', s.sso_provider_id, 'updated_at', s.updated_at);
end;
$$;

create function public.remote_saas_set_auth_settings(tenant uuid, sign_in_methods text[], mfa_required boolean,
                                                     sso_provider uuid default null)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; l record; clean text[];
begin
    me := remote_saas.actor(tenant, '{owner,admin}');
    select array_agg(distinct m order by m) into clean from unnest(sign_in_methods) m;
    if clean is null or cardinality(clean) = 0 or array_position(clean, null) is not null
        or not clean <@ '{password,magic_link,google,azure,apple,mixel_id,sso}'::text[]
        or mfa_required is null then
        raise exception 'Choose at least one valid sign-in method' using errcode = '22023';
    end if;
    select * into l from remote_saas.limits(tenant);
    if 'sso' = any(clean) and (not coalesce((l.features->>'sso')::boolean, false) or sso_provider is null) then
        raise exception 'Company SSO needs the Business plan and an SSO provider' using errcode = '22023';
    end if;
    if mfa_required and not coalesce((l.features->>'require_mfa')::boolean, false) then
        raise exception 'Required two-step sign-in needs the Business plan' using errcode = '22023';
    end if;
    -- Never lock out the person changing the settings.
    if not remote_saas.method_allowed(clean) then
        raise exception 'Keep the sign-in method you are using now allowed' using errcode = '22023';
    end if;
    if mfa_required and coalesce(auth.jwt()->>'aal', '') <> 'aal2' then
        raise exception 'Set up and use two-step sign-in yourself before requiring it' using errcode = '22023';
    end if;
    update remote_saas.auth_settings
        set methods = clean, require_mfa = mfa_required,
            sso_provider_id = case when 'sso' = any(clean) then sso_provider end,
            updated_by = me.user_id, updated_at = now()
        where tenant_id = tenant;
    perform remote_saas.audit(tenant, me.user_id, 'settings.sign_in', null,
        jsonb_build_object('methods', to_jsonb(clean), 'require_mfa', mfa_required));
    return public.remote_saas_get_auth_settings(tenant);
end;
$$;

-- Public (anonymous): which sign-in methods to show for an email address (by its verified company
-- domain) or a company slug. Unknown: no row, the portal shows the standard methods.
create function public.remote_saas_sign_in_options(identifier text)
returns table (company text, slug text, methods text[], require_mfa boolean, sso_provider_id uuid)
language sql stable security definer set search_path = '' as $$
    select t.name, t.slug, s.methods, s.require_mfa, s.sso_provider_id
    from remote_saas.tenants t
    join remote_saas.auth_settings s on s.tenant_id = t.id
    join public.organizations o on o.id = t.organization_id and o.is_active
    where remote_saas.effective_status(t) <> 'closed'
      and char_length(identifier) between 1 and 254
      and case when position('@' in identifier) > 0
          then exists (select 1 from remote_saas.domains d where d.tenant_id = t.id and d.verified_at is not null
                       and d.domain = lower(btrim(split_part(identifier, '@', 2))))
          else t.slug = lower(btrim(identifier)) end
    limit 1;
$$;

-- Company email domains: an owner/admin adds one and publishes the returned TXT value at
-- _mixel-remote.<domain>. Verification (DNS check) is done by Mixel (stage 3 automates it).
create function public.remote_saas_add_domain(tenant uuid, email_domain text)
returns text language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; clean text := lower(btrim(email_domain)); proof text;
begin
    me := remote_saas.actor(tenant, '{owner,admin}');
    if clean is null or char_length(clean) > 253 or clean !~ '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$' then
        raise exception 'Invalid domain' using errcode = '22023';
    end if;
    if (select count(*) from remote_saas.domains d where d.tenant_id = tenant) >= 20 then
        raise exception 'Too many domains' using errcode = '54000';
    end if;
    proof := 'mixel-remote-verify=' || pg_catalog.encode(public.gen_random_bytes(16), 'hex');
    insert into remote_saas.domains(tenant_id, domain, verification_token) values (tenant, clean, proof)
    on conflict (tenant_id, domain) do update set verification_token = remote_saas.domains.verification_token
    returning verification_token into proof;
    perform remote_saas.audit(tenant, me.user_id, 'settings.domain_add', clean);
    return proof;
end;
$$;

create function public.remote_saas_domains(tenant uuid)
returns table (domain text, verification_token text, verified_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
begin
    perform remote_saas.actor(tenant, '{owner,admin}', 'read');
    return query select d.domain, d.verification_token, d.verified_at from remote_saas.domains d
        where d.tenant_id = tenant order by d.domain;
end;
$$;

create function public.remote_saas_remove_domain(tenant uuid, email_domain text)
returns boolean language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members;
begin
    me := remote_saas.actor(tenant, '{owner,admin}', 'read');
    delete from remote_saas.domains where tenant_id = tenant and domain = lower(btrim(email_domain));
    if found then perform remote_saas.audit(tenant, me.user_id, 'settings.domain_remove', lower(btrim(email_domain))); end if;
    return found;
end;
$$;

-- Mixel staff (MFA) mark a domain verified after checking its TXT record.
create function public.remote_saas_verify_domain(tenant uuid, email_domain text)
returns boolean language plpgsql security definer set search_path = '' as $$
begin
    perform remote_private.staff_actor();
    update remote_saas.domains set verified_at = now()
        where tenant_id = tenant and domain = lower(btrim(email_domain)) and verified_at is null;
    return found;
end;
$$;

-- Business: the tenant audit log, newest first, 200 entries per page.
create function public.remote_saas_audit_log(tenant uuid, before_id bigint default null)
returns table (id bigint, actor_id uuid, actor_name text, action text, target text, detail jsonb, occurred_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
declare l record;
begin
    perform remote_saas.actor(tenant, '{owner,admin}', 'read');
    select * into l from remote_saas.limits(tenant);
    if not coalesce((l.features->>'audit_log')::boolean, false) then
        raise exception 'The audit log needs the Business plan' using errcode = '42501';
    end if;
    return query select a.id, a.actor_id, u.name, a.action, a.target, a.detail, a.occurred_at
        from remote_saas.audit_log a left join public.users u on u.id = a.actor_id
        where a.tenant_id = tenant and (before_id is null or a.id < before_id)
        order by a.id desc limit 200;
end;
$$;

do $$ declare f text; begin
    for f in select p.oid::regprocedure::text from pg_proc p
             where p.pronamespace = 'public'::regnamespace and p.proname like 'remote\_saas\_%' loop
        execute format('revoke all on function %s from public, anon, authenticated', f);
        execute format('grant execute on function %s to authenticated', f);
    end loop;
end $$;
grant execute on function public.remote_saas_sign_in_options(text) to anon;

commit;
notify pgrst, 'reload schema';
