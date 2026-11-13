// The Mixel Remote Stripe catalogue (docs/mixel-remote-plan.md, Phase 8).
export const META = { app: "mixel-remote", mixel_app: "remote" };
// CHF, Swiss VAT included (mirrors Mixel Shop). Yearly = list price x 12, monthly = +20 %.
export const CATALOGUE = [
  { product: "mixel_remote_solo", name: "Mixel Remote Solo", plan: "solo", kind: "plan", perMonth: 1200,
    description: "1 Techniker, 100 Geräte, 1 Sitzung gleichzeitig" },
  { product: "mixel_remote_team", name: "Mixel Remote Team", plan: "team", kind: "plan", perMonth: 3900,
    description: "5 Techniker, 500 Geräte, 3 Sitzungen gleichzeitig, Gruppen und Zugriffsrechte" },
  { product: "mixel_remote_business", name: "Mixel Remote Business", plan: "business", kind: "plan", perMonth: 8900,
    description: "15 Techniker, 2000 Geräte, 10 Sitzungen gleichzeitig, Firmen-SSO, Audit-Log, Richtlinien" },
  { product: "mixel_remote_technician", name: "Mixel Remote zusätzlicher Techniker", plan: null, kind: "seat", perMonth: 800,
    description: "Ein zusätzlicher Techniker für Solo, Team oder Business" },
];

/** @param {number} perMonth @param {"year" | "month"} interval */
export const amountFor = (perMonth, interval) => (interval === "year" ? perMonth * 12 : Math.round(perMonth * 1.2));

