// Security regressions of the billing service (SaaS review 2026-11).
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { after, before, test } from "node:test";
import { createApp } from "../src/app.js";
import { createAuthorize } from "../src/auth.js";
import { renderInvite } from "../src/invite-templates.js";
import { SECRET, TENANT } from "./fixtures.js";

const NOW = Date.parse("2026-11-13T12:00:00Z");
const token = (/** @type {number} */ n) => `eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1In0.sig-${String(n).padStart(24, "0")}`;

/** @type {import("node:http").Server} */ let server;
/** @type {string} */ let base;
let portalCalls = 0;

before(async () => {
  const app = createApp({
    db: {
      context: async () => ({ tenant_id: TENANT, name: "QA AG", slug: "qa-ag", plan: "business", status: "active",
        trial_ends_at: null, extra_technicians: 0, owner_email: null, stripe_customer_id: "cus_1",
        stripe_subscription_id: null, subscription_status: null, billing_interval: null, plan_price_id: null,
        seat_price_id: null, prices: [] }),
      linkCustomer: async (_t, c) => c,
      planProblem: async () => null,
      applyEvent: async () => ({ outcome: "applied" }),
      ping: async () => true,
      close: async () => {},
    },
    stripe: { request: async () => { portalCalls += 1; return { url: "https://billing.stripe.com/p/session/x" }; } },
    // Any token is a valid session of an owner (a user can mint many tokens by refreshing).
    authorize: async (_bearer, tenant) => ({ tenant_id: tenant, role: "owner", plan: "business", status: "active",
      has_subscription: false, has_customer: true }),
    config: {
      webhookSecret: SECRET, portalConfiguration: null, publicBaseUrl: "https://remote.mixel.ch",
      corsOrigins: ["https://remote.mixel.ch"], invoiceFooter: "", rateLimitPerMinute: 3,
    },
    now: () => NOW,
    log: () => {},
  });
  server = createServer((req, res) => void app(req, res));
  await new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(null)));
  base = `http://127.0.0.1:${/** @type {import("node:net").AddressInfo} */ (server.address()).port}/api/billing`;
});
after(() => new Promise((resolve) => server.close(() => resolve(null))));

/** @param {string} path @param {Record<string, unknown>} body @param {string} bearer */
const post = (path, body, bearer) => fetch(`${base}${path}`, {
  method: "POST", headers: { authorization: `Bearer ${bearer}`, "content-type": "application/json" }, body: JSON.stringify(body),
});

test("the per-tenant rate limit cannot be bypassed by rotating access tokens", async () => {
  const statuses = [];
  for (let i = 0; i < 5; i += 1) statuses.push((await post("/portal", { tenant_id: TENANT }, token(i))).status);
  assert.deepEqual(statuses, [200, 200, 200, 429, 429]);
  assert.equal(portalCalls, 3, "no Stripe session past the limit");
});

test("return_path may not carry a fragment (it would swallow the billing=success query)", async () => {
  const response = await post("/portal", { tenant_id: "22222222-2222-4333-8444-555555555555", return_path: "/app/billing#x" }, token(99));
  assert.equal(response.status, 400);
  assert.equal((await response.json()).error, "bad_return_path");
});

test("authorize passes on only the company's own refusal messages, never PostgREST internals", async () => {
  /** @param {number} status @param {unknown} body */
  const answer = (status, body) => createAuthorize({ postgrestUrl: "http://pg", fetchImpl: async () => new Response(JSON.stringify(body), { status }) });
  await assert.rejects(answer(404, { code: "PGRST202", message: "Could not find the function public.remote_saas_billing(tenant) in the schema cache" })("t".repeat(30), TENANT),
    (/** @type {any} */ err) => err.status === 403 && err.message === "Not allowed for this company");
  await assert.rejects(answer(400, { code: "22P02", message: "invalid input syntax for type uuid: \"x\"" })("t".repeat(30), TENANT),
    (/** @type {any} */ err) => err.status === 403 && err.message === "Not allowed for this company");
  await assert.rejects(answer(403, { code: "42501", message: "Two-step sign-in is required by this company" })("t".repeat(30), TENANT),
    (/** @type {any} */ err) => err.status === 403 && err.message === "Two-step sign-in is required by this company");
  await assert.rejects(answer(400, { code: "25006", message: "This Mixel Remote account is read-only" })("t".repeat(30), TENANT),
    (/** @type {any} */ err) => err.status === 403 && err.message === "This Mixel Remote account is read-only");
});

test("tenant-controlled names cannot plant links in the invitation e-mail (phishing from remote@mixel.ch)", () => {
  const link = `https://remote.mixel.ch/invite#mri_${"A".repeat(43)}`;
  const mail = renderInvite({
    locale: "en", company: "Mixel Billing - verify at https://evil.example/login", inviterName: "Support www.phish.example or help@phish.example",
    inviterEmail: "anna@muster.ch", email: "tech@muster.ch", role: "admin", link, expiresAt: new Date("2026-11-20T10:00:00Z"),
  });
  for (const [part, value] of Object.entries(mail)) {
    for (const bad of ["evil.example", "phish.example", "https://evil", "www.", "help@"]) {
      assert.ok(!value.includes(bad), `${part} contains ${bad}`);
    }
    assert.ok(value.includes("Mixel Billing"), `${part} still names the company`);
  }
  // The only links are the invitation link (button, fallback href and its visible text).
  assert.equal(mail.html.split("https://").length - 1, 3);
  assert.equal(mail.text.split("https://").length - 1, 1);
  // Ordinary company names are untouched.
  const plain = renderInvite({ locale: "de", company: "Müller & Co. AG", inviterName: "Dr. A. Muster", inviterEmail: "", email: "a@b.ch",
    role: "member", link, expiresAt: new Date("2026-11-20T10:00:00Z") });
  assert.ok(plain.subject.includes("Müller & Co. AG") && plain.subject.includes("Dr. A. Muster"));
});
