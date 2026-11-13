import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useI18n, type MessageKey } from '../i18n';
import { useTenant } from '../components/AppShell';
import { ConfirmDialog, CopyButton, ErrorNotice, Notice, Spinner, formatDate } from '../components/ui';
import { Icon } from '../components/Icon';
import { useAuth } from '../lib/auth';
import { api, type Invite, type Member } from '../lib/api';
import {
  ROLE_ORDER, assignableRoles, canRemoveMember, inviteLink, isEmail, isManager, isWritable, normalizeEmail, type Role,
} from '../lib/logic';
import { useLoad } from '../lib/useLoad';

export function Team() {
  const { t, locale } = useI18n();
  const navigate = useNavigate();
  const { session } = useAuth();
  const { tenant, overview, reload: reloadTenant } = useTenant();
  const manager = isManager(overview.role);
  const writable = isWritable(overview.status);
  const members = useLoad(() => api.members(tenant.tenant_id), tenant.tenant_id);
  const invites = useLoad(() => (manager ? api.invites(tenant.tenant_id) : Promise.resolve([] as Invite[])), `${tenant.tenant_id}|${manager}`);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<Role>('technician');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [invalid, setInvalid] = useState(false);
  const [created, setCreated] = useState<{ email: string; link: string } | null>(null);
  const [removing, setRemoving] = useState<Member | null>(null);
  const [revoking, setRevoking] = useState<Invite | null>(null);
  const [rowError, setRowError] = useState<unknown>(null);

  const list = members.data ?? [];
  const me = list.find((member) => member.email.toLowerCase() === (session?.user.email ?? '').toLowerCase());
  const owners = list.filter((member) => member.role === 'owner').length;

  async function invite(event: FormEvent) {
    event.preventDefault();
    if (!isEmail(email)) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    setBusy(true);
    setError(null);
    try {
      const clean = normalizeEmail(email);
      const token = await api.invite(tenant.tenant_id, clean, role);
      setCreated({ email: clean, link: inviteLink(window.location.origin, token) });
      setEmail('');
      await invites.reload();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  async function changeRole(member: Member, next: Role) {
    setRowError(null);
    try {
      await api.setMemberRole(tenant.tenant_id, member.user_id, next);
      await Promise.all([members.reload(), reloadTenant()]);
    } catch (failure) {
      setRowError(failure);
    }
  }

  return (
    <section>
      <div className="page-heading">
        <div>
          <h1>{t('team.title')}</h1>
          <p>{t('team.lead')} {t('team.techniciansUsed', { used: overview.usage.technicians, limit: overview.limits.technicians })}</p>
        </div>
      </div>

      {manager ? (
        <div className="panel">
          <div className="panel-heading"><h2>{t('team.invite')}</h2></div>
          <form className="inline-form" onSubmit={invite} noValidate data-testid="invite-form">
            <div className="field">
              <label htmlFor="invite-member-email">{t('team.inviteEmail')}</label>
              <input id="invite-member-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} />
            </div>
            <div className="field" style={{ flex: '0 1 200px' }}>
              <label htmlFor="invite-member-role">{t('team.inviteRole')}</label>
              <select id="invite-member-role" value={role} onChange={(event) => setRole(event.target.value as Role)}>
                {(['admin', 'technician', 'member'] as Role[]).map((item) => <option key={item} value={item}>{t(`role.${item}` as MessageKey)}</option>)}
              </select>
            </div>
            <button type="submit" disabled={busy || !writable}><Icon name="mail" />{t('team.inviteSend')}</button>
          </form>
          <p className="field-hint" style={{ marginTop: 10 }}>{t(`role.${role}.text` as MessageKey)}</p>
          {invalid && <p className="field-error" role="alert">{t('auth.emailInvalid')}</p>}
          <ErrorNotice error={error} />
          {created && (
            <div style={{ marginTop: 16 }} data-testid="invite-created">
              <Notice kind="success"><p><strong>{t('team.inviteCreated', { email: created.email })}</strong></p><p>{t('team.inviteLinkText', { email: created.email })}</p></Notice>
              <div className="secret-box">
                <code data-testid="invite-link">{created.link}</code>
                <CopyButton value={created.link} />
              </div>
            </div>
          )}
        </div>
      ) : <Notice><p>{t('team.readOnlyRole')}</p></Notice>}

      <div className="panel">
        <div className="panel-heading"><h2>{t('team.members')}</h2></div>
        <ErrorNotice error={members.error ?? rowError} onRetry={members.error ? () => void members.reload() : undefined} />
        {members.loading && !members.data ? <Spinner /> : (
          <div className="table-wrap">
            <table className="table" data-testid="members-table">
              <thead>
                <tr>
                  <th>{t('common.name')}</th>
                  <th>{t('common.role')}</th>
                  <th className="hide-sm">{t('team.joined')}</th>
                  <th><span className="sr-only">{t('common.actions')}</span></th>
                </tr>
              </thead>
              <tbody>
                {list.map((member) => {
                  const roles = me ? assignableRoles(overview.role, member.role) : [];
                  const isMe = member.user_id === me?.user_id;
                  return (
                    <tr key={member.user_id} data-member={member.email}>
                      <td className="cell-main"><strong>{member.name}{isMe ? ` (${t('common.you')})` : ''}</strong><span>{member.email}</span></td>
                      <td>
                        {roles.length > 0 && writable && !isMe ? (
                          <select className="role-select" aria-label={`${t('common.role')}: ${member.name}`} value={member.role}
                            onChange={(event) => void changeRole(member, event.target.value as Role)}>
                            {ROLE_ORDER.filter((item) => roles.includes(item) || item === member.role).map((item) => (
                              <option key={item} value={item}>{t(`role.${item}` as MessageKey)}</option>
                            ))}
                          </select>
                        ) : <span className={member.role === 'owner' ? 'badge badge-brand' : 'badge'}>{t(`role.${member.role}` as MessageKey)}</span>}
                      </td>
                      <td className="hide-sm">{formatDate(member.created_at, locale)}</td>
                      <td className="cell-actions">
                        {me && canRemoveMember(overview.role, me.user_id, member, owners) && (
                          <button type="button" className="button-danger button-small" onClick={() => setRemoving(member)}>
                            {isMe ? t('team.leave') : t('common.remove')}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {manager && (
        <div className="panel">
          <div className="panel-heading"><h2>{t('team.pending')}</h2></div>
          <ErrorNotice error={invites.error} />
          {(invites.data ?? []).length === 0 ? <p className="field-hint">{t('team.noPending')}</p> : (
            <div className="table-wrap">
              <table className="table" data-testid="invites-table">
                <thead><tr><th>{t('common.email')}</th><th>{t('common.role')}</th><th className="hide-sm">{t('common.expires')}</th><th><span className="sr-only">{t('common.actions')}</span></th></tr></thead>
                <tbody>
                  {invites.data?.map((item) => (
                    <tr key={item.id} data-invite={item.email}>
                      <td>{item.email}</td>
                      <td><span className="badge">{t(`role.${item.role}` as MessageKey)}</span></td>
                      <td className="hide-sm">{formatDate(item.expires_at, locale)}</td>
                      <td className="cell-actions"><button type="button" className="button-quiet button-small" onClick={() => setRevoking(item)}>{t('common.revoke')}</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {removing && (
        <ConfirmDialog
          title={removing.user_id === me?.user_id ? t('team.leaveTitle', { company: overview.name }) : t('team.removeTitle', { name: removing.name })}
          text={removing.user_id === me?.user_id ? t('team.leaveText') : t('team.removeText')}
          confirmLabel={removing.user_id === me?.user_id ? t('team.leave') : t('common.remove')}
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            await api.removeMember(tenant.tenant_id, removing.user_id);
            if (removing.user_id === me?.user_id) {
              navigate('/signup/finish', { replace: true });
              return;
            }
            await Promise.all([members.reload(), reloadTenant()]);
          }}
        />
      )}
      {revoking && (
        <ConfirmDialog title={t('team.revokeInviteTitle', { email: revoking.email })} text="" confirmLabel={t('common.revoke')}
          onClose={() => setRevoking(null)}
          onConfirm={async () => { await api.revokeInvite(tenant.tenant_id, revoking.id); await invites.reload(); }} />
      )}
    </section>
  );
}
