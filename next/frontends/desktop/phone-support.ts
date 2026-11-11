// Help someone on their phone: the technician creates a one-time support code, the person types it
// into the Mixel Quick Support app, and the phone then shows up here, ready to connect.
(() => {
  function element<T extends HTMLElement>(id: string): T {
    const value = document.getElementById(id); if (!value) throw Error(`Missing phone support control ${id}`); return value as T;
  }
  const invoke = window.__TAURI__.core.invoke;
  type Account = {phase: string; account: {profile: string; staff: boolean} | null};
  type Remote = {phase: string; host: number | null; online?: number[]};
  type Company = {client_id: string; client_name: string};
  type Phone = {connection_id: number; name: string};
  const card = element<HTMLElement>('phone-support'), company = element<HTMLSelectElement>('phone-support-company');
  const create = element<HTMLButtonElement>('phone-support-create'), result = element<HTMLDivElement>('phone-support-result');
  const codeText = element<HTMLParagraphElement>('phone-support-code'), copy = element<HTMLButtonElement>('phone-support-copy');
  const dot = element<HTMLSpanElement>('phone-support-dot'), stateText = element<HTMLParagraphElement>('phone-support-state');
  const connect = element<HTMLButtonElement>('phone-support-connect'), status = element<HTMLParagraphElement>('phone-support-status');
  const CODE_LIFETIME = 30 * 60 * 1000;
  let accountKey = '', staff = false, busy = false, version = 0, companiesLoaded = false;
  let remote: Remote = {phase: 'offline', host: null};
  let code = '', createdAt = 0, phone: Phone | null = null, requested = false;
  const formatId = (value: number): string => String(value).replace(/(\d{3})(\d{3})(\d{3})/, '$1 $2 $3');
  const ready = (): boolean => ['ready', 'active', 'incoming', 'outgoing'].includes(remote.phase);
  const online = (): boolean => !!phone && (remote.online ?? []).includes(phone.connection_id);
  function describe(text: string, name?: string): void {
    // The phone's name is data, never translated: it sits in its own element.
    stateText.replaceChildren();
    if (name) { const label = document.createElement('strong'); label.dataset.noTranslate = ''; label.textContent = name; stateText.append(label, ' '); }
    stateText.append(document.createTextNode(text));
  }
  function render(): void {
    card.hidden = !staff;
    company.disabled = create.disabled = !staff || busy || !companiesLoaded || !company.value;
    result.hidden = !code;
    if (!code) return;
    const expired = !phone && Date.now() - createdAt > CODE_LIFETIME;
    connect.hidden = !phone;
    if (expired) { dot.dataset.state = 'idle'; describe('This support code has expired. Create a new one.'); return; }
    if (!phone) { dot.dataset.state = 'waiting'; describe('Waiting for the person to type the code into Mixel Quick Support…'); return; }
    const session = ['active', 'incoming', 'outgoing'].includes(remote.phase);
    connect.disabled = busy || remote.phase !== 'ready' || !online();
    if (!ready()) { dot.dataset.state = 'idle'; describe(`· ${formatId(phone.connection_id)} has registered. To connect, choose “Make this computer available” above first.`, phone.name); }
    else if (session && requested) { dot.dataset.state = 'online'; describe(`· ${formatId(phone.connection_id)} · connection requested. The person confirms on their phone.`, phone.name); }
    else if (online()) { dot.dataset.state = 'online'; describe(`· ${formatId(phone.connection_id)} is ready. Choose Connect.`, phone.name); }
    else { dot.dataset.state = 'waiting'; describe(`· ${formatId(phone.connection_id)} has registered. Waiting for the person to start sharing on their phone…`, phone.name); }
  }
  async function loadCompanies(): Promise<void> {
    if (!staff || companiesLoaded) return;
    const request = version;
    try {
      const list = await invoke<Company[]>('enrollment_companies');
      if (version !== request || !staff) return;
      company.replaceChildren();
      for (const item of list) { const option = document.createElement('option'); option.value = item.client_id; option.textContent = item.client_name; company.appendChild(option); }
      companiesLoaded = true;
      status.textContent = list.length ? '' : 'No company is linked for Remote devices yet. Link the company in Mixel admin first.';
    } catch (error) { if (version === request) status.textContent = String(error); }
    finally { render(); }
  }
  function reset(): void { code = ''; createdAt = 0; phone = null; requested = false; codeText.textContent = ''; status.textContent = ''; }
  function accountChanged(value: Account): void {
    const key = value.phase === 'authenticated' && value.account?.staff ? JSON.stringify(value.account) : '';
    if (key === accountKey) return;
    ++version; accountKey = key; staff = !!key; busy = false; companiesLoaded = false; company.replaceChildren(); reset(); render();
    if (staff) void loadCompanies();
  }
  create.addEventListener('click', async () => {
    if (busy || !company.value) return;
    const request = ++version; busy = true; reset(); render(); status.textContent = 'Creating a support code…';
    try {
      const value = await invoke<string>('support_code_create', {company: company.value});
      if (version !== request) return;
      if (!/^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$/.test(value)) throw Error('The support code could not be created. Please try again.');
      code = value; createdAt = Date.now(); codeText.textContent = value; status.textContent = '';
    } catch (error) { if (version === request) status.textContent = String(error); }
    finally { if (version === request) { busy = false; render(); } }
  });
  copy.addEventListener('click', async () => {
    if (!code) return;
    try { await navigator.clipboard.writeText(code); copy.textContent = 'Copied'; window.setTimeout(() => { copy.textContent = 'Copy code'; }, 2000); }
    catch { status.textContent = 'The code could not be copied. Select it and copy it yourself.'; }
  });
  connect.addEventListener('click', async () => {
    if (!phone || busy || remote.phase !== 'ready') return;
    const request = ++version; busy = true; requested = true; render(); status.textContent = '';
    try { await invoke('remote_request', {host: phone.connection_id}); }
    catch (error) { if (version === request) { requested = false; status.textContent = String(error); } }
    finally { if (version === request) { busy = false; render(); } }
  });
  // Look for the phone while a code is waiting, then keep asking whether it is online.
  let looking = false;
  window.setInterval(async () => {
    if (!staff || !code || document.hidden) return;
    if (!phone) {
      if (looking || Date.now() - createdAt > CODE_LIFETIME) { render(); return; }
      looking = true; const request = version, asked = code;
      try {
        const found = await invoke<Phone | null>('support_code_device', {code: asked});
        if (version === request && code === asked && found && Number.isInteger(found.connection_id) && typeof found.name === 'string') {
          phone = found; render(); document.dispatchEvent(new CustomEvent('mixel-device-enrolled'));
        }
      } catch { /* asked again on the next round */ }
      finally { looking = false; }
    }
    if (phone && ready() && !['active', 'incoming', 'outgoing'].includes(remote.phase)) void invoke('remote_presence', {hosts: [phone.connection_id]}).catch(() => {});
  }, 4000);
  document.addEventListener('mixel-account-state', event => accountChanged((event as CustomEvent<Account>).detail));
  document.addEventListener('mixel-remote-state', event => {
    const next = (event as CustomEvent<Remote>).detail;
    if (requested && next.phase === 'ready' && remote.phase !== 'ready') requested = false;
    remote = next; render();
  });
  render();
  void invoke<Account>('account_status').then(accountChanged).catch(() => {});
  void invoke<Remote>('remote_status').then(value => { remote = value; render(); }).catch(() => {});
})();
