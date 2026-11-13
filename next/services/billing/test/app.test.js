import assert from "node:assert/strict";
import { createServer } from "node:http";
import { after, before, beforeEach, test } from "node:test";
import { createApp } from "../src/app.js";
import { AuthError } from "../src/auth.js";
import { signPayload } from "../src/stripe.js";
import { SECRET, TENANT, event, invoice, subscription } from "./fixtures.js";

const NOW = Date.parse("2026-11-13T12:00:00Z");
const TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1In0.signature-part";
const PRICES = [
  ["price_soloyear", "plan", "solo", "year", 14400], ["price_solomonth", "plan", "solo", "month", 1440],
  ["price_teamyear", "plan", "team", "year", 46800], ["price_teammonth", "plan", "team", "month", 4680],
  ["price_businessyear", "plan", "business", "year", 106800], ["price_businessmonth", "plan", "business", "month", 10680],
  ["price_seatyear", "seat", null, "year", 9600], ["price_seatmonth", "seat", null, "month", 960],
].map(([price_id, kind, plan, interval, amount_rappen]) => ({ price_id, kind, plan, interval, amount_rappen }));

/** @type {any} */ let state;
/** @type {import("node:http").Server} */ let server;
/** @type {string} */ let base;

function freshState() {
  return {
    view: { tenant_id: TENANT, role: "owner", plan: "business", status: "trial", has_subscription: false, has_customer: false },
    authError: /** @type {AuthError | null} */ (null),
    ctx: {
      tenant_id: TENANT, name: "QA AG", slug: "qa-ag", plan: "business", status: "trial",
      trial_ends_at: new Date(NOW + 10 * 86400000).toISOString(), extra_technicians: 0, owner_email: "owner@example.test",
      stripe_customer_id: null, stripe_subscription_id: null, subscription_status: null,
      billing_interval: null, plan_price_id: null, seat_price_id: null, prices: PRICES,
    },
    problem: /** @type {string | null} */ (null),
    stripeCalls: /** @type {any[]} */ ([]),
    applied: /** @type {any[]} */ ([]),
    linked: /** @type {any[]} */ ([]),
    stripeSubscription: subscription({ status: "active" }),
  };
}

before(async () => {
  const app = createApp({
    db: {
      context: async () => state.ctx,
      linkCustomer: async (tenant, customer) => { state.linked.push([tenant, customer]); return customer; },
      planProblem: async () => state.problem,
      applyEvent: async (id, type, created, ev) => { state.applied.push({ id, type, created, ev }); return { outcome: "applied", tenant_id: TENANT }; },
      ping: async () => true,
      close: async () => {},
    },
    stripe: {
      request: async (method, path, params = {}, options = {}) => {
        state.stripeCalls.push({ method, path, params, options });
        if (path === "/customers") return { id: "cus_new" };
        if (path === "/checkout/sessions") return { id: "cs_new", url: "https://checkout.stripe.com/c/pay/cs_new", expires_at: 1 };
        if (path === "/billing_portal/sessions") return { url: "https://billing.stripe.com/p/session/x" };
        if (method === "GET" && path.startsWith("/subscriptions/")) return state.stripeSubscription;
        if (method === "POST" && path.startsWith("/subscriptions/")) return { id: "sub_123", status: "active" };
        throw new Error(`unexpected ${method} ${path}`);
      },
    },
    authorize: async (bearer, tenant) => {
      assert.equal(bearer, TOKEN);
      assert.equal(tenant, TENANT);
      if (state.authError) throw state.authError;
      return state.view;
    },
    config: {
      webhookSecret: SECRET, portalConfiguration: "bpc_remote", publicBaseUrl: "https://remote.mixel.ch",
      corsOrigins: ["https://remote.mixel.ch"], invoiceFooter: "Preise inkl. 8.1 % MWST", rateLimitPerMinute: 1000,
    },
    now: () => NOW,
    log: () => {},
  });
  server = createServer((req, res) => void app(req, res));
  await new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(null)));
  const address = /** @type {import("node:net").AddressInfo} */ (server.address());
  base = `http://127.0.0.1:${address.port}/api/billing`;
});
after(() => new Promise((resolve) => server.close(() => resolve(null))));
beforeEach(() => { state = freshState(); });

/** @param {string} path @param {unknown} body @param {Record<string, string>} [headers] */
async function post(path, body, headers = { authorization: `Bearer ${TOKEN}` }) {
  const response = await fetch(`${base}${path}`, {
    method: "POST", headers: { "content-type": "application/json", ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
  return { status: response.status, body: await response.json(), headers: response.headers };
}

test("checkout during the trial: new customer, plan + seats, first charge when the trial ends", async () => {
  const r = await post("/checkout", { tenant_id: TENANT, plan: "team", interval: "year", extra_technicians: 2, return_path: "/billing?x=1" });
  assert.equal(r.status, 200);
  assert.equal(r.body.url, "https://checkout.stripe.com/c/pay/cs_new");
  const [customer, session] = state.stripeCalls;
  assert.equal(customer.path, "/customers");
  assert.equal(customer.options.idempotencyKey, `mixel-remote-customer-${TENANT}`);
  assert.equal(customer.params.metadata.app, "mixel-remote");
  assert.equal(customer.params.email, "owner@example.test");
  assert.deepEqual(state.linked, [[TENANT, "cus_new"]]);
  assert.equal(session.params.customer, "cus_new");
  assert.equal(session.params.mode, "subscription");
  assert.deepEqual(session.params.line_items, [{ price: "price_teamyear", quantity: 1 }, { price: "price_seatyear", quantity: 2 }]);
  assert.equal(session.params.subscription_data.trial_end, Math.floor(Date.parse(state.ctx.trial_ends_at) / 1000));
  assert.equal(session.params.subscription_data.metadata.tenant_id, TENANT);
  assert.equal(session.params.metadata.app, "mixel-remote");
  assert.equal(session.params.success_url, "https://remote.mixel.ch/billing?x=1&billing=success&session_id={CHECKOUT_SESSION_ID}");
  assert.equal(r.body.trial_end, session.params.subscription_data.trial_end);
});

test("checkout after the trial (or with < 2 days left) starts now; existing customer reused; no seat line", async () => {
  state.ctx.status = "read_only";
  state.ctx.stripe_customer_id = "cus_old";
  let r = await post("/checkout", { tenant_id: TENANT, plan: "solo", interval: "month" });
  assert.equal(r.status, 200);
  assert.equal(state.stripeCalls.length, 1);
  assert.equal(state.stripeCalls[0].params.customer, "cus_old");
  assert.deepEqual(state.stripeCalls[0].params.line_items, [{ price: "price_solomonth", quantity: 1 }]);
  assert.equal(state.stripeCalls[0].params.subscription_data.trial_end, undefined);
  state = freshState();
  state.ctx.trial_ends_at = new Date(NOW + 47 * 3600000).toISOString();
  r = await post("/checkout", { tenant_id: TENANT, plan: "business" });
  assert.equal(r.status, 200);
  const session = state.stripeCalls.find((/** @type {any} */ c) => c.path === "/checkout/sessions");
  assert.equal(session.params.subscription_data.trial_end, undefined);
  assert.equal(session.params.line_items[0].price, "price_businessyear", "yearly is the default");
});

test("checkout refusals: no token, not owner/admin, already subscribed, plan too small, bad input", async () => {
  assert.equal((await post("/checkout", { tenant_id: TENANT, plan: "solo" }, {})).status, 401);
  state.authError = new AuthError(403, "Not allowed for this company");
  let r = await post("/checkout", { tenant_id: TENANT, plan: "solo" });
  assert.deepEqual([r.status, r.body.message], [403, "Not allowed for this company"]);
  state = freshState();
  state.view.has_subscription = true;
  assert.equal((await post("/checkout", { tenant_id: TENANT, plan: "solo" })).body.error, "already_subscribed");
  state = freshState();
  state.problem = "Your company has 2 technicians; Solo with 0 extra allows 1";
  r = await post("/checkout", { tenant_id: TENANT, plan: "solo" });
  assert.deepEqual([r.status, r.body.error], [409, "plan_too_small"]);
  for (const bad of [
    { tenant_id: "x", plan: "solo" }, { tenant_id: TENANT, plan: "enterprise" }, { tenant_id: TENANT, plan: "solo", interval: "week" },
    { tenant_id: TENANT, plan: "solo", extra_technicians: -1 }, { tenant_id: TENANT, plan: "solo", extra_technicians: 1.5 },
    { tenant_id: TENANT, plan: "solo", return_path: "https://evil.example/" }, { tenant_id: TENANT, plan: "solo", return_path: "//evil.example" },
  ]) {
    assert.equal((await post("/checkout", bad)).status, 400, JSON.stringify(bad));
  }
  assert.equal((await post("/checkout", "not json")).status, 400);
  assert.equal(state.stripeCalls.length, 0);
});

test("portal session for the tenant's customer with the Mixel Remote configuration", async () => {
  assert.equal((await post("/portal", { tenant_id: TENANT })).body.error, "no_customer");
  state.ctx.stripe_customer_id = "cus_old";
  const r = await post("/portal", { tenant_id: TENANT, return_path: "/settings/billing" });
  assert.equal(r.status, 200);
  assert.deepEqual(state.stripeCalls[0].params, { customer: "cus_old", configuration: "bpc_remote",
    return_url: "https://remote.mixel.ch/settings/billing" });
});

test("change plan: swaps the plan item, updates/removes/adds the seat item, prorates now", async () => {
  state.ctx = { ...state.ctx, status: "active", stripe_customer_id: "cus_123", stripe_subscription_id: "sub_123", subscription_status: "active" };
  let r = await post("/change", { tenant_id: TENANT, plan: "business", interval: "year", extra_technicians: 5 });
  assert.equal(r.status, 200);
  let update = state.stripeCalls.find((/** @type {any} */ c) => c.method === "POST");
  assert.deepEqual(update.params.items, [{ id: "si_plan", price: "price_businessyear", quantity: 1 },
    { id: "si_seat", price: "price_seatyear", quantity: 5 }]);
  assert.equal(update.params.proration_behavior, "always_invoice");
  assert.equal(update.params.payment_behavior, "error_if_incomplete");
  state.stripeCalls = [];
  await post("/change", { tenant_id: TENANT, plan: "team", interval: "year", extra_technicians: 0 });
  update = state.stripeCalls.find((/** @type {any} */ c) => c.method === "POST");
  assert.deepEqual(update.params.items[1], { id: "si_seat", deleted: true });
  state.stripeCalls = [];
  state.stripeSubscription = subscription({ items: { data: [{ id: "si_plan", price: { id: "price_teamyear" }, quantity: 1 }] } });
  await post("/change", { tenant_id: TENANT, plan: "team", interval: "year", extra_technicians: 1 });
  update = state.stripeCalls.find((/** @type {any} */ c) => c.method === "POST");
  assert.deepEqual(update.params.items[1], { price: "price_seatyear", quantity: 1 });
  // Someone else's subscription, or none at all.
  state.stripeSubscription = subscription({ metadata: { app: "mixel-remote", tenant_id: "00000000-0000-4000-8000-000000000000" } });
  assert.equal((await post("/change", { tenant_id: TENANT, plan: "team" })).body.error, "foreign_subscription");
  state.ctx.subscription_status = "canceled";
  assert.equal((await post("/change", { tenant_id: TENANT, plan: "team" })).body.error, "no_subscription");
});

/** @param {any} ev @param {string} [secret] @param {number} [t] */
function signed(ev, secret = SECRET, t = Math.floor(NOW / 1000)) {
  const body = JSON.stringify(ev);
  return { body, headers: { "stripe-signature": signPayload(body, secret, t) } };
}

test("webhook: signed event is normalized and applied once with its Stripe time", async () => {
  const ev = event("customer.subscription.updated", subscription({ status: "past_due" }), Math.floor(NOW / 1000) - 10);
  const { body, headers } = signed(ev);
  const r = await post("/webhook", body, headers);
  assert.deepEqual([r.status, r.body.outcome], [200, "applied"]);
  assert.equal(state.applied.length, 1);
  assert.equal(state.applied[0].id, ev.id);
  assert.equal(state.applied[0].created.getTime(), (Math.floor(NOW / 1000) - 10) * 1000);
  assert.equal(state.applied[0].ev.tenant_status, "past_due");
  assert.deepEqual(state.applied[0].ev.items, [{ price: "price_teamyear", quantity: 1 }, { price: "price_seatyear", quantity: 2 }]);
});

test("webhook: bad signature 400 without touching the database; foreign events acknowledged", async () => {
  const ev = event("invoice.paid", invoice());
  let { body, headers } = signed(ev, "whsec_wrong");
  assert.equal((await post("/webhook", body, headers)).status, 400);
  ({ body, headers } = signed(ev, SECRET, Math.floor(NOW / 1000) - 3600));
  assert.equal((await post("/webhook", body, headers)).status, 400);
  assert.equal((await post("/webhook", JSON.stringify(ev), {})).status, 400);
  ({ body, headers } = signed(event("invoice.paid", invoice({ subscription_details: { metadata: { mixel_app: "pbx" } } }))));
  const r = await post("/webhook", body, headers);
  assert.deepEqual([r.status, r.body.ignored], [200, "other_app"]);
  assert.equal(state.applied.length, 0);
});

test("health, CORS for the portal origin only, unknown routes", async () => {
  const health = await fetch(`${base}/health`);
  assert.deepEqual(await health.json(), { ok: true, service: "mixel-remote-billing" });
  const pre = await fetch(`${base}/checkout`, { method: "OPTIONS", headers: { origin: "https://remote.mixel.ch" } });
  assert.equal(pre.status, 204);
  assert.equal(pre.headers.get("access-control-allow-origin"), "https://remote.mixel.ch");
  const other = await fetch(`${base}/checkout`, { method: "OPTIONS", headers: { origin: "https://evil.example" } });
  assert.equal(other.headers.get("access-control-allow-origin"), null);
  assert.equal((await fetch(`${base}/nope`, { method: "POST" })).status, 404);
  assert.equal((await fetch(`${base.replace("/api/billing", "")}/other`)).status, 404);
  assert.equal((await fetch(`${base}/checkout`)).status, 405);
});
