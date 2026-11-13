import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useI18n } from '../i18n';
import { useTenant } from '../components/AppShell';
import { ConfirmDialog, CopyButton, ErrorNotice, Notice, Spinner, formatDate } from '../components/ui';
import { methodLabelKey, useAvailableMethods } from '../components/auth';
import { useAuth } from '../lib/auth';
import { api, type AuditEntry, type Domain } from '../lib/api';
import { ALL_METHODS, isCompanyName, isDomain, isManager, isWritable, methodsToSave, normalizeDomain, type Method } from '../lib/logic';
import { useLoad } from '../lib/useLoad';

function SignInSettings() {
  const { t } = useI18n();
  const { tenant, overview } = useTenant();
  const { assurance } = useAuth();
  const available = useAvailableMethods();
  const settings = useLoad(() => api.authSettings(tenant.tenant_id), tenant.tenant_id);
  const [chosen, setChosen] = useState<Set<Method>>(new Set());
  const [mfa, setMfa] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const manager = isManager(overview.role) && isWritable(overview.status);
  const mfaFeature = !!overview.features.require_mfa;

  useEffect(() => {
    if (!settings.data) return;
    setChosen(new Set(settings.data.methods));
    setMfa(settings.data.require_mfa);
  }, [settings.data]);

  if (!settings.data || !available) return <div className="panel"><ErrorNotice error={settings.error} /><Spinner /></div>;
  const stored = settings.data.methods;

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await api.setAuthSettings(tenant.tenant_id, methodsToSave(stored, chosen, available!), mfa);
      await settings.reload();
      setSaved(true);
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={save} noValidate data-testid="signin-settings">
      <div className="panel-heading"><div><h2>{t('settings.methods')}</h2><p>{t('settings.methodsLead')}</p></div></div>
      {!manager && <Notice><p>{t('settings.readOnlyRole')}</p></Notice>}
      <div className="check-list">
        {ALL_METHODS.map((method) => {
          const ready = available.has(method);
          return (
            <label key={method} className="check-row" data-method={method}>
              <input type="checkbox" disabled={!manager || !ready} checked={ready ? chosen.has(method) : false} onChange={(event) => {
                const next = new Set(chosen);
                if (event.target.checked) next.add(method);
                else next.delete(method);
                setChosen(next);
              }} />
              <div>
                <strong>{t(methodLabelKey(method))}</strong>
                {!ready && <> <span className="badge">{t('common.comingSoon')}</span></>}
                {method === 'sso' && <p>{t('settings.ssoNote')}</p>}
              </div>
            </label>
          );
        })}
      </div>
      <div className="check-row" style={{ borderTop: '1px solid var(--line)', marginTop: 8, paddingTop: 16 }}>
        <input id="require-mfa" type="checkbox" checked={mfa} disabled={!manager || !mfaFeature || (!mfa && assurance.current !== 'aal2')}
          onChange={(event) => setMfa(event.target.checked)} />
        <div>
          <label htmlFor="require-mfa"><strong>{t('settings.mfa')}</strong></label>
          <p>{t('settings.mfaText')}</p>
          {!mfaFeature && <p>{t('settings.mfaNeedsBusiness')}</p>}
          {mfaFeature && assurance.current !== 'aal2' && <p>{t('settings.mfaSetupFirst')} <Link to="/app/account">{t('settings.mfaSetupLink')}</Link></p>}
        </div>
      </div>
      <ErrorNotice error={error} />
      {saved && <Notice kind="success"><p>{t('common.saved')}</p></Notice>}
      {manager && <button type="submit" disabled={busy}>{t('common.save')}</button>}
    </form>
  );
}

function Domains() {
  const { t } = useI18n();
  const { tenant, overview } = useTenant();
  const domains = useLoad(() => api.domains(tenant.tenant_id), tenant.tenant_id);
  const [value, setValue] = useState('');
  const [invalid, setInvalid] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<Domain | null>(null);
  const writable = isWritable(overview.status);
  return (
    <div className="panel" data-testid="domains">
      <div className="panel-heading"><div><h2>{t('settings.domains')}</h2><p>{t('settings.domainsLead')}</p></div></div>
      <form className="inline-form" noValidate onSubmit={async (event) => {
        event.preventDefault();
        if (!isDomain(value)) {
          setInvalid(true);
          return;
        }
        setInvalid(false);
        setBusy(true);
        setError(null);
        try {
          await api.addDomain(tenant.tenant_id, normalizeDomain(value));
          setValue('');
          await domains.reload();
        } catch (failure) {
          setError(failure);
        } finally {
          setBusy(false);
        }
      }}>
        <div className="field">
          <label htmlFor="domain-input">{t('settings.domainAdd')}</label>
          <input id="domain-input" placeholder={t('settings.domainPlaceholder')} value={value} onChange={(event) => setValue(event.target.value)} />
        </div>
        <button type="submit" disabled={busy || !writable}>{t('settings.domainAdd')}</button>
      </form>
      {invalid && <p className="field-error" role="alert">{t('settings.domainInvalid')}</p>}
      <ErrorNotice error={error ?? domains.error} />
      {(domains.data ?? []).length === 0 ? <p className="field-hint" style={{ marginTop: 14 }}>{t('settings.domainEmpty')}</p> : (
        <div className="check-list" style={{ marginTop: 14 }}>
          {domains.data?.map((domain) => (
            <div key={domain.domain} className="check-row" data-domain={domain.domain}>
              <div style={{ minWidth: 0 }}>
                <strong>{domain.domain}</strong>{' '}
                {domain.verified_at ? <span className="badge badge-success">{t('settings.domainVerified')}</span> : <span className="badge badge-warning">{t('settings.domainPending')}</span>}
                {!domain.verified_at && (
                  <>
                    <p>{t('settings.domainRecord', { host: `_mixel-remote.${domain.domain}` })}</p>
                    <div className="secret-box"><code>{domain.verification_token}</code><CopyButton value={domain.verification_token} /></div>
                  </>
                )}
              </div>
              <button type="button" className="button-quiet button-small" onClick={() => setRemoving(domain)}>{t('common.remove')}</button>
            </div>
          ))}
        </div>
      )}
      {removing && (
        <ConfirmDialog title={t('settings.domainRemoveTitle', { domain: removing.domain })} text="" confirmLabel={t('common.remove')}
          onClose={() => setRemoving(null)}
          onConfirm={async () => { await api.removeDomain(tenant.tenant_id, removing.domain); await domains.reload(); }} />
      )}
    </div>
  );
}

function AuditLog() {
  const { t, locale } = useI18n();
  const { tenant } = useTenant();
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  async function load(before: number | null) {
    setLoading(true);
    try {
      const page = await api.auditLog(tenant.tenant_id, before);
      setEntries((current) => (before === null ? page : [...current, ...page]));
      setDone(page.length < 200);
      setError(null);
    } catch (failure) {
      setError(failure);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenant.tenant_id]);

  const detail = (entry: AuditEntry) => {
    const parts = Object.entries(entry.detail ?? {}).filter(([, value]) => value !== null && value !== '')
      .map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`);
    return [entry.target, ...parts].filter(Boolean).join(' · ');
  };

  return (
    <div className="panel" data-testid="audit-log">
      <div className="panel-heading"><div><h2>{t('settings.audit')}</h2><p>{t('settings.auditLead')}</p></div></div>
      <ErrorNotice error={error} />
      {loading && entries.length === 0 ? <Spinner /> : entries.length === 0 ? <p className="field-hint">{t('settings.auditEmpty')}</p> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>{t('settings.auditWhen')}</th><th>{t('settings.auditWho')}</th><th>{t('settings.auditWhat')}</th><th className="hide-sm">{t('settings.auditTarget')}</th></tr></thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td style={{ whiteSpace: 'nowrap' }}>{formatDate(entry.occurred_at, locale, true)}</td>
                  <td>{entry.actor_name ?? '—'}</td>
                  <td><code>{entry.action}</code></td>
                  <td className="hide-sm" style={{ color: 'var(--muted)', fontSize: 13, overflowWrap: 'anywhere' }}>{detail(entry)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!done && entries.length > 0 && (
        <button type="button" className="secondary" style={{ marginTop: 12 }} disabled={loading} onClick={() => void load(entries[entries.length - 1].id)}>{t('common.loadMore')}</button>
      )}
    </div>
  );
}

function CompanyName() {
  const { t } = useI18n();
  const { tenant, overview, reload } = useTenant();
  const editable = isManager(overview.role) && isWritable(overview.status);
  const [name, setName] = useState(overview.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => setName(overview.name), [overview.name]);
  if (!editable) return <dd data-testid="settings-company">{overview.name}</dd>;
  return (
    <dd>
      <form className="inline-form" noValidate data-testid="company-rename" onSubmit={async (event) => {
        event.preventDefault();
        setSaved(false);
        if (!isCompanyName(name)) {
          setError({ message: 'Enter a company name of 2 to 80 characters' });
          return;
        }
        setBusy(true);
        setError(null);
        try {
          await api.renameTenant(tenant.tenant_id, name.trim());
          await reload();
          setSaved(true);
        } catch (failure) {
          setError(failure);
        } finally {
          setBusy(false);
        }
      }}>
        <div className="field">
          <label htmlFor="company-name-input" className="sr-only">{t('settings.companyName')}</label>
          <input id="company-name-input" data-testid="settings-company" value={name} maxLength={80} onChange={(event) => { setName(event.target.value); setSaved(false); }} />
        </div>
        <button type="submit" className="secondary" disabled={busy || name.trim() === overview.name}>{t('common.rename')}</button>
      </form>
      {error ? <p className="field-error" role="alert">{t('signup.companyInvalid')}</p> : null}
      {saved && <p className="field-hint" role="status">{t('settings.renamed')}</p>}
    </dd>
  );
}

export function Settings() {
  const { t } = useI18n();
  const { overview } = useTenant();
  const manager = isManager(overview.role);
  const signInUrl = `${window.location.origin}/signin?company=${encodeURIComponent(overview.slug)}`;
  return (
    <section>
      <div className="page-heading"><div><h1>{t('settings.title')}</h1></div></div>
      <div className="panel">
        <div className="panel-heading"><h2>{t('settings.company')}</h2></div>
        <dl className="kv">
          <dt>{t('settings.companyName')}</dt><CompanyName />
          <dt>{t('settings.companyId')}</dt><dd><code>{overview.slug}</code></dd>
        </dl>
        <p className="field-hint" style={{ marginTop: 10 }}>{t('settings.companyNameNote')}</p>
        <label style={{ display: 'block', marginTop: 16 }}>{t('settings.signInLink')}</label>
        <div className="secret-box"><code>{signInUrl}</code><CopyButton value={signInUrl} /></div>
      </div>
      <SignInSettings />
      {manager && <Domains />}
      {manager && (overview.features.audit_log ? <AuditLog /> : (
        <div className="panel"><h2>{t('settings.audit')}</h2><Notice kind="brand"><p>{t('settings.auditNeedsBusiness')}</p></Notice></div>
      ))}
    </section>
  );
}
