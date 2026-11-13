// Database access as role mixel_remote_billing_service (member of mixel_remote_billing), which may
// only execute the remote_saas billing functions of migration 023.
import pg from "pg";

/**
 * @typedef {{ price_id: string, kind: "plan" | "seat", plan: string | null, interval: "month" | "year", amount_rappen: number }} Price
 * @typedef {{ tenant_id: string, name: string, slug: string, plan: string, status: string,
 *   trial_ends_at: string | null, extra_technicians: number, owner_email: string | null,
 *   stripe_customer_id: string | null, stripe_subscription_id: string | null, subscription_status: string | null,
 *   billing_interval: string | null, plan_price_id: string | null, seat_price_id: string | null, prices: Price[] }} BillingContext
 * @typedef {{
 *   context(tenant: string): Promise<BillingContext | null>,
 *   linkCustomer(tenant: string, customer: string): Promise<string>,
 *   planProblem(tenant: string, plan: string, seats: number): Promise<string | null>,
 *   applyEvent(id: string, type: string, created: Date, event: Record<string, unknown>): Promise<Record<string, unknown>>,
 *   ping(): Promise<boolean>,
 *   close(): Promise<void>,
 * }} BillingDb
 */

/**
 * @param {string} connectionString
 * @returns {BillingDb}
 */
export function createDb(connectionString) {
  const pool = new pg.Pool({
    connectionString,
    max: 4,
    idleTimeoutMillis: 30000,
    connectionTimeoutMillis: 5000,
    statement_timeout: 15000,
    application_name: "mixel-remote-billing",
  });
  pool.on("error", (err) => console.error(JSON.stringify({ level: "error", msg: "db pool", error: err.message })));
  /** @param {string} sql @param {unknown[]} values */
  const one = async (sql, values) => (await pool.query(sql, values)).rows[0]?.result ?? null;
  return {
    context: (tenant) => one("select remote_saas.billing_context($1::uuid) as result", [tenant]),
    linkCustomer: (tenant, customer) =>
      one("select remote_saas.billing_link_customer($1::uuid, $2::text) as result", [tenant, customer]),
    planProblem: (tenant, plan, seats) =>
      one("select remote_saas.billing_plan_problem($1::uuid, $2::text, $3::integer) as result", [tenant, plan, seats]),
    applyEvent: (id, type, created, event) =>
      one("select remote_saas.billing_apply_event($1::text, $2::text, $3::timestamptz, $4::jsonb) as result",
        [id, type, created.toISOString(), JSON.stringify(event)]),
    ping: async () => (await pool.query("select 1 as ok")).rows[0]?.ok === 1,
    close: () => pool.end(),
  };
}
