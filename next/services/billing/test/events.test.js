import assert from "node:assert/strict";
import { test } from "node:test";
import { normalizeEvent, tenantStatusFor } from "../src/events.js";
import { TENANT, checkoutSession, event, invoice, subscription } from "./fixtures.js";

test("Stripe subscription status -> tenant status", () => {
  assert.deepEqual(
    ["trialing", "active", "past_due", "unpaid", "canceled", "paused", "incomplete", "incomplete_expired"].map(tenantStatusFor),
    ["active", "active", "past_due", "read_only", "read_only", "read_only", null, null]);
  assert.throws(() => tenantStatusFor("something_new"));
});

test("checkout.session.completed links customer and subscription", () => {
  assert.deepEqual(normalizeEvent(event("checkout.session.completed", checkoutSession())), {
    kind: "checkout", tenant_id: TENANT, customer: "cus_123", subscription: "sub_123", session: "cs_123" });
  // client_reference_id when metadata has no tenant; expanded objects reduce to ids.
  const n = normalizeEvent(event("checkout.session.completed", checkoutSession({
    metadata: { app: "mixel-remote" }, customer: { id: "cus_9" }, subscription: { id: "sub_9" } })));
  assert.equal(/** @type {any} */ (n).tenant_id, TENANT);
  assert.equal(/** @type {any} */ (n).customer, "cus_9");
  assert.deepEqual(normalizeEvent(event("checkout.session.completed", checkoutSession({ mode: "payment" }))), { ignore: "not_subscription" });
});

test("subscription events carry plan items, period and cancellation", () => {
  const n = normalizeEvent(event("customer.subscription.created", subscription()));
  assert.deepEqual(n, {
    kind: "subscription", tenant_id: TENANT, customer: "cus_123", subscription: "sub_123",
    stripe_status: "trialing", tenant_status: "active",
    items: [{ price: "price_teamyear", quantity: 1 }, { price: "price_seatyear", quantity: 2 }],
    current_period_end: 1794536000, cancel_at: null, canceled_at: null, cancel_at_period_end: false });
  const cancelled = /** @type {any} */ (normalizeEvent(event("customer.subscription.updated",
    subscription({ status: "active", cancel_at_period_end: true, cancel_at: 1794536000 }))));
  assert.equal(cancelled.cancel_at_period_end, true);
  assert.equal(cancelled.tenant_status, "active");
  const deleted = /** @type {any} */ (normalizeEvent(event("customer.subscription.deleted",
    subscription({ status: "canceled", canceled_at: 1763000500 }))));
  assert.equal(deleted.stripe_status, "canceled");
  assert.equal(deleted.tenant_status, "read_only");
  assert.equal(deleted.canceled_at, 1763000500);
  const pastDue = /** @type {any} */ (normalizeEvent(event("customer.subscription.updated", subscription({ status: "past_due" }))));
  assert.equal(pastDue.tenant_status, "past_due");
});

test("newer API shape: period end on the items", () => {
  const n = /** @type {any} */ (normalizeEvent(event("customer.subscription.updated", subscription({
    current_period_end: undefined,
    items: { data: [{ id: "si", price: { id: "price_soloyear" }, quantity: 1, current_period_end: 1800000000 }] } }))));
  assert.equal(n.current_period_end, 1800000000);
});

test("invoice events, both API shapes", () => {
  assert.deepEqual(normalizeEvent(event("invoice.paid", invoice())), {
    kind: "invoice_paid", tenant_id: TENANT, customer: "cus_123", subscription: "sub_123", invoice: "in_123",
    current_period_end: 1794536000 });
  const newer = /** @type {any} */ (normalizeEvent(event("invoice.payment_failed", invoice({
    subscription: undefined, subscription_details: undefined,
    parent: { subscription_details: { subscription: "sub_777", metadata: { app: "mixel-remote", tenant_id: TENANT } } } }))));
  assert.equal(newer.kind, "invoice_failed");
  assert.equal(newer.subscription, "sub_777");
  assert.deepEqual(normalizeEvent(event("invoice.paid", invoice({ subscription: null,
    subscription_details: { metadata: { app: "mixel-remote" } } }))), { ignore: "no_subscription" });
});

test("other Mixel apps' objects on the shared Stripe account are ignored", () => {
  const pbx = { app: undefined, mixel_app: "pbx" };
  assert.deepEqual(normalizeEvent(event("customer.subscription.updated", subscription({ metadata: pbx }))), { ignore: "other_app" });
  assert.deepEqual(normalizeEvent(event("checkout.session.completed", checkoutSession({ metadata: {} }))), { ignore: "other_app" });
  assert.deepEqual(normalizeEvent(event("invoice.paid", invoice({ subscription_details: { metadata: pbx } }))), { ignore: "other_app" });
  assert.deepEqual(normalizeEvent(event("invoice.paid", invoice({ subscription_details: null }))), { ignore: "other_app" });
  assert.deepEqual(normalizeEvent(event("charge.refunded", {})), { ignore: "event_type" });
});
