// Maps verified Stripe events to the normalized shape remote_saas.billing_apply_event() takes.
// The Stripe account is shared with other Mixel apps (Shop, PBX, Marketing ...), so only objects
// carrying metadata app=mixel-remote are handled; everything else is acknowledged and ignored.

export const APP = "mixel-remote";

export const HANDLED_EVENTS = Object.freeze([
  "checkout.session.completed",
  "customer.subscription.created",
  "customer.subscription.updated",
  "customer.subscription.deleted",
  "invoice.paid",
  "invoice.payment_failed",
]);

/**
 * Tenant status for a Stripe subscription status. Null: the subscription never started
 * (incomplete), so the tenant keeps its trial or current state.
 * @param {string} stripeStatus
 * @returns {"active" | "past_due" | "read_only" | null}
 */
export function tenantStatusFor(stripeStatus) {
  switch (stripeStatus) {
    case "trialing":
    case "active":
      return "active";
    case "past_due":
      return "past_due";
    case "unpaid":
    case "canceled":
    case "paused":
      return "read_only";
    case "incomplete":
    case "incomplete_expired":
      return null;
    default:
      throw new Error(`Unknown Stripe subscription status ${stripeStatus}`);
  }
}

/** @param {unknown} value @returns {string | null} */
function idOf(value) {
  if (typeof value === "string") return value;
  if (value && typeof value === "object" && typeof (/** @type {{ id?: unknown }} */ (value)).id === "string") {
    return /** @type {{ id: string }} */ (value).id;
  }
  return null;
}

/** @param {unknown} value @returns {number | null} */
function seconds(value) {
  return typeof value === "number" && Number.isFinite(value) ? Math.trunc(value) : null;
}

/** @param {any} metadata */
function isOurs(metadata) {
  return Boolean(metadata && typeof metadata === "object" && metadata.app === APP);
}

/**
 * @typedef {{ ignore: string }} Ignored
 * @typedef {{ kind: string, tenant_id: string | null, customer: string | null, subscription: string | null,
 *   [key: string]: unknown }} Normalized
 */

/**
 * @param {any} event a verified Stripe event
 * @returns {Normalized | Ignored}
 */
export function normalizeEvent(event) {
  const object = event?.data?.object ?? {};
  switch (event?.type) {
    case "checkout.session.completed": {
      if (!isOurs(object.metadata)) return { ignore: "other_app" };
      if (object.mode !== "subscription") return { ignore: "not_subscription" };
      return {
        kind: "checkout",
        tenant_id: object.metadata.tenant_id ?? object.client_reference_id ?? null,
        customer: idOf(object.customer),
        subscription: idOf(object.subscription),
        session: object.id ?? null,
      };
    }
    case "customer.subscription.created":
    case "customer.subscription.updated":
    case "customer.subscription.deleted": {
      if (!isOurs(object.metadata)) return { ignore: "other_app" };
      const items = Array.isArray(object.items?.data) ? object.items.data : [];
      // API 2024-06-20 has current_period_end on the subscription; newer versions on each item.
      const itemEnds = items.map((/** @type {any} */ i) => seconds(i.current_period_end)).filter((/** @type {number | null} */ s) => s !== null);
      const stripeStatus = event.type === "customer.subscription.deleted" ? "canceled" : String(object.status);
      return {
        kind: "subscription",
        tenant_id: object.metadata.tenant_id ?? null,
        customer: idOf(object.customer),
        subscription: idOf(object.id),
        stripe_status: stripeStatus,
        tenant_status: tenantStatusFor(stripeStatus),
        items: items.map((/** @type {any} */ i) => ({ price: idOf(i.price), quantity: seconds(i.quantity) ?? 0 })),
        current_period_end: seconds(object.current_period_end) ?? (itemEnds.length ? Math.max(...itemEnds) : null),
        cancel_at: seconds(object.cancel_at),
        canceled_at: seconds(object.canceled_at),
        cancel_at_period_end: object.cancel_at_period_end === true,
      };
    }
    case "invoice.paid":
    case "invoice.payment_failed": {
      // 2024-06-20: invoice.subscription + invoice.subscription_details; newer: invoice.parent.subscription_details.
      const details = object.subscription_details ?? object.parent?.subscription_details ?? null;
      if (!isOurs(details?.metadata)) return { ignore: "other_app" };
      const subscription = idOf(object.subscription) ?? idOf(details?.subscription);
      if (!subscription) return { ignore: "no_subscription" };
      const lines = Array.isArray(object.lines?.data) ? object.lines.data : [];
      const ends = lines.map((/** @type {any} */ l) => seconds(l.period?.end)).filter((/** @type {number | null} */ s) => s !== null);
      return {
        kind: event.type === "invoice.paid" ? "invoice_paid" : "invoice_failed",
        tenant_id: details.metadata.tenant_id ?? null,
        customer: idOf(object.customer),
        subscription,
        invoice: object.id ?? null,
        current_period_end: ends.length ? Math.max(...ends) : null,
      };
    }
    default:
      return { ignore: "event_type" };
  }
}
