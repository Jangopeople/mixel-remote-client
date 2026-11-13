// Sign-in building blocks shared by sign-in, sign-up and the invitation page.
import { useEffect, useState, type FormEvent } from 'react';
import { supabase } from '../lib/supabase';
import { goTrueSettings } from '../lib/api';
import { availableMethods, isOtpCode, OAUTH_PROVIDERS, type Method, type OAuthMethod } from '../lib/logic';
import { useAuth } from '../lib/auth';
import { useI18n } from '../i18n';
import { ErrorNotice } from './ui';
import { Icon } from './Icon';

let settingsPromise: ReturnType<typeof goTrueSettings> | null = null;

/** The sign-in methods the Mixel sign-in service offers right now (null while loading). */
export function useAvailableMethods(): Set<Method> | null {
  const [available, setAvailable] = useState<Set<Method> | null>(null);
  useEffect(() => {
    let active = true;
    settingsPromise ??= goTrueSettings();
    settingsPromise.then((settings) => active && setAvailable(availableMethods(settings)));
    return () => {
      active = false;
    };
  }, []);
  return available;
}

export function callbackUrl(next: string): string {
  return `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`;
}

export async function startOAuth(method: OAuthMethod, next: string): Promise<void> {
  const { provider, scopes } = OAUTH_PROVIDERS[method];
  const { error } = await supabase.auth.signInWithOAuth({ provider, options: { redirectTo: callbackUrl(next), scopes } });
  if (error) throw error;
}

function ProviderMark({ method }: { method: OAuthMethod }) {
  if (method === 'mixel_id') {
    return <img src="/mixel-icon.png" width={20} height={20} alt="" style={{ borderRadius: '50%', background: '#fff' }} />;
  }
  if (method === 'google') {
    return (
      <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
        <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.1C12.5 13.6 17.8 9.5 24 9.5z" />
        <path fill="#4285F4" d="M46.1 24.6c0-1.6-.1-3.1-.4-4.6H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.7c4.3-4 6.9-9.9 6.9-17z" />
        <path fill="#FBBC05" d="M10.6 28.6c-.5-1.4-.8-2.9-.8-4.6s.3-3.2.8-4.6l-7.9-6.1C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.9-6.1z" />
        <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.4-5.7c-2.1 1.4-4.8 2.3-8.5 2.3-6.2 0-11.5-4.1-13.4-9.9l-7.9 6.1C6.6 42.6 14.6 48 24 48z" />
      </svg>
    );
  }
  if (method === 'azure') {
    return (
      <svg width="18" height="18" viewBox="0 0 21 21" aria-hidden="true">
        <rect x="1" y="1" width="9" height="9" fill="#F25022" /><rect x="11" y="1" width="9" height="9" fill="#7FBA00" />
        <rect x="1" y="11" width="9" height="9" fill="#00A4EF" /><rect x="11" y="11" width="9" height="9" fill="#FFB900" />
      </svg>
    );
  }
  return (
    <svg width="18" height="18" viewBox="0 0 814 1000" aria-hidden="true" fill="currentColor">
      <path d="M788 341c-6 4-108 62-108 190 0 148 130 200 134 202-1 3-21 72-69 142-43 62-88 124-156 124s-86-40-164-40c-77 0-104 41-167 41s-106-57-156-128C44 788 0 668 0 554 0 371 119 274 236 274c62 0 114 41 153 41 37 0 95-43 166-43 27 0 124 2 188 69zM554 159c29-35 50-83 50-131 0-7-1-14-2-19-48 2-104 32-138 72-27 30-52 79-52 127 0 7 1 15 2 17 3 1 8 1 12 1 43 0 97-29 128-67z" />
    </svg>
  );
}

/** "Continue with Google / Microsoft / Apple / Mixel ID". onBefore can stop the start (form checks). */
export function OAuthButton({ method, next, onBefore }: { method: OAuthMethod; next: string; onBefore?: () => boolean }) {
  const { t } = useI18n();
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const name = t(`auth.method.${method}` as const);
  return (
    <>
      <button
        type="button"
        className="secondary button-block"
        disabled={busy}
        data-testid={method === 'mixel_id' ? 'mixel-id' : `oauth-${method}`}
        onClick={async () => {
          if (onBefore && !onBefore()) return;
          setBusy(true);
          setError(null);
          try {
            await startOAuth(method, next);
          } catch (failure) {
            setError(failure);
            setBusy(false);
          }
        }}
      >
        <ProviderMark method={method} />
        {method === 'mixel_id' ? t('auth.withMixelId') : t('auth.continueWith', { name })}
      </button>
      <ErrorNotice error={error} />
    </>
  );
}

/** Enter the 6-digit code that was emailed. type "signup" confirms a new password account. */
export function EmailCodeForm({
  email, type, onVerified, onResend, onBack,
}: { email: string; type: 'email' | 'signup'; onVerified: () => void; onResend: () => Promise<void>; onBack: () => void }) {
  const { t } = useI18n();
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [cooldown, setCooldown] = useState(60);
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setTimeout(() => setCooldown((value) => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [cooldown]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!isOtpCode(code)) {
      setError({ message: 'otp invalid' });
      return;
    }
    setBusy(true);
    setError(null);
    const { error: failure } = await supabase.auth.verifyOtp({ email, token: code.replace(/\s/g, ''), type });
    if (failure) {
      setError(failure);
      setBusy(false);
      return;
    }
    onVerified();
  }

  return (
    <form onSubmit={submit} noValidate>
      <p className="field-hint" style={{ marginBottom: 16 }}>{t('auth.codeSent', { email })}</p>
      <div className="field">
        <label htmlFor="email-code">{t('auth.code')}</label>
        <input id="email-code" className="otp-input" inputMode="numeric" autoComplete="one-time-code" maxLength={7}
          value={code} onChange={(event) => setCode(event.target.value)} autoFocus />
      </div>
      <ErrorNotice error={error} />
      <div className="stack">
        <button type="submit" className="button-block" disabled={busy}>{t('auth.verify')}</button>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" className="button-quiet" onClick={onBack}>{t('auth.useOther')}</button>
          <button type="button" className="button-quiet" disabled={cooldown > 0 || busy}
            onClick={async () => {
              setError(null);
              try {
                await onResend();
                setCooldown(60);
              } catch (failure) {
                setError(failure);
              }
            }}>
            {t('auth.resend')}{cooldown > 0 ? ` (${cooldown})` : ''}
          </button>
        </div>
      </div>
    </form>
  );
}

/** Second step at sign-in: a code from the authenticator app. */
export function MfaChallenge({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const { t } = useI18n();
  const { refreshAssurance } = useAuth();
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { data, error: listError } = await supabase.auth.mfa.listFactors();
      if (listError) throw listError;
      const factor = data.totp.find((item) => item.status === 'verified');
      if (!factor) throw new Error('no factor');
      const { error: verifyError } = await supabase.auth.mfa.challengeAndVerify({ factorId: factor.id, code: code.replace(/\s/g, '') });
      if (verifyError) throw verifyError;
      await refreshAssurance();
      onDone();
    } catch (failure) {
      setError(failure);
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate data-testid="mfa-challenge">
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 8 }}>
        <Icon name="shield" />
        <h2>{t('auth.mfa.title')}</h2>
      </div>
      <p className="field-hint" style={{ marginBottom: 16 }}>{t('auth.mfa.text')}</p>
      <div className="field">
        <label htmlFor="mfa-code">{t('auth.code')}</label>
        <input id="mfa-code" className="otp-input" inputMode="numeric" autoComplete="one-time-code" maxLength={7}
          value={code} onChange={(event) => setCode(event.target.value)} autoFocus />
      </div>
      <ErrorNotice error={error} />
      <div className="stack">
        <button type="submit" className="button-block" disabled={busy || !isOtpCode(code)}>{t('auth.verify')}</button>
        <button type="button" className="button-quiet" onClick={onCancel}>{t('auth.signOut')}</button>
      </div>
    </form>
  );
}

export function methodLabelKey(method: Method) {
  return `auth.method.${method}` as const;
}
