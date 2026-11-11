// Help someone on their phone, the way people expect: the person opens Mixel Quick Support and reads
// out the 9-digit ID it shows; the technician types it here and connects. The person allows it on the phone.
(() => {
  function element<T extends HTMLElement>(id: string): T {
    const value = document.getElementById(id); if (!value) throw Error(`Missing phone support control ${id}`); return value as T;
  }
  const invoke = window.__TAURI__.core.invoke;
  type Account = {phase: string; account: {profile: string; staff: boolean; personal?: boolean} | null};
  type Company = {client_id: string; client_name: string};
  const locked = element<HTMLDivElement>('phone-support-locked'), lockedText = element<HTMLParagraphElement>('phone-support-locked-text');
  const signIn = element<HTMLAnchorElement>('phone-support-signin'), body = element<HTMLDivElement>('phone-support-body');
  const setup = element<HTMLDivElement>('phone-support-setup'), company = element<HTMLSelectElement>('phone-support-company');
  const setupName = element<HTMLInputElement>('phone-support-setup-name'), setupButton = element<HTMLButtonElement>('phone-support-setup-button');
  const form = element<HTMLFormElement>('phone-support-form'), idField = element<HTMLInputElement>('phone-support-id');
  const connect = element<HTMLButtonElement>('phone-support-connect'), status = element<HTMLParagraphElement>('phone-support-status');
  let account: Account = {phase: 'signed_out', account: null};
  let accountKey = '', staff = false, busy = false, version = 0, companiesLoaded = false;
  let setUp: boolean | null = null, phase = 'offline';
  const formatId = (value: number): string => String(value).replace(/(\d{3})(\d{3})(\d{3})/, '$1 $2 $3');
  const digits = (): string => idField.value.replace(/\D/g, '');
  function render(): void {
    const signedIn = account.phase === 'authenticated' && !!account.account;
    locked.hidden = staff; body.hidden = !staff; signIn.hidden = signedIn;
    if (!signedIn) lockedText.textContent = 'Sign in with your Mixel team account to help someone on their phone.';
    else if (!staff) lockedText.textContent = 'Only Mixel team accounts can connect to phones. Sign in with your team account (with the second confirmation step) under Account.';
    setup.hidden = setUp !== false;
    form.hidden = setUp === false;
    setupButton.disabled = setupName.disabled = company.disabled = busy || !companiesLoaded || !company.value || !setupName.value.trim();
    company.disabled = busy || !companiesLoaded;
    const inSession = ['active', 'incoming', 'outgoing'].includes(phase);
    connect.disabled = busy || inSession || !/^[1-9][0-9]{8}$/.test(digits());
  }
  async function loadCompanies(): Promise<void> {
    if (companiesLoaded) return;
    const ask = version;
    try {
      const list = await invoke<Company[]>('enrollment_companies');
      if (version !== ask) return;
      company.replaceChildren();
      // The technician's own computer usually belongs to Mixel itself: offer that first.
      const sorted = [...list].sort((a, b) => Number(b.client_name.startsWith('Mixel')) - Number(a.client_name.startsWith('Mixel')));
      for (const item of sorted) { const option = document.createElement('option'); option.value = item.client_id; option.textContent = item.client_name; company.appendChild(option); }
      companiesLoaded = true;
      if (!list.length) status.textContent = 'No company is linked for Remote devices yet. Link the company in Mixel admin first.';
    } catch (error) { if (version === ask) status.textContent = String(error); }
    finally { render(); }
  }
  async function checkSetUp(): Promise<void> {
    if (!staff) return;
    const ask = version;
    try {
      const current = await invoke<{connection_id: number} | null>('enrollment_current');
      if (version !== ask) return;
      setUp = !!current;
      if (!setUp) { void loadCompanies(); if (!setupName.value) setupName.value = await invoke<string>('this_computer_name').catch(() => ''); }
    } catch { setUp = null; }
    render();
  }
  setupName.addEventListener('input', render);
  company.addEventListener('change', render);
  setupButton.addEventListener('click', async () => {
    const name = setupName.value.trim();
    if (busy || !name || !company.value) return;
    const ask = ++version; busy = true; render(); status.textContent = 'Setting up this computer…';
    try {
      if (!['offline', 'error'].includes(phase)) await invoke('remote_disconnect').catch(() => {});
      const device = await invoke<string>('add_this_device', {company: company.value, name});
      const id = await invoke<number>('enroll_this_device', {device, confirmed: true});
      if (version !== ask) return;
      if (!Number.isInteger(id) || id < 100000000 || id > 999999999) throw Error('Invalid enrolled connection ID');
      setUp = true; status.textContent = `This computer is set up. Connection ID ${formatId(id)}. Now type the ID from the phone.`;
      document.dispatchEvent(new CustomEvent('mixel-device-enrolled'));
      idField.focus();
    } catch (error) { if (version === ask) status.textContent = String(error); }
    finally { if (version === ask) { busy = false; render(); } }
  });
  idField.addEventListener('input', () => {
    const value = digits().slice(0, 9);
    idField.value = value.replace(/(\d{3})(?=\d)/g, '$1 ');
    render();
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (connect.disabled) return;
    status.textContent = '';
    document.dispatchEvent(new CustomEvent('mixel-connect-to', {detail: {id: Number(digits())}}));
  });
  // Progress of the connection, shown here where the technician is looking.
  document.addEventListener('mixel-connect-progress', event => { status.textContent = (event as CustomEvent<string>).detail; });
  function accountChanged(value: Account): void {
    account = value;
    const key = value.phase === 'authenticated' && value.account?.staff ? JSON.stringify(value.account) : '';
    if (key === accountKey) { render(); return; }
    ++version; accountKey = key; staff = !!key; busy = false; companiesLoaded = false; company.replaceChildren(); setUp = null; status.textContent = '';
    render();
    if (staff) void checkSetUp();
  }
  document.addEventListener('mixel-device-enrolled', () => { if (setUp !== true) void checkSetUp(); });
  document.addEventListener('mixel-account-state', event => accountChanged((event as CustomEvent<Account>).detail));
  document.addEventListener('mixel-remote-state', event => { phase = (event as CustomEvent<{phase: string}>).detail.phase; render(); });
  render();
  void invoke<Account>('account_status').then(accountChanged).catch(() => {});
})();
