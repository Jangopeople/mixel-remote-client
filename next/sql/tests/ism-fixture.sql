create role anon; create role authenticated;
create schema auth; create schema extensions;
create extension pgcrypto with schema extensions;
create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
create function auth.jwt() returns jsonb language sql stable as $$ select coalesce(nullif(current_setting('request.jwt.claims',true),''),'{}')::jsonb $$;
create table public.organizations(id uuid primary key,is_active boolean not null);
create table public.users(id uuid primary key,auth_user_id uuid unique,organization_id uuid references public.organizations(id),role text,is_active boolean);
create table public.clients(id uuid primary key,organization_id uuid references public.organizations(id),is_active boolean not null default true);
create table public.devices(id uuid primary key,client_id uuid references public.clients(id));
