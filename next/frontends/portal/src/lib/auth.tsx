import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import type { Session } from '@supabase/supabase-js';
import { supabase } from './supabase';

export interface Assurance {
  current: 'aal1' | 'aal2' | null;
  next: 'aal1' | 'aal2' | null;
}

interface Auth {
  session: Session | null;
  loading: boolean;
  assurance: Assurance;
  /** The session is signed in but its account has a verified 2-step factor not used yet. */
  needsSecondStep: boolean;
  refreshAssurance: () => Promise<Assurance>;
  signOut: () => Promise<void>;
}

const Context = createContext<Auth | null>(null);

async function readAssurance(): Promise<Assurance> {
  const { data } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
  return { current: data?.currentLevel ?? null, next: data?.nextLevel ?? null };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [assurance, setAssurance] = useState<Assurance>({ current: null, next: null });

  const refreshAssurance = useCallback(async () => {
    const value = await readAssurance();
    setAssurance(value);
    return value;
  }, []);

  useEffect(() => {
    let active = true;
    supabase.auth.getSession().then(async ({ data }) => {
      if (!active) return;
      setSession(data.session);
      if (data.session) await refreshAssurance();
      if (active) setLoading(false);
    });
    const { data: listener } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
      if (next) {
        // Outside the auth callback (supabase-js holds a lock while it runs).
        window.setTimeout(() => void refreshAssurance(), 0);
      } else {
        setAssurance({ current: null, next: null });
      }
    });
    return () => {
      active = false;
      listener.subscription.unsubscribe();
    };
  }, [refreshAssurance]);

  const signOut = useCallback(async () => {
    await supabase.auth.signOut({ scope: 'local' });
    setSession(null);
    setAssurance({ current: null, next: null });
  }, []);

  const value = useMemo<Auth>(() => ({
    session,
    loading,
    assurance,
    needsSecondStep: !!session && assurance.current === 'aal1' && assurance.next === 'aal2',
    refreshAssurance,
    signOut,
  }), [session, loading, assurance, refreshAssurance, signOut]);

  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useAuth(): Auth {
  const value = useContext(Context);
  if (!value) throw new Error('useAuth outside AuthProvider');
  return value;
}
