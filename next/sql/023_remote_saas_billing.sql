-- Mixel Remote as SaaS, stage 2: Stripe billing. The billing service (next/services/billing) creates
-- Checkout and customer-portal sessions and receives Stripe webhooks; it reaches this schema only
-- through the SECURITY DEFINER functions below, granted to role mixel_remote_billing (its login
-- role mixel_remote_billing_service is created at deploy time, see next/services/billing/README.md).
--
-- Prices are the amounts the customer pays, Swiss VAT (8.1 %) included, exactly like Mixel Shop:
-- no Stripe Tax and no Stripe tax rates; VAT is accounted in Mixel ACC. Yearly billing is the list
-- price, monthly billing is +20 %, an extra technician is CHF 8 per month (yearly) or 9.60 (monthly).
--
-- Lifecycle: a trial that ends without a subscription becomes read-only; a cancelled or unpaid
-- subscription becomes read-only. Devices of a read-only tenant are kept 30 days and then removed by
-- the daily job (remote_saas.billing_daily, systemd timer mixel-remote-billing-daily on mixel-vps).
begin;
set local lock_timeout = '5s';

do $$ begin
    create role mixel_remote_billing nologin;
exception when duplicate_object then null; end $$;
grant usage on schema remote_saas to mixel_remote_billing;

-- The Stripe catalogue (live ids, seeded by 024 after the catalogue script created them).
create table remote_saas.billing_prices (
    price_id text primary key check (price_id ~ '^price_[A-Za-z0-9]+$'),
    product_id text not null check (product_id ~ '^[A-Za-z0-9_]+$'),
    kind text not null check (kind in ('plan', 'seat')),
    plan text references remote_saas.plans(code) check (plan in ('solo', 'team', 'business')),
    billing_interval text not null check (billing_interval in ('month', 'year')),
    amount_rappen integer not null check (amount_rappen > 0),
    active boolean not null default true,
    created_at timestamptz not null default now(),
    check ((kind = 'plan') = (plan is not null))
);
create unique index billing_prices_one_active on remote_saas.billing_prices
    (kind, coalesce(plan, ''), billing_interval) where active;

-- One row per tenant once billing or the retention job has touched it.
create table remote_saas.billing (
    tenant_id uuid primary key references remote_saas.tenants(id) on delete cascade,
    stripe_customer_id text unique check (stripe_customer_id ~ '^cus_[A-Za-z0-9]+$'),
    stripe_subscription_id text unique check (stripe_subscription_id ~ '^sub_[A-Za-z0-9]+$'),
    subscription_status text check (subscription_status in ('incomplete', 'incomplete_expired',
        'trialing', 'active', 'past_due', 'canceled', 'unpaid', 'paused')),
    billing_interval text check (billing_interval in ('month', 'year')),
    plan_price_id text,
    seat_price_id text,
    seat_quantity integer not null default 0 check (seat_quantity between 0 and 10000),
    current_period_end timestamptz,
    cancel_at timestamptz,
    cancel_at_period_end boolean not null default false,
    canceled_at timestamptz,
    -- Stripe "created" time of the newest event applied; older events never overwrite newer state.
    last_event_at timestamptz,
    read_only_since timestamptz,
    devices_delete_after timestamptz,
    devices_deleted_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- Every webhook event id is applied at most once (Stripe retries and may deliver twice).
create table remote_saas.billing_events (
    event_id text primary key check (event_id ~ '^evt_[A-Za-z0-9_]+$'),
    event_type text not null check (char_length(event_type) <= 80),
    stripe_created timestamptz not null,
    tenant_id uuid references remote_saas.tenants(id) on delete set null,
    outcome text not null check (char_length(outcome) <= 40),
    received_at timestamptz not null default now()
);
create index billing_events_tenant on remote_saas.billing_events(tenant_id, received_at desc);

do $$ declare t text; begin
    foreach t in array array['billing_prices','billing','billing_events'] loop
        execute format('alter table remote_saas.%I enable row level security', t);
        execute format('revoke all on remote_saas.%I from public, anon, authenticated', t);
    end loop;
end $$;

---------------------------------------------------------------------------------------------------
-- Helpers

create function remote_saas.billing_row(tenant uuid) returns remote_saas.billing
language plpgsql security definer set search_path = '' as $$
declare b remote_saas.billing;
begin
    insert into remote_saas.billing(tenant_id) values (tenant) on conflict (tenant_id) do nothing;
    select * into b from remote_saas.billing where tenant_id = tenant for update;
    return b;
end;
$$;

-- Read-only from `since`: devices are kept 30 days. Active again: the countdown is cleared.
create function remote_saas.billing_mark(tenant uuid, new_status text, since timestamptz) returns void
language plpgsql security definer set search_path = '' as $$
begin
    perform remote_saas.billing_row(tenant);
    if new_status = 'read_only' then
        update remote_saas.billing set
            read_only_since = coalesce(read_only_since, since),
            devices_delete_after = coalesce(devices_delete_after, since + interval '30 days'),
            updated_at = now()
        where tenant_id = tenant;
    elsif new_status in ('active', 'trial', 'past_due') then
        update remote_saas.billing set read_only_since = null, devices_delete_after = null,
            devices_deleted_at = null, updated_at = now()
        where tenant_id = tenant and (read_only_since is not null or devices_delete_after is not null);
    end if;
end;
$$;

create function remote_saas.billing_set_status(tenant uuid, new_status text) returns void
language plpgsql security definer set search_path = '' as $$
begin
    update remote_saas.tenants set status = new_status, status_changed_at = now()
    where id = tenant and status <> new_status and status <> 'closed';
    perform remote_saas.billing_mark(tenant, new_status, now());
end;
$$;

-- Would this plan and number of extra technicians hold the tenant's current members and devices?
-- Null when it fits, else the reason (shown to the customer).
create function remote_saas.billing_plan_problem(tenant uuid, new_plan text, seats integer) returns text
language plpgsql stable security definer set search_path = '' as $$
declare p remote_saas.plans; t remote_saas.tenants;
begin
    select * into p from remote_saas.plans where code = new_plan and code in ('solo', 'team', 'business');
    if not found then return 'Choose Solo, Team or Business'; end if;
    if seats is null or seats < 0 or seats > 1000 then return 'Choose 0 to 1000 extra technicians'; end if;
    select * into t from remote_saas.tenants where id = tenant;
    if remote_saas.technician_count(tenant) > coalesce(t.max_technicians_override, p.max_technicians + seats) then
        return format('Your company has %s technicians; %s with %s extra allows %s',
            remote_saas.technician_count(tenant), p.name, seats,
            coalesce(t.max_technicians_override, p.max_technicians + seats));
    end if;
    if remote_saas.device_count(tenant) > coalesce(t.max_devices_override, p.max_devices) then
        return format('Your company has %s devices; %s allows %s',
            remote_saas.device_count(tenant), p.name, coalesce(t.max_devices_override, p.max_devices));
    end if;
    return null;
end;
$$;

---------------------------------------------------------------------------------------------------
-- Billing service functions (role mixel_remote_billing only)

-- Everything the service needs to build a Checkout or portal session for a tenant.
create function remote_saas.billing_context(tenant uuid) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare t remote_saas.tenants; b remote_saas.billing; owner_email text;
begin
    select * into t from remote_saas.tenants where id = tenant;
    if not found then return null; end if;
    select * into b from remote_saas.billing where tenant_id = tenant;
    select u.email into owner_email from remote_saas.members m join public.users u on u.id = m.user_id
        where m.tenant_id = tenant and m.role = 'owner' order by m.created_at limit 1;
    return jsonb_build_object(
        'tenant_id', t.id, 'name', t.name, 'slug', t.slug, 'plan', t.plan,
        'status', remote_saas.effective_status(t), 'trial_ends_at', t.trial_ends_at,
        'extra_technicians', t.extra_technicians, 'owner_email', owner_email,
        'stripe_customer_id', b.stripe_customer_id, 'stripe_subscription_id', b.stripe_subscription_id,
        'subscription_status', b.subscription_status, 'billing_interval', b.billing_interval,
        'plan_price_id', b.plan_price_id, 'seat_price_id', b.seat_price_id,
        'prices', coalesce((select jsonb_agg(jsonb_build_object('price_id', p.price_id, 'kind', p.kind,
                'plan', p.plan, 'interval', p.billing_interval, 'amount_rappen', p.amount_rappen)
                order by p.kind, p.plan, p.billing_interval)
            from remote_saas.billing_prices p where p.active), '[]'::jsonb));
end;
$$;

-- Links the tenant's Stripe customer once; returns the customer that is linked afterwards.
create function remote_saas.billing_link_customer(tenant uuid, customer text) returns text
language plpgsql security definer set search_path = '' as $$
declare b remote_saas.billing;
begin
    if not exists (select 1 from remote_saas.tenants where id = tenant) then
        raise exception 'Unknown tenant' using errcode = '22023';
    end if;
    b := remote_saas.billing_row(tenant);
    if b.stripe_customer_id is null then
        update remote_saas.billing set stripe_customer_id = customer, updated_at = now() where tenant_id = tenant;
        perform remote_saas.audit(tenant, null, 'billing.customer', customer, '{}'::jsonb);
        return customer;
    end if;
    return b.stripe_customer_id;
end;
$$;

-- Applies one verified Stripe webhook event, normalized by the billing service:
--   kind: checkout | subscription | invoice_paid | invoice_failed
--   tenant_id, customer, subscription, stripe_status, tenant_status (subscription only),
--   items [{price, quantity}], current_period_end, cancel_at, canceled_at (unix seconds),
--   cancel_at_period_end.
-- Returns {outcome, tenant_id, plan, status}. Outcomes: applied, duplicate, stale, ignored_*.
create function remote_saas.billing_apply_event(stripe_event text, stripe_type text, stripe_time timestamptz,
                                                 event jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    kind text := event->>'kind';
    tenant uuid;
    t remote_saas.tenants;
    b remote_saas.billing;
    result text := 'applied';
    plan_price remote_saas.billing_prices;
    seat_price remote_saas.billing_prices;
    seats integer := 0;
    item jsonb;
    p remote_saas.billing_prices;
    new_status text;
    period_end timestamptz := to_timestamp(nullif(event->>'current_period_end', '')::double precision);
begin
    if stripe_event is null or stripe_type is null or stripe_time is null or event is null then
        raise exception 'Incomplete event' using errcode = '22023';
    end if;
    -- Idempotency: one row per event id, written in the same transaction as the state change.
    insert into remote_saas.billing_events(event_id, event_type, stripe_created, outcome)
    values (stripe_event, stripe_type, stripe_time, 'pending') on conflict (event_id) do nothing;
    if not found then
        return jsonb_build_object('outcome', 'duplicate',
            'tenant_id', (select e.tenant_id from remote_saas.billing_events e where e.event_id = stripe_event));
    end if;

    -- Which tenant: the metadata the service set, or the linked subscription / customer.
    begin tenant := nullif(event->>'tenant_id', '')::uuid; exception when invalid_text_representation then tenant := null; end;
    if tenant is null and event->>'subscription' is not null then
        select tenant_id into tenant from remote_saas.billing where stripe_subscription_id = event->>'subscription';
    end if;
    if tenant is null and event->>'customer' is not null then
        select tenant_id into tenant from remote_saas.billing where stripe_customer_id = event->>'customer';
    end if;
    select * into t from remote_saas.tenants where id = tenant for update;
    if not found then
        update remote_saas.billing_events set outcome = 'ignored_unknown_tenant' where billing_events.event_id = stripe_event;
        return jsonb_build_object('outcome', 'ignored_unknown_tenant');
    end if;
    b := remote_saas.billing_row(tenant);
    -- A customer or subscription that belongs to another tenant is never re-linked.
    if (event->>'customer' is not null and b.stripe_customer_id is not null and b.stripe_customer_id <> event->>'customer')
        or exists (select 1 from remote_saas.billing x where x.tenant_id <> tenant
                   and (x.stripe_customer_id = event->>'customer' or x.stripe_subscription_id = event->>'subscription')) then
        result := 'ignored_foreign_customer';
    elsif kind not in ('checkout', 'subscription', 'invoice_paid', 'invoice_failed') then
        result := 'ignored_kind';
    elsif kind <> 'checkout' and b.last_event_at is not null and stripe_time < b.last_event_at then
        result := 'stale';
    elsif kind <> 'checkout' and b.stripe_subscription_id is not null and event->>'subscription' is not null
        and b.stripe_subscription_id <> event->>'subscription'
        and b.subscription_status in ('trialing', 'active', 'past_due', 'unpaid', 'paused')
        and kind <> 'subscription' then
        -- An invoice of an older subscription while a newer one is live.
        result := 'ignored_other_subscription';
    end if;
    if result <> 'applied' then
        update remote_saas.billing_events set outcome = result, tenant_id = tenant
            where billing_events.event_id = stripe_event;
        return jsonb_build_object('outcome', result, 'tenant_id', tenant);
    end if;

    if kind = 'checkout' then
        update remote_saas.billing set
            stripe_customer_id = coalesce(stripe_customer_id, event->>'customer'),
            stripe_subscription_id = coalesce(event->>'subscription', stripe_subscription_id),
            updated_at = now()
        where tenant_id = tenant;
        perform remote_saas.audit(tenant, null, 'billing.checkout_completed', event->>'subscription', '{}'::jsonb);

    elsif kind = 'subscription' then
        -- A replaced (older) subscription that ends must not end the newer one.
        if b.stripe_subscription_id is not null and b.stripe_subscription_id <> event->>'subscription'
            and event->>'stripe_status' in ('canceled', 'incomplete_expired')
            and b.subscription_status in ('trialing', 'active', 'past_due', 'unpaid', 'paused') then
            update remote_saas.billing_events set outcome = 'ignored_other_subscription', tenant_id = tenant
                where billing_events.event_id = stripe_event;
            return jsonb_build_object('outcome', 'ignored_other_subscription', 'tenant_id', tenant);
        end if;
        for item in select * from jsonb_array_elements(coalesce(event->'items', '[]'::jsonb)) loop
            select * into p from remote_saas.billing_prices where price_id = item->>'price';
            if not found then
                update remote_saas.billing_events set outcome = 'ignored_unknown_price', tenant_id = tenant
                    where billing_events.event_id = stripe_event;
                return jsonb_build_object('outcome', 'ignored_unknown_price', 'tenant_id', tenant);
            end if;
            if p.kind = 'plan' then
                if plan_price.price_id is not null then
                    raise exception 'Subscription has two plans' using errcode = '22023';
                end if;
                plan_price := p;
            else
                seat_price := p;
                seats := seats + greatest(coalesce((item->>'quantity')::integer, 0), 0);
            end if;
        end loop;
        if plan_price.price_id is null then
            update remote_saas.billing_events set outcome = 'ignored_no_plan', tenant_id = tenant
                where billing_events.event_id = stripe_event;
            return jsonb_build_object('outcome', 'ignored_no_plan', 'tenant_id', tenant);
        end if;
        new_status := event->>'tenant_status';
        if new_status is not null and new_status not in ('active', 'past_due', 'read_only') then
            raise exception 'Unknown tenant status %', new_status using errcode = '22023';
        end if;
        update remote_saas.billing set
            stripe_customer_id = coalesce(stripe_customer_id, event->>'customer'),
            stripe_subscription_id = event->>'subscription',
            subscription_status = event->>'stripe_status',
            billing_interval = plan_price.billing_interval,
            plan_price_id = plan_price.price_id,
            seat_price_id = seat_price.price_id,
            seat_quantity = seats,
            current_period_end = coalesce(period_end, current_period_end),
            cancel_at = to_timestamp(nullif(event->>'cancel_at', '')::double precision),
            cancel_at_period_end = coalesce((event->>'cancel_at_period_end')::boolean, false),
            canceled_at = to_timestamp(nullif(event->>'canceled_at', '')::double precision),
            last_event_at = stripe_time,
            updated_at = now()
        where tenant_id = tenant;
        -- incomplete / incomplete_expired (no tenant_status): the subscription never started, so the
        -- tenant keeps its trial or current state.
        if new_status is not null then
            update remote_saas.tenants set plan = plan_price.plan, extra_technicians = seats where id = tenant
                and (plan <> plan_price.plan or extra_technicians <> seats);
            perform remote_saas.billing_set_status(tenant, new_status);
        end if;
        perform remote_saas.audit(tenant, null, 'billing.subscription', event->>'subscription',
            jsonb_build_object('stripe_status', event->>'stripe_status', 'plan', plan_price.plan,
                'interval', plan_price.billing_interval, 'extra_technicians', seats, 'event', stripe_type));

    elsif kind = 'invoice_paid' then
        update remote_saas.billing set
            stripe_customer_id = coalesce(stripe_customer_id, event->>'customer'),
            stripe_subscription_id = coalesce(stripe_subscription_id, event->>'subscription'),
            current_period_end = greatest(current_period_end, period_end),
            last_event_at = stripe_time, updated_at = now()
        where tenant_id = tenant;
        if coalesce(b.subscription_status, '') not in ('canceled', 'incomplete_expired')
            and remote_saas.effective_status(t) in ('past_due', 'read_only', 'trial', 'active') then
            perform remote_saas.billing_set_status(tenant, 'active');
            update remote_saas.billing set subscription_status = 'active'
                where tenant_id = tenant and subscription_status in ('past_due', 'unpaid', 'incomplete');
        end if;
        perform remote_saas.audit(tenant, null, 'billing.invoice_paid', event->>'invoice', '{}'::jsonb);

    elsif kind = 'invoice_failed' then
        update remote_saas.billing set last_event_at = stripe_time, updated_at = now() where tenant_id = tenant;
        if t.status = 'active' then
            perform remote_saas.billing_set_status(tenant, 'past_due');
        end if;
        perform remote_saas.audit(tenant, null, 'billing.invoice_failed', event->>'invoice', '{}'::jsonb);
    end if;

    update remote_saas.billing_events set outcome = 'applied', tenant_id = tenant
        where billing_events.event_id = stripe_event;
    select * into t from remote_saas.tenants where id = tenant;
    return jsonb_build_object('outcome', 'applied', 'tenant_id', tenant, 'plan', t.plan,
        'status', remote_saas.effective_status(t), 'extra_technicians', t.extra_technicians);
end;
$$;

---------------------------------------------------------------------------------------------------
-- Daily job: trials that ended become read-only; devices of tenants read-only for 30 days are
-- removed (revoked and deleted like a manual removal; keys and connection IDs stay reserved).

create function remote_saas.billing_daily(dry_run boolean default true, max_devices integer default 2000)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    t remote_saas.tenants;
    r record;
    expired jsonb := '[]'::jsonb;
    marked jsonb := '[]'::jsonb;
    deleted jsonb := '[]'::jsonb;
    d record;
    removed integer;
    budget integer := max_devices;
begin
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('remote_saas_billing_daily', 0));
    -- 1. Trials that ran out without a subscription.
    for t in select * from remote_saas.tenants where status = 'trial' and trial_ends_at <= now()
             order by trial_ends_at for update loop
        expired := expired || jsonb_build_object('tenant_id', t.id, 'trial_ends_at', t.trial_ends_at);
        if not dry_run then
            update remote_saas.tenants set status = 'read_only', status_changed_at = t.trial_ends_at where id = t.id;
            perform remote_saas.billing_mark(t.id, 'read_only', t.trial_ends_at);
            perform remote_saas.audit(t.id, null, 'billing.trial_expired', null,
                jsonb_build_object('trial_ends_at', t.trial_ends_at));
        end if;
    end loop;
    -- 2. Read-only tenants without a countdown (e.g. set read-only by hand).
    for t in select x.* from remote_saas.tenants x left join remote_saas.billing y on y.tenant_id = x.id
             where x.status = 'read_only' and y.devices_delete_after is null loop
        marked := marked || jsonb_build_object('tenant_id', t.id, 'since', t.status_changed_at);
        if not dry_run then
            perform remote_saas.billing_mark(t.id, 'read_only', t.status_changed_at);
        end if;
    end loop;
    -- 3. Devices of tenants read-only for 30 days.
    for r in select x.id, x.client_id, x.created_by, y.devices_delete_after, y.devices_deleted_at, y.read_only_since
             from remote_saas.tenants x join remote_saas.billing y on y.tenant_id = x.id
             where x.status = 'read_only' and y.devices_delete_after <= now() order by y.devices_delete_after loop
        removed := 0;
        for d in select dv.id, dv.name from public.devices dv where dv.client_id = r.client_id
                 order by dv.created_at, dv.id loop
            exit when budget <= 0;
            removed := removed + 1; budget := budget - 1;
            if not dry_run then
                perform remote_saas.remove_device(d.id, r.created_by, 'remove');
            end if;
        end loop;
        if removed > 0 or r.devices_deleted_at is null then
            deleted := deleted || jsonb_build_object('tenant_id', r.id, 'devices', removed,
                'delete_after', r.devices_delete_after);
            if not dry_run then
                update remote_saas.billing set devices_deleted_at = now(), updated_at = now() where tenant_id = r.id;
                if removed > 0 then
                    perform remote_saas.audit(r.id, null, 'billing.devices_removed', null,
                        jsonb_build_object('devices', removed, 'read_only_since', r.read_only_since));
                end if;
            end if;
        end if;
    end loop;
    return jsonb_build_object('dry_run', dry_run, 'at', now(), 'trials_expired', expired,
        'marked_read_only', marked, 'devices_removed', deleted, 'device_budget_left', budget);
end;
$$;

---------------------------------------------------------------------------------------------------
-- Portal and app API

-- Billing state of a tenant for its owners and admins (also the billing service's authorization
-- check: the service calls it through PostgREST with the caller's own GoTrue session).
create function public.remote_saas_billing(tenant uuid)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare me remote_saas.members; t remote_saas.tenants; b remote_saas.billing;
begin
    me := remote_saas.actor(tenant, '{owner,admin}', 'read');
    select * into t from remote_saas.tenants where id = tenant;
    select * into b from remote_saas.billing where tenant_id = tenant;
    return jsonb_build_object(
        'tenant_id', t.id, 'role', me.role, 'plan', t.plan, 'status', remote_saas.effective_status(t),
        'trial_ends_at', t.trial_ends_at, 'extra_technicians', t.extra_technicians,
        'billing_interval', b.billing_interval, 'subscription_status', b.subscription_status,
        'current_period_end', b.current_period_end, 'cancel_at', b.cancel_at,
        'cancel_at_period_end', coalesce(b.cancel_at_period_end, false),
        'has_customer', b.stripe_customer_id is not null,
        'has_subscription', coalesce(b.subscription_status in ('trialing', 'active', 'past_due', 'unpaid', 'paused'), false),
        'read_only_since', b.read_only_since, 'devices_delete_after', b.devices_delete_after,
        'usage', jsonb_build_object('technicians', remote_saas.technician_count(tenant),
            'devices', remote_saas.device_count(tenant)));
end;
$$;

-- Public price list (CHF, VAT included) for the pricing page and the plan picker.
create function public.mixel_remote_prices()
returns table (kind text, plan text, billing_interval text, amount_chf numeric, per_month_chf numeric)
language sql stable security definer set search_path = '' as $$
    select p.kind, p.plan, p.billing_interval, round(p.amount_rappen / 100.0, 2),
        round(p.amount_rappen / 100.0 / case p.billing_interval when 'year' then 12 else 1 end, 2)
    from remote_saas.billing_prices p where p.active
    order by p.kind, (select s.sort from remote_saas.plans s where s.code = p.plan), p.billing_interval desc;
$$;

do $$ declare f text; begin
    for f in select p.oid::regprocedure::text from pg_proc p
             where p.pronamespace = 'remote_saas'::regnamespace and p.proname like 'billing%' loop
        execute format('revoke all on function %s from public, anon, authenticated', f);
    end loop;
end $$;
grant execute on function remote_saas.billing_context(uuid), remote_saas.billing_link_customer(uuid, text),
    remote_saas.billing_apply_event(text, text, timestamptz, jsonb),
    remote_saas.billing_plan_problem(uuid, text, integer) to mixel_remote_billing;
revoke all on function public.remote_saas_billing(uuid), public.mixel_remote_prices() from public, anon, authenticated;
grant execute on function public.remote_saas_billing(uuid) to authenticated;
grant execute on function public.mixel_remote_prices() to anon, authenticated;

commit;
notify pgrst, 'reload schema';
