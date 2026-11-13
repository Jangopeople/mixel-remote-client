// Database access as role mixel_remote_billing_service (member of mixel_remote_billing and
// mixel_remote_mail), which may only execute the remote_saas billing functions of migration 023 and
// the invitation mail functions of migration 026.
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
 * @returns {BillingDb & import("./invite-mail.js").MailDb}
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
    claimInvites: async (max) =>
      (await pool.query("select * from remote_saas.mail_claim_invites($1::integer)", [max])).rows,
    inviteSecret: (invite, claim, hash) =>
      one("select remote_saas.mail_invite_secret($1::uuid, $2::uuid, $3::bytea) as result", [invite, claim, hash]),
    inviteResult: (invite, claim, messageId, failure, giveUp) =>
      one("select remote_saas.mail_invite_result($1::uuid, $2::uuid, $3::text, $4::text, $5::boolean) as result",
        [invite, claim, messageId, failure, giveUp]),
    ping: async () => (await pool.query("select 1 as ok")).rows[0]?.ok === 1,
    close: () => pool.end(),
  };
}

/**
 * A dedicated connection that LISTENs for queued invitation e-mails (NOTIFY remote_saas_invite_mail).
 * @param {string} connectionString
 * @param {() => void} onNotify
 * @param {(error: Error) => void} onError called once when the connection is lost
 * @returns {Promise<() => Promise<void>>} close
 */
export async function listenForInvites(connectionString, onNotify, onError) {
  const client = new pg.Client({ connectionString, application_name: "mixel-remote-mail", connectionTimeoutMillis: 5000,
    keepAlive: true });
  let failed = false;
  const fail = (/** @type {Error} */ error) => {
    if (failed) return;
    failed = true;
    onError(error);
  };
  client.on("notification", (message) => {
    if (message.channel === "remote_saas_invite_mail") onNotify();
  });
  client.on("error", fail);
  client.on("end", () => fail(new Error("connection ended")));
  await client.connect();
  await client.query("listen remote_saas_invite_mail");
  return async () => {
    failed = true;
    await client.end();
  };
}
