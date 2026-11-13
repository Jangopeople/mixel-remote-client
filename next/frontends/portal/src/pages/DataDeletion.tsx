// Settings › Data and deletion (owners): export the company's data as JSON or CSV, and delete the
// company. Settings › Terms: the accepted version of the Terms of Service. Account: delete my account.
// Also the page shown after a deletion (/deleted).
import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useI18n, type MessageKey } from '../i18n';
import { useTenant } from '../components/AppShell';
import { ErrorNotice, Notice, Spinner, formatDate } from '../components/ui';
import { TypeToConfirmDialog } from '../components/legal';
import { Icon } from '../components/Icon';
import { PublicLayout } from '../components/PublicLayout';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { supabase } from '../lib/supabase';
import { CSV_COLUMNS, download, exportFileName, toCsv, type CsvTable } from '../lib/exportData';
import { useLoad } from '../lib/useLoad';
import { LEGAL_VERSION } from '../legal/types';

const TABLES: CsvTable[] = ['members', 'devices', 'groups', 'invitations', 'audit_log'];

/** Ends the local session after the account is gone (the server no longer knows it). */
async function leave() {
  await supabase.auth.signOut({ scope: 'local' }).catch(() => undefined);
}

export function DataAndDeletion() {
  const { t } = useI18n();
  const { tenant, overview } = useTenant();
  const navigate = useNavigate();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  if (overview.role !== 'owner') {
    return <div className="panel" data-testid="data-panel"><h2>{t('data.title')}</h2><p className="field-hint">{t('data.ownerOnly')}</p></div>;
  }

  async function run(what: 'json' | CsvTable) {
    setBusy(what);
    setError(null);
    setDone(null);
    try {
      const data = await api.exportCompany(tenant.tenant_id);
      const name = what === 'json' ? exportFileName(data, 'export', 'json') : exportFileName(data, what.replace('_', '-'), 'csv');
      if (what === 'json') download(name, JSON.stringify(data, null, 2), 'application/json');
      else download(name, toCsv(data[what] as Record<string, unknown>[], CSV_COLUMNS[what]), 'text/csv;charset=utf-8');
      setDone(name);
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="panel danger-zone" data-testid="data-panel">
      <div className="panel-heading"><div><h2>{t('data.title')}</h2><p>{t('data.lead')}</p></div></div>
      <h3 style={{ fontSize: 15, margin: '0 0 6px' }}>{t('data.export')}</h3>
      <p className="field-hint" style={{ marginBottom: 12 }}>{t('data.exportText')}</p>
      <div className="inline-form" style={{ alignItems: 'center' }}>
        <button type="button" onClick={() => void run('json')} disabled={!!busy} data-testid="export-json"><Icon name="download" />{t('data.json')}</button>
        {TABLES.map((table) => (
          <button key={table} type="button" className="secondary button-small" onClick={() => void run(table)} disabled={!!busy} data-testid={`export-${table}`}>
            {t('data.csv')}: {t(`data.table.${table}` as MessageKey)}
          </button>
        ))}
        {busy && <Spinner />}
      </div>
      {done && <Notice kind="success"><p data-testid="export-done">{t('data.exported', { file: done })}</p></Notice>}
      <ErrorNotice error={error} />
      <h3 style={{ fontSize: 15, margin: '24px 0 6px' }}>{t('data.deleteTitle')}</h3>
      <p className="field-hint" style={{ marginBottom: 12 }}>{t('data.deleteText', { company: overview.name })}</p>
      <button type="button" className="button-danger" onClick={() => setDeleting(true)} data-testid="delete-company">{t('data.deleteButton')}</button>
      {deleting && (
        <TypeToConfirmDialog
          title={t('data.deleteTitle')}
          text={t('data.deleteText', { company: overview.name })}
          label={t('data.deleteConfirmLabel', { company: overview.name })}
          expected={overview.name}
          confirmLabel={t('data.deleteConfirm')}
          onClose={() => setDeleting(false)}
          onConfirm={async () => {
            const result = await api.deleteCompany(tenant.tenant_id, overview.name);
            await leave();
            navigate(`/deleted?kind=company&devices=${result.devices_removed}&members=${result.members_removed}`, { replace: true });
          }}>
          <p className="field-hint">{t('data.exportFirst')}</p>
        </TypeToConfirmDialog>
      )}
    </div>
  );
}

export function TermsPanel() {
  const { t, locale } = useI18n();
  const { tenant, overview } = useTenant();
  const status = useLoad(() => api.termsStatus(tenant.tenant_id), tenant.tenant_id);
  const [error, setError] = useState<unknown>(null);
  const manager = overview.role === 'owner' || overview.role === 'admin';
  const data = status.data;
  return (
    <div className="panel" id="terms" data-testid="terms-panel">
      <div className="panel-heading"><div><h2>{t('terms.title')}</h2></div></div>
      {!data ? <><ErrorNotice error={status.error} /><Spinner /></> : (
        <>
          {data.accepted_at && (
            <p data-testid="terms-accepted">{t('terms.acceptedOn', { date: formatDate(data.accepted_at, locale, true), email: data.accepted_by ?? '—', version: data.accepted_version ?? '' })}</p>
          )}
          {!data.up_to_date && manager && (
            <>
              <Notice kind="warning"><p>{t('terms.dialogText', { version: data.current_version, company: overview.name })}</p></Notice>
              <ErrorNotice error={error} />
              <button type="button" onClick={async () => {
                setError(null);
                try {
                  await api.acceptTerms(tenant.tenant_id, LEGAL_VERSION);
                  await status.reload();
                } catch (failure) {
                  setError(failure);
                }
              }}>{t('terms.accept')}</button>
            </>
          )}
          <p className="field-hint" style={{ marginTop: 12 }}>
            <Link to="/legal/terms">{t('footer.terms')}</Link> · <Link to="/legal/dpa">{t('footer.dpa')}</Link> · <Link to="/legal/privacy">{t('footer.privacy')}</Link> · <Link to="/legal/subprocessors">{t('footer.subprocessors')}</Link>
          </p>
        </>
      )}
    </div>
  );
}

/** Account page and the "no company" screen: a person deletes their own account. */
export function DeleteMyAccount({ company, soleOwner }: { company: string | null; soleOwner: boolean }) {
  const { t } = useI18n();
  const { session } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const email = session?.user.email ?? '';
  return (
    <div className="panel danger-zone" data-testid="delete-account-panel">
      <h2>{t('account.deleteTitle')}</h2>
      <p className="field-hint" style={{ margin: '8px 0 12px' }}>{t('account.deleteText')}</p>
      {soleOwner && company ? (
        <Notice kind="warning"><p data-testid="sole-owner">{t('account.deleteOwner', { company })}</p></Notice>
      ) : (
        <button type="button" className="button-danger" onClick={() => setOpen(true)} data-testid="delete-account">{t('account.deleteButton')}</button>
      )}
      {open && (
        <TypeToConfirmDialog
          title={t('account.deleteTitle')}
          text={t('account.deleteText')}
          label={t('account.deleteConfirmLabel', { email })}
          expected={email}
          confirmLabel={t('account.deleteConfirm')}
          onClose={() => setOpen(false)}
          onConfirm={async () => {
            await api.deleteMyAccount(email);
            await leave();
            navigate('/deleted?kind=account', { replace: true });
          }} />
      )}
    </div>
  );
}

export function Deleted() {
  const { t } = useI18n();
  const [params] = useSearchParams();
  const company = params.get('kind') === 'company';
  const count = (name: string) => Math.max(0, Number(params.get(name) ?? 0) || 0);
  return (
    <PublicLayout>
      <div className="container legal-page" data-testid="deleted-page">
        <h1>{company ? t('deleted.companyTitle') : t('deleted.accountTitle')}</h1>
        <p className="lead">{company ? t('deleted.companyText', { devices: count('devices'), members: count('members') }) : t('deleted.accountText')}</p>
        <Link className="button" to="/">{t('notFound.home')}</Link>
      </div>
    </PublicLayout>
  );
}
