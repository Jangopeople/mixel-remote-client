import { useEffect, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useI18n, type MessageKey } from '../i18n';
import { useTenant } from '../components/AppShell';
import { ConfirmDialog, Dialog, ErrorNotice, Notice, Spinner, Toast, formatDate } from '../components/ui';
import { Icon } from '../components/Icon';
import { STORE_URL, WEB_VIEWER_URL } from '../components/PublicLayout';
import { api, type Device, type DevicePresence, type Group } from '../lib/api';
import { canManageDevices, deviceSections, formatConnectionId, isManager, isName, isWritable, osLabel, relativeTime, sinceTime } from '../lib/logic';
import { useLoad } from '../lib/useLoad';

function AddDevicesDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const { overview } = useTenant();
  return (
    <Dialog title={t('add.title')} onClose={onClose}>
      <h3 style={{ marginTop: 8 }}>{t('add.thisTitle')}</h3>
      <ol className="ol-steps">
        <li>{t('add.this1')} <a href={STORE_URL}>{t('nav.download')}</a></li>
        <li>{t('add.this2')}</li>
        <li>{t('add.this3')}</li>
      </ol>
      <h3 style={{ marginTop: 20 }}>{t('add.deployTitle')}</h3>
      <p className="field-hint">{t('add.deployText')}</p>
      {isManager(overview.role)
        ? <Link className="button" to="/app/deploy" style={{ marginTop: 8 }}>{t('add.deployCta')}</Link>
        : <p className="field-hint">{t('add.deployOnlyAdmins')}</p>}
      <div className="dialog-actions"><button type="button" className="secondary" onClick={onClose}>{t('common.close')}</button></div>
    </Dialog>
  );
}

function RenameDialog({ device, onClose, onDone }: { device: Device; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const { tenant } = useTenant();
  const [name, setName] = useState(device.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!isName(name, 60)) {
      setError({ message: 'Enter a device name' });
      return;
    }
    setBusy(true);
    try {
      await api.renameDevice(tenant.tenant_id, device.device_id, name.trim());
      onDone();
      onClose();
    } catch (failure) {
      setError(failure);
      setBusy(false);
    }
  }
  return (
    <Dialog title={t('devices.renameTitle')} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <div className="field">
          <label htmlFor="device-name">{t('common.name')}</label>
          <input id="device-name" value={name} maxLength={60} onChange={(event) => setName(event.target.value)} />
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

function MoveDialog({ device, groups, onClose, onDone }: { device: Device; groups: Group[]; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const { tenant } = useTenant();
  const [group, setGroup] = useState(device.group_id ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  return (
    <Dialog title={t('devices.moveTitle', { name: device.name })} onClose={onClose}>
      <form noValidate onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        try {
          await api.moveDevice(tenant.tenant_id, device.device_id, group || null);
          onDone();
          onClose();
        } catch (failure) {
          setError(failure);
          setBusy(false);
        }
      }}>
        <div className="field">
          <label htmlFor="device-group">{t('devices.col.group')}</label>
          <select id="device-group" value={group} onChange={(event) => setGroup(event.target.value)}>
            <option value="">{t('devices.noGroup')}</option>
            {groups.map((item) => <option key={item.group_id} value={item.group_id}>{item.name}</option>)}
          </select>
        </div>
        <ErrorNotice error={error} />
        <div className="dialog-actions">
          <button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button>
          <button type="submit" disabled={busy}>{t('devices.move')}</button>
        </div>
      </form>
    </Dialog>
  );
}

type Action = { kind: 'rename' | 'move' | 'remove'; device: Device } | { kind: 'add' } | null;

function StarToggle({ device, on, onToggle }: { device: Device; on: boolean; onToggle: () => void }) {
  const { t } = useI18n();
  const label = t(on ? 'devices.favorite.remove' : 'devices.favorite.add', { name: device.name });
  return (
    <button type="button" className="button-quiet star-toggle" aria-pressed={on} aria-label={label} title={label} data-testid="favorite-toggle" onClick={onToggle}>
      <Icon name="star" />
    </button>
  );
}

function PresenceCell({ device, presence }: { device: Device; presence: DevicePresence | undefined }) {
  const { t, locale } = useI18n();
  if (device.connection_id === null || device.revoked) {
    return <span className={device.revoked ? 'badge badge-warning' : 'badge'}>{device.revoked ? t('devices.revoked') : t('devices.notEnrolled')}</span>;
  }
  const status = presence?.status ?? 'unknown';
  const lang = locale.slice(0, 2);
  if (status === 'online') {
    return (
      <div className="presence-cell" data-status="online">
        <span className="badge badge-success"><span className="dot" />{t('devices.online')}</span>
        {presence?.online_since && <small>{t('devices.onlineSince', { time: sinceTime(presence.online_since, locale) })}</small>}
      </div>
    );
  }
  if (status === 'offline' || status === 'never') {
    return (
      <div className="presence-cell" data-status={status}>
        <span className="badge"><span className="dot" />{status === 'offline' ? t('devices.offline') : t('devices.never')}</span>
        <small>{status === 'offline' && presence?.last_seen ? `${t('devices.lastSeen', { time: relativeTime(presence.last_seen, lang) })} · ` : ''}
          {status === 'offline' ? t('devices.offlineReason') : t('devices.neverReason')}</small>
      </div>
    );
  }
  return (
    <div className="presence-cell" data-status="unknown">
      <span className="badge badge-warning">{t('devices.unknown')}</span>
      <small>{t('devices.unknownReason')}</small>
    </div>
  );
}

export function Devices() {
  const { t, locale } = useI18n();
  const { tenant, overview, reload: reloadTenant } = useTenant();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [groupFilter, setGroupFilter] = useState('');
  const [action, setAction] = useState<Action>(params.get('add') ? { kind: 'add' } : null);
  const [toast, setToast] = useState<string | null>(null);
  const groupsOn = !!overview.features.groups;
  const canManage = canManageDevices(overview.role);
  const writable = isWritable(overview.status);

  const devices = useLoad(() => api.devices(tenant.tenant_id, groupFilter || null, query || null), `${tenant.tenant_id}|${groupFilter}|${query}`);
  const presence = useLoad(() => api.devicePresence(tenant.tenant_id), tenant.tenant_id);
  const reloadPresence = presence.reload;
  useEffect(() => {
    const timer = window.setInterval(() => { if (!document.hidden) void reloadPresence(); }, 30_000);
    return () => window.clearInterval(timer);
  }, [reloadPresence]);
  const presenceOf = new Map((presence.data ?? []).map((row) => [row.device_id, row]));
  const groups = useLoad(() => (groupsOn && canManage ? api.groups(tenant.tenant_id) : Promise.resolve([] as Group[])), `${tenant.tenant_id}|${groupsOn}`);

  const close = () => {
    setAction(null);
    if (params.get('add')) setParams({}, { replace: true });
  };
  const refresh = async () => {
    await Promise.all([devices.reload(), groups.reload(), presence.reload(), reloadTenant()]);
  };

  // Favorites and recent connections (SQL 029). Until those functions exist on the server (or when
  // they fail), the stars and the two sections are simply not shown.
  const favorites = useLoad(() => api.favorites(tenant.tenant_id), tenant.tenant_id);
  const recent = useLoad(() => api.recentDevices(tenant.tenant_id, 10), tenant.tenant_id);
  const [favoriteIds, setFavoriteIds] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (favorites.data) setFavoriteIds(new Set(favorites.data.map((row) => row.device_id)));
  }, [favorites.data]);
  const starsOn = favoriteIds !== null && !favorites.error;
  async function toggleFavorite(device: Device) {
    if (!favoriteIds) return;
    const id = device.device_id;
    const was = favoriteIds.has(id);
    const change = (set: Set<string>, on: boolean) => {
      const next = new Set(set);
      if (on) next.add(id); else next.delete(id);
      return next;
    };
    setFavoriteIds((current) => change(current ?? new Set(), !was));
    try {
      await api.setFavorite(tenant.tenant_id, id, !was);
    } catch {
      setFavoriteIds((current) => change(current ?? new Set(), was));
      setToast(t('devices.favoriteFailed'));
    }
  }

  const list = devices.data ?? [];
  const sections = deviceSections(list, starsOn ? favoriteIds! : new Set<string>(), recent.error ? [] : recent.data ?? []);
  const grouped = sections.favorites.length > 0 || sections.recent.length > 0;
  const columns = 4 + (starsOn ? 1 : 0) + (groupsOn ? 1 : 0) + (canManage ? 1 : 0);
  const row = (device: Device) => (
    <tr key={device.device_id} data-device={device.name}>
      {starsOn && <td className="cell-star"><StarToggle device={device} on={favoriteIds!.has(device.device_id)} onToggle={() => void toggleFavorite(device)} /></td>}
      <td className="cell-main">
        <strong>{device.name}</strong>
        <span>{osLabel(device.os)}{device.added_via ? ` · ${t(`devices.via.${device.added_via}` as MessageKey)}` : ''}</span>
      </td>
      <td className="conn-id">{formatConnectionId(device.connection_id)}</td>
      {groupsOn && <td className="hide-sm">{device.group_name ?? <span className="field-hint">{t('devices.noGroup')}</span>}</td>}
      <td data-testid="device-status"><PresenceCell device={device} presence={presenceOf.get(device.device_id)} /></td>
      <td className="hide-sm">{formatDate(device.enrolled_at, locale)}</td>
      {canManage && (
        <td className="cell-actions">
          <button type="button" className="button-quiet button-small" disabled={!writable} onClick={() => setAction({ kind: 'rename', device })}>{t('common.rename')}</button>
          {groupsOn && <button type="button" className="button-quiet button-small" disabled={!writable} onClick={() => setAction({ kind: 'move', device })}>{t('devices.move')}</button>}
          <button type="button" className="button-danger button-small" onClick={() => setAction({ kind: 'remove', device })}>{t('common.remove')}</button>
        </td>
      )}
    </tr>
  );
  const section = (key: 'favorites' | 'recent' | 'others', items: Device[], icon: 'star' | 'clock' | 'monitor') => items.length > 0 && (
    <tbody key={key} data-testid={`devices-section-${key}`}>
      {grouped && (
        <tr className="table-section">
          <th scope="colgroup" colSpan={columns}><Icon name={icon} />{t(`devices.section.${key}`)}</th>
        </tr>
      )}
      {items.map(row)}
    </tbody>
  );
  return (
    <section>
      <div className="page-heading">
        <div>
          <h1>{t('devices.title')}</h1>
          <p>{t('devices.lead')}</p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <a className="button button-secondary" href={WEB_VIEWER_URL} target="_blank" rel="noopener" data-testid="open-viewer"><Icon name="globe" />{t('viewer.open')}</a>
          <button type="button" onClick={() => setAction({ kind: 'add' })}><Icon name="plus" />{t('devices.add')}</button>
        </div>
      </div>
      <div className="panel">
        <form className="toolbar" role="search" onSubmit={(event) => { event.preventDefault(); setQuery(search.trim()); }}>
          <label htmlFor="device-search" className="sr-only">{t('devices.search')}</label>
          <input id="device-search" type="search" placeholder={t('devices.search')} value={search} maxLength={80}
            onChange={(event) => { setSearch(event.target.value); if (!event.target.value) setQuery(''); }} />
          {groupsOn && (groups.data?.length ?? 0) > 0 && (
            <select aria-label={t('devices.col.group')} value={groupFilter} onChange={(event) => setGroupFilter(event.target.value)}>
              <option value="">{t('devices.allGroups')}</option>
              {groups.data?.map((group) => <option key={group.group_id} value={group.group_id}>{group.name}</option>)}
            </select>
          )}
          <button type="submit" className="secondary"><Icon name="search" />{t('common.search')}</button>
        </form>
        <p className="field-hint" style={{ marginBottom: 8 }}><Icon name="info" className="" /> {t('devices.presenceNote')} · {t('devices.count', { count: new Intl.NumberFormat(locale).format(list.length) })}</p>
        <ErrorNotice error={devices.error} onRetry={() => void devices.reload()} />
        {devices.loading && !devices.data ? <Spinner /> : list.length === 0 ? (
          <div className="empty">
            <Icon name="monitor" />
            <h3>{query ? t('devices.emptySearch') : t('devices.empty')}</h3>
            {!query && <p>{t('devices.emptyText')}</p>}
            {!query && <button type="button" onClick={() => setAction({ kind: 'add' })}>{t('devices.add')}</button>}
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table" data-testid="devices-table">
              <thead>
                <tr>
                  {starsOn && <th className="cell-star"><span className="sr-only">{t('devices.col.favorite')}</span></th>}
                  <th>{t('devices.col.name')}</th>
                  <th>{t('devices.col.id')}</th>
                  {groupsOn && <th className="hide-sm">{t('devices.col.group')}</th>}
                  <th>{t('common.status')}</th>
                  <th className="hide-sm">{t('devices.col.added')}</th>
                  {canManage && <th><span className="sr-only">{t('common.actions')}</span></th>}
                </tr>
              </thead>
              {section('favorites', sections.favorites, 'star')}
              {section('recent', sections.recent, 'clock')}
              {section('others', sections.others, 'monitor')}
            </table>
          </div>
        )}
        {!groupsOn && <Notice><p>{t('devices.groupsNeedTeam')}</p></Notice>}
      </div>
      {action?.kind === 'add' && <AddDevicesDialog onClose={close} />}
      {action?.kind === 'rename' && <RenameDialog device={action.device} onClose={close} onDone={() => void refresh()} />}
      {action?.kind === 'move' && <MoveDialog device={action.device} groups={groups.data ?? []} onClose={close} onDone={() => void refresh()} />}
      {action?.kind === 'remove' && (
        <ConfirmDialog
          title={t('devices.removeTitle', { name: action.device.name })}
          text={t('devices.removeText')}
          confirmLabel={t('common.remove')}
          onClose={close}
          onConfirm={async () => {
            await api.removeDevice(tenant.tenant_id, action.device.device_id);
            setToast(t('devices.removed'));
            await refresh();
          }}
        />
      )}
      <Toast message={toast} onDone={() => setToast(null)} />
    </section>
  );
}
