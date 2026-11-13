import assert from "node:assert/strict";
import { test } from "node:test";
import { createCloseWorker } from "../src/billing-close.js";
import { StripeError } from "../src/stripe.js";

const TENANT = "11111111-2222-4333-8444-555555555555";
const item = (overrides = {}) => ({ closure_id: "c1", claim: "k1", tenant_id: TENANT, stripe_customer_id: "cus_A",
  stripe_subscription_id: "sub_A", attempt: 1, ...overrides });
const meta = { app: "mixel-remote", tenant_id: TENANT };

/** @param {any[]} queue @param {Record<string, (params: any) => any>} routes */
function setup(queue, routes) {
  const calls = /** @type {string[]} */ ([]);
  const results = /** @type {any[]} */ ([]);
  const db = {
    claimClosures: async () => queue.splice(0, 5),
    closeResult: async (/** @type {string} */ closure, /** @type {string} */ claim, /** @type {string | null} */ failure, /** @type {boolean} */ giveUp) => {
      results.push({ closure, claim, failure, giveUp });
      return failure ? (giveUp ? "failed" : "pending") : "done";
    },
  };
  const stripe = {
    request: async (/** @type {string} */ method, /** @type {string} */ path, /** @type {any} */ params = {}) => {
      calls.push(`${method} ${path}${Object.keys(params).length ? " " + JSON.stringify(params) : ""}`);
      const route = routes[`${method} ${path}`];
      if (!route) throw new Error(`unexpected ${method} ${path}`);
      return route(params);
    },
  };
  const worker = createCloseWorker({ db, stripe, log: () => {} });
  return { worker, calls, results };
}

test("a deleted company: the subscription ends at once and the Stripe customer is deleted", async () => {
  const { worker, calls, results } = setup([item()], {
    "GET /subscriptions/sub_A": () => ({ id: "sub_A", status: "active", metadata: meta }),
    "DELETE /subscriptions/sub_A": () => ({ id: "sub_A", status: "canceled" }),
    "GET /customers/cus_A": () => ({ id: "cus_A", metadata: meta }),
    "DELETE /customers/cus_A": () => ({ id: "cus_A", deleted: true }),
  });
  await worker.runOnce();
  assert.deepEqual(calls, [
    "GET /subscriptions/sub_A",
    'DELETE /subscriptions/sub_A {"prorate":false,"invoice_now":false}',
    "GET /customers/cus_A",
    "DELETE /customers/cus_A",
  ]);
  assert.deepEqual(results, [{ closure: "c1", claim: "k1", failure: null, giveUp: false }]);
  assert.equal(worker.stats.done, 1);
});

test("objects already gone or already cancelled count as done", async () => {
  const gone = () => { throw new StripeError(404, "No such customer", "resource_missing", "invalid_request_error"); };
  const { worker, calls, results } = setup([item()], {
    "GET /subscriptions/sub_A": () => ({ id: "sub_A", status: "canceled", metadata: meta }),
    "GET /customers/cus_A": gone,
  });
  await worker.runOnce();
  assert.deepEqual(calls, ["GET /subscriptions/sub_A", "GET /customers/cus_A"]);
  assert.equal(results[0].failure, null);

  const second = setup([item({ stripe_subscription_id: null })], {
    "GET /customers/cus_A": () => ({ id: "cus_A", deleted: true }),
  });
  await second.worker.runOnce();
  assert.deepEqual(second.calls, ["GET /customers/cus_A"]);
  assert.equal(second.results[0].failure, null);
});

test("never touches objects of another app or company", async () => {
  const { worker, calls, results } = setup([item()], {
    "GET /subscriptions/sub_A": () => ({ id: "sub_A", status: "active", metadata: { app: "mixel-shop" } }),
  });
  await worker.runOnce();
  assert.deepEqual(calls, ["GET /subscriptions/sub_A"]);
  assert.deepEqual(results[0], { closure: "c1", claim: "k1", failure: "subscription is not this company's", giveUp: true });

  const other = setup([item({ stripe_subscription_id: null })], {
    "GET /customers/cus_A": () => ({ id: "cus_A", metadata: { app: "mixel-remote", tenant_id: "other" } }),
  });
  await other.worker.runOnce();
  assert.deepEqual(other.calls, ["GET /customers/cus_A"]);
  assert.equal(other.results[0].giveUp, true);
});

test("Stripe outages are retried; client errors give up", async () => {
  const down = () => { throw new StripeError(503, "down", undefined, "api_error"); };
  const { worker, results } = setup([item()], { "GET /subscriptions/sub_A": down });
  await worker.runOnce();
  assert.deepEqual(results[0], { closure: "c1", claim: "k1", failure: "stripe 503", giveUp: false });
  assert.equal(worker.stats.failed, 1);

  const bad = () => { throw new StripeError(400, "bad", "parameter_invalid", "invalid_request_error"); };
  const second = setup([item()], {
    "GET /subscriptions/sub_A": () => ({ id: "sub_A", status: "active", metadata: meta }),
    "DELETE /subscriptions/sub_A": bad,
  });
  await second.worker.runOnce();
  assert.deepEqual(second.results[0], { closure: "c1", claim: "k1", failure: "stripe 400 parameter_invalid", giveUp: true });
});

test("concurrent wake-ups are coalesced into one more run", async () => {
  const queue = [item()];
  const { worker, results } = setup(queue, {
    "GET /subscriptions/sub_A": () => ({ id: "sub_A", status: "canceled", metadata: meta }),
    "GET /customers/cus_A": () => ({ id: "cus_A", deleted: true }),
  });
  const first = worker.runOnce();
  queue.push(item({ closure_id: "c2", claim: "k2" }));
  await Promise.all([first, worker.runOnce()]);
  assert.deepEqual(results.map((r) => r.closure), ["c1", "c2"]);
});
