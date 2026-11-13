// Sign-in building blocks shared by sign-in, sign-up and the invitation page.
import { useEffect, useState, type FormEvent } from 'react';
import { supabase } from '../lib/supabase';
import { goTrueSettings } from '../lib/api';
import { availableMethods, isOtpCode, type Method } from '../lib/logic';
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

export async function startMixelId(next: string): Promise<void> {
  const { error } = await supabase.auth.signInWithOAuth({
    provider: 'keycloak',
    options: { redirectTo: callbackUrl(next), scopes: 'openid email profile' },
  });
  if (error) throw error;
}

export function MixelIdButton({ next, onBefore }: { next: string; onBefore?: () => boolean }) {
  const { t } = useI18n();
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return (
    <>
      <button
        type="button"
        className="secondary button-block"
        disabled={busy}
        data-testid="mixel-id"
        onClick={async () => {
          if (onBefore && !onBefore()) return;
          setBusy(true);
          setError(null);
          try {
            await startMixelId(next);
          } catch (failure) {
            setError(failure);
            setBusy(false);
          }
        }}
      >
        <img src="/mixel-icon.png" width={20} height={20} alt="" style={{ borderRadius: '50%', background: '#fff' }} />
        {t('auth.withMixelId')}
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
