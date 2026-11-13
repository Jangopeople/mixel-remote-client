// Minimal Stripe client (form-encoded REST over fetch) and webhook signature verification.
// No Stripe SDK: the surface used here is small and the signature scheme is documented
// (https://docs.stripe.com/webhooks#verify-manually).
import { createHmac, timingSafeEqual } from "node:crypto";

export const STRIPE_API_VERSION = "2024-06-20";
export const DEFAULT_TOLERANCE_SECONDS = 300;

/**
 * Stripe's form encoding: nested objects as a[b][c]=v, arrays as a[0]=v / a[0][k]=v.
 * Null and undefined values are left out.
 * @param {Record<string, unknown>} params
 * @returns {string}
 */
export function formEncode(params) {
  /** @type {[string, string][]} */
  const pairs = [];
  /** @param {string} key @param {unknown} value */
  const walk = (key, value) => {
    if (value === null || value === undefined) return;
    if (Array.isArray(value)) {
      value.forEach((v, i) => walk(`${key}[${i}]`, v));
    } else if (typeof value === "object") {
      for (const [k, v] of Object.entries(/** @type {Record<string, unknown>} */ (value))) walk(`${key}[${k}]`, v);
    } else {
      pairs.push([key, String(value)]);
    }
  };
  for (const [k, v] of Object.entries(params)) walk(k, v);
  return new URLSearchParams(pairs).toString();
}

export class StripeError extends Error {
  /** @param {number} status @param {string} message @param {string | undefined} code @param {string | undefined} type */
  constructor(status, message, code, type) {
    super(message);
    this.name = "StripeError";
    this.status = status;
    this.code = code;
    this.type = type;
  }
}

export class StripeClient {
  /**
   * @param {{ secretKey: string, apiVersion?: string, baseUrl?: string, fetchImpl?: typeof fetch, timeoutMs?: number }} options
   */
  constructor({ secretKey, apiVersion = STRIPE_API_VERSION, baseUrl = "https://api.stripe.com/v1",
    fetchImpl = fetch, timeoutMs = 20000 }) {
    if (!secretKey) throw new Error("Stripe secret key missing");
    this.secretKey = secretKey;
    this.apiVersion = apiVersion;
    this.baseUrl = baseUrl;
    this.fetchImpl = fetchImpl;
    this.timeoutMs = timeoutMs;
  }

  /**
   * @param {"GET" | "POST" | "DELETE"} method
   * @param {string} path e.g. "/customers"
   * @param {Record<string, unknown>} [params]
   * @param {{ idempotencyKey?: string }} [options]
   * @returns {Promise<any>}
   */
  async request(method, path, params = {}, options = {}) {
    const encoded = formEncode(params);
    const url = method === "POST" || !encoded ? `${this.baseUrl}${path}` : `${this.baseUrl}${path}?${encoded}`;
    /** @type {Record<string, string>} */
    const headers = {
      authorization: `Bearer ${this.secretKey}`,
      "stripe-version": this.apiVersion,
    };
    if (method === "POST") headers["content-type"] = "application/x-www-form-urlencoded";
    if (options.idempotencyKey) headers["idempotency-key"] = options.idempotencyKey;
    const response = await this.fetchImpl(url, {
      method,
      headers,
      body: method === "POST" ? encoded : undefined,
      signal: AbortSignal.timeout(this.timeoutMs),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const err = /** @type {{ error?: { message?: string, code?: string, type?: string } }} */ (body).error ?? {};
      throw new StripeError(response.status, err.message ?? `Stripe ${response.status}`, err.code, err.type);
    }
    return body;
  }
}

/**
 * Signs a payload like Stripe does (tests and synthetic live checks).
 * @param {string} payload raw request body
 * @param {string} secret endpoint secret (whsec_...)
 * @param {number} timestamp unix seconds
 */
export function signPayload(payload, secret, timestamp) {
  const signature = createHmac("sha256", secret).update(`${timestamp}.${payload}`, "utf8").digest("hex");
  return `t=${timestamp},v1=${signature}`;
}

export class SignatureError extends Error {
  /** @param {string} message */
  constructor(message) {
    super(message);
    this.name = "SignatureError";
  }
}

/**
 * Verifies the Stripe-Signature header for a raw body and returns the parsed event.
 * @param {string | Buffer} rawBody
 * @param {string | undefined | null} header
 * @param {string} secret
 * @param {{ toleranceSeconds?: number, now?: number }} [options] now in unix seconds
 * @returns {any}
 */
export function verifyWebhook(rawBody, header, secret, options = {}) {
  const tolerance = options.toleranceSeconds ?? DEFAULT_TOLERANCE_SECONDS;
  const now = options.now ?? Math.floor(Date.now() / 1000);
  if (!secret) throw new SignatureError("No webhook secret configured");
  if (!header || typeof header !== "string") throw new SignatureError("Missing Stripe-Signature header");
  let timestamp = NaN;
  /** @type {string[]} */
  const signatures = [];
  for (const part of header.split(",")) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    const key = part.slice(0, eq).trim();
    const value = part.slice(eq + 1).trim();
    if (key === "t" && /^\d{1,12}$/.test(value)) timestamp = Number(value);
    if (key === "v1" && /^[0-9a-f]{64}$/.test(value)) signatures.push(value);
  }
  if (!Number.isFinite(timestamp)) throw new SignatureError("Signature header has no timestamp");
  if (signatures.length === 0) throw new SignatureError("Signature header has no v1 signature");
  const payload = typeof rawBody === "string" ? rawBody : rawBody.toString("utf8");
  const expected = Buffer.from(
    createHmac("sha256", secret).update(`${timestamp}.${payload}`, "utf8").digest("hex"), "utf8");
  const matches = signatures.some((s) => {
    const candidate = Buffer.from(s, "utf8");
    return candidate.length === expected.length && timingSafeEqual(candidate, expected);
  });
  if (!matches) throw new SignatureError("Signature does not match");
  if (Math.abs(now - timestamp) > tolerance) throw new SignatureError("Signature timestamp outside the tolerance");
  let event;
  try {
    event = JSON.parse(payload);
  } catch {
    throw new SignatureError("Body is not JSON");
  }
  if (!event || typeof event !== "object" || typeof event.id !== "string" || typeof event.type !== "string"
    || typeof event.created !== "number" || !event.data || typeof event.data.object !== "object") {
    throw new SignatureError("Body is not a Stripe event");
  }
  return event;
}
