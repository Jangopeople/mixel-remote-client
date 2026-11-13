import { describe, expect, it } from 'vitest';
import { PLANS, formatChf, monthlyPrice, planByCode } from '../../src/lib/plans';

describe('plans', () => {
  it('matches the agreed plan table', () => {
    expect(PLANS.map((plan) => [plan.code, plan.priceYearly, plan.technicians, plan.devices, plan.sessions])).toEqual([
      ['personal', 0, 1, 10, 1],
      ['solo', 12, 1, 100, 1],
      ['team', 39, 5, 500, 3],
      ['business', 89, 15, 2000, 10],
      ['enterprise', null, null, null, null],
    ]);
  });
  it('adds 20 % for monthly billing, rounded to 5 Rappen', () => {
    expect(monthlyPrice(planByCode('solo')!, 'monthly')).toBe(14.4);
    expect(monthlyPrice(planByCode('team')!, 'monthly')).toBe(46.8);
    expect(monthlyPrice(planByCode('business')!, 'monthly')).toBe(106.8);
    expect(monthlyPrice(planByCode('business')!, 'yearly')).toBe(89);
    expect(monthlyPrice(planByCode('enterprise')!, 'monthly')).toBeNull();
  });
  it('formats Swiss francs', () => {
    expect(formatChf(89, 'de')).toBe('89');
    expect(formatChf(106.8, 'en')).toBe('106.80');
  });
});

import { formatMoney, periodTotal, shownMonthly, type PriceRow } from '../../src/lib/plans';

const PRICES: PriceRow[] = [
  { kind: 'plan', plan: 'solo', billing_interval: 'year', amount_chf: 144, per_month_chf: 12 },
  { kind: 'plan', plan: 'solo', billing_interval: 'month', amount_chf: 14.4, per_month_chf: 14.4 },
  { kind: 'plan', plan: 'business', billing_interval: 'year', amount_chf: 1068, per_month_chf: 89 },
  { kind: 'seat', plan: null, billing_interval: 'year', amount_chf: 96, per_month_chf: 8 },
  { kind: 'seat', plan: null, billing_interval: 'month', amount_chf: 9.6, per_month_chf: 9.6 },
];

describe('billing totals', () => {
  it('adds extra technicians to the plan price', () => {
    expect(periodTotal(PRICES, 'business', 'year', 2)).toEqual({ plan: 1068, seat: 96, total: 1260 });
    expect(periodTotal(PRICES, 'solo', 'month', 3)?.total).toBe(43.2);
    expect(periodTotal(PRICES, 'team', 'year', 0)).toBeNull();
  });
  it('prefers the live price list on the pricing page', () => {
    expect(shownMonthly(planByCode('solo')!, 'monthly', PRICES)).toBe(14.4);
    expect(shownMonthly(planByCode('team')!, 'yearly', PRICES)).toBe(39);
    expect(shownMonthly(planByCode('team')!, 'yearly', null)).toBe(39);
  });
  it('formats amounts', () => {
    expect(formatMoney(1260, 'de').replace(/\D/g, '')).toBe('126000');
    expect(formatMoney(14.4, 'en')).toBe('CHF 14.40');
  });
});
