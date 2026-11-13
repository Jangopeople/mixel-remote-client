// Legal building blocks: the terms checkbox of the sign-up, the legal links of the footers, and
// the "type to confirm" dialog used for deleting a company or an account.
import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useI18n } from '../i18n';
import { confirmMatches, linkParts } from '../lib/logic';
import { Dialog, ErrorNotice } from './ui';

const TERMS_LINKS = ['/legal/terms', '/legal/dpa', '/legal/privacy'];

export function TermsCheckbox({ checked, onChange, error }: { checked: boolean; onChange: (value: boolean) => void; error: boolean }) {
  const { t } = useI18n();
  let link = 0;
  return (
    <>
      <label className="terms-check" data-testid="terms-check">
        <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} aria-invalid={error} />
        <span>
          {linkParts(t('signup.terms')).map((part, index) => part.link
            ? <Link key={index} to={TERMS_LINKS[link++] ?? '/legal'} target="_blank" rel="noopener">{part.text}</Link>
            : <span key={index}>{part.text}</span>)}
          {' '}<span className="field-hint" style={{ display: 'inline' }}>{t('signup.termsDraft')}</span>
        </span>
      </label>
      {error && <p className="field-error" style={{ marginTop: -10, marginBottom: 14 }}>{t('signup.termsRequired')}</p>}
    </>
  );
}

export function LegalLinks() {
  const { t } = useI18n();
  return (
    <>
      <Link to="/legal/terms">{t('footer.terms')}</Link>
      <Link to="/legal/privacy">{t('footer.privacy')}</Link>
      <Link to="/legal/dpa">{t('footer.dpa')}</Link>
      <Link to="/legal/subprocessors">{t('footer.subprocessors')}</Link>
    </>
  );
}

export function TypeToConfirmDialog({
  title, text, label, expected, confirmLabel, onConfirm, onClose, children,
}: {
  title: string; text: string; label: string; expected: string; confirmLabel: string;
  onConfirm: () => Promise<unknown>; onClose: () => void; children?: ReactNode;
}) {
  const { t } = useI18n();
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const ok = confirmMatches(typed, expected);
  return (
    <Dialog title={title} onClose={busy ? () => undefined : onClose}>
      <p>{text}</p>
      {children}
      <div className="field">
        <label htmlFor="type-to-confirm">{label}</label>
        <input id="type-to-confirm" value={typed} autoComplete="off" spellCheck={false} onChange={(event) => setTyped(event.target.value)} data-testid="type-to-confirm" />
      </div>
      <ErrorNotice error={error} />
      <div className="dialog-actions">
        <button type="button" className="secondary" onClick={onClose} disabled={busy}>{t('common.cancel')}</button>
        <button type="button" className="button-danger" disabled={!ok || busy} data-testid="confirm-delete" onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await onConfirm();
          } catch (failure) {
            setError(failure);
            setBusy(false);
          }
        }}>{confirmLabel}</button>
      </div>
    </Dialog>
  );
}
