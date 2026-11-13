// Session reports (SQL 029, plan feature "reports"): who connected to which device, when and for how
// long. Owners and admins see every session and filter by technician; everyone else sees their own.
// Notes are edited inline (owners/admins any, the others their own); the shown rows export as CSV or PDF.
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useI18n } from '../i18n';
import { useTenant } from '../components/AppShell';
import { ErrorNotice, Notice, Spinner, Toast, formatDate } from '../components/ui';
import { Icon } from '../components/Icon';
import { api, type Device, type Member, type SessionReportRow } from '../lib/api';
import { useAuth } from '../lib/auth';
import { download } from '../lib/exportData';
import { canEditNote, canPickTechnician, defaultReportDays, errorKey, isManager, isMissingRpc, reportParams, type ReportFilter } from '../lib/logic';
import { formatDuration, reportCsv, reportFileName, reportPdf, reportTotals, rowSeconds, type ReportLabels } from '../lib/reportExport';
import { useLoad } from '../lib/useLoad';

const MAX_ROWS = 5000;
const NOTE_MAX = 4000;

function NoteCell({ row, editable, onSaved }: { row: SessionReportRow; editable: boolean; onSaved: (note: string | null) => void }) {
  const { t } = useI18n();
  const { tenant } = useTenant();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(row.note ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const id = `note-${row.session_id}`;
  if (!editing) {
    return (
      <>
        {row.note && <p className="report-note-text" data-testid="note-text">{row.note}</p>}
        {editable && (
          <button type="button" className="button-quiet button-small" data-testid="note-edit"
            aria-label={`${t(row.note ? 'reports.editNote' : 'reports.addNote')}: ${row.device_name ?? ''}`}
            onClick={() => { setValue(row.note ?? ''); setError(null); setEditing(true); }}>
            <Icon name="edit" />{t(row.note ? 'reports.editNote' : 'reports.addNote')}
          </button>
        )}
      </>
    );
  }
  return (
    <form className="report-note-edit" noValidate onSubmit={async (event) => {
      event.preventDefault();
      if (value.length > NOTE_MAX) { setError({ message: 'note too long' }); return; }
      setBusy(true);
      setError(null);
      try {
        await api.sessionNote(tenant.tenant_id, row.session_id, value.trim());
        onSaved(value.trim() || null);
        setEditing(false);
      } catch (failure) {
        setError(failure);
      } finally {
        setBusy(false);
      }
    }}>
      <label htmlFor={id} className="sr-only">{t('reports.noteLabel', { device: row.device_name ?? '' })}</label>
      <textarea id={id} value={value} maxLength={NOTE_MAX} autoFocus data-testid="note-input"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); setEditing(false); } }} />
      <small className="field-hint">{t('reports.noteHint')}</small>
      <ErrorNotice error={error} />
      <div className="report-actions">
        <button type="submit" className="button-small" disabled={busy} data-testid="note-save">{t('common.save')}</button>
        <button type="button" className="secondary button-small" disabled={busy} onClick={() => setEditing(false)}>{t('common.cancel')}</button>
      </div>
    </form>
  );
}

export function Reports() {
  const { t, locale } = useI18n();
  const { tenant, overview } = useTenant();
  const { session } = useAuth();
  const manager = isManager(overview.role);
  // Before SQL 029 the plan features have no "reports" key: then the RPC decides (and may not exist yet).
  const planAllows = overview.features.reports !== false;
  const [filter, setFilter] = useState<ReportFilter>(() => ({ ...defaultReportDays(), technician: '', device: '' }));
  const [notes, setNotes] = useState<Record<string, string | null>>({});
  const [toast, setToast] = useState<string | null>(null);

  const parsed = reportParams(filter);
  const problem = 'problem' in parsed ? parsed.problem : null;
  const report = useLoad<SessionReportRow[]>(
    () => (planAllows && 'params' in parsed ? api.sessionReport(tenant.tenant_id, parsed.params) : Promise.resolve([])),
    `${tenant.tenant_id}|${planAllows}|${problem ? 'x' : JSON.stringify(filter)}`,
  );
  const members = useLoad<Member[]>(
    () => (planAllows && canPickTechnician(overview.role) ? api.members(tenant.tenant_id) : Promise.resolve([])),
    `${tenant.tenant_id}|${planAllows}|${overview.role}`,
  );
  const devices = useLoad<Device[]>(() => (planAllows ? api.devices(tenant.tenant_id) : Promise.resolve([])), `${tenant.tenant_id}|${planAllows}`);

  const rows = useMemo(
    () => (report.data ?? []).map((row) => (row.session_id in notes ? { ...row, note: notes[row.session_id] } : row)),
    [report.data, notes],
  );
  const totals = reportTotals(rows);
  const number = new Intl.NumberFormat(locale);
  const missing = isMissingRpc(report.error);
  const needsPlan = !planAllows || errorKey(report.error) === 'error.reports_plan';

  const labels: ReportLabels = {
    technician: t('reports.technician'), email: t('reports.csv.email'), device: t('reports.device'), remoteId: t('devices.col.id'),
    start: t('reports.col.start'), end: t('reports.col.end'), duration: t('reports.col.duration'), durationSeconds: t('reports.csv.durationSeconds'),
    kind: t('reports.col.kind'), colleague: t('reports.colleague'), note: t('reports.col.note'), sessionId: t('reports.csv.sessionId'),
    attended: t('reports.attended'), unattended: t('reports.unattended'), running: t('reports.running'), yes: t('common.yes'), no: t('common.no'),
  };
  const rangeText = `${formatDate(filter.from + 'T12:00:00', locale)} – ${formatDate(filter.to + 'T12:00:00', locale)}`;
  const exportCsv = () => download(reportFileName(tenant.slug, filter.from, filter.to, 'csv'), reportCsv(rows, labels), 'text/csv;charset=utf-8');
  const exportPdf = () => {
    const now = new Date();
    const pdf = reportPdf(rows, {
      company: overview.name,
      title: t('reports.pdfTitle'),
      range: rangeText,
      generated: t('reports.pdfGenerated', { time: formatDate(now.toISOString(), locale, true) }),
      totals: t('reports.totals', { count: number.format(totals.count), duration: formatDuration(totals.seconds) }),
      page: (page, pages) => t('reports.pdfPage', { page, pages }),
      labels,
      formatTime: (iso) => formatDate(iso, locale, true),
      now,
    });
    download(reportFileName(tenant.slug, filter.from, filter.to, 'pdf'), pdf, 'application/pdf');
  };
  const set = (key: keyof ReportFilter) => (event: { target: { value: string } }) => setFilter((current) => ({ ...current, [key]: event.target.value }));
  const sortedDevices = [...(devices.data ?? [])].filter((device) => !device.revoked).sort((a, b) => a.name.localeCompare(b.name, locale));
  const sortedMembers = [...(members.data ?? [])].sort((a, b) => (a.name || a.email).localeCompare(b.name || b.email, locale));

  return (
    <section>
      <div className="page-heading">
        <div>
          <h1>{t('reports.title')}</h1>
          <p>{manager ? t('reports.leadAll') : t('reports.leadOwn')}</p>
        </div>
        {!needsPlan && !missing && (
          <div className="report-actions">
            <button type="button" className="secondary" data-testid="report-export-csv" disabled={!rows.length || report.loading} onClick={exportCsv}>
              <Icon name="download" />{t('reports.exportCsv')}
            </button>
            <button type="button" className="secondary" data-testid="report-export-pdf" disabled={!rows.length || report.loading} onClick={exportPdf}>
              <Icon name="download" />{t('reports.exportPdf')}
            </button>
          </div>
        )}
      </div>
      {needsPlan ? (
        <div className="panel upgrade-card" data-testid="report-upgrade">
          <Icon name="chart" />
          <h2>{t('reports.upgradeTitle')}</h2>
          <p>{t('reports.upgradeText')}</p>
          <Link className="button" to="/app/billing">{t('reports.upgradeCta')}</Link>
        </div>
      ) : missing ? (
        <div className="panel" data-testid="report-unavailable"><Notice><p>{t('reports.unavailable')}</p></Notice></div>
      ) : (
        <div className="panel">
          <form className="report-filters" role="search" onSubmit={(event) => { event.preventDefault(); void report.reload(); }}>
            <div className="field">
              <label htmlFor="report-from">{t('reports.from')}</label>
              <input id="report-from" type="date" value={filter.from} max={filter.to || undefined} onChange={set('from')} />
            </div>
            <div className="field">
              <label htmlFor="report-to">{t('reports.to')}</label>
              <input id="report-to" type="date" value={filter.to} min={filter.from || undefined} onChange={set('to')} />
            </div>
            {canPickTechnician(overview.role) && (
              <div className="field">
                <label htmlFor="report-technician">{t('reports.technician')}</label>
                <select id="report-technician" value={filter.technician} onChange={set('technician')}>
                  <option value="">{t('reports.allTechnicians')}</option>
                  {sortedMembers.map((member) => <option key={member.user_id} value={member.user_id}>{member.name || member.email}</option>)}
                </select>
              </div>
            )}
            <div className="field">
              <label htmlFor="report-device">{t('reports.device')}</label>
              <select id="report-device" value={filter.device} onChange={set('device')}>
                <option value="">{t('reports.allDevices')}</option>
                {sortedDevices.map((device) => <option key={device.device_id} value={device.device_id}>{device.name}</option>)}
              </select>
            </div>
            <div className="field">
              <button type="submit" className="secondary"><Icon name="search" />{t('reports.show')}</button>
            </div>
          </form>
          {problem && <div className="notice notice-error" role="alert"><Icon name="alert" /><p>{t(problem)}</p></div>}
          <ErrorNotice error={problem ? null : report.error} onRetry={() => void report.reload()} />
          {!problem && !report.error && (report.loading && !report.data ? <Spinner /> : rows.length === 0 ? (
            <div className="empty" data-testid="report-empty">
              <Icon name="chart" />
              <h3>{t('reports.empty')}</h3>
              <p>{t('reports.emptyText')}</p>
            </div>
          ) : (
            <>
              <div className="report-summary">
                <span data-testid="report-totals"><strong>{t('reports.totals', { count: number.format(totals.count), duration: formatDuration(totals.seconds) })}</strong></span>
                {rows.length >= MAX_ROWS && <span>{t('reports.limitNote', { count: number.format(MAX_ROWS) })}</span>}
              </div>
              <div className="table-wrap">
                <table className="table report-table" data-testid="report-table">
                  <thead>
                    <tr>
                      <th>{t('reports.technician')}</th>
                      <th>{t('reports.device')}</th>
                      <th className="cell-time">{t('reports.col.start')}</th>
                      <th className="cell-time">{t('reports.col.end')}</th>
                      <th className="cell-duration">{t('reports.col.duration')}</th>
                      <th>{t('reports.col.kind')}</th>
                      <th>{t('reports.col.note')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.session_id} data-session={row.session_id} data-testid="report-row">
                        <td className="cell-main">
                          <strong>{row.technician_name || row.technician_email || '—'}</strong>
                          {row.technician_name && row.technician_email && <span>{row.technician_email}</span>}
                        </td>
                        <td className="cell-main">
                          <strong>{row.device_name || '—'}</strong>
                          {row.connection_id !== null && <span className="conn-id">{String(row.connection_id).replace(/\B(?=(\d{3})+(?!\d))/g, ' ')}</span>}
                        </td>
                        <td className="cell-time">{formatDate(row.started_at, locale, true)}</td>
                        <td className="cell-time">{row.ended_at ? formatDate(row.ended_at, locale, true) : <span className="badge badge-success">{t('reports.running')}</span>}</td>
                        <td className="cell-duration">{formatDuration(rowSeconds(row))}</td>
                        <td>
                          <div className="report-kind">
                            <span className={row.kind === 'unattended' ? 'badge badge-brand' : 'badge'}>{row.kind === 'unattended' ? t('reports.unattended') : t('reports.attended')}</span>
                            {row.colleague && <span className="badge" data-testid="colleague-badge"><Icon name="users" />{t('reports.colleague')}</span>}
                          </div>
                        </td>
                        <td className="cell-note">
                          <NoteCell row={row} editable={canEditNote(overview.role, row, session?.user.email)}
                            onSaved={(note) => { setNotes((current) => ({ ...current, [row.session_id]: note })); setToast(t('reports.noteSaved')); }} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ))}
        </div>
      )}
      <Toast message={toast} onDone={() => setToast(null)} />
    </section>
  );
}
