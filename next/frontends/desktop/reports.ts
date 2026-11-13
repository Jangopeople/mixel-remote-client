// Session reports for Mixel Remote companies (SaaS tenants, SQL 029): a "Session reports" page with
// filters (period, technician for owners/admins, device), the table, note editing and CSV/PDF export,
// and the optional "Session note" after a session this computer controlled on a company device has
// ended (plan with reports). Staff, ISM companies and personal spaces have no tenant: nothing shows.
// Rules, export builders and the command names: report-export.ts (window.mixelReports).
(() => {
  const invoke = window.__TAURI__.core.invoke;
  type Row = {session_id: string; technician_id: string | null; technician_name: string | null; technician_email: string | null; device_id: string | null; device_name: string | null;
    connection_id: number | null; started_at: string; ended_at: string | null; duration_seconds: number | null; kind: string; colleague: boolean | null; note: string | null};
  type Labels = Record<'technician' | 'email' | 'device' | 'remoteId' | 'start' | 'end' | 'duration' | 'durationSeconds' | 'kind' | 'colleague' | 'note' | 'sessionId' | 'attended' | 'unattended' | 'running' | 'yes' | 'no', string>;
  type Filter = {from: string; to: string; technician: string; device: string};
  type Params = {from_at: string; to_at: string; technician: string | null; device: string | null};
  type RemoteView = {phase: string; session?: string | null; peer?: number | null; sharing?: boolean};
  type Tools = {
    COMMANDS: {sessionReport: string; sessionNote: string; saveFile: string; openBilling: string};
    ARGS: {sessionReport(tenant: string, filter: Params): Record<string, unknown>; noteForSession(tenant: string, note: string, session: string): Record<string, unknown>;
      noteForConnection(tenant: string, note: string, connection: number): Record<string, unknown>; saveFile(name: string, content: string): Record<string, unknown>};
    isMissing(error: unknown): boolean; needsPlan(error: unknown): boolean; isManager(role: string | null | undefined): boolean;
    canEditNote(role: string | null | undefined, row: {technician_id: string | null}, me: string | null | undefined): boolean;
    defaultDays(now?: Date): {from: string; to: string}; reportParams(filter: Filter): {params: Params} | {problem: string};
    noteWatcher(): {update(state: RemoteView): number | null};
    shouldAskNote(context: {tenant: string | null; reports: boolean; companyHosts: ReadonlySet<number>; host: number; dialogOpen: boolean}): boolean;
    rowSeconds(row: Row, now?: Date): number; formatDuration(seconds: number | null | undefined): string; reportTotals(rows: Row[], now?: Date): {count: number; seconds: number};
    kindLabel(row: Row, labels: Labels): string; reportCsv(rows: Row[], labels: Labels, now?: Date): string;
    reportPdf(rows: Row[], options: {company: string; title: string; range: string; generated: string; totals: string; page: (page: number, pages: number) => string; labels: Labels; formatTime: (iso: string) => string; now?: Date}): string;
    reportFileName(slug: string, from: string, to: string, extension: 'csv' | 'pdf'): string;
  };
  type Tenant = {tenant_id: string; name: string; slug?: string; role: string; plan: string; status: string};
  type Overview = Tenant & {features: Record<string, boolean>};
  type Member = {user_id: string; name: string; role: string};
  type Device = {device_id: string; name: string; connection_id: number | null; revoked: boolean | null};
  type Field = {id: string; kind: 'text'; label: string; value?: string; maxLength?: number; required?: boolean; hint?: string};
  type Dialog = {open(options: {title: string; text?: string; fields?: Field[]; confirm: string; danger?: boolean; run: (values: Record<string, string>) => Promise<HTMLElement | void>}): void};
  const tools = (): Tools | undefined => (window as unknown as {mixelReports?: Tools}).mixelReports;
  const dialog = (): Dialog => (window as unknown as {mixelDialog: Dialog}).mixelDialog;
  const tr = (text: string): string => (window as unknown as {mixelText?: (value: string) => string}).mixelText?.(text) ?? text;
  const lang = (): string => { const value = document.documentElement?.lang ?? 'en'; return ['en', 'de', 'fr', 'it'].includes(value) ? value : 'en'; };
  const when = (iso: string): string => { const at = new Date(iso); return Number.isNaN(at.getTime()) ? '' : at.toLocaleString(lang(), {dateStyle: 'medium', timeStyle: 'short'}); };
  const day = (value: string): string => { const at = new Date(`${value}T12:00:00`); return Number.isNaN(at.getTime()) ? value : at.toLocaleDateString(lang(), {dateStyle: 'medium'}); };
  const formatId = (value: number | null): string => value === null ? '' : String(value).replace(/(\d{3})(\d{3})(\d{3})/, '$1 $2 $3');

  function make<K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<Record<string, string>> = {}, ...children: (Node | string)[]): HTMLElementTagNameMap[K] {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(props)) {
      if (value === undefined) continue;
      if (name === 'className') node.className = value;
      else if (name === 'text') node.textContent = value;
      else node.setAttribute(name, value);
    }
    for (const child of children) node.append(child);
    return node;
  }

  // ---------------------------------------------------------------------------------------------
  // The page (built here, so index.html only gains the script tags) and its navigation entry.

  const field = (label: string, control: HTMLElement): HTMLElement => make('label', {className: 'stacked-label report-field'}, make('span', {text: label}), control);
  const fromInput = make('input', {id: 'reports-from', type: 'date'});
  const toInput = make('input', {id: 'reports-to', type: 'date'});
  const technicianSelect = make('select', {id: 'reports-technician', 'data-no-translate': ''});
  const deviceSelect = make('select', {id: 'reports-device', 'data-no-translate': ''});
  const technicianField = field('Technician', technicianSelect);
  const showButton = make('button', {id: 'reports-show', className: 'button-secondary', type: 'submit', text: 'Show'});
  const filters = make('form', {id: 'reports-filters', className: 'report-filters'}, field('From', fromInput), field('To', toInput), technicianField, field('Device', deviceSelect), showButton);
  const csvButton = make('button', {id: 'reports-csv', className: 'button-secondary', type: 'button', text: 'Export CSV'});
  const pdfButton = make('button', {id: 'reports-pdf', className: 'button-secondary', type: 'button', text: 'Export PDF'});
  const lead = make('p', {className: 'subtitle', id: 'reports-lead', text: 'Who connected to which device, when and for how long.'});
  const notice = make('div', {id: 'reports-notice', className: 'plan-notice report-notice', role: 'status'});
  const noticeText = make('p', {id: 'reports-notice-text'});
  const upgrade = make('button', {id: 'reports-upgrade', className: 'button-primary', type: 'button', text: 'Choose a plan'});
  notice.append(noticeText, upgrade);
  const summary = make('p', {id: 'reports-summary', className: 'report-summary', role: 'status'});
  const rowsBody = make('tbody', {id: 'reports-rows'});
  const head = make('tr');
  for (const label of ['Technician', 'Device', 'Start', 'End', 'Duration', 'Type', 'Note']) head.append(make('th', {scope: 'col', text: label}));
  const table = make('table', {id: 'reports-table', className: 'report-table'}, make('thead', {}, head), rowsBody);
  const tableWrap = make('div', {className: 'report-table-wrap'}, table);
  const empty = make('p', {id: 'reports-empty', className: 'device-empty report-empty', text: 'No sessions in this period.'});
  const statusLine = make('p', {id: 'reports-status', className: 'field-hint', role: 'status'});
  const page = make('section', {id: 'reports', 'data-workspace-page': ''},
    make('div', {className: 'page-heading'},
      make('div', {}, make('p', {className: 'eyebrow', text: 'YOUR COMPANY'}), make('h1', {text: 'Session reports'}), lead),
      make('div', {className: 'heading-actions'}, csvButton, pdfButton)),
    make('div', {className: 'panel'}, notice, filters, summary, tableWrap, empty, statusLine));
  page.hidden = true;
  const link = make('a', {href: '#reports', 'aria-label': 'Session reports', id: 'nav-reports'}, make('span', {text: 'Session reports'}));
  link.hidden = true;
  if (typeof document.querySelector === 'function') {
    const content = document.querySelector('.app-content'), devicesPage = document.getElementById('devices');
    if (content) { if (devicesPage && devicesPage.parentElement === content) devicesPage.after(page); else content.append(page); }
    const devicesLink = document.querySelector('.app-navigation a[href="#devices"]');
    // The same icon as the Devices entry would be confusing; a small chart drawn here.
    const svg = document.createElementNS?.('http://www.w3.org/2000/svg', 'svg');
    if (svg) {
      svg.setAttribute('class', 'icon'); svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('focusable', 'false');
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', 'M4 20V11M10 20V5M16 20v-6M21 20H3'); svg.append(path);
      link.prepend(svg);
    }
    if (devicesLink) devicesLink.after(link);
    // workspace.js routed before this page existed: route again (a reload on #reports stays there).
    if (window.location?.hash === '#reports') window.dispatchEvent?.(new Event('hashchange'));
  }

  // ---------------------------------------------------------------------------------------------
  // State

  let me = '', tenant: Tenant | null = null, overview: Overview | null = null, members: Member[] = [], devices: Device[] = [], hosts = new Set<number>();
  let rows: Row[] = [], state: 'idle' | 'loading' | 'ok' | 'missing' | 'plan' | 'error' = 'idle', problem = '', version = 0, devicesVersion = 0, exporting = false;
  const initial = tools()?.defaultDays() ?? {from: '', to: ''};
  const filter: Filter = {from: initial.from, to: initial.to, technician: '', device: ''};
  fromInput.value = filter.from; toInput.value = filter.to;
  const role = (): string => overview?.role ?? tenant?.role ?? '';
  const planOff = (): boolean => overview?.features?.reports === false;
  const visible = (): boolean => window.location?.hash === '#reports';

  function labels(): Labels {
    return {technician: tr('Technician'), email: tr('Email'), device: tr('Device'), remoteId: tr('Connection ID'), start: tr('Start'), end: tr('End'), duration: tr('Duration'),
      durationSeconds: tr('Duration (seconds)'), kind: tr('Type'), colleague: tr('Joined as colleague'), note: tr('Note'), sessionId: tr('Session ID'),
      attended: tr('Attended'), unattended: tr('Unattended'), running: tr('Running'), yes: tr('Yes'), no: tr('No')};
  }
  const totalsText = (count: number, seconds: number, helper: Tools): string => `${count} ${tr(count === 1 ? 'session' : 'sessions')} · ${tr('total')} ${helper.formatDuration(seconds)}`;

  function option(value: string, label: string): HTMLOptionElement { const item = document.createElement('option'); item.value = value; item.textContent = label; return item; }
  function renderChoices(): void {
    const chosenTech = technicianSelect.value || filter.technician, chosenDevice = deviceSelect.value || filter.device;
    technicianSelect.replaceChildren(option('', tr('All technicians')), ...[...members].sort((a, b) => a.name.localeCompare(b.name)).map(item => option(item.user_id, item.name)));
    technicianSelect.value = members.some(item => item.user_id === chosenTech) ? chosenTech : '';
    deviceSelect.replaceChildren(option('', tr('All devices')), ...devices.filter(item => !item.revoked).sort((a, b) => a.name.localeCompare(b.name)).map(item => option(item.device_id, item.name)));
    deviceSelect.value = devices.some(item => item.device_id === chosenDevice) ? chosenDevice : '';
  }

  function render(): void {
    const helper = tools();
    link.hidden = !tenant;
    const manager = !!helper?.isManager(role());
    lead.textContent = !tenant ? 'Session reports are available for Mixel Remote company accounts.' : manager
      ? 'Every remote session of your company: who connected to which device, when and for how long.'
      : 'Your remote sessions: which device, when and for how long.';
    technicianField.hidden = !manager;
    const blocked = !tenant || !helper || planOff() || state === 'plan' || state === 'missing';
    notice.hidden = !blocked;
    upgrade.hidden = !tenant || !(planOff() || state === 'plan');
    noticeText.textContent = !tenant ? 'Sign in with a Mixel Remote company account to see session reports.'
      : !helper || state === 'missing' ? 'Session reports are not available yet. Please try again later.'
      : 'Session reports need a paid plan. They are included in the Solo, Team, Business and Enterprise plans.';
    filters.hidden = blocked;
    csvButton.hidden = pdfButton.hidden = blocked;
    csvButton.disabled = pdfButton.disabled = exporting || state !== 'ok' || !rows.length;
    showButton.disabled = state === 'loading';
    tableWrap.hidden = blocked || state !== 'ok' || !rows.length;
    empty.hidden = blocked || state !== 'ok' || rows.length > 0;
    summary.hidden = blocked || state !== 'ok' || !rows.length;
    if (!blocked && helper) {
      const totals = helper.reportTotals(rows);
      summary.textContent = totalsText(totals.count, totals.seconds, helper) + (rows.length >= 5000 ? ` · ${tr('Only the newest 5000 sessions are shown. Choose a shorter period to see all of them.')}` : '');
    }
    if (state === 'loading') statusLine.textContent = 'Loading the sessions…';
    else if (state === 'error' || problem) statusLine.textContent = problem;
    rowsBody.replaceChildren();
    if (tableWrap.hidden || !helper) return;
    for (const row of rows) {
      const line = make('tr', {'data-session': row.session_id});
      const who = make('td', {className: 'report-main'}, make('strong', {'data-no-translate': '', text: row.technician_name || row.technician_email || '—'}));
      if (row.technician_name && row.technician_email) who.append(make('span', {'data-no-translate': '', text: row.technician_email}));
      const device = make('td', {className: 'report-main'}, make('strong', {'data-no-translate': '', text: row.device_name || '—'}));
      if (row.connection_id !== null) device.append(make('span', {'data-no-translate': '', text: formatId(row.connection_id)}));
      const end = make('td', {className: 'report-time'});
      if (row.ended_at) end.textContent = when(row.ended_at); else end.append(make('span', {className: 'small-badge report-running', text: 'Running'}));
      const kind = make('td', {}, make('span', {className: row.kind === 'unattended' ? 'small-badge report-unattended' : 'small-badge', text: row.kind === 'unattended' ? 'Unattended' : 'Attended'}));
      if (row.colleague) kind.append(make('span', {className: 'small-badge report-colleague', text: 'Joined as colleague'}));
      const note = make('td', {className: 'report-note'});
      if (row.note) note.append(make('p', {'data-no-translate': '', className: 'report-note-text', text: row.note}));
      if (helper.canEditNote(role(), row, me)) {
        const edit = make('button', {type: 'button', className: 'button-quiet report-note-edit', text: row.note ? 'Edit note' : 'Add note', 'aria-label': `${tr(row.note ? 'Edit note' : 'Add note')}: ${row.device_name ?? ''}`});
        edit.addEventListener('click', () => editNote(row));
        note.append(edit);
      }
      line.append(who, device, make('td', {className: 'report-time', text: when(row.started_at)}), end,
        make('td', {className: 'report-duration', text: helper.formatDuration(helper.rowSeconds(row))}), kind, note);
      rowsBody.append(line);
    }
  }

  async function load(): Promise<void> {
    const helper = tools(), current = tenant;
    if (!helper || !current || planOff()) { render(); return; }
    filter.from = fromInput.value; filter.to = toInput.value; filter.technician = helper.isManager(role()) ? technicianSelect.value : ''; filter.device = deviceSelect.value;
    const parsed = helper.reportParams(filter);
    if ('problem' in parsed) { problem = parsed.problem; state = 'error'; render(); return; }
    const request = ++version;
    state = 'loading'; problem = ''; render();
    try {
      const value = await invoke<Row[]>(helper.COMMANDS.sessionReport, helper.ARGS.sessionReport(current.tenant_id, parsed.params));
      if (request !== version || tenant?.tenant_id !== current.tenant_id) return;
      if (!Array.isArray(value) || value.length > 5000 || value.some(row => !row || typeof row.session_id !== 'string' || typeof row.started_at !== 'string')) throw Error('Invalid session report response');
      rows = value; state = 'ok';
      statusLine.textContent = '';
    } catch (error) {
      if (request !== version) return;
      rows = [];
      state = helper.isMissing(error) ? 'missing' : helper.needsPlan(error) ? 'plan' : 'error';
      problem = state === 'error' ? String(error).replace(/^Error: /, '') : '';
    }
    render();
  }

  async function loadDevices(): Promise<void> {
    const current = tenant;
    if (!current) { devices = []; hosts = new Set(); renderChoices(); return; }
    const request = ++devicesVersion;
    try {
      const value = await invoke<Device[]>('saas_devices', {tenant: current.tenant_id, group: null, search: null});
      if (request !== devicesVersion || tenant?.tenant_id !== current.tenant_id || !Array.isArray(value)) return;
      devices = value.filter(item => item && typeof item.device_id === 'string' && typeof item.name === 'string');
      hosts = new Set(devices.filter(item => !item.revoked && Number.isInteger(item.connection_id)).map(item => item.connection_id!));
    } catch { /* the device filter stays "All devices"; no note prompt without the company's devices */ }
    renderChoices();
  }

  function editNote(row: Row): void {
    const helper = tools(), current = tenant;
    if (!helper || !current) return;
    dialog().open({
      title: 'Session note', text: `${row.device_name ?? ''} · ${when(row.started_at)}`,
      fields: [{id: 'note', kind: 'text', label: 'Note', value: row.note ?? '', maxLength: 4000, required: false, hint: 'Up to 4000 characters. Leave it empty to remove the note.'}],
      confirm: 'Save note',
      run: async values => {
        const text = (values.note ?? '').trim();
        if (text === (row.note ?? '')) return;
        await invoke(helper.COMMANDS.sessionNote, helper.ARGS.noteForSession(current.tenant_id, text, row.session_id));
        row.note = text || null;
        statusLine.textContent = text ? 'Note saved.' : 'Note removed.';
        render();
      },
    });
  }

  async function exportAs(kind: 'csv' | 'pdf'): Promise<void> {
    const helper = tools(), current = tenant;
    if (!helper || !current || !rows.length || exporting) return;
    exporting = true; render();
    try {
      const now = new Date(), words = labels(), totals = helper.reportTotals(rows, now);
      const content = kind === 'csv' ? helper.reportCsv(rows, words, now) : helper.reportPdf(rows, {
        company: overview?.name ?? current.name, title: tr('Session report'), range: `${day(filter.from)} – ${day(filter.to)}`,
        generated: `${tr('Generated')} ${now.toLocaleString(lang(), {dateStyle: 'medium', timeStyle: 'short'})}`,
        totals: totalsText(totals.count, totals.seconds, helper), page: (number, pages) => `${tr('Page')} ${number} ${tr('of')} ${pages}`,
        labels: words, formatTime: when, now,
      });
      const name = helper.reportFileName(current.slug ?? current.name, filter.from, filter.to, kind);
      const saved = await invoke<string | null>(helper.COMMANDS.saveFile, helper.ARGS.saveFile(name, content));
      statusLine.textContent = saved ? `${tr('Saved as')} ${saved}` : 'Export cancelled.';
    } catch (error) {
      statusLine.textContent = helper.isMissing(error) ? 'This version of Mixel Remote cannot save reports yet. Please update the app.' : String(error).replace(/^Error: /, '');
    } finally { exporting = false; render(); }
  }

  filters.addEventListener('submit', event => { event.preventDefault(); void load(); });
  for (const control of [fromInput, toInput, technicianSelect, deviceSelect]) control.addEventListener('change', () => { void load(); });
  csvButton.addEventListener('click', () => { void exportAs('csv'); });
  pdfButton.addEventListener('click', () => { void exportAs('pdf'); });
  upgrade.addEventListener('click', () => {
    const helper = tools();
    if (helper) void invoke(helper.COMMANDS.openBilling, {lang: lang()}).catch(error => { statusLine.textContent = String(error); });
  });

  // ---------------------------------------------------------------------------------------------
  // After a session: an optional note (only this computer as the controller of a company device)

  const watcher = tools()?.noteWatcher() ?? null;
  let remote: RemoteView = {phase: 'offline'};
  function askNote(host: number): void {
    const helper = tools(), current = tenant;
    if (!helper || !current) return;
    // A reconnect to the same computer is the same piece of work: ask when that one ends.
    if (remote.phase === 'active' && !remote.sharing && remote.peer === host) return;
    const box = document.getElementById('manage-dialog');
    if (!helper.shouldAskNote({tenant: current.tenant_id, reports: overview?.features?.reports === true, companyHosts: hosts, host, dialogOpen: !!box && !box.hidden})) return;
    const name = devices.find(item => item.connection_id === host)?.name ?? formatId(host);
    dialog().open({
      title: 'Session note', text: `${tr('Session with')} ${name}. ${tr('Add a note? It appears in the session report of your company.')}`,
      fields: [{id: 'note', kind: 'text', label: 'Note (optional)', maxLength: 4000, required: false}],
      confirm: 'Save note',
      run: async values => {
        const text = (values.note ?? '').trim();
        if (!text) return;
        await invoke(helper.COMMANDS.sessionNote, helper.ARGS.noteForConnection(current.tenant_id, text, host));
        if (visible()) void load();
      },
    });
    const skip = document.getElementById('manage-dialog-cancel');
    if (skip) skip.textContent = 'Skip';
  }
  document.addEventListener('mixel-remote-state', event => {
    const detail = (event as CustomEvent<RemoteView>).detail ?? {phase: 'offline'};
    remote = detail;
    const host = watcher?.update(detail) ?? null;
    if (host !== null) window.setTimeout(() => askNote(host), 1500);
  });

  // ---------------------------------------------------------------------------------------------
  // Account, company and navigation

  document.addEventListener('mixel-account-state', event => {
    const detail = (event as CustomEvent<{phase: string; account: {profile: string} | null}>).detail;
    me = detail?.phase === 'authenticated' ? detail.account?.profile ?? '' : '';
  });
  document.addEventListener('mixel-company-state', event => {
    const detail = (event as CustomEvent<{tenant: Tenant | null; overview: Overview | null}>).detail;
    const changed = (detail?.tenant?.tenant_id ?? '') !== (tenant?.tenant_id ?? '');
    tenant = detail?.tenant ?? null; overview = detail?.overview ?? null;
    if (changed) { rows = []; state = 'idle'; problem = ''; members = []; ++version; void loadDevices(); }
    if (tenant && visible() && (changed || state === 'idle')) void load(); else render();
  });
  document.addEventListener('mixel-company-members', event => {
    const detail = (event as CustomEvent<{members: Member[]}>).detail;
    members = Array.isArray(detail?.members) ? detail.members.filter(item => item && typeof item.user_id === 'string' && typeof item.name === 'string') : [];
    renderChoices();
  });
  for (const name of ['mixel-device-enrolled', 'mixel-device-removed']) document.addEventListener(name, () => { void loadDevices(); });
  window.addEventListener?.('hashchange', () => { if (visible() && tenant && state !== 'loading') void load(); });
  renderChoices();
  render();
})();
