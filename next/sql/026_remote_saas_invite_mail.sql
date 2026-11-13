-- Mixel Remote as SaaS: invitation e-mails.
--
-- Until now remote_saas_invite_member returned the invitation secret to the caller, which had to
-- pass the link on by hand. Now the secret exists only in the e-mail:
--   1. remote_saas_invite_member (portal, desktop app or any RPC caller) stores the invitation
--      WITHOUT a secret, state 'pending', and signals the mail service (NOTIFY, no payload).
--   2. The mail service (next/services/billing, login role mixel_remote_billing_service, member of
--      mixel_remote_mail) claims pending invitations, creates a fresh random secret, stores only its
--      SHA-256 (remote_saas_mail_invite_secret), sends the e-mail with the link
--      https://remote.mixel.ch/invite#<secret> through Resend, and records the result.
--   3. "Resend" makes the invitation pending again: a new secret, the old link stops working.
-- The secret is never stored, logged or returned by any function. The language of the e-mail is
-- the inviter's choice in the UI (argument `locale`), else the inviter's profile language, else English.
begin;
set local lock_timeout = '5s';

do $$ begin
    create role mixel_remote_mail nologin;
exception when duplicate_object then null; end $$;
do $$ begin
    if exists (select 1 from pg_roles where rolname = 'mixel_remote_billing_service') then
        grant mixel_remote_mail to mixel_remote_billing_service;
    end if;
end $$;
grant usage on schema remote_saas to mixel_remote_mail;

alter table remote_saas.invites alter column token_hash drop not null;
alter table remote_saas.invites
    add column mail_locale text not null default 'en' check (mail_locale in ('en', 'de', 'fr', 'it')),
    -- pending: waits for the mail service; sending: claimed; sent: delivered to Resend; failed: gave up.
    add column mail_state text not null default 'sent' check (mail_state in ('pending', 'sending', 'sent', 'failed')),
    add column mail_attempts integer not null default 0 check (mail_attempts between 0 and 100),
    add column mail_next_at timestamptz,
    add column mail_claim uuid,
    add column mail_claimed_at timestamptz,
    add column mail_sent_at timestamptz,
    add column mail_message_id text check (mail_message_id is null or char_length(mail_message_id) <= 200),
    add column mail_error text check (mail_error is null or char_length(mail_error) <= 300),
    add column mail_sends integer not null default 0 check (mail_sends between 0 and 1000),
    add column mail_requested_at timestamptz;
-- Only a sent invitation has a link that can be used.
alter table remote_saas.invites add constraint invites_link_only_when_sent
    check (token_hash is not null or mail_state <> 'sent');
create index invites_mail_queue on remote_saas.invites(mail_next_at)
    where mail_state in ('pending', 'sending') and accepted_at is null and revoked_at is null;

-- The language of the invitation e-mail.
create function remote_saas.mail_locale_for(inviter uuid, wanted text) returns text
language sql stable security definer set search_path = '' as $$
    select case
        when lower(btrim(wanted)) in ('en', 'de', 'fr', 'it') then lower(btrim(wanted))
        when (select u.language::text from public.users u where u.id = inviter) in ('en', 'de', 'fr', 'it')
            then (select u.language::text from public.users u where u.id = inviter)
        else 'en' end
$$;

---------------------------------------------------------------------------------------------------
-- Owner/admin RPCs

drop function public.remote_saas_invite_member(uuid, text, text);
-- Invites a person; the invitation e-mail is sent by the mail service. Returns the invitation id.
create function public.remote_saas_invite_member(tenant uuid, invitee_email text, member_role text,
                                                 locale text default null)
returns uuid language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; clean_email text := lower(btrim(invitee_email)); created uuid; l record;
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
    insert into remote_saas.invites(tenant_id, email, role, token_hash, invited_by, expires_at,
                                    mail_locale, mail_state, mail_next_at, mail_requested_at)
    values (tenant, clean_email, member_role, null, me.user_id, now() + interval '7 days',
            remote_saas.mail_locale_for(me.user_id, locale), 'pending', now(), now())
    returning id into created;
    perform remote_saas.audit(tenant, me.user_id, 'member.invite', clean_email, jsonb_build_object('role', member_role));
    perform pg_notify('remote_saas_invite_mail', '');
    return created;
end;
$$;

-- Sends the invitation again with a new link (the previous link stops working) and 7 more days.
create function public.remote_saas_resend_invite(tenant uuid, invite uuid, locale text default null)
returns boolean language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members; row_now remote_saas.invites;
begin
    me := remote_saas.actor(tenant, '{owner,admin}');
    select * into row_now from remote_saas.invites i
        where i.id = invite and i.tenant_id = tenant and i.accepted_at is null and i.revoked_at is null for update;
    if not found then return false; end if;
    if row_now.mail_requested_at is not null and row_now.mail_requested_at > now() - interval '1 minute' then
        raise exception 'Wait a minute before sending this invitation again' using errcode = '54000';
    end if;
    if row_now.mail_sends >= 10 then
        raise exception 'This invitation was sent too often; revoke it and invite again' using errcode = '54000';
    end if;
    update remote_saas.invites set token_hash = null, mail_state = 'pending', mail_attempts = 0, mail_next_at = now(),
            mail_claim = null, mail_claimed_at = null, mail_error = null, mail_requested_at = now(),
            mail_locale = case when locale is null then row_now.mail_locale
                               else remote_saas.mail_locale_for(me.user_id, locale) end,
            expires_at = now() + interval '7 days'
        where id = invite;
    perform remote_saas.audit(tenant, me.user_id, 'member.invite_resend', row_now.email);
    perform pg_notify('remote_saas_invite_mail', '');
    return true;
end;
$$;

drop function public.remote_saas_invites(uuid);
create function public.remote_saas_invites(tenant uuid)
returns table (id uuid, email text, role text, created_at timestamptz, expires_at timestamptz,
               mail_state text, mail_sent_at timestamptz, mail_locale text, mail_sends integer)
language plpgsql stable security definer set search_path = '' as $$
begin
    perform remote_saas.actor(tenant, '{owner,admin}', 'read');
    return query select i.id, i.email, i.role, i.created_at, i.expires_at,
            i.mail_state, i.mail_sent_at, i.mail_locale, i.mail_sends from remote_saas.invites i
        where i.tenant_id = tenant and i.accepted_at is null and i.revoked_at is null and i.expires_at > now()
        order by i.created_at desc limit 200;
end;
$$;

-- Revoking also stops an e-mail that was not sent yet (the mail service checks before sending).
create or replace function public.remote_saas_revoke_invite(tenant uuid, invite uuid)
returns boolean language plpgsql security definer set search_path = '' as $$
declare me remote_saas.members;
begin
    me := remote_saas.actor(tenant, '{owner,admin}', 'read');
    update remote_saas.invites set revoked_at = now(), token_hash = null,
            mail_state = case when mail_state in ('pending', 'sending') then 'failed' else mail_state end,
            mail_error = case when mail_state in ('pending', 'sending') then 'revoked before sending' else mail_error end
        where id = invite and tenant_id = tenant and accepted_at is null and revoked_at is null;
    if found then perform remote_saas.audit(tenant, me.user_id, 'member.invite_revoke', invite::text); end if;
    return found;
end;
$$;

do $$ declare f text; begin
    for f in select p.oid::regprocedure::text from pg_proc p
             where p.pronamespace = 'public'::regnamespace
               and p.proname in ('remote_saas_invite_member', 'remote_saas_resend_invite', 'remote_saas_invites',
                                 'remote_saas_revoke_invite') loop
        execute format('revoke all on function %s from public, anon, authenticated', f);
        execute format('grant execute on function %s to authenticated', f);
    end loop;
end $$;

---------------------------------------------------------------------------------------------------
-- Mail service functions (role mixel_remote_mail only)

-- Claims up to `max_rows` invitations whose e-mail is due. A claim older than 5 minutes (a crashed
-- service) is taken over. Returns what the e-mail needs; never a secret.
create function remote_saas.mail_claim_invites(max_rows integer default 10)
returns table (invite_id uuid, claim uuid, email text, role text, locale text, expires_at timestamptz,
               company text, inviter_name text, inviter_email text, attempt integer)
language plpgsql security definer set search_path = '' as $$
begin
    return query
    with due as (
        select i.id from remote_saas.invites i
        where i.accepted_at is null and i.revoked_at is null and i.expires_at > now()
          and ((i.mail_state = 'pending' and coalesce(i.mail_next_at, now()) <= now())
               or (i.mail_state = 'sending' and i.mail_claimed_at < now() - interval '5 minutes'))
        order by i.mail_next_at nulls first, i.created_at
        limit greatest(1, least(coalesce(max_rows, 10), 50))
        for update skip locked
    ), claimed as (
        update remote_saas.invites i set mail_state = 'sending', mail_claim = gen_random_uuid(),
                mail_claimed_at = now(), mail_attempts = i.mail_attempts + 1, token_hash = null
        from due where i.id = due.id
        returning i.*
    )
    select c.id, c.mail_claim, c.email, c.role, c.mail_locale, c.expires_at, t.name,
           coalesce(nullif(btrim(u.name), ''), split_part(u.email, '@', 1)), u.email, c.mail_attempts
    from claimed c
    join remote_saas.tenants t on t.id = c.tenant_id
    join public.users u on u.id = c.invited_by;
end;
$$;

-- Stores the SHA-256 of the secret the service just created, right before it sends the e-mail.
-- False when the invitation was revoked or re-claimed meanwhile (then nothing is sent).
create function remote_saas.mail_invite_secret(invite uuid, claim_id uuid, secret_hash bytea)
returns boolean language plpgsql security definer set search_path = '' as $$
begin
    if octet_length(secret_hash) is distinct from 32 then
        raise exception 'Invalid secret hash' using errcode = '22023';
    end if;
    update remote_saas.invites set token_hash = secret_hash
        where id = invite and mail_claim = claim_id and mail_state = 'sending'
          and accepted_at is null and revoked_at is null and expires_at > now();
    return found;
end;
$$;

-- The result of one send. On failure the link is removed and the e-mail is retried later
-- (1, 5, 15, 60 minutes); after 5 attempts, or at once with give_up (the address was refused),
-- the invitation shows "not sent".
create function remote_saas.mail_invite_result(invite uuid, claim_id uuid, message_id text, failure text,
                                               give_up boolean default false)
returns text language plpgsql security definer set search_path = '' as $$
declare row_now remote_saas.invites; next_state text;
begin
    select * into row_now from remote_saas.invites i where i.id = invite and i.mail_claim = claim_id for update;
    if not found then return 'stale'; end if;
    if row_now.revoked_at is not null or row_now.accepted_at is not null then
        update remote_saas.invites set mail_claim = null, mail_claimed_at = null where id = invite;
        return 'stale';
    end if;
    if failure is null and message_id is not null and row_now.token_hash is not null then
        update remote_saas.invites set mail_state = 'sent', mail_sent_at = now(), mail_sends = mail_sends + 1,
                mail_message_id = left(message_id, 200), mail_error = null, mail_claim = null,
                mail_claimed_at = null, mail_next_at = null
            where id = invite;
        return 'sent';
    end if;
    next_state := case when row_now.mail_attempts >= 5 or coalesce(give_up, false) then 'failed' else 'pending' end;
    update remote_saas.invites set token_hash = null, mail_state = next_state,
            mail_error = left(coalesce(failure, 'not sent'), 300), mail_claim = null, mail_claimed_at = null,
            mail_next_at = case next_state when 'pending' then now() + (case row_now.mail_attempts
                when 1 then interval '1 minute' when 2 then interval '5 minutes'
                when 3 then interval '15 minutes' else interval '60 minutes' end) end
        where id = invite;
    return next_state;
end;
$$;

revoke all on function remote_saas.mail_claim_invites(integer), remote_saas.mail_invite_secret(uuid, uuid, bytea),
    remote_saas.mail_invite_result(uuid, uuid, text, text, boolean), remote_saas.mail_locale_for(uuid, text)
    from public, anon, authenticated;
grant execute on function remote_saas.mail_claim_invites(integer), remote_saas.mail_invite_secret(uuid, uuid, bytea),
    remote_saas.mail_invite_result(uuid, uuid, text, text, boolean) to mixel_remote_mail;

commit;
notify pgrst, 'reload schema';
