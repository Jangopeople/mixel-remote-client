interface Window {
  __TAURI__: { core: { invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> } };
}
(() => {
  function element<T extends HTMLElement>(id: string): T {
    const node = document.getElementById(id);
    if (!node) throw Error(`Missing UI element ${id}`);
    return node as T;
  }
  const invoke = window.__TAURI__.core.invoke;
  type CompanyAdministration = {company_id: string; company_name: string};
  type AccountStatus = {phase: string; message: string; account: {has_company?: boolean; has_personal?: boolean; profile: string; organization: string | null; staff: boolean; display_name?: string | null; personal?: boolean; companies?: CompanyAdministration[]} | null; factors: {id: string; friendly_name: string | null}[]};
  const accountStatus = element<HTMLParagraphElement>('account-status');
  const accountError = element<HTMLParagraphElement>('account-error');
  const signIn = element<HTMLButtonElement>('account-sign-in');
  const alternateBrowser = element<HTMLButtonElement>('account-sign-in-link');
  const browserLink = element<HTMLDivElement>('account-browser-link');
  const browserUrl = element<HTMLInputElement>('account-browser-url');
  const registerMixel = element<HTMLButtonElement>('account-register-mixel');
  const deviceSettings = element<HTMLAnchorElement>('account-device-settings');
  const workspace = element<HTMLDivElement>('account-workspace');
  const workspaceLabel = element<HTMLElement>('account-workspace-label');
  const workspaceDetail = element<HTMLParagraphElement>('account-workspace-detail');
  const workspaceToggle = element<HTMLButtonElement>('account-workspace-toggle');
  const personalForm = element<HTMLFormElement>('account-personal-form');
  const personalName = element<HTMLInputElement>('account-personal-name');
  const personalCreate = element<HTMLButtonElement>('account-personal-create');
  const manageRemote = element<HTMLButtonElement>('account-remote-management');
  const profileSetup = element<HTMLFormElement>('account-profile-setup');
  const displayName = element<HTMLInputElement>('account-display-name');
  const saveProfile = element<HTMLButtonElement>('account-save-profile');
  const signOut = element<HTMLButtonElement>('account-cancel');
  const mfa = element<HTMLFormElement>('account-mfa');
  const factor = element<HTMLSelectElement>('account-factor');
  const code = element<HTMLInputElement>('account-code');
  const verify = element<HTMLButtonElement>('account-verify');
  const companyPanel = element<HTMLElement>('account-companies');
  const companyList = element<HTMLDivElement>('account-company-list');
  const companyError = element<HTMLParagraphElement>('account-company-error');
  let displayedCompanies = '';
  let account: AccountStatus | undefined, accountBusy = false, pollingAccount = false, accountReadVersion = 0;
  let pendingBrowserUrl = '', showBrowserUrl = false;
  function renderCompanies(companies: CompanyAdministration[]): void {
    const signature = JSON.stringify(companies);
    if (signature === displayedCompanies) return;
    displayedCompanies = signature;
    companyPanel.hidden = companies.length === 0;
    companyError.textContent = '';
    companyList.replaceChildren();
    for (const company of companies) {
      const row = document.createElement('div');
      row.className = 'setting-row';
      const name = document.createElement('strong');
      name.textContent = company.company_name;
      const settings = document.createElement('button');
      settings.className = 'button-secondary';
      settings.type = 'button';
      settings.textContent = 'Manage company';
      settings.setAttribute('aria-label', `Manage ${company.company_name}`);
      settings.addEventListener('click', async () => {
        settings.disabled = true;
        companyError.textContent = '';
        try { await invoke<void>('open_company_settings', {companyId: company.company_id}); }
        catch (error) { if (settings.isConnected) companyError.textContent = String(error); }
        finally { settings.disabled = false; }
      });
      row.append(name, settings);
      companyList.append(row);
    }
  }
  function renderAccount(): void {
    const phase = account?.phase;
    renderCompanies(phase === 'authenticated' ? account?.account?.companies ?? [] : []);
    document.dispatchEvent(new CustomEvent('mixel-account-state',{detail:account ?? {phase:'signed_out',account:null}}));
    signIn.disabled = accountBusy || !account || !['signed_out', 'error'].includes(phase!);
    alternateBrowser.disabled = signIn.disabled &&
      (accountBusy || phase !== 'waiting_browser' || !pendingBrowserUrl || showBrowserUrl);
    if (phase !== 'waiting_browser' && !accountBusy) {
      pendingBrowserUrl = '';
      showBrowserUrl = false;
    }
    browserUrl.value = phase === 'waiting_browser' && showBrowserUrl ? pendingBrowserUrl : '';
    browserLink.hidden = !browserUrl.value;
    signOut.disabled = accountBusy || !account || ['signed_out', 'error'].includes(phase!);
    signOut.textContent = phase === 'authenticated' ? 'Sign out' : 'Cancel sign-in';
    const actionRow = signIn.parentElement as HTMLElement | null;
    if (actionRow?.dataset) actionRow.dataset.phase = phase ?? '';
    mfa.hidden = phase !== 'mfa_required';
    profileSetup.hidden = phase !== 'profile_setup';
    deviceSettings.hidden = phase !== 'authenticated' || !(account?.account?.personal || account?.account?.staff);
    deviceSettings.textContent = account?.account?.staff ? 'Set up this computer' : 'Register a device';
    saveProfile.disabled = displayName.disabled = accountBusy;
    const info = phase === 'authenticated' ? account?.account : undefined;
    workspace.hidden = !(info?.has_company && info?.has_personal);
    personalForm.hidden = !(info?.has_company && !info?.has_personal);
    workspaceLabel.textContent = info?.personal ? 'Using your personal devices' : 'Using your company devices';
    workspaceDetail.textContent = info?.personal ? 'Connection IDs and devices of your personal space.' : 'Devices your company manages. Your personal devices are one switch away.';
    workspaceToggle.textContent = info?.personal ? 'Switch to company devices' : 'Switch to personal devices';
    workspaceToggle.disabled = personalCreate.disabled = personalName.disabled = accountBusy;
    registerMixel.disabled = signIn.disabled;
    if (phase !== 'profile_setup') displayName.value = '';
    verify.disabled = accountBusy;
    factor.disabled = accountBusy;
    code.disabled = accountBusy;
    if (account) accountStatus.textContent = account.message + (account.account?.display_name ? ` · ${account.account.display_name}` : '') + (phase === 'authenticated' ? '. Next: choose “' + (account.account?.staff ? 'Set up this computer' : 'Register a device') + '” to give this computer its connection ID, then open Remote control.' : '');
    if (phase !== 'mfa_required') code.value = '';
    const previous = factor.value;
    factor.replaceChildren();
    for (const item of account?.factors ?? []) {
      const option = document.createElement('option');
      option.value = item.id; option.textContent = item.friendly_name || 'Authenticator app';
      factor.appendChild(option);
    }
    if (Array.from(factor.options).some(option => option.value === previous)) factor.value = previous;
  }
  async function readAccount(force = false): Promise<void> {
    if ((pollingAccount || accountBusy || document.hidden) && !force) return;
    pollingAccount = true; const version = ++accountReadVersion;
    try { const status = await invoke<AccountStatus>('account_status'); if (version === accountReadVersion) { account = status; renderAccount(); } }
    catch (error) { if (version === accountReadVersion) { account=undefined; renderAccount(); accountError.textContent = String(error); signIn.disabled = signOut.disabled = true; } }
    finally { if (version === accountReadVersion) pollingAccount = false; }
  }
  async function accountAction(command: string, args?: Record<string, unknown>): Promise<void> {
    if (accountBusy) return;
    ++accountReadVersion; accountBusy = true; accountError.textContent = ''; renderAccount();
    try {
      const result = await invoke<string | null>(command, args);
      if (command === 'account_sign_in' && result) {
        pendingBrowserUrl = result;
        showBrowserUrl = args?.showLink === true;
      }
    }
    catch (error) { accountError.textContent = String(error); }
    finally { accountBusy = false; await readAccount(true); renderAccount(); }
  }
  registerMixel.addEventListener('click', () => { void accountAction('open_mixel_registration'); });
  manageRemote.addEventListener('click', () => { void accountAction('open_remote_management'); });
  profileSetup.addEventListener('submit', event => {
    event.preventDefault();
    if (accountBusy || account?.phase !== 'profile_setup' || !profileSetup.reportValidity()) return;
    const name = displayName.value.trim();
    if (name) void accountAction('account_setup', {name});
  });
  void invoke<boolean>('remote_signup_available').then(available => { registerMixel.hidden = !available; }).catch(() => {});
  signIn.addEventListener('click', () => { void accountAction('account_sign_in'); });
  alternateBrowser.addEventListener('click', () => {
    if (account?.phase === 'waiting_browser' && pendingBrowserUrl) {
      showBrowserUrl = true;
      renderAccount();
      browserUrl.select();
      return;
    }
    void accountAction('account_sign_in', {showLink: true});
  });
  workspaceToggle.addEventListener('click', () => { void accountAction('account_workspace', {personal: !account?.account?.personal}); });
  personalForm.addEventListener('submit', event => {
    event.preventDefault();
    const name = personalName.value.trim();
    if (!name || accountBusy) return;
    void accountAction('account_create_personal', {name}).then(() => { personalName.value = ''; });
  });
  signOut.addEventListener('click', () => { code.value = ''; account=undefined; void accountAction('account_cancel'); });
  mfa.addEventListener('submit', event => {
    event.preventDefault();
    if (accountBusy || account?.phase !== 'mfa_required' || !mfa.reportValidity()) return;
    const entered = code.value; code.value = '';
    void accountAction('account_verify', {factor: factor.value, code: entered});
  });
  const updateNotice = element<HTMLButtonElement>('update-notice');
  async function checkUpdate(): Promise<void> {
    try {
      const version = await invoke<string | null>('update_check');
      updateNotice.hidden = !version;
      if (version) updateNotice.textContent = `Mixel Remote ${version} is available · Download`;
    } catch { /* offline: keep the current notice */ }
  }
  updateNotice.addEventListener('click', () => { void invoke('open_update_page').catch(() => {}); });
  void checkUpdate();
  window.setInterval(() => { void checkUpdate(); }, 6 * 3600 * 1000);
  const languageChoice = document.getElementById('language-choice') as HTMLSelectElement | null;
  if (languageChoice) {
    const language = window as unknown as {mixelLanguageChoice?: string; mixelSetLanguage?: (value: string) => void};
    languageChoice.value = language.mixelLanguageChoice ?? 'auto';
    languageChoice.addEventListener('change', () => language.mixelSetLanguage?.(languageChoice.value));
  }
  void readAccount();
  window.setInterval(() => { void readAccount(); }, 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void readAccount(); });
  const identityStatus = element<HTMLParagraphElement>('identity-status');
  const identityButtons = [element<HTMLButtonElement>('load-identity'), element<HTMLButtonElement>('create-identity')];
  let identityBusy = false;
  async function openIdentity(create: boolean): Promise<void> {
    if (identityBusy) return;
    identityBusy = true;
    identityButtons.forEach(button => { button.disabled = true; });
    try {
      const identity = await invoke<{public_key: string; storage: string; enrolled: boolean}>('open_device_identity', {create});
      if (!/^[0-9a-f]{64}$/.test(identity.public_key) || identity.enrolled) throw Error('Invalid local identity response');
      identityStatus.textContent = `Saved public device key: ${identity.public_key} · ${identity.storage} · Not enrolled`;
    } catch (error) { identityStatus.textContent = String(error); }
    finally { identityBusy = false; identityButtons.forEach(button => { button.disabled = false; }); }
  }
  identityButtons[0].addEventListener('click', () => { void openIdentity(false); });
  identityButtons[1].addEventListener('click', () => { void openIdentity(true); });
})();
