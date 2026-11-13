// Sample Stripe events (shapes of API version 2024-06-20, plus the newer invoice/item shapes).
export const TENANT = "11111111-2222-4333-8444-555555555555";
export const SECRET = "whsec_test_" + "a".repeat(32);

/** @param {string} type @param {Record<string, unknown>} object @param {number} [created] */
export function event(type, object, created = 1763000000) {
  return { id: `evt_${type.replace(/\W/g, "")}${created}`, object: "event", api_version: "2024-06-20", created, type,
    livemode: false, data: { object } };
}

export const subscription = (overrides = {}) => ({
  id: "sub_123", object: "subscription", customer: "cus_123", status: "trialing",
  metadata: { app: "mixel-remote", tenant_id: TENANT, plan: "team", interval: "year", extra_technicians: "2" },
  current_period_end: 1794536000, cancel_at: null, canceled_at: null, cancel_at_period_end: false,
  items: { object: "list", data: [
    { id: "si_plan", price: { id: "price_teamyear" }, quantity: 1 },
    { id: "si_seat", price: { id: "price_seatyear" }, quantity: 2 },
  ] },
  ...overrides,
});

export const checkoutSession = (overrides = {}) => ({
  id: "cs_123", object: "checkout.session", mode: "subscription", customer: "cus_123", subscription: "sub_123",
  client_reference_id: TENANT, metadata: { app: "mixel-remote", tenant_id: TENANT }, ...overrides,
});

export const invoice = (overrides = {}) => ({
  id: "in_123", object: "invoice", customer: "cus_123", subscription: "sub_123",
  subscription_details: { metadata: { app: "mixel-remote", tenant_id: TENANT } },
  lines: { data: [{ period: { start: 1763000000, end: 1794536000 } }] },
  ...overrides,
});
