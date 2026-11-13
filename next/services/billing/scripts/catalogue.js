// Creates (or finds) the Mixel Remote Stripe catalogue, customer-portal configuration and webhook
// endpoint. Idempotent: products have fixed ids, prices fixed lookup keys, the portal configuration
// and the webhook endpoint carry metadata app=mixel-remote. Never touches other apps' objects.
//   STRIPE_SECRET_KEY=... node scripts/catalogue.js [--webhook-url URL --secret-out FILE] [--dry-run]
// Prints the ids as JSON (no secrets). The webhook signing secret is only returned by Stripe when the
// endpoint is created; it is written to --secret-out (mode 0600) and never printed.
import { writeFileSync } from "node:fs";
import { StripeClient, StripeError } from "../src/stripe.js";
import { HANDLED_EVENTS } from "../src/events.js";
import { CATALOGUE, META, amountFor } from "../src/catalogue.js";

/** @param {string[]} argv */
function args(argv) {
  /** @type {Record<string, string | boolean>} */
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    const next = argv[i + 1];
    if (next && !next.startsWith("--")) { out[a.slice(2)] = next; i++; } else out[a.slice(2)] = true;
  }
  return out;
}

async function main() {
  const opts = args(process.argv.slice(2));
  const dry = opts["dry-run"] === true;
  const stripe = new StripeClient({ secretKey: process.env.STRIPE_SECRET_KEY ?? "" });
  /** @type {any} */
  const result = { live: (process.env.STRIPE_SECRET_KEY ?? "").startsWith("sk_live_"), products: [], prices: [] };

  for (const entry of CATALOGUE) {
    let product;
    try {
      product = await stripe.request("GET", `/products/${entry.product}`);
    } catch (err) {
      if (!(err instanceof StripeError && err.status === 404)) throw err;
      product = dry ? { id: entry.product, dry: true } : await stripe.request("POST", "/products", {
        id: entry.product, name: entry.name, description: entry.description,
        metadata: { ...META, kind: entry.kind, mixel_plan: entry.plan ?? "seat" },
      }, { idempotencyKey: `mixel-remote-product-${entry.product}-v1` });
    }
    if (product.metadata && product.metadata.app !== undefined && product.metadata.app !== META.app) {
      throw new Error(`Product ${entry.product} exists but is not a Mixel Remote product`);
    }
    result.products.push({ id: product.id, name: entry.name });
    for (const interval of /** @type {const} */ (["year", "month"])) {
      const lookupKey = `mixel_remote_${entry.plan ?? "technician"}_chf_${interval}`;
      const amount = amountFor(entry.perMonth, interval);
      const found = await stripe.request("GET", "/prices", { lookup_keys: [lookupKey], active: true, limit: 1 });
      let price = found.data?.[0];
      if (price && (price.unit_amount !== amount || price.currency !== "chf" || price.recurring?.interval !== interval)) {
        throw new Error(`Price ${lookupKey} exists with different terms (${price.id}); not changing it`);
      }
      if (!price) {
        price = dry ? { id: `(new ${lookupKey})` } : await stripe.request("POST", "/prices", {
          product: product.id, currency: "chf", unit_amount: amount, tax_behavior: "inclusive",
          recurring: { interval, usage_type: "licensed" }, lookup_key: lookupKey,
          nickname: `${entry.name} ${interval === "year" ? "jährlich" : "monatlich"}`,
          metadata: { ...META, kind: entry.kind, mixel_plan: entry.plan ?? "seat", interval },
        }, { idempotencyKey: `mixel-remote-price-${lookupKey}-v1` });
      }
      result.prices.push({ id: price.id, lookup_key: lookupKey, product: product.id, kind: entry.kind,
        plan: entry.plan, interval, amount_rappen: amount });
    }
  }

  // Customer portal: payment methods, invoices, billing details, cancel at period end. Plan and
  // seat changes go through POST /api/billing/change (the portal cannot update multi-item
  // subscriptions).
  const configs = await stripe.request("GET", "/billing_portal/configurations", { limit: 100 });
  let portal = configs.data.find((/** @type {any} */ c) => c.metadata?.app === META.app && c.active);
  if (!portal && !dry) {
    portal = await stripe.request("POST", "/billing_portal/configurations", {
      business_profile: { headline: "Mixel Remote – Abo, Zahlungsmittel und Rechnungen" },
      default_return_url: "https://remote.mixel.ch/billing",
      features: {
        customer_update: { enabled: true, allowed_updates: ["email", "address", "name", "tax_id"] },
        invoice_history: { enabled: true },
        payment_method_update: { enabled: true },
        subscription_cancel: { enabled: true, mode: "at_period_end", proration_behavior: "none",
          cancellation_reason: { enabled: true, options: ["too_expensive", "missing_features", "switched_service", "unused", "other"] } },
        subscription_update: { enabled: false },
      },
      metadata: META,
    }, { idempotencyKey: "mixel-remote-portal-config-v2" });
  }
  result.portal_configuration = portal ? { id: portal.id, is_default: portal.is_default } : null;

  const webhookUrl = typeof opts["webhook-url"] === "string" ? opts["webhook-url"] : null;
  if (webhookUrl) {
    const endpoints = await stripe.request("GET", "/webhook_endpoints", { limit: 100 });
    let endpoint = endpoints.data.find((/** @type {any} */ w) => w.url === webhookUrl);
    if (endpoint && endpoint.metadata?.app !== META.app) throw new Error("Webhook URL is used by another endpoint");
    if (!endpoint && !dry) {
      const secretOut = opts["secret-out"];
      if (typeof secretOut !== "string") throw new Error("--secret-out FILE is required to create the webhook endpoint");
      endpoint = await stripe.request("POST", "/webhook_endpoints", {
        url: webhookUrl, enabled_events: [...HANDLED_EVENTS], api_version: "2024-06-20",
        description: "Mixel Remote billing (remote_saas plan state)", metadata: META,
      });
      writeFileSync(secretOut, `STRIPE_WEBHOOK_SECRET=${endpoint.secret}\n`, { mode: 0o600 });
      result.webhook_secret_written_to = secretOut;
    }
    result.webhook_endpoint = endpoint ? { id: endpoint.id, url: endpoint.url, status: endpoint.status,
      api_version: endpoint.api_version, enabled_events: endpoint.enabled_events } : null;
  }
  console.log(JSON.stringify(result, null, 2));
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
