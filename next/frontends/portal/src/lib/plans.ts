// The Mixel Remote plans (docs/mixel-remote-plan.md, Phase 8, and remote_saas.plans in SQL 018).
// CHF per month with yearly billing; monthly billing costs 20 % more.

export type PlanCode = 'personal' | 'solo' | 'team' | 'business' | 'enterprise';

export interface Plan {
  code: PlanCode;
  name: string;
  /** CHF per month, billed yearly. Null: on request. */
  priceYearly: number | null;
  /** Null: set per contract. */
  technicians: number | null;
  devices: number | null;
  sessions: number | null;
  /** Feature keys shown as extras (i18n keys under "feature."). */
  extras: string[];
}

export const PLANS: readonly Plan[] = [
  { code: 'personal', name: 'Personal', priceYearly: 0, technicians: 1, devices: 10, sessions: 1, extras: ['private_use_only'] },
  { code: 'solo', name: 'Solo', priceYearly: 12, technicians: 1, devices: 100, sessions: 1, extras: [] },
  { code: 'team', name: 'Team', priceYearly: 39, technicians: 5, devices: 500, sessions: 3, extras: ['groups', 'access_rights'] },
  {
    code: 'business', name: 'Business', priceYearly: 89, technicians: 15, devices: 2000, sessions: 10,
    extras: ['sso', 'audit_log', 'policies', 'require_mfa'],
  },
  { code: 'enterprise', name: 'Enterprise', priceYearly: null, technicians: null, devices: null, sessions: null, extras: [] },
];

/** Included in every plan. */
export const EVERY_PLAN = ['phone_support', 'browser_viewer', 'files', 'recording', 'meetings'] as const;

export const EXTRA_TECHNICIAN_CHF = 8;
export const TRIAL_DAYS = 14;
export const READ_ONLY_KEEP_DAYS = 30;
export const MONTHLY_SURCHARGE = 0.2;

export type Billing = 'yearly' | 'monthly';

/** Price per month in CHF for the billing period, or null when on request. */
export function monthlyPrice(plan: Plan, billing: Billing): number | null {
  if (plan.priceYearly === null) return null;
  if (billing === 'yearly') return plan.priceYearly;
  // Round to 5 Rappen, the smallest Swiss coin.
  return Math.round(plan.priceYearly * (1 + MONTHLY_SURCHARGE) * 20) / 20;
}

export function formatChf(amount: number, locale: string): string {
  const whole = Number.isInteger(amount);
  return new Intl.NumberFormat(locale + '-CH', {
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(amount);
}

export function planByCode(code: string | null | undefined): Plan | undefined {
  return PLANS.find((plan) => plan.code === code);
}

export interface PriceRow { kind: 'plan' | 'seat'; plan: string | null; billing_interval: 'year' | 'month'; amount_chf: number; per_month_chf: number }

/** Amount per billing period for a plan plus extra technicians, from the live price list. */
export function periodTotal(prices: readonly PriceRow[], plan: string, interval: 'year' | 'month', extra: number):
  { plan: number; seat: number; total: number } | null {
  const planPrice = prices.find((row) => row.kind === 'plan' && row.plan === plan && row.billing_interval === interval);
  const seatPrice = prices.find((row) => row.kind === 'seat' && row.billing_interval === interval);
  if (!planPrice || !seatPrice) return null;
  const seats = Math.max(0, Math.floor(extra));
  const total = Math.round((Number(planPrice.amount_chf) + seats * Number(seatPrice.amount_chf)) * 100) / 100;
  return { plan: Number(planPrice.amount_chf), seat: Number(seatPrice.amount_chf), total };
}

/** Per-month price shown on the pricing page: live price list first, the agreed table as fallback. */
export function shownMonthly(plan: Plan, billing: Billing, prices: readonly PriceRow[] | null): number | null {
  const interval = billing === 'yearly' ? 'year' : 'month';
  const row = prices?.find((entry) => entry.kind === 'plan' && entry.plan === plan.code && entry.billing_interval === interval);
  return row ? Number(row.per_month_chf) : monthlyPrice(plan, billing);
}

export function formatMoney(amount: number, locale: string): string {
  return `CHF ${new Intl.NumberFormat(locale + '-CH', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount)}`;
}
