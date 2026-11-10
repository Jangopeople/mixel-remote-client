create table auth.users(id uuid primary key, banned_until timestamptz, deleted_at timestamptz);
create table auth.identities(user_id uuid not null references auth.users(id),
    provider text not null, provider_id text not null, identity_data jsonb not null);
create role mixel_remote_policy_reader nologin;
grant usage on schema remote_private to mixel_remote_policy_reader;
