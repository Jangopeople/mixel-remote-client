import assert from "node:assert/strict";
import { test } from "node:test";
import { SignatureError, StripeClient, StripeError, formEncode, signPayload, verifyWebhook } from "../src/stripe.js";
import { SECRET, event, subscription } from "./fixtures.js";

test("formEncode nests objects and arrays the way Stripe expects", () => {
  const encoded = formEncode({
    mode: "subscription", line_items: [{ price: "price_a", quantity: 1 }, { price: "price_b", quantity: 3 }],
    subscription_data: { metadata: { tenant_id: "t" }, trial_end: undefined }, skip: null, flag: true,
  });
  assert.equal(decodeURIComponent(encoded), "mode=subscription&line_items[0][price]=price_a&line_items[0][quantity]=1"
    + "&line_items[1][price]=price_b&line_items[1][quantity]=3&subscription_data[metadata][tenant_id]=t&flag=true");
});

test("a correctly signed event verifies and parses", () => {
  const body = JSON.stringify(event("customer.subscription.updated", subscription()));
  const header = signPayload(body, SECRET, 1763000000);
  const parsed = verifyWebhook(body, header, SECRET, { now: 1763000100 });
  assert.equal(parsed.type, "customer.subscription.updated");
  assert.equal(parsed.data.object.id, "sub_123");
});

test("verification accepts any matching v1 signature (secret rotation)", () => {
  const body = JSON.stringify(event("invoice.paid", {}));
  const good = signPayload(body, SECRET, 1763000000).split("v1=")[1];
  const header = `t=1763000000,v1=${"0".repeat(64)},v1=${good},v0=ignored`;
  assert.equal(verifyWebhook(body, header, SECRET, { now: 1763000000 }).type, "invoice.paid");
});

test("tampered body, wrong secret, old timestamp and bad headers are rejected", () => {
  const body = JSON.stringify(event("customer.subscription.updated", subscription()));
  const header = signPayload(body, SECRET, 1763000000);
  const cases = [
    [body.replace("trialing", "active"), header, SECRET, 1763000000, /does not match/],
    [body, header, "whsec_other", 1763000000, /does not match/],
    [body, header, SECRET, 1763000000 + 301, /tolerance/],
    [body, header, SECRET, 1763000000 - 301, /tolerance/],
    [body, undefined, SECRET, 1763000000, /Missing/],
    [body, "v1=" + "a".repeat(64), SECRET, 1763000000, /timestamp/],
    [body, "t=1763000000", SECRET, 1763000000, /no v1/],
    [body, header, "", 1763000000, /No webhook secret/],
  ];
  for (const [b, h, s, now, message] of cases) {
    assert.throws(() => verifyWebhook(/** @type {string} */ (b), /** @type {any} */ (h), /** @type {string} */ (s),
      { now: /** @type {number} */ (now) }), (err) => err instanceof SignatureError && /** @type {RegExp} */ (message).test(err.message));
  }
});

test("a signed body that is not a Stripe event is rejected", () => {
  for (const body of ["not json", JSON.stringify({ id: "evt_1" }), JSON.stringify([1])]) {
    assert.throws(() => verifyWebhook(body, signPayload(body, SECRET, 1), SECRET, { now: 1 }), SignatureError);
  }
});

test("Buffer bodies verify like strings", () => {
  const body = JSON.stringify(event("invoice.paid", {}));
  assert.equal(verifyWebhook(Buffer.from(body), signPayload(body, SECRET, 5), SECRET, { now: 5 }).id, "evt_invoicepaid1763000000");
});

test("StripeClient sends version, idempotency key and form body; maps errors", async () => {
  /** @type {any[]} */
  const calls = [];
  const fetchImpl = /** @type {typeof fetch} */ (async (url, init) => {
    calls.push({ url, init });
    if (String(url).includes("/fail")) {
      return new Response(JSON.stringify({ error: { message: "No such customer", code: "resource_missing", type: "invalid_request_error" } }), { status: 404 });
    }
    return new Response(JSON.stringify({ id: "cus_1" }), { status: 200 });
  });
  const client = new StripeClient({ secretKey: "sk_test_x", fetchImpl });
  assert.equal((await client.request("POST", "/customers", { name: "A B" }, { idempotencyKey: "k1" })).id, "cus_1");
  assert.equal(calls[0].init.headers["stripe-version"], "2024-06-20");
  assert.equal(calls[0].init.headers["idempotency-key"], "k1");
  assert.equal(calls[0].init.body, "name=A+B");
  await client.request("GET", "/prices", { lookup_keys: ["a"] });
  assert.match(String(calls[1].url), /\/prices\?lookup_keys%5B0%5D=a$/);
  await assert.rejects(client.request("GET", "/fail"), (err) => err instanceof StripeError && err.status === 404 && err.code === "resource_missing");
});

test("catalogue amounts: yearly = 12 x list price, monthly = +20 %, CHF rappen", async () => {
  const { CATALOGUE, amountFor } = await import("../src/catalogue.js");
  const amounts = CATALOGUE.flatMap((c) => [amountFor(c.perMonth, "year"), amountFor(c.perMonth, "month")]);
  assert.deepEqual(amounts, [14400, 1440, 46800, 4680, 106800, 10680, 9600, 960]);
});
