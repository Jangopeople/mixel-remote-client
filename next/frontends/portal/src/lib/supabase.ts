import { createClient } from '@supabase/supabase-js';

/** ISM identity and data API (GoTrue + PostgREST), same accounts as the Mixel Remote apps. */
export const ISM_URL = 'https://ism-api.mixel.ch';
const ANON_KEY = import.meta.env.VITE_ISM_ANON_KEY as string | undefined;

if (!ANON_KEY) {
  // Built without the public anon key (see README): fail loudly instead of sending bad requests.
  throw new Error('VITE_ISM_ANON_KEY is missing');
}

export const supabase = createClient(ISM_URL, ANON_KEY, {
  auth: {
    flowType: 'pkce',
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: true,
    storageKey: 'mixel-remote-portal-auth',
  },
});

export const anonKey = ANON_KEY;
