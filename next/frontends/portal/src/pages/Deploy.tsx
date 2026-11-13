import { useEffect, useState, type FormEvent } from 'react';
import { Navigate } from 'react-router-dom';
import { useI18n, type MessageKey } from '../i18n';
import { useTenant } from '../components/AppShell';
import { ConfirmDialog, CopyButton, Dialog, ErrorNotice, Notice, Spinner, formatDate } from '../components/ui';
import { Icon } from '../components/Icon';
import { api, windowsInstaller, type DeployLink, type Group, type Installer } from '../lib/api';
import {
  browserOs, deployLinkState, isManager, isName, isWritable, linuxDeployCommand, macDeployCommand, windowsDeployCommand, type DeployOs,
} from '../lib/logic';
import { useLoad } from '../lib/useLoad';

const SYSTEMS: DeployOs[] = ['windows', 'macos', 'linux'];

function CreatedDialog({ token, onClose }: { token: string; onClose: () => void }) {
  const { t } = useI18n();
  const [installer, setInstaller] = useState<Installer | null | undefined>(undefined);
  const [os, setOs] = useState<DeployOs>(() => browserOs(typeof navigator === 'undefined' ? '' : navigator.userAgent));
  useEffect(() => {
    windowsInstaller().then(setInstaller);
  }, []);
  const command = os === 'macos' ? macDeployCommand(token) : os === 'linux' ? linuxDeployCommand(token)
    : installer ? windowsDeployCommand(token, installer.url) : '';
  return (
    <Dialog title={t('deploy.createdTitle')} onClose={onClose}>
      <Notice kind="warning"><p>{t('deploy.createdText')}</p></Notice>
      <label>{t('deploy.token')}</label>
      <div className="secret-box">
        <code data-testid="deploy-token">{token}</code>
        <CopyButton value={token} label={t('deploy.token')} />
      </div>
      <label id="deploy-os-label">{t('deploy.os')}</label>
      <div className="billing-toggle" role="group" aria-labelledby="deploy-os-label" data-testid="deploy-os">
        {SYSTEMS.map((value) => (
          <button key={value} type="button" aria-pressed={os === value} onClick={() => setOs(value)}>{t(`deploy.os.${value}` as MessageKey)}</button>
        ))}
      </div>
      <label>{t('deploy.command')}</label>
      {os === 'windows' && installer === undefined ? <Spinner /> : command ? (
        <>
          <div className="secret-box">
            <textarea readOnly value={command} data-testid="deploy-command" data-os={os} aria-label={t('deploy.command')} />
            <CopyButton value={command} label={t('deploy.command')} />
          </div>
          {os === 'windows' && <p className="field-hint">{t('deploy.steps')} (v{installer?.version})</p>}
          {os === 'macos' && <><p className="field-hint">{t('deploy.steps.macos')}</p><p className="field-hint">{t('deploy.macPermission')}</p></>}
          {os === 'linux' && <p className="field-hint">{t('deploy.steps.linux')}</p>}
          {os !== 'windows' && <p className="field-hint" data-testid="deploy-scripted">{t('deploy.scripted')}</p>}
        </>
      ) : <Notice><p>{t('deploy.commandMissing')}</p></Notice>}
      <div className="dialog-actions"><button type="button" onClick={onClose}>{t('common.close')}</button></div>
    </Dialog>
  );
}

export function Deploy() {
  const { t, locale } = useI18n();
  const { tenant, overview } = useTenant();
  const [label, setLabel] = useState('');
  const [days, setDays] = useState(30);
  const room = Math.max(0, overview.limits.devices - overview.usage.devices);
  const [computers, setComputers] = useState(Math.min(25, Math.max(1, room)));
  const [group, setGroup] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [invalid, setInvalid] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<DeployLink | null>(null);
  const links = useLoad(() => api.deployLinks(tenant.tenant_id), tenant.tenant_id);
  const groupsOn = !!overview.features.groups;
  const groups = useLoad(() => (groupsOn ? api.groups(tenant.tenant_id) : Promise.resolve([] as Group[])), `${tenant.tenant_id}|${groupsOn}`);
  const writable = isWritable(overview.status);

  if (!isManager(overview.role)) return <Navigate to="/app/devices" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!isName(label, 80) || !(days >= 1 && days <= 365) || !(computers >= 1 && computers <= 10000)) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    setBusy(true);
    setError(null);
    try {
      const token = await api.createDeployLink(tenant.tenant_id, label.trim(), days, computers, group || null);
      setCreated(token);
      setLabel('');
      await links.reload();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  const groupName = (id: string | null) => groups.data?.find((item) => item.group_id === id)?.name ?? '';

  return (
    <section>
      <div className="page-heading">
        <div>
          <h1>{t('deploy.title')}</h1>
          <p>{t('deploy.lead')}</p>
        </div>
      </div>
      <div className="panel">
        <div className="panel-heading"><div><h2>{t('deploy.new')}</h2><p>{t('deploy.room', { count: new Intl.NumberFormat(locale).format(room) })}</p></div></div>
        <form onSubmit={submit} noValidate data-testid="deploy-form">
          <div className="field">
            <label htmlFor="deploy-label">{t('deploy.label')}</label>
            <input id="deploy-label" value={label} maxLength={80} placeholder={t('deploy.labelPlaceholder')} onChange={(event) => setLabel(event.target.value)} />
          </div>
          <div className="form-row">
            <div className="field">
              <label htmlFor="deploy-days">{t('deploy.days')}</label>
              <input id="deploy-days" type="number" min={1} max={365} value={days} onChange={(event) => setDays(Number(event.target.value))} />
            </div>
            <div className="field">
              <label htmlFor="deploy-computers">{t('deploy.computers')}</label>
              <input id="deploy-computers" type="number" min={1} max={Math.max(1, room)} value={computers} onChange={(event) => setComputers(Number(event.target.value))} />
            </div>
            {groupsOn && (
              <div className="field">
                <label htmlFor="deploy-group">{t('deploy.group')}</label>
                <select id="deploy-group" value={group} onChange={(event) => setGroup(event.target.value)}>
                  <option value="">{t('devices.noGroup')}</option>
                  {groups.data?.map((item) => <option key={item.group_id} value={item.group_id}>{item.name}</option>)}
                </select>
              </div>
            )}
          </div>
          {invalid && <p className="field-error" role="alert">{t('deploy.invalid')}</p>}
          <ErrorNotice error={error} />
          <button type="submit" disabled={busy || !writable || room === 0}><Icon name="rocket" />{t('common.create')}</button>
        </form>
      </div>
      <div className="panel">
        <div className="panel-heading"><h2>{t('deploy.list')}</h2></div>
        <ErrorNotice error={links.error} onRetry={() => void links.reload()} />
        {links.loading && !links.data ? <Spinner /> : (links.data ?? []).length === 0 ? <p className="field-hint">{t('deploy.empty')}</p> : (
          <div className="table-wrap">
            <table className="table" data-testid="deploy-table">
              <thead>
                <tr>
                  <th>{t('deploy.label')}</th>
                  <th>{t('common.status')}</th>
                  <th>{t('deploy.uses')}</th>
                  <th className="hide-sm">{t('common.expires')}</th>
                  <th><span className="sr-only">{t('common.actions')}</span></th>
                </tr>
              </thead>
              <tbody>
                {links.data?.map((link) => {
                  const state = deployLinkState(link);
                  return (
                    <tr key={link.id} data-link={link.label}>
                      <td className="cell-main"><strong>{link.label}</strong><span>{groupName(link.group_id) || t('devices.noGroup')} · {formatDate(link.created_at, locale)}</span></td>
                      <td><span className={state === 'active' ? 'badge badge-success' : 'badge'}>{t(`deploy.state.${state}` as MessageKey)}</span></td>
                      <td className="conn-id">{link.uses} / {link.max_uses}</td>
                      <td className="hide-sm">{formatDate(link.expires_at, locale)}</td>
                      <td className="cell-actions">
                        {state === 'active' && <button type="button" className="button-danger button-small" onClick={() => setRevoking(link)}>{t('common.revoke')}</button>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {created && <CreatedDialog token={created} onClose={() => setCreated(null)} />}
      {revoking && (
        <ConfirmDialog title={t('deploy.revokeTitle', { label: revoking.label })} text={t('deploy.revokeText')} confirmLabel={t('common.revoke')}
          onClose={() => setRevoking(null)}
          onConfirm={async () => { await api.revokeDeployLink(tenant.tenant_id, revoking.id); await links.reload(); }} />
      )}
    </section>
  );
}
