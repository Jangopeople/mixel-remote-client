alter table public.organizations add column mixel_org_id uuid, add column name text;
alter table public.users add column deleted_at timestamptz;
create table auth.identities(user_id uuid, provider text, provider_id text, identity_data jsonb);
\ir ../002_company_administrators.sql

insert into public.organizations (id, is_active, mixel_org_id, name) values
    ('10000000-0000-0000-0000-000000000001', true, '10000000-0000-0000-0000-000000000002', 'Fixture Company');
insert into public.users (id, auth_user_id, role, is_active) values
    ('20000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'admin', true),
    ('20000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'admin', true);
insert into auth.identities values (
    '20000000-0000-0000-0000-000000000001', 'keycloak', 'owner-subject',
    '{"iss":"https://auth.mixel.ch/application/o/mixel-ism-user/","sub":"owner-subject"}'
);
insert into remote_private.company_administrators (profile_id, organization_id, mixel_subject) values
    ('20000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-000000000001', 'owner-subject');

do $$
begin
    assert not has_function_privilege('anon', 'public.remote_my_company_administration()', 'EXECUTE');
    assert not has_table_privilege('authenticated', 'remote_private.company_administrators', 'SELECT');
end $$;
\echo PASS anonymous and direct grant access denied

set role authenticated;
set request.jwt.claim.sub = '20000000-0000-0000-0000-000000000001';
set request.jwt.claims = '{"aal":"aal1"}';
do $$ begin assert not exists (select 1 from public.remote_my_company_administration()); end $$;
\echo PASS AAL1 cannot resolve company administration

set request.jwt.claims = '{"aal":"aal2"}';
do $$ begin assert (select company_name = 'Fixture Company'
    and company_id = '10000000-0000-0000-0000-000000000002'::uuid
    from public.remote_my_company_administration()); end $$;
\echo PASS AAL2 resolves the bound company for the same identity

set request.jwt.claim.sub = '20000000-0000-0000-0000-000000000002';
do $$ begin assert not exists (select 1 from public.remote_my_company_administration()); end $$;
\echo PASS a different staff admin receives no company grant

reset role;
update auth.identities set identity_data = '{"iss":"https://auth.mixel.ch/application/o/other","sub":"owner-subject"}';
set role authenticated;
set request.jwt.claim.sub = '20000000-0000-0000-0000-000000000001';
do $$ begin assert not exists (select 1 from public.remote_my_company_administration()); end $$;
\echo PASS an identity from a different issuer receives no grant

reset role;
update auth.identities set identity_data = '{"iss":"https://auth.mixel.ch/application/o/mixel-ism-user/","sub":"owner-subject"}';
update public.organizations set is_active = false;
set role authenticated;
do $$ begin assert not exists (select 1 from public.remote_my_company_administration()); end $$;
\echo PASS an inactive company disappears from administration
