import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { en, type MessageKey } from './en';
import { de } from './de';
import { fr } from './fr';
import { it } from './it';

export type Lang = 'en' | 'de' | 'fr' | 'it';
export const LANGS: { code: Lang; label: string }[] = [
  { code: 'de', label: 'Deutsch' },
  { code: 'en', label: 'English' },
  { code: 'fr', label: 'Français' },
  { code: 'it', label: 'Italiano' },
];
export const DICTIONARIES: Record<Lang, Record<MessageKey, string>> = { en, de, fr, it };
const STORAGE_KEY = 'mixel-remote-portal-lang';

export type Translate = (key: MessageKey, vars?: Record<string, string | number>) => string;

export function format(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => (name in vars ? String(vars[name]) : match));
}

export function detectLang(stored: string | null, browser: readonly string[]): Lang {
  if (stored && stored in DICTIONARIES) return stored as Lang;
  for (const candidate of browser) {
    const code = candidate.toLowerCase().slice(0, 2);
    if (code in DICTIONARIES) return code as Lang;
  }
  return 'en';
}

function readStored(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

interface I18n {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: Translate;
  /** BCP 47 locale for dates and numbers (Swiss variants). */
  locale: string;
}

const Context = createContext<I18n | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    const fromUrl = new URLSearchParams(window.location.search).get('lang');
    return detectLang(fromUrl ?? readStored(), navigator.languages ?? [navigator.language]);
  });
  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* private mode: keep it for this page only */
    }
  }, []);
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);
  const value = useMemo<I18n>(() => {
    const dictionary = DICTIONARIES[lang];
    return {
      lang,
      setLang,
      locale: `${lang}-CH`,
      t: (key, vars) => format(dictionary[key] ?? en[key] ?? key, vars),
    };
  }, [lang, setLang]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useI18n(): I18n {
  const value = useContext(Context);
  if (!value) throw new Error('useI18n outside I18nProvider');
  return value;
}

export type { MessageKey };
