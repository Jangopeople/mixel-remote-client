-- Trust Authentik (Mixel ID) OIDC logins as MFA-verified in Mixel Remote SQL helpers.
-- Authentik's mixel-id-secure-sso-v1-authentication flow enforces MFA (Passkey / Mixel ID Push / TOTP),
-- matching public._staff_mfa_pending(), so staff and company admins are not forced through a second
-- Supabase GoTrue TOTP code after already authenticating via Mixel ID.

CREATE OR REPLACE FUNCTION remote_private.mfa_verified()
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
  SELECT coalesce(auth.jwt()->>'aal', '') = 'aal2'
    OR (
      (
        coalesce(auth.jwt()->'app_metadata'->>'provider', '') = 'keycloak'
        OR coalesce(auth.jwt()->'app_metadata'->'providers', '[]'::jsonb) ? 'keycloak'
      )
      AND EXISTS (
        SELECT 1
        FROM jsonb_array_elements(
          CASE
            WHEN jsonb_typeof(auth.jwt()->'amr') = 'array' THEN auth.jwt()->'amr'
            ELSE '[]'::jsonb
          END
        ) item
        WHERE item->>'method' = 'oauth'
      )
      AND remote_private.verified_mixel_subject(auth.uid()) IS NOT NULL
    );
$function$;

CREATE OR REPLACE FUNCTION remote_private.staff_actor()
 RETURNS uuid
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare actor uuid;
begin
    if not remote_private.mfa_verified() then
        raise exception 'MFA-authenticated staff required' using errcode = '42501';
    end if;
    select u.id into actor from public.users u
    where u.auth_user_id = auth.uid() and u.is_active
      and u.organization_id is null and u.role::text in ('admin','agent');
    if actor is null then
        raise exception 'Active internal staff required' using errcode = '42501';
    end if;
    return actor;
end;
$function$;

CREATE OR REPLACE FUNCTION public.remote_my_company_administration()
 RETURNS TABLE(company_id uuid, company_name text)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
    select o.mixel_org_id, o.name
    from remote_private.company_administrators ca
    join public.users u on u.id = ca.profile_id
    join public.organizations o on o.id = ca.organization_id
    where u.auth_user_id = auth.uid()
      and u.is_active
      and u.deleted_at is null
      and o.is_active
      and o.mixel_org_id is not null
      and remote_private.mfa_verified()
      and exists (
        select 1 from auth.identities i
        where i.user_id = u.auth_user_id
          and i.provider = 'keycloak'
          and i.identity_data->>'iss' in (
            'https://auth.mixel.ch/application/o/mixel-ism-user',
            'https://auth.mixel.ch/application/o/mixel-ism-user/'
          )
          and i.identity_data->>'sub' = ca.mixel_subject
          and i.provider_id = ca.mixel_subject
      )
    order by o.name, o.mixel_org_id;
$function$;

CREATE OR REPLACE FUNCTION remote_private.can_admin_device(target uuid)
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
    select case
        when remote_private.mfa_verified() and exists (
            select 1 from public.users u
            where u.auth_user_id = auth.uid()
              and u.is_active
              and u.deleted_at is null
              and u.organization_id is null
              and u.role::text = 'admin'
        ) then true
        when t.id is not null then exists (
            select 1 from remote_saas.members m
            join public.users u on u.id = m.user_id
            where m.tenant_id = t.id
              and m.role in ('owner', 'admin')
              and u.auth_user_id = auth.uid()
              and u.is_active
              and u.deleted_at is null
              and u.organization_id = t.organization_id
        )
        else remote_private.mfa_verified() and exists (
            select 1 from remote_private.company_administrators ca
            join public.users u on u.id = ca.profile_id
            join public.organizations o on o.id = ca.organization_id and o.is_active
            join auth.identities i on i.user_id = u.auth_user_id and i.provider = 'keycloak'
              and i.identity_data->>'iss' in ('https://auth.mixel.ch/application/o/mixel-ism-user',
                                              'https://auth.mixel.ch/application/o/mixel-ism-user/')
              and i.identity_data->>'sub' = ca.mixel_subject and i.provider_id = ca.mixel_subject
            where u.auth_user_id = auth.uid()
              and u.is_active
              and u.deleted_at is null
              and ca.organization_id = c.organization_id
        )
    end
    from public.devices d
    join public.clients c on c.id = d.client_id
    left join remote_saas.tenants t on t.client_id = d.client_id
    where d.id = target;
$function$;

CREATE OR REPLACE FUNCTION remote_private.can_read_device(target uuid)
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
    select case when t.id is null then exists (
        select 1 from public.users u
        join public.devices d on d.id = target
        join public.clients c on c.id = d.client_id
        left join public.organizations o on o.id = c.organization_id
        where u.auth_user_id = auth.uid() and u.is_active and c.is_active
          and (c.organization_id is null or o.is_active)
          and (
            (u.organization_id is null and u.role::text in ('admin','agent')
             and remote_private.mfa_verified())
            or (u.organization_id is not null and u.organization_id = c.organization_id)
          )
    ) else exists (
        select 1 from public.users u
        join remote_saas.members m on m.tenant_id = t.id and m.user_id = u.id
        join public.organizations o on o.id = t.organization_id and o.is_active
        join public.clients c on c.id = t.client_id and c.is_active
        left join remote_saas.auth_settings s on s.tenant_id = t.id
        where u.auth_user_id = auth.uid() and u.is_active and u.deleted_at is null
          and u.organization_id = t.organization_id
          and remote_saas.effective_status(t) <> 'closed'
          and (s.tenant_id is null or ((not s.require_mfa or remote_private.mfa_verified())
               and remote_saas.method_allowed(s.methods)))
          and remote_saas.can_access_device(u.id, target)
    ) end
    from (select 1) one
    left join public.devices d on d.id = target
    left join remote_saas.tenants t on t.client_id = d.client_id;
$function$;

CREATE OR REPLACE FUNCTION remote_private.admin_context(target uuid, need_admin boolean, need text DEFAULT 'write'::text)
 RETURNS remote_private.device_admin_ctx
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare ctx remote_private.device_admin_ctx; me public.users; t remote_saas.tenants; c public.clients;
begin
    me := remote_private.me();
    if target is null or me.id is null then
        perform remote_private.raise_hint('PT404', 'Device not found', 'device_not_found');
    end if;
    select d.id, d.client_id, null::uuid, me.id, d.name, e.connection_id into ctx
        from public.devices d
        join public.remote_device_enrollments e on e.device_id = d.id and e.revoked_at is null
        where d.id = target;
    if ctx.device_id is null or not remote_private.can_read_device(target) then
        if ctx.device_id is not null and not remote_private.mfa_verified()
            and (me.organization_id is null or exists (
                select 1 from remote_saas.tenants tt join remote_saas.auth_settings s on s.tenant_id = tt.id
                where tt.client_id = ctx.client_id and s.require_mfa and tt.organization_id = me.organization_id)) then
            perform remote_private.raise_hint('42501', 'Two-step sign-in is required', 'mfa_required');
        end if;
        perform remote_private.raise_hint('PT404', 'Device not found', 'device_not_found');
    end if;
    select * into t from remote_saas.tenants where client_id = ctx.client_id;
    ctx.tenant_id := t.id;
    if t.id is not null then
        select coalesce(dm.display_name, ctx.device_name) into ctx.device_name
            from remote_saas.device_meta dm where dm.device_id = target;
        ctx.device_name := coalesce(ctx.device_name, (select d.name from public.devices d where d.id = target));
    end if;
    if need_admin and not coalesce(remote_private.can_admin_device(target), false) then
        if t.id is null and not remote_private.mfa_verified() then
            perform remote_private.raise_hint('42501', 'Two-step sign-in is required', 'mfa_required');
        end if;
        perform remote_private.raise_hint('42501', 'Only an administrator can change this device', 'not_admin');
    end if;
    if t.id is not null and need = 'write'
        and remote_saas.effective_status(t) not in ('active', 'trial', 'past_due') then
        perform remote_private.raise_hint('25006', 'This Mixel Remote account is read-only', 'read_only');
    end if;
    return ctx;
end;
$function$;

CREATE OR REPLACE FUNCTION remote_saas.actor(target uuid, roles text[], need text DEFAULT 'write'::text)
 RETURNS remote_saas.members
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
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
        if s.require_mfa and not remote_private.mfa_verified() then
            raise exception 'Two-step sign-in is required by this company' using errcode = '42501';
        end if;
        if not remote_saas.method_allowed(s.methods) then
            raise exception 'This sign-in method is not allowed by this company' using errcode = '42501';
        end if;
    end if;
    return m;
end;
$function$;
