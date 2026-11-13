import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

export type ThemeChoice = 'light' | 'dark' | 'system';
const STORAGE_KEY = 'mixel-remote-portal-theme';

interface Theme {
  choice: ThemeChoice;
  setChoice: (choice: ThemeChoice) => void;
}
const Context = createContext<Theme | null>(null);

function stored(): ThemeChoice {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (value === 'light' || value === 'dark' || value === 'system') return value;
  } catch {
    /* storage unavailable */
  }
  return 'system';
}

/** Sets [data-theme] on <html>; "system" removes it so the prefers-color-scheme rules apply. */
export function applyTheme(choice: ThemeChoice, root: HTMLElement = document.documentElement): void {
  if (choice === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', choice);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setState] = useState<ThemeChoice>(() => {
    const fromUrl = new URLSearchParams(window.location.search).get('theme');
    return fromUrl === 'light' || fromUrl === 'dark' ? fromUrl : stored();
  });
  useEffect(() => applyTheme(choice), [choice]);
  const setChoice = useCallback((next: ThemeChoice) => {
    setState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* storage unavailable */
    }
  }, []);
  const value = useMemo(() => ({ choice, setChoice }), [choice, setChoice]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useTheme(): Theme {
  const value = useContext(Context);
  if (!value) throw new Error('useTheme outside ThemeProvider');
  return value;
}
