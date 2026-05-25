# Swiss Compliance — Audit Rules

Load when the PR touches financial data, personal data, accounting flows, or anything user-facing that could be subject to Swiss / EU regulation.

This is the rule file that protects MIXEL from legal exposure. Compliance gaps in Swiss SME software cost contracts and damage reputation. Be aggressive.

---

## GeBüV — Swiss bookkeeping retention

GeBüV requires bookkeeping records to be kept for **10 years**, **append-only**, and **tamper-resistant**. Applies to any product touching: invoices, receipts, journal entries, accounting transactions, tax records, contracts with financial commitments.

### Audit log table

🔴 Product handles financial data without an `audit_log` table (or equivalent).

🔴 `audit_log` table has any update or delete policy enabled — must be append-only.

🔴 `audit_log` triggers missing on tables that store financial / contractual data.

🟡 `audit_log` retention not configured (auto-delete after some period). Should retain 10 years minimum.

🟡 `audit_log` only captures the action, not before/after state — can't reconstruct what changed.

🟡 No partitioning on `audit_log` for high-volume tables (performance risk at year 2+).

### Tamper resistance

🔴 `audit_log` modifications possible via direct database access (no constraint preventing this on the application level — only RLS enforces append-only).

🟡 No periodic hash chain / checkpoint on `audit_log` to detect tampering at the DB level.

### UI access

🟡 Product has audit log table but no UI to view it (clients need to access their audit trail for compliance reviews).

🟡 Audit log UI doesn't filter by date range, actor, or entity — unusable in practice for a 10-year record.

---

## FADP/DSG + GDPR — personal data

Revised Swiss Federal Act on Data Protection (FADP/DSG, effective Sep 2023) + GDPR for EU users. Both require: lawful basis, transparency, right to access, right to delete, data minimization.

### Right to access (data export)

🔴 Product collects personal data without a user-facing "Export my data" endpoint.

🟡 Export endpoint exists but doesn't include data from all tables — partial export = legal risk.

🟡 Export format not machine-readable (JSON or CSV preferred).

🟡 Export endpoint not rate limited / abuse-protected.

### Right to delete

🔴 Product collects personal data without a user-facing account deletion flow.

🔴 "Delete account" implemented as soft-delete only (sets a flag) without an option for hard delete after retention period.

🟡 Account deletion doesn't cascade to all personal data tables (some data remains).

🟡 Audit log retains personal data (emails, names) — GeBüV doesn't require this; should be anonymized to user UUID only.

### Consent and lawful basis

🟡 Product processes personal data for tracking / analytics / marketing without a consent banner.

🟡 Consent banner uses pre-checked boxes (illegal under GDPR).

🟡 No consent log — can't prove which user consented to what at what time.

🟢 Privacy policy missing or not linked from sign-up flow.

### Data minimization

🟡 Sign-up form collects fields not required for the service (age, gender, etc. when irrelevant).

🟡 Optional fields stored as `not null` empty strings — should be nullable.

### Cross-border transfers

🟡 Product uses third-party services that store data outside CH/EU without a documented adequacy basis (currently OK for US under EU-US DPF, but conditions apply).

🟢 SaaS dependencies that send personal data to US-based providers not disclosed in privacy policy.

---

## Swiss VAT (Mehrwertsteuer / TVA / IVA)

Current rates (2024+): **8.1% standard**, **2.6% reduced**, **3.8% accommodation**.

### Rate values

🔴 Hardcoded VAT rate that doesn't match current Swiss rates (e.g. 7.7% — the pre-2024 rate).

🟡 VAT rates stored as constants in code instead of a `vat_rates` reference table with a `valid_from` / `valid_to` schema (rates change — need history).

🟡 VAT calculation rounding inconsistent across the codebase. Standard: round per-line-item using banker's rounding or always-up, documented and consistent.

### VAT on invoices

🔴 Invoice generation doesn't include VAT breakdown (required by Swiss law for B2B invoices > CHF 400).

🟡 Invoice doesn't include the seller's UID (Swiss business identifier) — required.

🟡 Multi-rate invoices show only a single VAT line instead of per-rate breakdown.

### VAT reporting

🟡 No VAT report / export by quarter (clients need this to file with the Federal Tax Administration).

🟡 VAT report includes voided invoices in revenue — should be net of reversals.

---

## TWINT integration (if payments)

🔴 TWINT webhook handler doesn't verify signature before processing.

🔴 TWINT response not idempotently handled — duplicate notifications can credit the customer twice.

🟡 TWINT branding violations (using TWINT name or logo outside their brand guidelines).

🟡 Refund flow not implemented — manual refunds via TWINT dashboard only.

---

## QR-bill (Swiss QR-Rechnung)

If invoicing in Switzerland, QR-bill compliance is expected since Sept 2022.

🔴 Invoices don't include QR-bill section.

🟡 QR-bill IBAN field uses regular IBAN where QR-IBAN is required (for structured reference).

🟡 QR-bill reference uses wrong checksum.

🟢 QR-bill PDF rendering doesn't match exact dimension specs (perforation line, size).

---

## Currency handling

🔴 Money stored as `numeric` / `float` — precision pitfall. Store as `bigint` cents (`integer` for ≤ CHF 21M, `bigint` beyond).

🔴 Multi-currency app stores amounts without a `currency` column → ambiguity in reporting.

🟡 Exchange rate history not stored when transactions cross currencies — can't reconstruct what rate was used.

🟡 Display formats use comma thousands separator (`1,000.00`) instead of Swiss apostrophe (`1'000.00`).

---

## Languages

🟡 Product designed for Swiss SMEs but only ships in English (DE expected as primary; FR for Romandy; IT for Ticino).

🟡 DE translations use informal `Du` instead of formal `Sie` (formal expected in B2B Swiss-German contexts).

🟢 Translations machine-generated without review for technical terms.

---

## Date / number formats

🔴 Dates formatted as `MM/DD/YYYY` for Swiss users — should be `DD.MM.YYYY`.

🔴 Numbers formatted with English thousands separator (`1,000.00`) for Swiss users — should be `1'000.00`.

🟡 Locale not derived from user preference / tenant setting — hardcoded to a single locale.

🟡 First day of week assumed to be Sunday — Swiss convention is Monday.

---

## Documentation for compliance

🟢 No `COMPLIANCE.md` or equivalent doc describing how the product satisfies GeBüV / DSG.

🟢 Privacy policy / terms not in the repo or referenced.

🟢 No documented data retention schedule.

---

## Reverse-engineering finding patterns (App Doctor pairing)

When auditing an existing MIXEL app, common compliance debt:

- Audit log added late, missing triggers on half the tables → 🔴 partial coverage = no coverage from a legal standpoint
- "Delete user" sets `deleted_at` flag but data remains in 10 other tables → 🔴 fails right-to-delete
- VAT rates still hardcoded to pre-2024 7.7% standard → 🔴 wrong amounts charged
- No QR-bill on invoices because it was an early MVP shortcut → 🔴 non-compliant Swiss invoicing
- Money as `numeric(10,2)` → 🟡 works most of the time, fails on edge cases

If you see these, cross-link with the App Doctor's findings.
