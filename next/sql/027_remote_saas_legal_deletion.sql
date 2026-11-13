-- Mixel Remote as SaaS: legal and data-protection features (Swiss nDSG / EU GDPR).
--   1. Terms acceptance at sign-up: the owner accepts the Terms of Service and the Data Processing
--      Agreement; the version and time are stored per tenant and person (terms_acceptances).
--   2. Data export: an owner downloads the company's data (company, members, invitations, devices,
--      groups, deploy links, sign-in settings, billing state, terms acceptances, audit log).
--   3. Deleting a company (owner): every device is revoked and deleted, pending invitations are
--      revoked, the members' profiles and sign-in accounts are deleted, then the tenant, its ISM
--      client and organization. Only a minimal billing record stays (closures: company name, Stripe
--      ids, counts; 10 years, Swiss Code of Obligations art. 958f). The billing service cancels the
--      Stripe subscription and deletes the Stripe customer from the queue in `closures`.
--   4. Deleting one's own account (any member except the last owner, or an account without a company):
--      pending invitations the person sent are revoked, company objects they created are handed to an
--      owner, the profile and the sign-in account are deleted.
-- All public functions are SECURITY DEFINER with an empty search_path; tenant functions check the
-- caller through remote_saas.actor() (membership, role, sign-in method and 2-step rules).
begin;
set local lock_timeout = '5s';

---------------------------------------------------------------------------------------------------
-- Tables

-- The published versions of the legal documents. Exactly one current version per document.
create table remote_saas.legal_documents (
    document text not null check (document in ('terms', 'privacy', 'dpa')),
    version text not null check (version ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}(-[a-z0-9]+)?$'),
    effective_at timestamptz not null default now(),
    is_current boolean not null default false,
    primary key (document, version)
);
create unique index legal_documents_one_current on remote_saas.legal_documents(document) where is_current;
-- DRAFT pending Michael's review: the pages under https://remote.mixel.ch/legal/ say so. Publishing
-- the final texts = a new version row marked current (and the old one not current).
insert into remote_saas.legal_documents(document, version, is_current) values
    ('terms', '2026-11-13-draft', true), ('privacy', '2026-11-13-draft', true), ('dpa', '2026-11-13-draft', true);

create table remote_saas.terms_acceptances (
    id bigint generated always as identity primary key,
    tenant_id uuid references remote_saas.tenants(id) on delete cascade,
    profile_id uuid references public.users(id) on delete set null,
    auth_user_id uuid not null,
    email text not null check (char_length(email) <= 254),
    document text not null check (document in ('terms', 'dpa')),
    version text not null,
    source text not null check (source in ('portal', 'app', 'api')),
    accepted_at timestamptz not null default now(),
    foreign key (document, version) references remote_saas.legal_documents(document, version)
);
create index terms_acceptances_tenant on remote_saas.terms_acceptances(tenant_id, accepted_at desc);

-- A deleted company: the billing record Mixel must keep, and the Stripe clean-up queue.
create table remote_saas.closures (
    id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null unique,
    company_name text not null,
    slug text not null,
    plan text not null,
    status_at_close text not null,
    stripe_customer_id text check (stripe_customer_id ~ '^cus_[A-Za-z0-9]+$'),
    stripe_subscription_id text check (stripe_subscription_id ~ '^sub_[A-Za-z0-9]+$'),
    subscription_status text,
    terms_version text,
    terms_accepted_at timestamptz,
    devices_removed integer not null default 0,
    members_removed integer not null default 0,
    accounts_deleted integer not null default 0,
    invites_revoked integer not null default 0,
    closed_at timestamptz not null default now(),
    retain_until timestamptz not null default now() + interval '10 years',
    -- none: no Stripe objects; pending/working: the billing service cancels and deletes them; done; failed.
    stripe_state text not null check (stripe_state in ('none', 'pending', 'working', 'done', 'failed')),
    stripe_attempts integer not null default 0 check (stripe_attempts between 0 and 100),
    stripe_next_at timestamptz,
    stripe_claim uuid,
    stripe_claimed_at timestamptz,
    stripe_done_at timestamptz,
    stripe_error text check (stripe_error is null or char_length(stripe_error) <= 300)
);
create index closures_stripe_queue on remote_saas.closures(stripe_next_at) where stripe_state in ('pending', 'working');

-- Deleted personal accounts (no company or a member who left): when, nothing that identifies the person.
create table remote_saas.account_deletions (
    id bigint generated always as identity primary key,
    tenant_id uuid,
    role text,
    deleted_at timestamptz not null default now()
);

do $$ declare t text; begin
    foreach t in array array['legal_documents','terms_acceptances','closures','account_deletions'] loop
        execute format('alter table remote_saas.%I enable row level security', t);
        execute format('revoke all on remote_saas.%I from public, anon, authenticated', t);
    end loop;
end $$;

-- The device-enrolment audit outlives a deleted person: the actor becomes unknown instead of
-- blocking the deletion.
alter table remote_private.enrollment_audit alter column actor_id drop not null;
alter table remote_private.enrollment_audit drop constraint if exists enrollment_audit_actor_id_fkey;
alter table remote_private.enrollment_audit add constraint enrollment_audit_actor_id_fkey
    foreign key (actor_id) references public.users(id) on delete set null;

---------------------------------------------------------------------------------------------------
-- Terms acceptance at sign-up

create function remote_saas.current_version(doc text) returns text
language sql stable security definer set search_path = '' as $$
    select d.version from remote_saas.legal_documents d where d.document = doc and d.is_current;
$$;

-- Public (anonymous): the current versions, for the sign-up form and the legal pages.
create function public.remote_saas_legal_versions()
returns table (document text, version text, effective_at timestamptz)
language sql stable security definer set search_path = '' as $$
    select d.document, d.version, d.effective_at from remote_saas.legal_documents d where d.is_current order by d.document;
$$;

create function remote_saas.record_acceptance(tenant uuid, profile uuid, wanted text, origin text)
returns text language plpgsql security definer set search_path = '' as $$
declare current_terms text := remote_saas.current_version('terms'); account auth.users;
begin
    if wanted is not null and wanted <> current_terms then
        raise exception 'The Terms of Service have changed. Reload the page and accept the current version.'
            using errcode = '22023';
    end if;
    select * into account from auth.users where id = auth.uid();
    insert into remote_saas.terms_acceptances(tenant_id, profile_id, auth_user_id, email, document, version, source)
    select tenant, profile, auth.uid(), lower(btrim(account.email)), d, remote_saas.current_version(d),
           case when origin in ('portal', 'app') then origin else 'api' end
    from unnest(array['terms', 'dpa']) d;
    perform remote_saas.audit(tenant, profile, 'legal.accept_terms', current_terms,
        jsonb_build_object('terms', current_terms, 'dpa', remote_saas.current_version('dpa'), 'source', origin));
    return current_terms;
end;
$$;

-- The sign-up of 019 becomes internal; the public sign-up requires accepting the Terms of Service
-- and the Data Processing Agreement and records which version was accepted and when.
alter function public.remote_saas_signup(text) set schema remote_saas;
alter function remote_saas.remote_saas_signup(text) rename to signup_core;
revoke all on function remote_saas.signup_core(text) from public, anon, authenticated;

create function public.remote_saas_signup(company_name text, accept_terms boolean default false,
                                          terms_version text default null, source text default 'portal')
returns jsonb language plpgsql security definer set search_path = '' as $$
declare created jsonb; owner_profile uuid;
begin
    if accept_terms is not true then
        raise exception 'Accept the Terms of Service and the Data Processing Agreement to create a company'
            using errcode = '22023';
    end if;
    if terms_version is not null and terms_version <> remote_saas.current_version('terms') then
        raise exception 'The Terms of Service have changed. Reload the page and accept the current version.'
            using errcode = '22023';
    end if;
    created := remote_saas.signup_core(company_name);
    select m.user_id into owner_profile from remote_saas.members m
        where m.tenant_id = (created->>'tenant_id')::uuid and m.role = 'owner';
    return created || jsonb_build_object('terms_version',
        remote_saas.record_acceptance((created->>'tenant_id')::uuid, owner_profile, terms_version, source));
end;
$$;

-- Owners and admins accept a newer version for their company (e.g. after the Terms changed).
create function public.remote_saas_accept_terms(tenant uuid, terms_version text, source text default 'portal')
returns jsonb language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members;
begin
    me := remote_saas.actor(tenant, '{owner,admin}', 'read');
    if terms_version is null then raise exception 'Choose the version you accept' using errcode = '22023'; end if;
    perform remote_saas.record_acceptance(tenant, me.user_id, terms_version, source);
    return public.remote_saas_terms_status(tenant);
end;
$$;

-- Any member: the current versions and what the company accepted last.
create function public.remote_saas_terms_status(tenant uuid)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare a remote_saas.terms_acceptances; current_terms text := remote_saas.current_version('terms');
begin
    perform remote_saas.actor(tenant, '{owner,admin,technician,member}', 'read');
    select * into a from remote_saas.terms_acceptances x
        where x.tenant_id = tenant and x.document = 'terms' order by x.accepted_at desc, x.id desc limit 1;
    return jsonb_build_object('current_version', current_terms,
        'privacy_version', remote_saas.current_version('privacy'), 'dpa_version', remote_saas.current_version('dpa'),
        'accepted_version', a.version, 'accepted_at', a.accepted_at, 'accepted_by', a.email,
        'up_to_date', coalesce(a.version = current_terms, false));
end;
$$;

---------------------------------------------------------------------------------------------------
-- Data export (owner)

create function public.remote_saas_export(tenant uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; t remote_saas.tenants; l record; b remote_saas.billing; s remote_saas.auth_settings;
        reported timestamptz; me_email text;
begin
    me := remote_saas.actor(tenant, '{owner}', 'read');
    select * into t from remote_saas.tenants where id = tenant;
    select * into l from remote_saas.limits(tenant);
    select * into b from remote_saas.billing where tenant_id = tenant;
    select * into s from remote_saas.auth_settings where tenant_id = tenant;
    select r.reported_at into reported from remote_private.presence_reports r where r.reporter = 'broker';
    select u.email into me_email from public.users u where u.id = me.user_id;
    perform remote_saas.audit(tenant, me.user_id, 'tenant.export', null, '{}'::jsonb);
    return jsonb_build_object(
        'format', 'mixel-remote-export', 'format_version', 1, 'exported_at', now(), 'exported_by', me_email,
        'company', jsonb_build_object('tenant_id', t.id, 'name', t.name, 'slug', t.slug, 'plan', t.plan,
            'status', l.status, 'trial_ends_at', t.trial_ends_at, 'extra_technicians', t.extra_technicians,
            'created_at', t.created_at,
            'limits', jsonb_build_object('technicians', l.max_technicians, 'devices', l.max_devices, 'sessions', l.max_sessions)),
        'members', coalesce((select jsonb_agg(jsonb_build_object('user_id', u.id, 'name', u.name, 'email', u.email,
                'role', m.role, 'joined_at', m.created_at) order by m.created_at, u.id)
            from remote_saas.members m join public.users u on u.id = m.user_id where m.tenant_id = tenant), '[]'::jsonb),
        'invitations', coalesce((select jsonb_agg(jsonb_build_object('email', i.email, 'role', i.role,
                'created_at', i.created_at, 'expires_at', i.expires_at, 'accepted_at', i.accepted_at,
                'revoked_at', i.revoked_at, 'mail_state', i.mail_state) order by i.created_at)
            from remote_saas.invites i where i.tenant_id = tenant), '[]'::jsonb),
        'groups', coalesce((select jsonb_agg(jsonb_build_object('group_id', g.id, 'name', g.name, 'created_at', g.created_at,
                'technicians', coalesce((select jsonb_agg(u.email order by u.email) from remote_saas.group_access ga
                    join public.users u on u.id = ga.user_id where ga.group_id = g.id), '[]'::jsonb))
                order by lower(g.name), g.id)
            from remote_saas.device_groups g where g.tenant_id = tenant), '[]'::jsonb),
        'devices', coalesce((select jsonb_agg(jsonb_build_object('device_id', d.id, 'name', coalesce(dm.display_name, d.name),
                'os', d.os::text, 'group', g.name, 'added_via', dm.added_via, 'added_by', au.email,
                'created_at', d.created_at, 'remote_id', e.connection_id, 'enrolled_at', e.enrolled_at,
                'revoked', e.revoked_at is not null, 'last_seen', p.last_seen,
                'status', case when e.connection_id is null or e.revoked_at is not null then 'not_enrolled'
                    else remote_saas.presence_status(p.last_seen, reported) end)
                order by lower(coalesce(dm.display_name, d.name)), d.id)
            from public.devices d
            left join remote_saas.device_meta dm on dm.device_id = d.id
            left join remote_saas.device_groups g on g.id = dm.group_id
            left join public.users au on au.id = dm.added_by
            left join public.remote_device_enrollments e on e.device_id = d.id
            left join remote_private.device_presence p on p.connection_id = e.connection_id
            where d.client_id = t.client_id), '[]'::jsonb),
        'deploy_links', coalesce((select jsonb_agg(jsonb_build_object('label', r.label, 'created_at', r.created_at,
                'expires_at', r.expires_at, 'uses', r.uses, 'max_uses', r.max_uses, 'revoked_at', r.revoked_at)
                order by r.created_at)
            from remote_private.rollout_tokens r where r.client_id = t.client_id), '[]'::jsonb),
        'sign_in', jsonb_build_object('methods', to_jsonb(s.methods), 'require_mfa', s.require_mfa,
            'domains', coalesce((select jsonb_agg(jsonb_build_object('domain', d.domain, 'verified_at', d.verified_at)
                order by d.domain) from remote_saas.domains d where d.tenant_id = tenant), '[]'::jsonb)),
        'billing', jsonb_build_object('billing_interval', b.billing_interval, 'subscription_status', b.subscription_status,
            'current_period_end', b.current_period_end, 'cancel_at', b.cancel_at, 'read_only_since', b.read_only_since,
            'devices_delete_after', b.devices_delete_after),
        'terms_acceptances', coalesce((select jsonb_agg(jsonb_build_object('document', a.document, 'version', a.version,
                'accepted_by', a.email, 'accepted_at', a.accepted_at, 'source', a.source) order by a.accepted_at, a.id)
            from remote_saas.terms_acceptances a where a.tenant_id = tenant), '[]'::jsonb),
        'audit_log', coalesce((select jsonb_agg(jsonb_build_object('id', a.id, 'occurred_at', a.occurred_at,
                'actor', u.email, 'action', a.action, 'target', a.target, 'detail', a.detail) order by a.id)
            from remote_saas.audit_log a left join public.users u on u.id = a.actor_id where a.tenant_id = tenant), '[]'::jsonb));
end;
$$;

---------------------------------------------------------------------------------------------------
-- Deletion helpers

-- Deletes a profile; when ISM records outside Mixel Remote still point to it (tickets, time entries),
-- the profile is anonymized and deactivated instead. Then deletes the sign-in account unless it is
-- still used by another profile or a personal Mixel Remote account. Returns true when the sign-in
-- account was deleted. The JWT subject is hidden for these statements (see remote_saas.set_profile_org).
create function remote_saas.erase_profile(profile uuid) returns boolean
language plpgsql security definer set search_path = '' as $$
declare claims text := current_setting('request.jwt.claims', true);
        subject text := current_setting('request.jwt.claim.sub', true);
        account uuid; erased boolean := false;
begin
    select u.auth_user_id into account from public.users u where u.id = profile;
    if not found then return false; end if;
    perform set_config('request.jwt.claims', '', true);
    perform set_config('request.jwt.claim.sub', '', true);
    begin
        delete from public.users where id = profile;
    exception when foreign_key_violation then
        update public.users set name = 'Deleted user', email = 'deleted-' || replace(id::text, '-', '') || '@deleted.invalid',
            phone = null, auth_user_id = null, organization_id = null, is_active = false,
            deleted_at = now(), updated_at = now()
        where id = profile;
    end;
    if account is not null
        and not exists (select 1 from public.users u where u.auth_user_id = account)
        and not exists (select 1 from remote_private.personal_accounts p where p.auth_user_id = account) then
        delete from auth.users where id = account;
        erased := found;
    end if;
    perform set_config('request.jwt.claims', coalesce(claims, ''), true);
    perform set_config('request.jwt.claim.sub', coalesce(subject, ''), true);
    return erased;
end;
$$;

-- Hands what a leaving person created in a company to another member (an owner), so company objects
-- stay, and revokes the invitations that person sent that are not accepted yet. Returns the number
-- of revoked invitations.
create function remote_saas.hand_over(tenant uuid, profile uuid, heir uuid) returns integer
language plpgsql security definer set search_path = '' as $$
declare t remote_saas.tenants; revoked integer;
begin
    select * into t from remote_saas.tenants where id = tenant;
    update remote_saas.invites set revoked_at = now(), mail_state = case when mail_state in ('pending', 'sending')
            then 'failed' else mail_state end, token_hash = case when mail_state = 'sent' then token_hash end,
            mail_claim = null, mail_claimed_at = null, mail_next_at = null
        where tenant_id = tenant and invited_by = profile and accepted_at is null and revoked_at is null;
    get diagnostics revoked = row_count;
    update remote_saas.invites set invited_by = heir where tenant_id = tenant and invited_by = profile;
    update remote_saas.tenants set created_by = heir where id = tenant and created_by = profile;
    update public.remote_device_enrollments e set enrolled_by = heir
        from public.devices d where d.id = e.device_id and d.client_id = t.client_id and e.enrolled_by = profile;
    update remote_private.rollout_tokens set created_by = heir where client_id = t.client_id and created_by = profile;
    update remote_private.support_codes set created_by = heir where client_id = t.client_id and created_by = profile;
    return revoked;
end;
$$;

---------------------------------------------------------------------------------------------------
-- Delete a company (owner)

create function public.remote_saas_delete_company(tenant uuid, confirm_name text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; t remote_saas.tenants; b remote_saas.billing; a remote_saas.terms_acceptances;
        d record; m record; devices_removed integer := 0; members_removed integer := 0; accounts integer := 0;
        invites_revoked integer := 0; connections integer[]; device_ids uuid[]; profiles uuid[]; closure uuid;
        stripe text;
begin
    me := remote_saas.actor(tenant, '{owner}', 'read');
    select * into t from remote_saas.tenants where id = tenant for update;
    if confirm_name is null or lower(btrim(confirm_name)) <> lower(t.name) then
        raise exception 'Type the company name exactly to confirm' using errcode = '22023';
    end if;
    select * into b from remote_saas.billing where tenant_id = tenant;
    select * into a from remote_saas.terms_acceptances x where x.tenant_id = tenant and x.document = 'terms'
        order by x.accepted_at desc, x.id desc limit 1;

    -- 1. Pending invitations: revoked first, so an e-mail being sent right now finds no link to store.
    update remote_saas.invites set revoked_at = now(), token_hash = case when mail_state = 'sent' then token_hash end,
            mail_claim = null, mail_claimed_at = null,
            mail_next_at = null, mail_state = case when mail_state in ('pending', 'sending') then 'failed' else mail_state end
        where tenant_id = tenant and accepted_at is null and revoked_at is null;
    get diagnostics invites_revoked = row_count;

    -- 2. Devices: revoked and deleted like a manual removal (keys and Remote IDs stay reserved and are
    --    never given to another computer); running sessions end at the broker's next policy check.
    select coalesce(array_agg(e.connection_id) filter (where e.connection_id is not null), '{}'),
           coalesce(array_agg(dv.id), '{}')
        into connections, device_ids
        from public.devices dv left join public.remote_device_enrollments e on e.device_id = dv.id
        where dv.client_id = t.client_id;
    for d in select dv.id from public.devices dv where dv.client_id = t.client_id order by dv.created_at, dv.id loop
        perform remote_saas.remove_device(d.id, me.user_id, 'remove');
        devices_removed := devices_removed + 1;
    end loop;
    delete from remote_private.device_presence where connection_id = any(connections);
    delete from remote_private.enrollment_audit where device_id = any(device_ids);
    -- ISM session and support records of the company's devices (normally none for a SaaS company).
    delete from public.sessions where client_id = t.client_id;
    delete from public.support_invites where client_id = t.client_id;
    delete from public.support_requests where client_id = t.client_id;

    -- 3. The tenant (members, invitations, groups, settings, domains, audit log, billing state and
    --    terms acceptances go with it), its ISM client (deploy links, support codes) and organization.
    select coalesce(array_agg(mm.user_id), '{}') into profiles from remote_saas.members mm where mm.tenant_id = tenant;
    members_removed := cardinality(profiles);
    stripe := case when b.stripe_customer_id is not null or b.stripe_subscription_id is not null then 'pending' else 'none' end;
    insert into remote_saas.closures(tenant_id, company_name, slug, plan, status_at_close, stripe_customer_id,
            stripe_subscription_id, subscription_status, terms_version, terms_accepted_at, devices_removed,
            members_removed, invites_revoked, stripe_state, stripe_next_at)
    values (t.id, t.name, t.slug, t.plan, remote_saas.effective_status(t), b.stripe_customer_id, b.stripe_subscription_id,
            b.subscription_status, a.version, a.accepted_at, devices_removed, members_removed, invites_revoked,
            stripe, case when stripe = 'pending' then now() end)
    returning id into closure;
    update public.organizations set owner_id = null where id = t.organization_id;
    delete from remote_saas.tenants where id = tenant;
    delete from remote_private.rollout_tokens where client_id = t.client_id;
    delete from remote_private.support_codes where client_id = t.client_id;

    -- 4. The members' profiles and sign-in accounts (the caller's own last), before the organization
    --    goes (its deletion would otherwise move the profiles out of it).
    for m in select x as profile from unnest(profiles) x order by (x = me.user_id), x loop
        if remote_saas.erase_profile(m.profile) then accounts := accounts + 1; end if;
    end loop;
    delete from public.clients where id = t.client_id;
    delete from public.organizations where id = t.organization_id;
    update remote_saas.closures set accounts_deleted = accounts where id = closure;
    if stripe = 'pending' then perform pg_notify('remote_saas_billing_close', ''); end if;
    return jsonb_build_object('deleted', true, 'tenant_id', t.id, 'devices_removed', devices_removed,
        'members_removed', members_removed, 'accounts_deleted', accounts, 'invites_revoked', invites_revoked,
        'billing', stripe);
end;
$$;

---------------------------------------------------------------------------------------------------
-- Delete one's own account

-- Any signed-in account deletes itself: a member of a company (except its last owner, who deletes
-- the company or makes someone else owner first), or an account without a company. Mixel staff and
-- people of ISM customer companies are refused (their accounts are managed by Mixel).
create function public.remote_saas_delete_my_account(confirm_email text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare account auth.users; profile public.users; mine remote_saas.members; t remote_saas.tenants;
        heir uuid; revoked integer := 0; erased boolean := false;
begin
    select * into account from auth.users u where u.id = auth.uid();
    if auth.uid() is null or not found then raise exception 'Sign in first' using errcode = '42501'; end if;
    if confirm_email is null or lower(btrim(confirm_email)) <> lower(btrim(coalesce(account.email, ''))) then
        raise exception 'Type your email address exactly to confirm' using errcode = '22023';
    end if;
    select * into profile from public.users u where u.auth_user_id = auth.uid();
    if found then
        if profile.role::text in ('admin', 'agent') then
            raise exception 'Mixel team accounts are not deleted here' using errcode = '42501';
        end if;
        select mm.* into mine from remote_saas.members mm where mm.user_id = profile.id;
        if profile.organization_id is not null and mine.user_id is null then
            raise exception 'This account is managed by your company. Ask Mixel to delete it.' using errcode = '42501';
        end if;
    end if;
    if mine.user_id is not null then
        -- Same checks as any other action of the company (sign-in method, required 2-step login).
        perform remote_saas.actor(mine.tenant_id, '{owner,admin,technician,member}', 'read');
        select * into t from remote_saas.tenants where id = mine.tenant_id for update;
        perform 1 from remote_saas.members x where x.tenant_id = t.id and x.role = 'owner' for update;
        if mine.role = 'owner' and (select count(*) from remote_saas.members x where x.tenant_id = t.id and x.role = 'owner') = 1 then
            raise exception 'You are the only owner. Delete the company, or make another member owner first.'
                using errcode = '55000';
        end if;
        select x.user_id into heir from remote_saas.members x
            where x.tenant_id = t.id and x.role = 'owner' and x.user_id <> profile.id order by x.created_at, x.user_id limit 1;
        revoked := remote_saas.hand_over(t.id, profile.id, heir);
        update public.organizations set owner_id = heir where id = t.organization_id and owner_id = profile.id;
        delete from remote_saas.members where tenant_id = t.id and user_id = profile.id;
        perform remote_saas.audit(t.id, null, 'member.account_deleted', mine.role,
            jsonb_build_object('role', mine.role, 'invites_revoked', revoked));
    end if;
    if profile.id is not null then
        erased := remote_saas.erase_profile(profile.id);
    end if;
    if not erased and not exists (select 1 from public.users u where u.auth_user_id = account.id) then
        -- No profile (signed up, no company yet) or the profile is gone: the sign-in account itself,
        -- including a personal Mixel Remote account and its devices.
        delete from remote_private.personal_devices pd using remote_private.personal_accounts pa
            where pa.id = pd.account_id and pa.auth_user_id = account.id;
        delete from auth.users where id = account.id;
        erased := found;
    end if;
    insert into remote_saas.account_deletions(tenant_id, role) values (mine.tenant_id, mine.role);
    return jsonb_build_object('deleted', erased, 'tenant_id', mine.tenant_id, 'invites_revoked', revoked);
end;
$$;

---------------------------------------------------------------------------------------------------
-- Billing service: Stripe clean-up of deleted companies (role mixel_remote_billing)

-- Claims closures whose Stripe objects still have to be cancelled/deleted. A claim older than
-- 5 minutes (a crashed service) is taken over. Billing records past their retention are purged here.
create function remote_saas.billing_claim_closures(max_rows integer default 5)
returns table (closure_id uuid, claim uuid, tenant_id uuid, stripe_customer_id text, stripe_subscription_id text,
               attempt integer)
language plpgsql security definer set search_path = '' as $$
begin
    delete from remote_saas.closures where retain_until < now() and stripe_state in ('none', 'done', 'failed');
    return query
    with due as (
        select c.id from remote_saas.closures c
        where (c.stripe_state = 'pending' and coalesce(c.stripe_next_at, now()) <= now())
           or (c.stripe_state = 'working' and c.stripe_claimed_at < now() - interval '5 minutes')
        order by c.stripe_next_at nulls first, c.closed_at
        limit greatest(1, least(coalesce(max_rows, 5), 20))
        for update skip locked
    )
    update remote_saas.closures c set stripe_state = 'working', stripe_claim = gen_random_uuid(),
            stripe_claimed_at = now(), stripe_attempts = c.stripe_attempts + 1
    from due where c.id = due.id
    returning c.id, c.stripe_claim, c.tenant_id, c.stripe_customer_id, c.stripe_subscription_id, c.stripe_attempts;
end;
$$;

-- The result of one clean-up. On failure it is retried (1, 5, 15, 60 minutes, then hourly); after
-- 10 attempts, or at once with give_up, the closure shows 'failed' and needs a person.
create function remote_saas.billing_close_result(closure uuid, claim_id uuid, failure text, give_up boolean default false)
returns text language plpgsql security definer set search_path = '' as $$
declare c remote_saas.closures; next_state text;
begin
    select * into c from remote_saas.closures x where x.id = closure and x.stripe_claim = claim_id for update;
    if not found then return 'stale'; end if;
    if failure is null then
        update remote_saas.closures set stripe_state = 'done', stripe_done_at = now(), stripe_error = null,
            stripe_claim = null, stripe_claimed_at = null, stripe_next_at = null where id = closure;
        return 'done';
    end if;
    next_state := case when c.stripe_attempts >= 10 or coalesce(give_up, false) then 'failed' else 'pending' end;
    update remote_saas.closures set stripe_state = next_state, stripe_error = left(failure, 300),
            stripe_claim = null, stripe_claimed_at = null,
            stripe_next_at = case next_state when 'pending' then now() + case c.stripe_attempts
                when 1 then interval '1 minute' when 2 then interval '5 minutes' when 3 then interval '15 minutes'
                else interval '60 minutes' end end
        where id = closure;
    return next_state;
end;
$$;

---------------------------------------------------------------------------------------------------
-- Grants

do $$ declare f text; begin
    for f in select p.oid::regprocedure::text from pg_proc p
             where p.pronamespace = 'remote_saas'::regnamespace
               and p.proname in ('current_version', 'record_acceptance', 'erase_profile', 'hand_over',
                                 'billing_claim_closures', 'billing_close_result', 'signup_core') loop
        execute format('revoke all on function %s from public, anon, authenticated', f);
    end loop;
end $$;
revoke all on function public.remote_saas_signup(text, boolean, text, text), public.remote_saas_accept_terms(uuid, text, text),
    public.remote_saas_terms_status(uuid), public.remote_saas_export(uuid), public.remote_saas_delete_company(uuid, text),
    public.remote_saas_delete_my_account(text), public.remote_saas_legal_versions() from public, anon, authenticated;
grant execute on function public.remote_saas_signup(text, boolean, text, text), public.remote_saas_accept_terms(uuid, text, text),
    public.remote_saas_terms_status(uuid), public.remote_saas_export(uuid), public.remote_saas_delete_company(uuid, text),
    public.remote_saas_delete_my_account(text), public.remote_saas_legal_versions() to authenticated;
grant execute on function public.remote_saas_legal_versions() to anon;
grant execute on function remote_saas.billing_claim_closures(integer), remote_saas.billing_close_result(uuid, uuid, text, boolean)
    to mixel_remote_billing;

commit;
notify pgrst, 'reload schema';
