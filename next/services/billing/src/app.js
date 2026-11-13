// HTTP API of the billing service (behind nginx at https://remote.mixel.ch/api/billing/).
//   POST /api/billing/checkout  {tenant_id, plan, interval, extra_technicians?, return_path?} -> {id, url, expires_at, trial_end}
//   POST /api/billing/portal    {tenant_id, return_path?}                                     -> {url}
//   POST /api/billing/change    {tenant_id, plan, interval, extra_technicians?}               -> {subscription, status}
//   POST /api/billing/webhook   Stripe events (signature verified)                            -> {received, outcome}
//   GET  /api/billing/health
// checkout, portal and change need "Authorization: Bearer <GoTrue access token>" of an owner or
// admin of the tenant.
import { AuthError, bearerToken } from "./auth.js";
import { normalizeEvent } from "./events.js";
import { SignatureError, StripeError, verifyWebhook } from "./stripe.js";

const PREFIX = "/api/billing";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const PLANS = new Set(["solo", "team", "business"]);
const INTERVALS = new Set(["month", "year"]);
// Stripe Checkout needs a trial end at least 48 hours ahead; keep one hour of margin.
const MIN_TRIAL_LEFT_SECONDS = 49 * 3600;
const LIVE_SUBSCRIPTION = new Set(["trialing", "active", "past_due", "unpaid", "paused"]);

/**
 * @typedef {import("./db.js").BillingDb} BillingDb
 * @typedef {import("./db.js").BillingContext} BillingContext
 * @typedef {import("./auth.js").Authorize} Authorize
 * @typedef {{ request(method: "GET" | "POST" | "DELETE", path: string, params?: Record<string, unknown>,
 *   options?: { idempotencyKey?: string }): Promise<any> }} Stripe
 * @typedef {{ webhookSecret: string, portalConfiguration: string | null, publicBaseUrl: string,
 *   corsOrigins: string[], invoiceFooter: string, rateLimitPerMinute: number }} AppConfig
 * @typedef {{ db: BillingDb, stripe: Stripe, authorize: Authorize, config: AppConfig,
 *   now?: () => number, log?: (entry: Record<string, unknown>) => void }} AppDeps
 */

class HttpError extends Error {
  /** @param {number} status @param {string} code @param {string} message */
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

/**
 * @param {import("node:http").IncomingMessage} req
 * @param {number} limit
 * @returns {Promise<Buffer>}
 */
function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    /** @type {Buffer[]} */
    const chunks = [];
    let size = 0;
    req.on("data", (/** @type {Buffer} */ chunk) => {
      size += chunk.length;
      if (size > limit) {
        reject(new HttpError(413, "too_large", "Request body too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

/** @param {Buffer} raw @returns {Record<string, unknown>} */
function parseJson(raw) {
  try {
    const value = JSON.parse(raw.toString("utf8") || "{}");
    if (value && typeof value === "object" && !Array.isArray(value)) return value;
  } catch {
    // fall through
  }
  throw new HttpError(400, "bad_json", "Send a JSON object");
}

/** @param {unknown} path @param {string} fallback */
function returnPath(path, fallback) {
  if (path === undefined || path === null || path === "") return fallback;
  if (typeof path !== "string" || path.length > 300 || !/^\/(?![/\\])[A-Za-z0-9\-._~/?&=%#]*$/.test(path)) {
    throw new HttpError(400, "bad_return_path", "return_path must be a path on this site");
  }
  return path;
}

/** @param {Record<string, unknown>} body */
function planChoice(body) {
  const tenant = body.tenant_id;
  if (typeof tenant !== "string" || !UUID.test(tenant)) throw new HttpError(400, "bad_tenant", "tenant_id is required");
  const plan = body.plan;
  if (typeof plan !== "string" || !PLANS.has(plan)) {
    throw new HttpError(400, "bad_plan", "plan must be solo, team or business (Enterprise: contact Mixel)");
  }
  const interval = body.interval ?? "year";
  if (typeof interval !== "string" || !INTERVALS.has(interval)) throw new HttpError(400, "bad_interval", "interval must be month or year");
  const seats = body.extra_technicians ?? 0;
  if (typeof seats !== "number" || !Number.isInteger(seats) || seats < 0 || seats > 1000) {
    throw new HttpError(400, "bad_seats", "extra_technicians must be a whole number from 0 to 1000");
  }
  return { tenant, plan, interval, seats };
}

/** @param {BillingContext} ctx @param {string} plan @param {string} interval */
function pricesFor(ctx, plan, interval) {
  const planPrice = ctx.prices.find((p) => p.kind === "plan" && p.plan === plan && p.interval === interval);
  const seatPrice = ctx.prices.find((p) => p.kind === "seat" && p.interval === interval);
  if (!planPrice || !seatPrice) throw new HttpError(503, "no_price", "This plan is not on sale right now");
  return { planPrice, seatPrice };
}

/**
 * @param {AppDeps} deps
 * @returns {(req: import("node:http").IncomingMessage, res: import("node:http").ServerResponse) => Promise<void>}
 */
export function createApp({ db, stripe, authorize, config, now = () => Date.now(), log = (e) => console.log(JSON.stringify(e)) }) {
  /** @type {Map<string, number[]>} */
  const hits = new Map();

  /** @param {string} key */
  function rateLimit(key) {
    const t = now();
    const recent = (hits.get(key) ?? []).filter((x) => t - x < 60000);
    if (recent.length >= config.rateLimitPerMinute) throw new HttpError(429, "rate_limited", "Too many requests, try again in a minute");
    recent.push(t);
    hits.set(key, recent);
    if (hits.size > 10000) hits.clear();
  }

  /** @param {import("node:http").IncomingMessage} req @param {string} tenant */
  async function caller(req, tenant) {
    const token = bearerToken(req.headers.authorization);
    if (!token) throw new HttpError(401, "sign_in", "Sign in first");
    rateLimit(`${tenant}:${token.slice(-24)}`);
    try {
      return await authorize(token, tenant);
    } catch (err) {
      if (err instanceof AuthError) throw new HttpError(err.status, err.status === 401 ? "sign_in" : "forbidden", err.message);
      throw err;
    }
  }

  /** @param {string} tenant */
  async function context(tenant) {
    const ctx = await db.context(tenant);
    if (!ctx) throw new HttpError(404, "no_tenant", "Unknown company");
    return ctx;
  }

  /** @param {BillingContext} ctx */
  async function ensureCustomer(ctx) {
    if (ctx.stripe_customer_id) return ctx.stripe_customer_id;
    const customer = await stripe.request("POST", "/customers", {
      name: ctx.name,
      email: ctx.owner_email ?? undefined,
      preferred_locales: ["de"],
      metadata: { app: "mixel-remote", mixel_app: "remote", tenant_id: ctx.tenant_id, tenant_slug: ctx.slug },
      invoice_settings: { footer: config.invoiceFooter },
    }, { idempotencyKey: `mixel-remote-customer-${ctx.tenant_id}` });
    return db.linkCustomer(ctx.tenant_id, customer.id);
  }

  /** @param {string} tenant @param {string} plan @param {number} seats */
  async function checkFit(tenant, plan, seats) {
    const problem = await db.planProblem(tenant, plan, seats);
    if (problem) throw new HttpError(409, "plan_too_small", problem);
  }

  /** @param {import("node:http").IncomingMessage} req */
  async function checkout(req) {
    const body = parseJson(await readBody(req, 16 * 1024));
    const { tenant, plan, interval, seats } = planChoice(body);
    const back = returnPath(body.return_path, "/billing");
    const view = await caller(req, tenant);
    if (view.has_subscription) {
      throw new HttpError(409, "already_subscribed", "This company already has a subscription; change it instead");
    }
    await checkFit(tenant, plan, seats);
    const ctx = await context(tenant);
    if (ctx.status === "closed") throw new HttpError(409, "closed", "This account is closed");
    const { planPrice, seatPrice } = pricesFor(ctx, plan, interval);
    const customer = await ensureCustomer(ctx);
    const nowSeconds = Math.floor(now() / 1000);
    // Bought during the trial: the remaining trial days stay free and the first charge is on the
    // day the trial would have ended. Otherwise (or with less than ~2 days left) it starts now.
    const trialEnd = ctx.status === "trial" && ctx.trial_ends_at
      ? Math.floor(Date.parse(ctx.trial_ends_at) / 1000) : null;
    const keepTrial = trialEnd !== null && trialEnd - nowSeconds >= MIN_TRIAL_LEFT_SECONDS;
    const lineItems = [{ price: planPrice.price_id, quantity: 1 }];
    if (seats > 0) lineItems.push({ price: seatPrice.price_id, quantity: seats });
    const metadata = { app: "mixel-remote", tenant_id: tenant, plan, interval, extra_technicians: String(seats) };
    const base = config.publicBaseUrl.replace(/\/$/, "");
    const sep = back.includes("?") ? "&" : "?";
    const session = await stripe.request("POST", "/checkout/sessions", {
      mode: "subscription",
      customer,
      client_reference_id: tenant,
      line_items: lineItems,
      payment_method_types: ["card"],
      billing_address_collection: "required",
      tax_id_collection: { enabled: true },
      customer_update: { name: "auto", address: "auto" },
      locale: "auto",
      metadata,
      subscription_data: {
        metadata,
        description: `Mixel Remote ${plan[0].toUpperCase()}${plan.slice(1)} – ${ctx.name}`.slice(0, 500),
        trial_end: keepTrial ? trialEnd : undefined,
      },
      success_url: `${base}${back}${sep}billing=success&session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${base}${back}${sep}billing=cancelled`,
      expires_at: nowSeconds + 3600,
    });
    log({ level: "info", msg: "checkout created", tenant, plan, interval, seats, session: session.id, trial: keepTrial });
    return { status: 200, body: { id: session.id, url: session.url, expires_at: session.expires_at, trial_end: keepTrial ? trialEnd : null } };
  }

  /** @param {import("node:http").IncomingMessage} req */
  async function portal(req) {
    const body = parseJson(await readBody(req, 16 * 1024));
    const tenant = body.tenant_id;
    if (typeof tenant !== "string" || !UUID.test(tenant)) throw new HttpError(400, "bad_tenant", "tenant_id is required");
    const back = returnPath(body.return_path, "/billing");
    await caller(req, tenant);
    const ctx = await context(tenant);
    if (!ctx.stripe_customer_id) throw new HttpError(409, "no_customer", "Choose a plan first");
    const session = await stripe.request("POST", "/billing_portal/sessions", {
      customer: ctx.stripe_customer_id,
      configuration: config.portalConfiguration ?? undefined,
      return_url: `${config.publicBaseUrl.replace(/\/$/, "")}${back}`,
    });
    log({ level: "info", msg: "portal created", tenant });
    return { status: 200, body: { url: session.url } };
  }

  /** @param {import("node:http").IncomingMessage} req */
  async function change(req) {
    const body = parseJson(await readBody(req, 16 * 1024));
    const { tenant, plan, interval, seats } = planChoice(body);
    await caller(req, tenant);
    const ctx = await context(tenant);
    if (!ctx.stripe_subscription_id || !LIVE_SUBSCRIPTION.has(ctx.subscription_status ?? "")) {
      throw new HttpError(409, "no_subscription", "Choose a plan first");
    }
    await checkFit(tenant, plan, seats);
    const { planPrice, seatPrice } = pricesFor(ctx, plan, interval);
    const sub = await stripe.request("GET", `/subscriptions/${encodeURIComponent(ctx.stripe_subscription_id)}`);
    if (sub?.metadata?.app !== "mixel-remote" || sub?.metadata?.tenant_id !== tenant) {
      throw new HttpError(409, "foreign_subscription", "This subscription cannot be changed here");
    }
    const ourPlans = new Set(ctx.prices.filter((p) => p.kind === "plan").map((p) => p.price_id));
    const ourSeats = new Set(ctx.prices.filter((p) => p.kind === "seat").map((p) => p.price_id));
    /** @type {{ id: string, price: { id: string } }[]} */
    const items = sub.items?.data ?? [];
    const planItem = items.find((i) => ourPlans.has(i.price.id));
    const seatItem = items.find((i) => ourSeats.has(i.price.id));
    if (!planItem) throw new HttpError(409, "foreign_subscription", "This subscription cannot be changed here");
    /** @type {Record<string, unknown>[]} */
    const update = [{ id: planItem.id, price: planPrice.price_id, quantity: 1 }];
    if (seatItem && seats > 0) update.push({ id: seatItem.id, price: seatPrice.price_id, quantity: seats });
    else if (seatItem) update.push({ id: seatItem.id, deleted: true });
    else if (seats > 0) update.push({ price: seatPrice.price_id, quantity: seats });
    const metadata = { app: "mixel-remote", tenant_id: tenant, plan, interval, extra_technicians: String(seats) };
    // Upgrades are charged right away (prorated); a failed payment rejects the change.
    const updated = await stripe.request("POST", `/subscriptions/${encodeURIComponent(sub.id)}`, {
      items: update,
      metadata,
      proration_behavior: "always_invoice",
      payment_behavior: "error_if_incomplete",
    });
    log({ level: "info", msg: "subscription changed", tenant, plan, interval, seats, subscription: sub.id });
    return { status: 200, body: { subscription: updated.id, status: updated.status } };
  }

  /** @param {import("node:http").IncomingMessage} req */
  async function webhook(req) {
    const raw = await readBody(req, 1024 * 1024);
    let event;
    try {
      event = verifyWebhook(raw, /** @type {string | undefined} */ (req.headers["stripe-signature"]), config.webhookSecret,
        { now: Math.floor(now() / 1000) });
    } catch (err) {
      if (err instanceof SignatureError) {
        log({ level: "warn", msg: "webhook rejected", reason: err.message });
        throw new HttpError(400, "bad_signature", "Invalid signature");
      }
      throw err;
    }
    const normalized = normalizeEvent(event);
    if ("ignore" in normalized) {
      return { status: 200, body: { received: true, ignored: normalized.ignore } };
    }
    const result = await db.applyEvent(event.id, event.type, new Date(event.created * 1000), normalized);
    log({ level: "info", msg: "webhook applied", event: event.id, type: event.type, ...result });
    return { status: 200, body: { received: true, outcome: result?.outcome ?? null } };
  }

  /** @param {import("node:http").IncomingMessage} req @param {import("node:http").ServerResponse} res */
  function cors(req, res) {
    const origin = req.headers.origin;
    if (origin && config.corsOrigins.includes(origin)) {
      res.setHeader("access-control-allow-origin", origin);
      res.setHeader("vary", "origin");
      res.setHeader("access-control-allow-headers", "authorization, content-type, apikey, x-client-info");
      res.setHeader("access-control-allow-methods", "POST, GET, OPTIONS");
      res.setHeader("access-control-max-age", "600");
    }
  }

  return async (req, res) => {
    const url = new URL(req.url ?? "/", "http://billing.local");
    const path = url.pathname.startsWith(PREFIX) ? url.pathname.slice(PREFIX.length) || "/" : null;
    res.setHeader("cache-control", "no-store");
    res.setHeader("x-content-type-options", "nosniff");
    /** @param {number} status @param {unknown} body */
    const send = (status, body) => {
      res.statusCode = status;
      res.setHeader("content-type", "application/json; charset=utf-8");
      res.end(JSON.stringify(body));
    };
    try {
      if (path === null) return send(404, { error: "not_found" });
      if (path !== "/webhook") cors(req, res);
      if (req.method === "OPTIONS" && path !== "/webhook") {
        res.statusCode = 204;
        res.end();
        return;
      }
      /** @type {{ status: number, body: unknown }} */
      let result;
      if (req.method === "GET" && path === "/health") {
        result = { status: 200, body: { ok: await db.ping(), service: "mixel-remote-billing" } };
      } else if (req.method !== "POST") {
        result = { status: 405, body: { error: "method_not_allowed" } };
      } else if (path === "/checkout") result = await checkout(req);
      else if (path === "/portal") result = await portal(req);
      else if (path === "/change") result = await change(req);
      else if (path === "/webhook") result = await webhook(req);
      else result = { status: 404, body: { error: "not_found" } };
      return send(result.status, result.body);
    } catch (err) {
      if (err instanceof HttpError) return send(err.status, { error: err.code, message: err.message });
      if (err instanceof StripeError) {
        log({ level: "error", msg: "stripe", status: err.status, code: err.code, type: err.type, error: err.message, path });
        const clientError = err.status === 402 || err.type === "card_error";
        return send(clientError ? 402 : 502, {
          error: clientError ? "payment_failed" : "stripe_unavailable",
          message: clientError ? err.message : "Payment provider unavailable, please try again",
        });
      }
      log({ level: "error", msg: "unhandled", path, error: err instanceof Error ? err.message : String(err) });
      // 500 makes Stripe retry a webhook later.
      return send(500, { error: "internal", message: "Something went wrong, please try again" });
    }
  };
}
