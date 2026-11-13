// Legal documents of Mixel Remote (Terms of Service, Privacy Policy, Data Processing Agreement,
// sub-processors) in English, German, French and Italian. DRAFT pending Michael's review: every page
// shows the draft banner and is served with "noindex" (worker/index.ts, pages/Legal.tsx).

/** The version stored with each acceptance (must equal the current version in SQL 027). */
export const LEGAL_VERSION = '2026-11-13-draft';
/** Shown on the pages; the final texts get a new version and date. */
export const LEGAL_DATE = '2026-11-13';

export type LegalDocKey = 'terms' | 'privacy' | 'dpa' | 'subprocessors';
export const LEGAL_DOCS: LegalDocKey[] = ['terms', 'privacy', 'dpa', 'subprocessors'];

/** A paragraph, a bullet list, or the sub-processor table. */
export type Block = string | { list: string[] } | { table: 'subprocessors' };

export interface LegalSection {
  heading: string;
  body: Block[];
}

export interface LegalDoc {
  title: string;
  summary: string;
  sections: LegalSection[];
}

export interface SubprocessorText {
  purpose: string;
  data: string;
  location: string;
  safeguard: string;
}

export interface LegalTexts {
  ui: {
    centre: string;
    centreLead: string;
    draftTitle: string;
    draftText: string;
    version: string;
    updated: string;
    contents: string;
    otherLanguages: string;
    columns: { name: string; purpose: string; data: string; location: string; safeguard: string };
    print: string;
  };
  terms: LegalDoc;
  privacy: LegalDoc;
  dpa: LegalDoc;
  subprocessors: LegalDoc;
  /** Same order as SUBPROCESSORS. */
  subprocessorRows: SubprocessorText[];
}

/** Names and addresses (not translated). */
export const SUBPROCESSORS: { name: string; address: string; url: string }[] = [
  { name: 'Hetzner Online GmbH', address: 'Industriestrasse 25, 91710 Gunzenhausen, Germany', url: 'https://www.hetzner.com/legal/privacy-policy' },
  { name: 'Cloudflare, Inc.', address: '101 Townsend St, San Francisco, CA 94107, USA', url: 'https://www.cloudflare.com/privacypolicy/' },
  { name: 'Stripe Payments Europe, Ltd. (with Stripe, Inc.)', address: '1 Grand Canal Street Lower, Dublin 2, Ireland', url: 'https://stripe.com/privacy' },
  { name: 'Resend (Plus Five Five, Inc.)', address: 'San Francisco, California, USA', url: 'https://resend.com/legal/privacy-policy' },
];

/** Provider details (the same as on mixel.ch/impressum). */
export const PROVIDER = {
  name: 'Mixel IT and Corporate Services GmbH',
  street: 'Rüterspüelstrasse 17',
  city: '8173 Neerach',
  country: 'Switzerland',
  uid: 'CHE-184.355.981',
  email: 'info@mixel.ch',
  phone: '+41 44 433 14 00',
};
