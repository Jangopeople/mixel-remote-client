import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { LEGAL, LEGAL_DOCS, LEGAL_VERSION, SUBPROCESSORS, type Block, type LegalDoc } from '../../src/legal';
import { DICTIONARIES } from '../../src/i18n';
import { CSV_COLUMNS, exportFileName, toCsv, type CompanyExport } from '../../src/lib/exportData';
import { confirmMatches, errorKey, linkParts } from '../../src/lib/logic';

const text = (doc: LegalDoc) => [doc.title, doc.summary, ...doc.sections.flatMap((s) => [s.heading, ...s.body.flatMap((b: Block) =>
  typeof b === 'string' ? [b] : 'list' in b ? b.list : [])])].join('\n');

describe('legal documents', () => {
  it('the version stored with an acceptance is the current version of SQL 027', () => {
    const sql = readFileSync(resolve(__dirname, '../../../../sql/027_remote_saas_legal_deletion.sql'), 'utf8');
    expect(sql).toContain(`('terms', '${LEGAL_VERSION}', true)`);
    expect(sql).toContain(`('dpa', '${LEGAL_VERSION}', true)`);
  });
  it('every language has every document with the same structure', () => {
    const en = LEGAL.en;
    for (const [lang, texts] of Object.entries(LEGAL)) {
      for (const key of LEGAL_DOCS) {
        expect(texts[key].sections.length, `${lang} ${key}`).toBe(en[key].sections.length);
        texts[key].sections.forEach((section, index) => {
          expect(section.body.length, `${lang} ${key} ${section.heading}`).toBe(en[key].sections[index].body.length);
          section.body.forEach((block, at) => {
            const ref = en[key].sections[index].body[at];
            expect(typeof block, `${lang} ${key} ${index}.${at}`).toBe(typeof ref);
            if (typeof block !== 'string' && 'list' in block) expect(block.list.length).toBe((ref as { list: string[] }).list.length);
          });
        });
      }
      expect(texts.subprocessorRows.length, lang).toBe(SUBPROCESSORS.length);
      expect(texts.ui.draftTitle.length).toBeGreaterThan(3);
    }
  });
  it('states the facts the product implements', () => {
    for (const [lang, texts] of Object.entries(LEGAL)) {
      const all = LEGAL_DOCS.map((key) => text(texts[key])).join('\n');
      for (const fact of ['14', '30', '958f', '12', 'Stripe', 'Hetzner', 'Cloudflare', 'Resend', 'CHE-184.355.981', 'info@mixel.ch']) {
        expect(all, `${lang} mentions ${fact}`).toContain(fact);
      }
      for (const price of ['12', '39', '89', '8']) expect(text(texts.terms), `${lang} price ${price}`).toContain(`CHF ${price}`);
    }
  });
  it('names no competitor and uses Swiss German spelling', () => {
    const all = Object.values(LEGAL).map((texts) => LEGAL_DOCS.map((key) => text(texts[key])).join('\n')).join('\n');
    expect(all).not.toMatch(/teamviewer|anydesk|splashtop|logmein|gotoassist|rustdesk|bomgar|beyondtrust|connectwise|zoho assist/i);
    expect(LEGAL_DOCS.map((key) => text(LEGAL.de[key])).join('\n')).not.toContain('ß');
  });
  it('the sign-up sentence has three links (Terms, DPA, Privacy) in every language', () => {
    for (const dictionary of Object.values(DICTIONARIES)) {
      expect(linkParts(dictionary['signup.terms']).filter((part) => part.link)).toHaveLength(3);
    }
    expect(linkParts('a [b] c [d]')).toEqual([{ text: 'a ', link: false }, { text: 'b', link: true }, { text: ' c ', link: false }, { text: 'd', link: true }]);
  });
});

describe('deletion and export helpers', () => {
  it('confirmation compares like the database (trimmed, any case)', () => {
    expect(confirmMatches(' qa legal ag ', 'QA Legal AG')).toBe(true);
    expect(confirmMatches('QA Legal', 'QA Legal AG')).toBe(false);
    expect(confirmMatches('', '')).toBe(false);
  });
  it('maps the deletion and terms messages of SQL 027', () => {
    expect(errorKey({ message: 'Accept the Terms of Service and the Data Processing Agreement to create a company', code: '22023' })).toBe('error.terms_required');
    expect(errorKey({ message: 'The Terms of Service have changed. Reload the page and accept the current version.' })).toBe('error.terms_changed');
    expect(errorKey({ message: 'Type the company name exactly to confirm' })).toBe('error.confirm_company');
    expect(errorKey({ message: 'Type your email address exactly to confirm' })).toBe('error.confirm_email_mismatch');
    expect(errorKey({ message: 'You are the only owner. Delete the company, or make another member owner first.', code: '55000' })).toBe('error.only_owner');
    expect(errorKey({ message: 'Mixel team accounts are not deleted here' })).toBe('error.account_managed');
  });
  it('CSV: header, quoting, formula guard, UTF-8 BOM', () => {
    const csv = toCsv([{ name: 'Anna, "A"', email: 'a@x.ch', role: 'owner', joined_at: '2026-11-13', user_id: 'u1' },
      { name: '=HYPERLINK("x")', email: '', role: 'member', joined_at: null, user_id: 'u2' }], CSV_COLUMNS.members);
    expect(csv.startsWith('﻿name,email,role,joined_at,user_id\r\n')).toBe(true);
    expect(csv).toContain('"Anna, ""A""",a@x.ch,owner,2026-11-13,u1');
    expect(csv).toContain(`"'=HYPERLINK(""x"")",,member,,u2`);
    expect(toCsv([{ technicians: ['a@x.ch', 'b@x.ch'], name: 'G' }], ['name', 'technicians'])).toContain('G,a@x.ch; b@x.ch');
    expect(toCsv([{ detail: { from: 'a' } }], ['detail'])).toContain('"{""from"":""a""}"');
  });
  it('file names carry the company and the day', () => {
    const data = { exported_at: '2026-11-13T10:00:00Z', company: { name: 'QA', slug: 'qa-legal-ag' } } as unknown as CompanyExport;
    expect(exportFileName(data, 'export', 'json')).toBe('mixel-remote-qa-legal-ag-export-2026-11-13.json');
    expect(exportFileName({ ...data, company: { name: 'x', slug: '../../etc' } } as CompanyExport, 'members', 'csv')).toBe('mixel-remote-etc-members-2026-11-13.csv');
  });
});
