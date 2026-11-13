// Stripe clean-up of deleted companies (SQL 027). When an owner deletes a company, the database
// deletes the company's data at once and queues its Stripe ids in remote_saas.closures; this worker
// claims each entry, ends the subscription immediately (no proration, no final invoice) and deletes
// the Stripe customer, then records the result. Invoices and payments stay in Stripe and in Mixel's
// accounting (statutory retention); the customer object with its payment methods is gone.
//
// Only Mixel Remote objects are touched: a subscription or customer whose metadata does not name
// app=mixel-remote and this tenant is left alone and the entry fails for a person to look at.
// Objects that are already gone (404 resource_missing) count as done.
//
// Wake-ups: LISTEN remote_saas_billing_close (NOTIFY from remote_saas_delete_company) plus a sweep.
import { StripeError } from "./stripe.js";

/**
 * @typedef {{ closure_id: string, claim: string, tenant_id: string, stripe_customer_id: string | null,
 *   stripe_subscription_id: string | null, attempt: number }} ClaimedClosure
 * @typedef {{
 *   claimClosures(max: number): Promise<ClaimedClosure[]>,
 *   closeResult(closure: string, claim: string, failure: string | null, giveUp: boolean): Promise<string>,
 * }} CloseDb
 * @typedef {{ request(method: "GET" | "POST" | "DELETE", path: string, params?: Record<string, unknown>,
 *   options?: { idempotencyKey?: string }): Promise<any> }} Stripe
 */

const ENDED = new Set(["canceled", "incomplete_expired"]);

class Refused extends Error {}

/** @param {unknown} error */
const missing = (error) => error instanceof StripeError && (error.status === 404 || error.code === "resource_missing");

/** @param {any} object @param {string} tenant */
function ours(object, tenant) {
  return object?.metadata?.app === "mixel-remote" && object?.metadata?.tenant_id === tenant;
}

/**
 * @param {{ db: CloseDb, stripe: Stripe, log?: (entry: Record<string, unknown>) => void }} deps
 */
export function createCloseWorker({ db, stripe, log = (e) => console.log(JSON.stringify(e)) }) {
  let running = false;
  let again = false;
  const stats = { done: 0, failed: 0, lastRun: /** @type {string | null} */ (null), lastError: /** @type {string | null} */ (null) };

  /** @param {ClaimedClosure} item */
  async function cleanUp(item) {
    if (item.stripe_subscription_id) {
      const id = encodeURIComponent(item.stripe_subscription_id);
      try {
        const sub = await stripe.request("GET", `/subscriptions/${id}`);
        if (!ours(sub, item.tenant_id)) throw new Refused("subscription is not this company's");
        if (!ENDED.has(sub.status)) {
          await stripe.request("DELETE", `/subscriptions/${id}`, { prorate: false, invoice_now: false });
        }
      } catch (error) {
        if (!missing(error)) throw error;
      }
    }
    if (item.stripe_customer_id) {
      const id = encodeURIComponent(item.stripe_customer_id);
      try {
        const customer = await stripe.request("GET", `/customers/${id}`);
        if (!customer?.deleted) {
          if (!ours(customer, item.tenant_id)) throw new Refused("customer is not this company's");
          // Deleting the customer also cancels any other subscription left on it.
          await stripe.request("DELETE", `/customers/${id}`);
        }
      } catch (error) {
        if (!missing(error)) throw error;
      }
    }
  }

  /** @param {ClaimedClosure} item */
  async function closeOne(item) {
    /** @type {string | null} */
    let failure = null;
    let giveUp = false;
    try {
      await cleanUp(item);
    } catch (error) {
      if (error instanceof Refused) {
        failure = error.message;
        giveUp = true;
      } else if (error instanceof StripeError) {
        failure = `stripe ${error.status}${error.code ? ` ${error.code}` : ""}`;
        // 4xx other than rate limiting will not get better by retrying.
        giveUp = error.status >= 400 && error.status < 500 && error.status !== 429;
      } else {
        failure = error instanceof Error ? error.message.slice(0, 200) : "error";
      }
    }
    const outcome = await db.closeResult(item.closure_id, item.claim, failure, giveUp);
    if (outcome === "done") stats.done += 1;
    else stats.failed += 1;
    log({ level: failure ? "warn" : "info", msg: "company closed in stripe", closure: item.closure_id, tenant: item.tenant_id,
      outcome, attempt: item.attempt, ...(failure ? { error: failure } : {}) });
  }

  async function runOnce() {
    if (running) {
      again = true;
      return;
    }
    running = true;
    try {
      do {
        again = false;
        for (let round = 0; round < 20; round += 1) {
          const batch = await db.claimClosures(5);
          for (const item of batch) await closeOne(item);
          if (batch.length < 5) break;
        }
      } while (again);
      stats.lastError = null;
    } catch (error) {
      stats.lastError = error instanceof Error ? error.message.slice(0, 200) : "error";
      log({ level: "error", msg: "stripe clean-up run", error: stats.lastError });
    } finally {
      stats.lastRun = new Date().toISOString();
      running = false;
    }
  }

  return { runOnce, stats };
}
