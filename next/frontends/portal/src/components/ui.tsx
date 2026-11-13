import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { LANGS, useI18n, type MessageKey } from '../i18n';
import { useTheme, type ThemeChoice } from '../lib/theme';
import { errorKey, usage as usageOf } from '../lib/logic';
import { Icon } from './Icon';

export function Brand({ to = '/' }: { to?: string }) {
  return (
    <Link className="brand" to={to} aria-label="Mixel Remote">
      <img src="/mixel-icon.png" width={36} height={36} alt="" />
      <span>
        Mixel <strong>Remote</strong>
      </span>
    </Link>
  );
}

export function Spinner({ label }: { label?: string }) {
  const { t } = useI18n();
  return (
    <div className="center-fill" role="status" aria-live="polite">
      <div className="spinner" />
      <span className="sr-only">{label ?? t('common.loading')}</span>
    </div>
  );
}

export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useI18n();
  if (!error) return null;
  return (
    <div className="notice notice-error" role="alert">
      <Icon name="alert" />
      <p>{t(errorKey(error) as MessageKey)}</p>
      {onRetry && (
        <button type="button" className="button-quiet button-small" onClick={onRetry} style={{ marginLeft: 'auto' }}>
          {t('common.retry')}
        </button>
      )}
    </div>
  );
}

export function Notice({ kind = 'info', children }: { kind?: 'info' | 'brand' | 'success' | 'warning'; children: ReactNode }) {
  return (
    <div className={`notice${kind === 'info' ? '' : ' notice-' + kind}`} role={kind === 'success' ? 'status' : undefined}>
      <Icon name={kind === 'success' ? 'check' : kind === 'warning' ? 'alert' : 'info'} />
      <div>{children}</div>
    </div>
  );
}

export function Dialog({ title, children, onClose, labelledBy }: { title: string; children: ReactNode; onClose: () => void; labelledBy?: string }) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>('input, select, textarea, button:not(.dialog-close)');
    first?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      previous?.focus?.();
    };
  }, [onClose]);
  return (
    <div className="dialog-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={labelledBy ?? id} ref={ref}>
        <h2 id={labelledBy ?? id}>{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function ConfirmDialog({
  title, text, confirmLabel, onConfirm, onClose, danger = true,
}: { title: string; text: string; confirmLabel: string; onConfirm: () => Promise<unknown>; onClose: () => void; danger?: boolean }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  return (
    <Dialog title={title} onClose={onClose}>
      <p>{text}</p>
      <ErrorNotice error={error} />
      <div className="dialog-actions">
        <button type="button" className="secondary" onClick={onClose} disabled={busy}>
          {t('common.cancel')}
        </button>
        <button
          type="button"
          className={danger ? 'button-danger' : undefined}
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              await onConfirm();
              onClose();
            } catch (failure) {
              setError(failure);
              setBusy(false);
            }
          }}
        >
          {confirmLabel}
        </button>
      </div>
    </Dialog>
  );
}

export function CopyButton({ value, label }: { value: string; label?: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1800);
    return () => window.clearTimeout(timer);
  }, [copied]);
  return (
    <button
      type="button"
      className="secondary button-small"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
        } catch {
          setCopied(false);
        }
      }}
      aria-label={label ? `${t('common.copy')}: ${label}` : t('common.copy')}
    >
      <Icon name={copied ? 'check' : 'copy'} />
      {copied ? t('common.copied') : t('common.copy')}
    </button>
  );
}

export function Meter({ used, limit, label }: { used: number; limit: number; label: string }) {
  const value = usageOf(used, limit);
  return (
    <div className="meter" data-level={value.level} role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={value.limit} aria-valuenow={used}>
      <span style={{ width: `${value.percent}%` }} />
    </div>
  );
}

function useOutside(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) close();
    };
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && close();
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, close]);
  return ref;
}

/** Language and appearance in one compact menu. */
export function PreferencesMenu() {
  const { t, lang, setLang } = useI18n();
  const { choice, setChoice } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useOutside(open, () => setOpen(false));
  const themes: { code: ThemeChoice; key: MessageKey }[] = [
    { code: 'light', key: 'theme.light' },
    { code: 'dark', key: 'theme.dark' },
    { code: 'system', key: 'theme.system' },
  ];
  return (
    <div className="menu" ref={ref}>
      <button
        type="button"
        className="button-quiet"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={`${t('lang.label')} / ${t('theme.label')}`}
        data-testid="preferences"
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name="globe" />
        <span>{lang.toUpperCase()}</span>
      </button>
      {open && (
        <div className="menu-panel" role="menu">
          <div className="menu-title">{t('lang.label')}</div>
          {LANGS.map((item) => (
            <button key={item.code} type="button" role="menuitemradio" aria-checked={item.code === lang} lang={item.code}
              onClick={() => { setLang(item.code); setOpen(false); }}>
              {item.label}
            </button>
          ))}
          <hr />
          <div className="menu-title">{t('theme.label')}</div>
          {themes.map((item) => (
            <button key={item.code} type="button" role="menuitemradio" aria-checked={item.code === choice}
              onClick={() => { setChoice(item.code); setOpen(false); }}>
              <Icon name={item.code === 'dark' ? 'moon' : item.code === 'light' ? 'sun' : 'monitor'} />
              {t(item.key)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function Toast({ message, onDone }: { message: string | null; onDone: () => void }) {
  const done = useRef(onDone);
  useEffect(() => {
    done.current = onDone;
  });
  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => done.current(), 2600);
    return () => window.clearTimeout(timer);
  }, [message]);
  if (!message) return null;
  return <div className="toast" role="status">{message}</div>;
}

export function formatDate(value: string | null | undefined, locale: string, withTime = false): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat(locale, withTime
    ? { dateStyle: 'medium', timeStyle: 'short' }
    : { dateStyle: 'medium' }).format(date);
}
