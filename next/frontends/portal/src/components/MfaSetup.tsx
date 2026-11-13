import { useState, type FormEvent } from 'react';
import { supabase } from '../lib/supabase';
import { useAuth } from '../lib/auth';
import { isOtpCode } from '../lib/logic';
import { useI18n } from '../i18n';
import { CopyButton, ErrorNotice } from './ui';

interface Enrollment { id: string; qr: string; secret: string }

/** Set up an authenticator app (TOTP) for the signed-in account and verify it once. */
export function MfaSetup({ onDone }: { onDone: () => void }) {
  const { t } = useI18n();
  const { refreshAssurance } = useAuth();
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      // Unfinished set-ups from earlier attempts would block a new one.
      const { data: factors } = await supabase.auth.mfa.listFactors();
      for (const factor of factors?.all ?? []) {
        if (factor.status !== 'verified') await supabase.auth.mfa.unenroll({ factorId: factor.id });
      }
      const { data, error: failure } = await supabase.auth.mfa.enroll({
        factorType: 'totp',
        issuer: 'Mixel Remote',
        friendlyName: `Mixel Remote ${new Date().toISOString().slice(0, 16)}`,
      });
      if (failure) throw failure;
      setEnrollment({ id: data.id, qr: data.totp.qr_code, secret: data.totp.secret });
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  async function verify(event: FormEvent) {
    event.preventDefault();
    if (!enrollment) return;
    setBusy(true);
    setError(null);
    const { error: failure } = await supabase.auth.mfa.challengeAndVerify({ factorId: enrollment.id, code: code.replace(/\s/g, '') });
    if (failure) {
      setError(failure);
      setBusy(false);
      return;
    }
    await refreshAssurance();
    setBusy(false);
    onDone();
  }

  if (!enrollment) {
    return (
      <div>
        <ErrorNotice error={error} />
        <button type="button" onClick={start} disabled={busy} data-testid="mfa-start">{t('account.mfaSetup')}</button>
      </div>
    );
  }
  return (
    <form onSubmit={verify} noValidate data-testid="mfa-setup">
      <p className="field-hint">{t('account.mfaScan')}</p>
      <div className="mfa-setup" style={{ marginTop: 12 }}>
        <div className="qr"><img src={enrollment.qr} alt="QR code" /></div>
        <div style={{ flex: '1 1 220px', minWidth: 0 }}>
          <label>{t('account.mfaKey')}</label>
          <div className="secret-box">
            <code data-testid="mfa-secret">{enrollment.secret}</code>
            <CopyButton value={enrollment.secret} label={t('account.mfaKey')} />
          </div>
          <div className="field">
            <label htmlFor="mfa-enroll-code">{t('auth.code')}</label>
            <input id="mfa-enroll-code" className="otp-input" inputMode="numeric" autoComplete="one-time-code" maxLength={7}
              value={code} onChange={(event) => setCode(event.target.value)} />
          </div>
          <ErrorNotice error={error} />
          <button type="submit" disabled={busy || !isOtpCode(code)}>{t('account.mfaActivate')}</button>
        </div>
      </div>
    </form>
  );
}
