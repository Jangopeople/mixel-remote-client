begin;

create table remote_private.company_administrators (
    profile_id uuid not null references public.users(id) on delete cascade,
    organization_id uuid not null references public.organizations(id) on delete cascade,
    mixel_subject text not null check (length(mixel_subject) between 1 and 255),
    granted_at timestamptz not null default now(),
    primary key (profile_id, organization_id)
);
alter table remote_private.company_administrators enable row level security;
revoke all on remote_private.company_administrators from public, anon, authenticated;

-- Return only the calling account's active grants. The Mixel ID identity is
-- owned by GoTrue, and the registered grant uses its stable subject.
create function public.remote_my_company_administration()
returns table (company_id uuid, company_name text)
language sql stable security definer set search_path = '' as $$
    select o.mixel_org_id, o.name
    from remote_private.company_administrators ca
    join public.users u on u.id = ca.profile_id
    join public.organizations o on o.id = ca.organization_id
    where u.auth_user_id = auth.uid()
      and u.is_active
      and u.deleted_at is null
      and o.is_active
      and o.mixel_org_id is not null
      and auth.jwt()->>'aal' = 'aal2'
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
$$;
revoke all on function public.remote_my_company_administration() from public, anon;
grant execute on function public.remote_my_company_administration() to authenticated;

commit;
