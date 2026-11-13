import { describe, expect, it } from 'vitest';
import { en } from '../../src/i18n/en';
import { de } from '../../src/i18n/de';
import { fr } from '../../src/i18n/fr';
import { it as italian } from '../../src/i18n/it';
import { detectLang, format } from '../../src/i18n/index';

const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();

describe('translations', () => {
  for (const [name, dictionary] of Object.entries({ de, fr, it: italian })) {
    it(`${name} has every key, no extra key and the same placeholders`, () => {
      expect(Object.keys(dictionary).sort()).toEqual(Object.keys(en).sort());
      for (const key of Object.keys(en) as (keyof typeof en)[]) {
        expect(dictionary[key].trim().length, key).toBeGreaterThan(0);
        expect(placeholders(dictionary[key]), key).toEqual(placeholders(en[key]));
      }
    });
  }
  it('uses Swiss German spelling (no ß)', () => {
    for (const value of Object.values(de)) expect(value).not.toContain('ß');
  });
  it('fills placeholders and picks the language', () => {
    expect(format('{a} of {b}', { a: 1, b: 'x' })).toBe('1 of x');
    expect(format('{missing}', {})).toBe('{missing}');
    expect(detectLang(null, ['fr-CH', 'de'])).toBe('fr');
    expect(detectLang('it', ['de-CH'])).toBe('it');
    expect(detectLang(null, ['es-ES'])).toBe('en');
    expect(detectLang('xx', ['de-CH'])).toBe('de');
  });
});
