// Caller authorization: the caller's own GoTrue access token is sent to PostgREST, which verifies
// it, and public.remote_saas_billing(tenant) checks that the caller is an owner or admin of that
// tenant (including the tenant's sign-in method and 2-step rules). The service never sees the JWT
// secret and cannot act for a user without that user's valid session.

export class AuthError extends Error {
  /** @param {number} status @param {string} message */
  constructor(status, message) {
    super(message);
    this.name = "AuthError";
    this.status = status;
  }
}

/**
 * @typedef {{ tenant_id: string, role: "owner" | "admin", plan: string, status: string,
 *   has_subscription: boolean, has_customer: boolean, [key: string]: unknown }} BillingView
 * @typedef {(bearer: string, tenant: string) => Promise<BillingView>} Authorize
 */

// SQL error codes whose message is written for people (remote_saas.actor and the billing RPC):
// insufficient_privilege, read_only_sql_transaction, raise_exception. Any other refusal (PostgREST
// PGRST*, SQL syntax or type errors) would show schema internals, so the caller gets a generic text.
const PUBLIC_CODES = new Set(["42501", "25006", "P0001"]);

/**
 * @param {{ postgrestUrl: string, anonKey?: string, fetchImpl?: typeof fetch }} options
 * @returns {Authorize}
 */
export function createAuthorize({ postgrestUrl, anonKey, fetchImpl = fetch }) {
  return async (bearer, tenant) => {
    /** @type {Record<string, string>} */
    const headers = { authorization: `Bearer ${bearer}`, "content-type": "application/json", accept: "application/json" };
    if (anonKey) headers.apikey = anonKey;
    const response = await fetchImpl(`${postgrestUrl.replace(/\/$/, "")}/rpc/remote_saas_billing`, {
      method: "POST",
      headers,
      body: JSON.stringify({ tenant }),
      signal: AbortSignal.timeout(10000),
    });
    const body = await response.json().catch(() => null);
    if (response.ok && body && (body.role === "owner" || body.role === "admin")) return body;
    if (response.status === 401) throw new AuthError(401, "Sign in again");
    const message = typeof body?.message === "string" && PUBLIC_CODES.has(String(body?.code)) ? body.message.slice(0, 200)
      : "Not allowed for this company";
    if (response.status >= 500) throw new AuthError(503, "Sign-in check unavailable, please try again");
    throw new AuthError(403, message);
  };
}

/**
 * @param {string | undefined} header
 * @returns {string | null}
 */
export function bearerToken(header) {
  if (!header) return null;
  const match = /^Bearer\s+([A-Za-z0-9._~+/=-]{20,8192})$/.exec(header.trim());
  return match ? match[1] : null;
}
