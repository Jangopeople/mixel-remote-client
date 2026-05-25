# Multi-Tenant Isolation — Audit Rules

Load when the PR touches the database, RLS, Edge Functions, or tenant-scoped frontend code.

This is the single highest-leverage rule file. A tenant-isolation bug is the kind that destroys client trust and triggers legal exposure under DSG/GDPR. Be aggressive in flagging.

---

## Schema-level

### Every domain table must have `tenant_id`

🔴 New table introducing tenant data without a `tenant_id uuid not null` column.

🔴 `tenant_id` declared but no FK to `public.tenants(id)`.

🔴 `tenant_id` declared without `on delete cascade` (orphan rows on tenant deletion).

🟡 `tenant_id` not indexed (every query filters by it → guaranteed slow).

🟢 `tenant_id` placed in the middle of the column list rather than near the top (convention: id, tenant_id, …, created_at, updated_at).

### Lookup / reference tables

🟢 Public lookup tables (currencies, countries, VAT rates) should NOT have `tenant_id` — they're shared.

🟡 Lookup table without RLS `select using (true)` policy explicitly stated (defense-in-depth — even if "anyone can read", make it explicit).

---

## RLS policies

### Every domain table has all four policies

🔴 Table has RLS enabled but missing one or more of: select / insert / update / delete policies. Explicit denial-by-omission policies still need to be written explicitly.

### Tenant filter in every policy

🔴 RLS policy on a tenant table that doesn't check `tenant_id = (auth.jwt() ->> 'tenant_id')::uuid` — guaranteed cross-tenant data leak.

🔴 RLS policy uses `using (true)` on a write operation (insert / update / delete) on a tenant table.

🟡 RLS policy uses `using (true)` on a select on a tenant table where data is supposed to be tenant-scoped.

### Role-aware policies

🟡 Update / delete policies don't check role (`(auth.jwt() ->> 'role')` IN allowed roles) for actions that should be role-restricted (e.g. only owners can delete).

🟢 Policy naming inconsistent — should follow `<table>_<operation>` (e.g. `orders_select`, `orders_insert`).

### Audit and system tables

🔴 `audit_log` table has any update or delete policy (must be append-only).

🔴 `webhook_deliveries`, `job_runs`, `inbound_webhook_events` have policies allowing authenticated users to write — these are system tables, accessed only via Edge Functions with `service_role`.

---

## JWT claim handling

### Claims used everywhere

🔴 Frontend code accesses tenant data from a non-JWT source (e.g. local storage, URL only, a global variable that could be tampered with).

🟡 Edge Function reads `tenant_id` from request body instead of JWT claim. Body-supplied tenant is untrusted input — RLS would normally catch this, but it's a code smell that suggests a missing trust boundary.

### Tenant switch flow

🔴 Tenant switch implemented client-side only (just sets a variable) without re-issuing the JWT via the `switch-tenant` Edge Function.

🟡 `switch-tenant` Edge Function doesn't verify the user is a member of the target tenant before re-issuing the JWT.

---

## Frontend tenant context

### URL pattern

🔴 Route doesn't include the tenant slug for tenant-scoped pages (`/orders/123` instead of `/{tenant}/orders/123`).

🟡 Route uses the tenant slug but the slug isn't validated against the JWT claim on every render → leak on direct URL edit.

🟢 Tenant switcher UI absent in the header (UI Spec usually requires it for multi-tenant products).

### Realtime channels

🔴 Supabase Realtime subscription without a `tenant_id` filter — broadcasts cross-tenant events to all subscribers.

```ts
// 🔴 BAD — leaks across tenants
supabase.channel('orders').on('postgres_changes', { event: '*', schema: 'public', table: 'orders' }, handler);

// ✅ GOOD — tenant-scoped
supabase.channel(`orders:${tenantId}`).on('postgres_changes', {
  event: '*',
  schema: 'public',
  table: 'orders',
  filter: `tenant_id=eq.${tenantId}`
}, handler);
```

🟡 Realtime channel naming doesn't include tenant ID (defense-in-depth — even with filter, scope the channel name).

---

## Edge Function tenant boundaries

### Cross-tenant operations

🔴 Edge Function that calls Postgres with `service_role` doesn't explicitly check the caller's tenant context before reading/writing on behalf of another tenant.

🟡 Edge Function admin endpoints (cross-tenant) accessible to non-admin roles — should require global admin role (a distinct claim from tenant role).

### Idempotency

🟡 Resource-creating Edge Function (POST creates) without an `Idempotency-Key` header check → duplicate creates on client retry.

---

## TanStack Query keys

### Tenant in every key

🔴 Query key for tenant-scoped data doesn't include `tenantId`:
```ts
// 🔴 BAD — caches data from tenant A, shows it to tenant B after switch
useQuery(['orders'], fetchOrders);

// ✅ GOOD
useQuery(['orders', tenantId], () => fetchOrders(tenantId));
```

🟡 Cache not invalidated on tenant switch — stale data from previous tenant visible briefly.

---

## Across-tenant features

🟡 New cross-tenant feature (admin dashboard, analytics across tenants, billing) without explicit ADR documenting the security model.

🟡 Cross-tenant feature without a separate admin Edge Function (mixing cross-tenant and per-tenant operations in the same function = audit nightmare).

---

## Test coverage for tenant isolation

🟡 No test verifying that user from tenant A cannot read tenant B's data (the canonical isolation test).

🟢 RLS policies tested only via direct SQL — should also have integration tests through the frontend Supabase client.

---

## Reverse-engineering finding patterns (when paired with App Doctor)

When auditing an existing app against this rule file, common issues:

- **Tenancy bolted on**: app started single-tenant, `tenant_id` added later, not all tables / queries / routes migrated → 🔴 inconsistent isolation
- **Manual tenant context**: tenant ID passed around in props / context instead of derived from JWT → 🟡 audit risk
- **Implicit tenant**: code assumes one tenant per user (1:1 in DB) but reality has users in multiple tenants → 🔴 reads wrong tenant's data

When you find these, the App Doctor produces a remediation. Cross-link findings.
