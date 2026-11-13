-- Remote SaaS billing (migration 023). One transaction that is rolled back, so it can run against
-- the live database after the migration without leaving data behind. Synthetic accounts only.
--   psql -v ON_ERROR_STOP=1 -f next/sql/tests/remote-saas-billing.sql
\set ON_ERROR_STOP on
\set QUIET on
begin;
set local client_min_messages = notice;

create function pg_temp.act(who text) returns void language plpgsql as $$ begin
    perform set_config('request.jwt.claim.sub', '', true);
    perform set_config('request.jwt.claims', case when who is null then '' else json_build_object(
        'sub', current_setting('t.' || who), 'role', 'authenticated', 'aal', 'aal1',
        'amr', json_build_array(json_build_object('method', 'password')))::text end, true);
end $$;
create function pg_temp.id(name text) returns uuid language sql as $$ select current_setting('t.' || name)::uuid $$;
create function pg_temp.remember(name text, value uuid) returns void language sql as $$ select set_config('t.' || name, value::text, true) $$;
create function pg_temp.check(ok boolean, what text) returns void language plpgsql as $$ begin
    if not coalesce(ok, false) then raise exception 'FAILED: %', what; end if;
end $$;
-- A normalized subscription event as the billing service sends it.
create function pg_temp.sub_event(tenant uuid, sub text, stripe_status text, tenant_status text, plan_price text,
                                  seat_price text default null, seats integer default 0) returns jsonb
language sql as $$ select jsonb_build_object('kind', 'subscription', 'tenant_id', tenant, 'customer', 'cus_QAbilling1',
    'subscription', sub, 'stripe_status', stripe_status, 'tenant_status', tenant_status,
    'items', case when seat_price is null then jsonb_build_array(jsonb_build_object('price', plan_price, 'quantity', 1))
        else jsonb_build_array(jsonb_build_object('price', plan_price, 'quantity', 1),
                               jsonb_build_object('price', seat_price, 'quantity', seats)) end,
    'current_period_end', extract(epoch from now() + interval '1 year')::bigint,
    'cancel_at', null, 'canceled_at', null, 'cancel_at_period_end', false) $$;

do $$ declare n text; begin
    foreach n in array array['owner_a','tech_a','owner_b'] loop
        perform pg_temp.remember(n, gen_random_uuid());
        insert into auth.users(id, email, email_confirmed_at, aud, role, raw_user_meta_data)
        values (pg_temp.id(n), 'qa-billing-' || replace(n, '_', '-') || '-' || substr(md5(random()::text), 1, 6) || '@example.test',
                now(), 'authenticated', 'authenticated', jsonb_build_object('name', 'QA ' || n));
    end loop;
end $$;

-- Test catalogue (the live one is switched off inside this transaction).
update remote_saas.billing_prices set active = false;
insert into remote_saas.billing_prices(price_id, product_id, kind, plan, billing_interval, amount_rappen) values
    ('price_QAsoloyear', 'prod_QAsolo', 'plan', 'solo', 'year', 14400),
    ('price_QAsolomonth', 'prod_QAsolo', 'plan', 'solo', 'month', 1440),
    ('price_QAteamyear', 'prod_QAteam', 'plan', 'team', 'year', 46800),
    ('price_QAteammonth', 'prod_QAteam', 'plan', 'team', 'month', 4680),
    ('price_QAbusinessyear', 'prod_QAbusiness', 'plan', 'business', 'year', 106800),
    ('price_QAbusinessmonth', 'prod_QAbusiness', 'plan', 'business', 'month', 10680),
    ('price_QAseatyear', 'prod_QAseat', 'seat', null, 'year', 9600),
    ('price_QAseatmonth', 'prod_QAseat', 'seat', null, 'month', 960);

---------------------------------------------------------------------------------------------------
\echo '-- sign-up, member, portal API'
set local role authenticated;
select pg_temp.act('owner_a');
do $$ declare r jsonb; begin
    r := public.remote_saas_signup('QA Billing Alpha', true);
    perform pg_temp.remember('tenant_a', (r->>'tenant_id')::uuid);
end $$;
select pg_temp.act('owner_b');
do $$ declare r jsonb; begin
    r := public.remote_saas_signup('QA Billing Beta', true);
    perform pg_temp.remember('tenant_b', (r->>'tenant_id')::uuid);
end $$;
reset role;
select pg_temp.act(null);
-- tech_a joins tenant A as a technician (as an accepted invitation would).
do $$ declare org uuid; prof uuid; begin
    select organization_id into org from remote_saas.tenants where id = pg_temp.id('tenant_a');
    insert into public.users(auth_user_id, name, email, role, organization_id, is_active)
    values (pg_temp.id('tech_a'), 'QA tech', 'qa-billing-tech@example.test', 'user', org, true) returning id into prof;
    insert into remote_saas.members(tenant_id, user_id, role) values (pg_temp.id('tenant_a'), prof, 'technician');
end $$;

set local role authenticated;
select pg_temp.act('owner_a');
do $$ declare r jsonb; begin
    r := public.remote_saas_billing(pg_temp.id('tenant_a'));
    perform pg_temp.check(r->>'role' = 'owner' and r->>'plan' = 'business' and r->>'status' = 'trial'
        and (r->>'has_subscription')::boolean = false and (r->'usage'->>'technicians')::int = 2, 'owner billing view ' || r::text);
    begin perform public.remote_saas_billing(pg_temp.id('tenant_b')); raise exception 'FAILED: other tenant visible';
    exception when insufficient_privilege then null; end;
    perform pg_temp.check((select count(*) from public.mixel_remote_prices()) = 8, 'price list');
    perform pg_temp.check((select per_month_chf from public.mixel_remote_prices() where plan = 'team' and billing_interval = 'year') = 39,
        'team yearly per month');
    perform pg_temp.check((select amount_chf from public.mixel_remote_prices() where kind = 'seat' and billing_interval = 'month') = 9.60,
        'seat monthly');
    begin perform remote_saas.billing_apply_event('evt_QAx', 'x', now(), '{}'::jsonb); raise exception 'FAILED: user reached service function';
    exception when insufficient_privilege then null; end;
end $$;
select pg_temp.act('tech_a');
do $$ begin
    begin perform public.remote_saas_billing(pg_temp.id('tenant_a')); raise exception 'FAILED: technician sees billing';
    exception when insufficient_privilege then null; end;
end $$;
reset role;
set local role anon;
do $$ begin
    perform pg_temp.check((select count(*) from public.mixel_remote_prices()) = 8, 'anon price list');
    begin perform public.remote_saas_billing(pg_temp.id('tenant_a')); raise exception 'FAILED: anon billing';
    exception when insufficient_privilege then null; end;
end $$;
reset role;

---------------------------------------------------------------------------------------------------
\echo '-- billing service: context, customer, plan fit'
set local role mixel_remote_billing;
do $$ declare c jsonb; begin
    c := remote_saas.billing_context(pg_temp.id('tenant_a'));
    perform pg_temp.check(c->>'status' = 'trial' and c->>'owner_email' like 'qa-billing-owner-a-%'
        and jsonb_array_length(c->'prices') = 8 and c->>'stripe_customer_id' is null, 'context ' || c::text);
    perform pg_temp.check(remote_saas.billing_link_customer(pg_temp.id('tenant_a'), 'cus_QAbilling1') = 'cus_QAbilling1', 'link');
    perform pg_temp.check(remote_saas.billing_link_customer(pg_temp.id('tenant_a'), 'cus_QAother') = 'cus_QAbilling1', 'link once');
    perform pg_temp.check(remote_saas.billing_plan_problem(pg_temp.id('tenant_a'), 'solo', 0) like 'Your company has 2 technicians%', 'solo too small');
    perform pg_temp.check(remote_saas.billing_plan_problem(pg_temp.id('tenant_a'), 'solo', 1) is null, 'solo + 1 fits');
    perform pg_temp.check(remote_saas.billing_plan_problem(pg_temp.id('tenant_a'), 'enterprise', 0) is not null, 'enterprise not sold');
    perform pg_temp.check(remote_saas.billing_plan_problem(pg_temp.id('tenant_a'), 'team', -1) is not null, 'negative seats');
    perform pg_temp.check(remote_saas.billing_apply_event('evt_QAgrant', 'ping', now(), '{"kind":"x"}')->>'outcome'
        = 'ignored_unknown_tenant', 'service applies events');
    begin perform * from remote_saas.tenants; raise exception 'FAILED: service reads tables';
    exception when insufficient_privilege then null; end;
end $$;

reset role;
\echo '-- webhook events: checkout, subscription, idempotency, ordering'
do $$ declare r jsonb; t remote_saas.tenants; b remote_saas.billing; base timestamptz := now() - interval '1 hour'; begin
    r := remote_saas.billing_apply_event('evt_QA1', 'checkout.session.completed', base,
        jsonb_build_object('kind', 'checkout', 'tenant_id', pg_temp.id('tenant_a'), 'customer', 'cus_QAbilling1', 'subscription', 'sub_QA1'));
    perform pg_temp.check(r->>'outcome' = 'applied', 'checkout ' || r::text);
    -- Trialing subscription (bought during the trial): Team yearly + 2 extra technicians.
    r := remote_saas.billing_apply_event('evt_QA2', 'customer.subscription.created', base + interval '1 second',
        pg_temp.sub_event(pg_temp.id('tenant_a'), 'sub_QA1', 'trialing', 'active', 'price_QAteamyear', 'price_QAseatyear', 2));
    perform pg_temp.check(r->>'outcome' = 'applied' and r->>'plan' = 'team' and r->>'status' = 'active'
        and (r->>'extra_technicians')::int = 2, 'subscription created ' || r::text);
    r := remote_saas.billing_apply_event('evt_QA2', 'customer.subscription.created', base + interval '1 second',
        pg_temp.sub_event(pg_temp.id('tenant_a'), 'sub_QA1', 'trialing', 'active', 'price_QAteamyear', 'price_QAseatyear', 2));
    perform pg_temp.check(r->>'outcome' = 'duplicate', 'duplicate ' || r::text);
    -- An older event that arrives late does not overwrite newer state.
    r := remote_saas.billing_apply_event('evt_QA0', 'customer.subscription.updated', base - interval '1 minute',
        pg_temp.sub_event(pg_temp.id('tenant_a'), 'sub_QA1', 'past_due', 'past_due', 'price_QAsoloyear'));
    perform pg_temp.check(r->>'outcome' = 'stale', 'stale ' || r::text);
    r := remote_saas.billing_apply_event('evt_QA3', 'customer.subscription.updated', base + interval '2 seconds',
        pg_temp.sub_event(pg_temp.id('tenant_a'), 'sub_QA1', 'active', 'active', 'price_QAteamyear', 'price_QAunknown', 1));
    perform pg_temp.check(r->>'outcome' = 'ignored_unknown_price', 'unknown price ' || r::text);
    -- Another tenant's customer is never linked to tenant B.
    r := remote_saas.billing_apply_event('evt_QA4', 'customer.subscription.created', base + interval '2 seconds',
        pg_temp.sub_event(pg_temp.id('tenant_b'), 'sub_QA1', 'active', 'active', 'price_QAsoloyear'));
    perform pg_temp.check(r->>'outcome' = 'ignored_foreign_customer', 'foreign ' || r::text);
    r := remote_saas.billing_apply_event('evt_QA5', 'customer.subscription.updated', base,
        jsonb_build_object('kind', 'subscription', 'tenant_id', gen_random_uuid(), 'subscription', 'sub_QAnone'));
    perform pg_temp.check(r->>'outcome' = 'ignored_unknown_tenant', 'unknown tenant ' || r::text);
    select * into t from remote_saas.tenants where id = pg_temp.id('tenant_b');
    perform pg_temp.check(t.plan = 'business' and t.status = 'trial', 'tenant B untouched');

    raise notice '-- invoices: failed payment, paid again';
    r := remote_saas.billing_apply_event('evt_QA6', 'invoice.payment_failed', base + interval '3 seconds',
        jsonb_build_object('kind', 'invoice_failed', 'tenant_id', pg_temp.id('tenant_a'), 'customer', 'cus_QAbilling1',
                           'subscription', 'sub_QA1', 'invoice', 'in_QA1'));
    perform pg_temp.check(r->>'status' = 'past_due', 'payment failed ' || r::text);
    r := remote_saas.billing_apply_event('evt_QA7', 'invoice.paid', base + interval '4 seconds',
        jsonb_build_object('kind', 'invoice_paid', 'customer', 'cus_QAbilling1', 'subscription', 'sub_QA1', 'invoice', 'in_QA1',
                           'current_period_end', extract(epoch from now() + interval '2 years')::bigint));
    perform pg_temp.check(r->>'status' = 'active' and r->>'tenant_id' = pg_temp.id('tenant_a')::text, 'paid (tenant by subscription) ' || r::text);
    select * into b from remote_saas.billing where tenant_id = pg_temp.id('tenant_a');
    perform pg_temp.check(b.current_period_end > now() + interval '23 months' and b.billing_interval = 'year'
        and b.seat_quantity = 2 and b.plan_price_id = 'price_QAteamyear', 'billing row ' || row_to_json(b));

    raise notice '-- cancel at period end, then cancelled: read-only, devices kept 30 days';
    r := remote_saas.billing_apply_event('evt_QA8', 'customer.subscription.updated', base + interval '5 seconds',
        pg_temp.sub_event(pg_temp.id('tenant_a'), 'sub_QA1', 'active', 'active', 'price_QAteammonth', 'price_QAseatmonth', 3)
        || jsonb_build_object('cancel_at_period_end', true, 'cancel_at', extract(epoch from now() + interval '20 days')::bigint));
    select * into b from remote_saas.billing where tenant_id = pg_temp.id('tenant_a');
    perform pg_temp.check(r->>'status' = 'active' and b.cancel_at_period_end and b.cancel_at > now()
        and b.billing_interval = 'month' and b.seat_quantity = 3, 'cancel scheduled ' || r::text);
    r := remote_saas.billing_apply_event('evt_QA9', 'customer.subscription.deleted', base + interval '6 seconds',
        pg_temp.sub_event(pg_temp.id('tenant_a'), 'sub_QA1', 'canceled', 'read_only', 'price_QAteammonth', 'price_QAseatmonth', 3));
    select * into b from remote_saas.billing where tenant_id = pg_temp.id('tenant_a');
    perform pg_temp.check(r->>'status' = 'read_only' and b.devices_delete_after between now() + interval '29 days' and now() + interval '31 days',
        'cancelled ' || r::text || ' ' || row_to_json(b));
    -- A late payment-failed mail for the ended subscription keeps it read-only.
    r := remote_saas.billing_apply_event('evt_QA10', 'invoice.payment_failed', base + interval '7 seconds',
        jsonb_build_object('kind', 'invoice_failed', 'tenant_id', pg_temp.id('tenant_a'), 'subscription', 'sub_QA1'));
    perform pg_temp.check(r->>'status' = 'read_only', 'failed after cancel ' || r::text);
    -- Buying again (new subscription): active, countdown cleared.
    r := remote_saas.billing_apply_event('evt_QA11', 'customer.subscription.created', base + interval '8 seconds',
        pg_temp.sub_event(pg_temp.id('tenant_a'), 'sub_QA2', 'active', 'active', 'price_QAsolomonth', 'price_QAseatmonth', 1));
    select * into b from remote_saas.billing where tenant_id = pg_temp.id('tenant_a');
    perform pg_temp.check(r->>'status' = 'active' and r->>'plan' = 'solo' and b.devices_delete_after is null
        and b.stripe_subscription_id = 'sub_QA2', 'bought again ' || r::text);
    -- The old subscription's late deletion does not end the new one.
    r := remote_saas.billing_apply_event('evt_QA12', 'customer.subscription.deleted', base + interval '9 seconds',
        pg_temp.sub_event(pg_temp.id('tenant_a'), 'sub_QA1', 'canceled', 'read_only', 'price_QAteammonth'));
    perform pg_temp.check(r->>'outcome' = 'ignored_other_subscription', 'old subscription ' || r::text);
    -- incomplete: recorded, tenant unchanged.
    r := remote_saas.billing_apply_event('evt_QA13', 'customer.subscription.updated', base + interval '10 seconds',
        pg_temp.sub_event(pg_temp.id('tenant_a'), 'sub_QA2', 'incomplete', null, 'price_QAbusinessmonth'));
    perform pg_temp.check(r->>'plan' = 'solo' and r->>'status' = 'active', 'incomplete keeps tenant ' || r::text);
    perform pg_temp.check((select count(*) from remote_saas.billing_events where event_id like 'evt_QA%') = 15, 'events stored');
end $$;
reset role;

---------------------------------------------------------------------------------------------------
\echo '-- daily job: trial expiry, retention, device removal'
select pg_temp.act(null);
do $$ declare r jsonb; t remote_saas.tenants; b remote_saas.billing; dev uuid; client uuid; begin
    update remote_saas.tenants set trial_ends_at = now() - interval '31 days' where id = pg_temp.id('tenant_b');
    select client_id into client from remote_saas.tenants where id = pg_temp.id('tenant_b');
    insert into public.devices(client_id, name, type, os) values (client, 'QA billing device', 'desktop', 'macos') returning id into dev;
    perform pg_temp.remember('device_b', dev);
end $$;
set local role mixel_remote_billing;
do $$ begin
    begin perform remote_saas.billing_daily(true); raise exception 'FAILED: service runs the daily job';
    exception when insufficient_privilege then null; end;
end $$;
reset role;
do $$ declare r jsonb; t remote_saas.tenants; b remote_saas.billing; begin
    r := remote_saas.billing_daily(true);
    select * into t from remote_saas.tenants where id = pg_temp.id('tenant_b');
    perform pg_temp.check(r->'trials_expired' @> jsonb_build_array(jsonb_build_object('tenant_id', pg_temp.id('tenant_b')))
        and t.status = 'trial', 'dry run lists, changes nothing ' || r::text);
    r := remote_saas.billing_daily(false);
    select * into t from remote_saas.tenants where id = pg_temp.id('tenant_b');
    select * into b from remote_saas.billing where tenant_id = pg_temp.id('tenant_b');
    -- Trial ended 31 days ago: read-only since then, so its devices are due now (same run).
    perform pg_temp.check(t.status = 'read_only' and b.read_only_since = t.trial_ends_at
        and b.devices_delete_after = t.trial_ends_at + interval '30 days', 'trial expired ' || row_to_json(b));
    perform pg_temp.check(r->'devices_removed' @> jsonb_build_array(jsonb_build_object('tenant_id', pg_temp.id('tenant_b'), 'devices', 1)),
        'device removed ' || r::text);
    perform pg_temp.check(not exists (select 1 from public.devices where id = pg_temp.id('device_b')), 'device gone');
    perform pg_temp.check(exists (select 1 from remote_saas.audit_log where tenant_id = pg_temp.id('tenant_b')
        and action = 'billing.devices_removed'), 'audited');
    -- Tenant A (active) untouched; a second run removes nothing more.
    perform pg_temp.check((select status from remote_saas.tenants where id = pg_temp.id('tenant_a')) = 'active', 'tenant A active');
    r := remote_saas.billing_daily(false);
    perform pg_temp.check(not (r->'devices_removed' @> jsonb_build_array(jsonb_build_object('tenant_id', pg_temp.id('tenant_b')))),
        'second run idle ' || r::text);
end $$;

-- A recent trial end: marked, devices kept.
do $$ declare r jsonb; b remote_saas.billing; begin
    update remote_saas.tenants set status = 'trial', trial_ends_at = now() - interval '1 day' where id = pg_temp.id('tenant_b');
    delete from remote_saas.billing where tenant_id = pg_temp.id('tenant_b');
    r := remote_saas.billing_daily(false);
    select * into b from remote_saas.billing where tenant_id = pg_temp.id('tenant_b');
    perform pg_temp.check(b.devices_delete_after between now() + interval '28 days' and now() + interval '30 days'
        and b.devices_deleted_at is null, 'recent expiry keeps devices ' || row_to_json(b));
end $$;

\echo 'ALL REMOTE SAAS BILLING TESTS PASSED'
rollback;
