import { useState } from 'react';
import { useI18n } from '../i18n';
import { useTenant } from '../components/AppShell';
import { ConfirmDialog, Dialog, ErrorNotice, Notice, Spinner } from '../components/ui';
import { Icon } from '../components/Icon';
import { api, type Group, type Member } from '../lib/api';
import { isManager, isName, isWritable } from '../lib/logic';
import { useLoad } from '../lib/useLoad';

function NameDialog({ title, initial, onSave, onClose }: { title: string; initial: string; onSave: (name: string) => Promise<unknown>; onClose: () => void }) {
  const { t } = useI18n();
  const [name, setName] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  return (
    <Dialog title={title} onClose={onClose}>
      <form noValidate onSubmit={async (event) => {
        event.preventDefault();
        if (!isName(name, 60)) {
          setError({ message: 'Enter a group name' });
          return;
        }
        setBusy(true);
        try {
          await onSave(name.trim());
          onClose();
        } catch (failure) {
          setError(failure);
          setBusy(false);
        }
      }}>
        <div className="field">
          <label htmlFor="group-name">{t('groups.name')}</label>
          <input id="group-name" value={name} maxLength={60} onChange={(event) => setName(event.target.value)} />
        </div>
        <ErrorNotice error={error} />
        <div className="dialog-actions">
          <button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button>
          <button type="submit" disabled={busy}>{t('common.save')}</button>
        </div>
      </form>
    </Dialog>
  );
}

function AccessDialog({ group, technicians, onSave, onClose }: { group: Group; technicians: Member[]; onSave: (ids: string[]) => Promise<unknown>; onClose: () => void }) {
  const { t } = useI18n();
  const [chosen, setChosen] = useState<Set<string>>(new Set(group.technicians));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  return (
    <Dialog title={t('groups.accessTitle', { name: group.name })} onClose={onClose}>
      <p>{t('groups.accessText')}</p>
      {technicians.length === 0 ? <Notice><p>{t('groups.noTechniciansInTeam')}</p></Notice> : (
        <div className="check-list" style={{ margin: '8px 0' }}>
          {technicians.map((member) => (
            <label key={member.user_id} className="check-row">
              <input type="checkbox" checked={chosen.has(member.user_id)} onChange={(event) => {
                const next = new Set(chosen);
                if (event.target.checked) next.add(member.user_id);
                else next.delete(member.user_id);
                setChosen(next);
              }} />
              <div><strong>{member.name}</strong><p>{member.email}</p></div>
            </label>
          ))}
        </div>
      )}
      <ErrorNotice error={error} />
      <div className="dialog-actions">
        <button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button>
        <button type="button" disabled={busy || technicians.length === 0} onClick={async () => {
          setBusy(true);
          try {
            await onSave([...chosen]);
            onClose();
          } catch (failure) {
            setError(failure);
            setBusy(false);
          }
        }}>{t('common.save')}</button>
      </div>
    </Dialog>
  );
}

type Action = { kind: 'create' } | { kind: 'rename' | 'delete' | 'access'; group: Group } | null;

export function Groups() {
  const { t, locale } = useI18n();
  const { tenant, overview } = useTenant();
  const [action, setAction] = useState<Action>(null);
  const manager = isManager(overview.role);
  const writable = isWritable(overview.status);
  const groups = useLoad(() => api.groups(tenant.tenant_id), tenant.tenant_id);
  const members = useLoad(() => api.members(tenant.tenant_id), tenant.tenant_id);
  const technicians = (members.data ?? []).filter((member) => member.role === 'technician');
  const nameOf = (id: string) => members.data?.find((member) => member.user_id === id)?.name ?? id.slice(0, 8);

  if (!overview.features.groups) {
    return <section><h1>{t('groups.title')}</h1><Notice kind="brand"><p>{t('groups.needTeam')}</p></Notice></section>;
  }
  return (
    <section>
      <div className="page-heading">
        <div>
          <h1>{t('groups.title')}</h1>
          <p>{t('groups.lead')}</p>
        </div>
        {manager && <button type="button" disabled={!writable} onClick={() => setAction({ kind: 'create' })}><Icon name="plus" />{t('groups.new')}</button>}
      </div>
      <div className="panel">
        <ErrorNotice error={groups.error} onRetry={() => void groups.reload()} />
        {groups.loading && !groups.data ? <Spinner /> : (groups.data ?? []).length === 0 ? (
          <div className="empty"><Icon name="folder" /><h3>{t('groups.empty')}</h3></div>
        ) : (
          <div className="table-wrap">
            <table className="table" data-testid="groups-table">
              <thead>
                <tr>
                  <th>{t('groups.name')}</th>
                  <th className="hide-sm">{t('groups.technicians')}</th>
                  {manager && <th><span className="sr-only">{t('common.actions')}</span></th>}
                </tr>
              </thead>
              <tbody>
                {groups.data?.map((group) => (
                  <tr key={group.group_id} data-group={group.name}>
                    <td className="cell-main">
                      <strong>{group.name}</strong>
                      <span>{t('groups.devices', { count: new Intl.NumberFormat(locale).format(group.devices) })}</span>
                    </td>
                    <td className="hide-sm">
                      {group.technicians.length === 0 ? <span className="field-hint">{t('groups.noTechnicians')}</span>
                        : group.technicians.map((id) => <span key={id} className="badge" style={{ marginRight: 4 }}>{nameOf(id)}</span>)}
                    </td>
                    {manager && (
                      <td className="cell-actions">
                        <button type="button" className="button-quiet button-small" disabled={!writable || !overview.features.access_rights} onClick={() => setAction({ kind: 'access', group })}>{t('groups.editAccess')}</button>
                        <button type="button" className="button-quiet button-small" disabled={!writable} onClick={() => setAction({ kind: 'rename', group })}>{t('common.rename')}</button>
                        <button type="button" className="button-danger button-small" onClick={() => setAction({ kind: 'delete', group })}>{t('groups.delete')}</button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {action?.kind === 'create' && <NameDialog title={t('groups.new')} initial="" onClose={() => setAction(null)}
        onSave={async (name) => { await api.createGroup(tenant.tenant_id, name); await groups.reload(); }} />}
      {action?.kind === 'rename' && <NameDialog title={t('common.rename')} initial={action.group.name} onClose={() => setAction(null)}
        onSave={async (name) => { await api.renameGroup(tenant.tenant_id, action.group.group_id, name); await groups.reload(); }} />}
      {action?.kind === 'access' && <AccessDialog group={action.group} technicians={technicians} onClose={() => setAction(null)}
        onSave={async (ids) => { await api.setGroupAccess(tenant.tenant_id, action.group.group_id, ids); await groups.reload(); }} />}
      {action?.kind === 'delete' && (
        <ConfirmDialog title={t('groups.deleteTitle', { name: action.group.name })} text={t('groups.deleteText')} confirmLabel={t('groups.delete')}
          onClose={() => setAction(null)}
          onConfirm={async () => { await api.deleteGroup(tenant.tenant_id, action.group.group_id); await groups.reload(); }} />
      )}
    </section>
  );
}
