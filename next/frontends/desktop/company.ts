// The signed-in person's Mixel Remote company (SaaS tenant): plan, trial, usage and limits, members
// and invitations, and "Create a company" for a Mixel ID without one. Announces the company to the
// other pages (Devices, This computer) with the event mixel-company-state. Staff (Mixel team),
// ISM company members and personal spaces have no tenant: nothing here is shown for them.
// The rules mirror the database (next/sql/019): it decides; the app only hides what would be refused.
(() => {
  function element<T extends HTMLElement>(id: string): T { const node = document.getElementById(id); if (!node) throw Error(`Missing company control ${id}`); return node as T; }
  const invoke = window.__TAURI__.core.invoke;
  type Account = {phase: string; account: {profile: string; organization: string | null; staff: boolean; personal?: boolean; has_company?: boolean} | null};
  type Tenant = {tenant_id: string; organization_id: string; name: string; slug: string; role: string; plan: string; status: string; trial_ends_at: string | null};
  type Overview = {tenant_id: string; name: string; role: string; plan: string; status: string; trial_ends_at: string | null; features: Record<string, boolean>; limits: {technicians: number | null; devices: number | null; sessions: number | null}; usage: {technicians: number; devices: number; members: number}};
  type Member = {user_id: string; name: string; email: string; role: string; created_at: string};
  type Invite = {id: string; email: string; role: string; created_at: string; expires_at: string; mail_state?: string | null; mail_sent_at?: string | null; mail_sends?: number | null};
  type Dialog = {open(options: {title: string; text?: string; fields?: unknown[]; confirm: string; danger?: boolean; run: (values: Record<string, string>) => Promise<HTMLElement | void>}): void};
  const dialog = (): Dialog => (window as unknown as {mixelDialog: Dialog}).mixelDialog;
  const tr = (text: string): string => (window as unknown as {mixelText?: (value: string) => string}).mixelText?.(text) ?? text;
  const panel = element<HTMLElement>('company-panel'), nameLine = element<HTMLElement>('company-name'), roleLine = element<HTMLElement>('company-role');
  const planLine = element<HTMLElement>('company-plan'), statusLine = element<HTMLElement>('company-status'), usage = element<HTMLElement>('company-usage');
  const membersBox = element<HTMLElement>('company-members-section'), members = element<HTMLUListElement>('company-members'), invites = element<HTMLUListElement>('company-invites');
  const rename = element<HTMLButtonElement>('company-rename');
  const invitesTitle = element<HTMLElement>('company-invites-title'), invite = element<HTMLButtonElement>('company-invite'), membersStatus = element<HTMLElement>('company-members-status'), problem = element<HTMLElement>('company-error');
  const signup = element<HTMLFormElement>('company-signup'), signupName = element<HTMLInputElement>('company-signup-name'), signupSubmit = element<HTMLButtonElement>('company-signup-submit'), signupStatus = element<HTMLElement>('company-signup-status');
  const PLAN: Record<string, string> = {personal: 'Personal', solo: 'Solo', team: 'Team', business: 'Business', enterprise: 'Enterprise'};
  const ROLE: Record<string, string> = {owner: 'Owner', admin: 'Administrator', technician: 'Technician', member: 'Member'};
  const writable = (status: string): boolean => ['active', 'trial', 'past_due'].includes(status);
  // The language of invitation e-mails: the app's language (English, German, French or Italian).
  const mailLocale = (): string => { const lang = document.documentElement?.lang ?? 'en'; return ['en', 'de', 'fr', 'it'].includes(lang) ? lang : 'en'; };
  function mailText(item: Invite): string {
    switch (item.mail_state) {
      case 'pending': case 'sending': return 'e-mail being sent';
      case 'failed': return 'e-mail not delivered';
      case 'sent': return item.mail_sent_at ? `e-mail sent ${new Date(item.mail_sent_at).toLocaleDateString()}` : 'e-mail sent';
      default: return '';
    }
  }
  // Present from the start, so the Devices page knows to wait for the company before loading.
  (window as unknown as {mixelCompany: unknown}).mixelCompany = {tenant: null, overview: null};
  let account: Account = {phase: 'signed_out', account: null}, accountKey = '', version = 0, busy = false;
  let tenant: Tenant | null = null, overview: Overview | null = null, memberList: Member[] = [], inviteList: Invite[] = [];
  function announce(): void {
    (window as unknown as {mixelCompany: unknown}).mixelCompany = {tenant, overview};
    document.dispatchEvent(new CustomEvent('mixel-company-state', {detail: {tenant, overview}}));
    // Colleagues a technician can invite into a running session.
    document.dispatchEvent(new CustomEvent('mixel-company-members', {detail: {members: memberList.map(item => ({user_id: item.user_id, name: item.name, role: item.role})), me: account.phase === 'authenticated' ? account.account?.profile ?? '' : ''}}));
  }
  function daysLeft(until: string | null): number {
    if (!until) return 0;
    return Math.max(0, Math.ceil((new Date(until).getTime() - Date.now()) / 86400000));
  }
  function limitText(limit: number | null): string { return limit === null || limit === undefined ? 'no limit' : String(limit); }
  function technicianRoom(): number {
    if (!overview || overview.limits.technicians === null) return Infinity;
    const pending = inviteList.filter(item => item.role === 'admin' || item.role === 'technician').length;
    return overview.limits.technicians - overview.usage.technicians - pending;
  }
  const owners = (): number => memberList.filter(item => item.role === 'owner').length;
  // Mirrors remote_saas_set_member_role: owners set any role; admins set admin/technician/member on non-owners.
  function roleChoices(target: Member): string[] {
    if (!overview || !writable(overview.status)) return [];
    if (overview.role === 'owner') return ['owner', 'admin', 'technician', 'member'];
    if (overview.role === 'admin' && target.role !== 'owner') return ['admin', 'technician', 'member'];
    return [];
  }
  // Mirrors remote_saas_remove_member: owners/admins remove (admins not owners); anyone may leave; one owner stays.
  function mayRemove(target: Member, self: boolean): boolean {
    if (!overview) return false;
    if (target.role === 'owner' && owners() <= 1) return false;
    if (self) return true;
    return overview.role === 'owner' || (overview.role === 'admin' && target.role !== 'owner');
  }
  function render(): void {
    const info = account.phase === 'authenticated' ? account.account : null;
    // "Create a company": a Mixel ID that is not yet set up, or has only a personal space.
    signup.hidden = !(account.phase === 'profile_setup' || (info && !info.staff && !info.has_company));
    signupName.disabled = signupSubmit.disabled = busy;
    panel.hidden = !tenant;
    if (!tenant) return;
    const view = overview ?? {...tenant, features: {}, limits: {technicians: null, devices: null, sessions: null}, usage: {technicians: 0, devices: 0, members: 0}} as Overview;
    nameLine.textContent = view.name;
    roleLine.textContent = `${ROLE[view.role] ?? view.role}`;
    planLine.textContent = `${PLAN[view.plan] ?? view.plan} plan`;
    statusLine.dataset.state = view.status;
    const days = daysLeft(view.trial_ends_at);
    statusLine.textContent = view.status === 'trial' ? (days > 1 ? `Free trial · ${days} days left` : days === 1 ? 'Free trial · 1 day left' : 'Free trial · ends today')
      : view.status === 'active' ? 'Active'
      : view.status === 'past_due' ? 'Payment overdue. Please update the payment details in the Mixel Remote portal.'
      : view.status === 'read_only' ? 'Read-only: the trial has ended. Devices are kept for 30 days. You can still remove devices and members; everything else needs a paid plan.'
      : 'This company is closed.';
    usage.replaceChildren();
    if (overview) {
      for (const [label, used, limit] of [['Devices', overview.usage.devices, overview.limits.devices], ['Technicians', overview.usage.technicians, overview.limits.technicians], ['Members', overview.usage.members, null]] as [string, number, number | null][]) {
        const row = document.createElement('div'), caption = document.createElement('span'), value = document.createElement('strong'), bar = document.createElement('span');
        row.className = 'usage-row'; caption.textContent = label; value.dataset.noTranslate = ''; value.textContent = label === 'Members' ? String(used) : `${used} / ${limitText(limit)}`;
        bar.className = 'usage-bar';
        if (limit !== null && limit > 0) { const fill = Math.min(100, Math.round(used / limit * 100)); bar.style.setProperty?.('--fill', `${fill}%`); bar.dataset.full = String(used >= limit); }
        else bar.hidden = true;
        row.append(caption, value, bar); usage.append(row);
      }
    }
    // Members: owners, admins and technicians see the list; members only themselves (the database refuses the list).
    const listing = ['owner', 'admin', 'technician'].includes(view.role);
    membersBox.hidden = false;
    invite.hidden = !['owner', 'admin'].includes(view.role);
    rename.hidden = !['owner', 'admin'].includes(view.role);
    rename.disabled = busy || !writable(view.status);
    rename.title = !writable(view.status) ? 'This company is read-only' : '';
    invite.disabled = busy || !writable(view.status);
    invite.title = !writable(view.status) ? 'This company is read-only' : '';
    members.replaceChildren();
    const shown = listing ? memberList : memberList.filter(item => item.user_id === info?.profile);
    for (const person of shown) {
      const row = document.createElement('li'), who = document.createElement('div'), name = document.createElement('strong'), email = document.createElement('span'), role = document.createElement('span'), actions = document.createElement('div');
      const self = person.user_id === info?.profile;
      name.textContent = person.name; name.dataset.noTranslate = ''; email.textContent = person.email; email.dataset.noTranslate = '';
      who.append(name, email); role.className = 'member-role'; role.textContent = ROLE[person.role] ?? person.role;
      if (self) { const you = document.createElement('small'); you.className = 'small-badge'; you.textContent = 'You'; who.append(you); }
      actions.className = 'member-actions';
      const choices = roleChoices(person);
      if (choices.length) {
        const change = document.createElement('button'); change.type = 'button'; change.className = 'button-quiet'; change.textContent = 'Change role';
        change.setAttribute('aria-label', `Change the role of ${person.name}`);
        change.disabled = busy;
        change.addEventListener('click', () => changeRole(person, choices));
        actions.append(change);
      }
      const removable = mayRemove(person, self);
      if (removable) {
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button-quiet'; remove.textContent = self ? 'Leave company' : 'Remove';
        remove.setAttribute('aria-label', self ? 'Leave this company' : `Remove ${person.name}`);
        remove.disabled = busy;
        remove.addEventListener('click', () => removeMember(person, self));
        actions.append(remove);
      } else if (person.role === 'owner' && owners() <= 1 && (self || overview?.role === 'owner')) {
        const note = document.createElement('small'); note.className = 'field-hint'; note.textContent = 'A company needs at least one owner.'; actions.append(note);
      }
      row.append(who, role, actions); members.append(row);
    }
    invitesTitle.hidden = invites.hidden = !inviteList.length || !['owner', 'admin'].includes(view.role);
    invites.replaceChildren();
    for (const item of inviteList) {
      const row = document.createElement('li'), label = document.createElement('span'), email = document.createElement('strong'), detail = document.createElement('span');
      email.textContent = item.email; email.dataset.noTranslate = '';
      const mail = mailText(item);
      detail.textContent = `${ROLE[item.role] ?? item.role} · valid until ${new Date(item.expires_at).toLocaleDateString()}${mail ? ` · ${tr(mail)}` : ''}`;
      label.append(email, detail);
      const actions = document.createElement('div'); actions.className = 'member-actions';
      const resend = document.createElement('button'); resend.type = 'button'; resend.className = 'button-quiet'; resend.textContent = 'Send again';
      resend.setAttribute('aria-label', `Send the invitation of ${item.email} again`); resend.disabled = busy || !writable(view.status) || item.mail_state === 'pending' || item.mail_state === 'sending';
      resend.addEventListener('click', () => { void act(async () => { await invoke('saas_resend_invite', {tenant: tenant!.tenant_id, invite: item.id, locale: mailLocale()}); membersStatus.textContent = `A new invitation e-mail is on its way to ${item.email}. The previous link no longer works.`; }); });
      const revoke = document.createElement('button'); revoke.type = 'button'; revoke.className = 'button-quiet'; revoke.textContent = 'Revoke';
      revoke.setAttribute('aria-label', `Revoke the invitation of ${item.email}`); revoke.disabled = busy;
      revoke.addEventListener('click', () => { void act(async () => { await invoke('saas_revoke_invite', {tenant: tenant!.tenant_id, invite: item.id}); membersStatus.textContent = `Invitation of ${item.email} revoked.`; }); });
      actions.append(resend, revoke);
      row.append(label, actions); invites.append(row);
    }
    // While an e-mail is on its way, look again shortly so the state turns to "sent".
    if (inviteList.some(item => item.mail_state === 'pending' || item.mail_state === 'sending')) scheduleRefresh();
  }
  let refreshTimer = false;
  function scheduleRefresh(): void {
    if (refreshTimer) return;
    refreshTimer = true;
    window.setTimeout(() => { refreshTimer = false; if (!busy) void load(); }, 5000);
  }
  async function load(): Promise<void> {
    const request = ++version;
    const info = account.phase === 'authenticated' ? account.account : null;
    if (!info || info.staff || info.personal) { tenant = null; overview = null; memberList = []; inviteList = []; render(); announce(); return; }
    try {
      const list = await invoke<Tenant[] | undefined>('saas_my_tenants');
      if (request !== version) return;
      tenant = Array.isArray(list) && list.length ? list[0] : null;
      if (!tenant) { overview = null; memberList = []; inviteList = []; render(); announce(); return; }
      problem.textContent = '';
      const id = tenant.tenant_id;
      overview = await invoke<Overview>('saas_tenant_overview', {tenant: id});
      if (request !== version) return;
      const listing = ['owner', 'admin', 'technician'].includes(overview.role);
      memberList = listing ? await invoke<Member[]>('saas_members', {tenant: id}) : [];
      if (!listing && info) memberList = [{user_id: info.profile, name: tr('You'), email: '', role: overview.role, created_at: ''}];
      inviteList = ['owner', 'admin'].includes(overview.role) ? await invoke<Invite[]>('saas_invites', {tenant: id}) : [];
      if (request !== version) return;
    } catch (error) {
      if (request !== version) return;
      problem.textContent = String(error);
    }
    render(); announce();
  }
  async function act(work: () => Promise<void>): Promise<void> {
    if (busy || !tenant) return;
    busy = true; problem.textContent = ''; render();
    try { await work(); } catch (error) { problem.textContent = String(error); }
    finally { busy = false; await load(); }
  }
  function roleOptions(choices: string[], current: string): {value: string; label: string; disabled?: boolean}[] {
    const room = technicianRoom();
    return choices.map(role => {
      const promotes = current === 'member' && role !== 'member';
      return {value: role, label: tr(ROLE[role]) + (promotes && room <= 0 ? ` (${tr('plan limit reached')})` : ''), disabled: promotes && room <= 0};
    });
  }
  function changeRole(person: Member, choices: string[]): void {
    const target = tenant!;
    dialog().open({
      title: 'Change role', text: `${person.name} · ${person.email}`,
      fields: [{id: 'role', kind: 'select', label: 'Role', value: person.role, options: roleOptions(choices, person.role), hint: 'Owners and administrators manage the company. Technicians connect to and manage devices. Members use the devices they added.'}],
      confirm: 'Save role',
      run: async values => {
        if (values.role === person.role) return;
        await invoke('saas_set_member_role', {tenant: target.tenant_id, member: person.user_id, role: values.role});
        membersStatus.textContent = `Role of ${person.name} changed to ${tr(ROLE[values.role] ?? values.role)}.`;
        await load();
      },
    });
  }
  function removeMember(person: Member, self: boolean): void {
    const target = tenant!;
    dialog().open({
      title: self ? 'Leave this company?' : `Remove ${person.name}?`,
      text: self ? `You lose access to the devices of ${target.name} at once and are signed out. An owner or administrator can invite you again.` : `${person.name} loses access to the devices of ${target.name} at once; running sessions end at the next check. The devices this person added stay in the company.`,
      confirm: self ? 'Leave company' : 'Remove member', danger: true,
      run: async () => {
        await invoke('saas_remove_member', {tenant: target.tenant_id, member: person.user_id});
        if (self) { await invoke('account_cancel').catch(() => {}); tenant = null; overview = null; render(); announce(); return; }
        membersStatus.textContent = `${person.name} was removed from the company.`;
        await load();
      },
    });
  }
  rename.addEventListener('click', () => {
    if (!tenant || !overview) return;
    const target = tenant;
    dialog().open({
      title: 'Rename the company', text: 'The new name is shown to everyone in the company and on the Mixel Remote portal.',
      fields: [{id: 'name', kind: 'text', label: 'Company name', value: overview.name, maxLength: 80, required: true}],
      confirm: 'Rename',
      run: async values => {
        const name = (values.name ?? '').trim();
        if (name.length < 2 || name.length > 80) throw new Error(tr('Enter a company name of 2 to 80 characters.'));
        const saved = await invoke<string>('saas_rename_tenant', {tenant: target.tenant_id, name});
        membersStatus.textContent = `Company renamed to ${saved}.`;
        await load();
      },
    });
  });
  invite.addEventListener('click', () => {
    if (!tenant || !overview) return;
    const target = tenant, room = technicianRoom();
    dialog().open({
      title: 'Invite a person', text: `Mixel Remote e-mails an invitation link to this address. The person signs in with it and joins ${target.name}.`,
      fields: [
        {id: 'email', kind: 'email', label: 'Email address', maxLength: 254},
        {id: 'role', kind: 'select', label: 'Role', value: room > 0 ? 'technician' : 'member', options: [
          {value: 'technician', label: tr('Technician') + (room <= 0 ? ` (${tr('plan limit reached')})` : ''), disabled: room <= 0},
          {value: 'admin', label: tr('Administrator') + (room <= 0 ? ` (${tr('plan limit reached')})` : ''), disabled: room <= 0},
          {value: 'member', label: tr('Member')}],
        hint: room <= 0 ? 'Technician limit of your plan reached: you can still invite members.' : 'Administrators and technicians count against the technicians of your plan.'},
      ],
      confirm: 'Send invitation',
      run: async values => {
        const id = await invoke<string>('saas_invite_member', {tenant: target.tenant_id, email: values.email, role: values.role, locale: mailLocale()});
        if (typeof id !== 'string' || !/^[0-9a-f-]{36}$/.test(id)) throw Error('Invalid invitation response');
        membersStatus.textContent = `Invitation e-mail on its way to ${values.email}. The link in it works for 7 days, only with this e-mail address.`;
        await load();
      },
    });
  });
  signup.addEventListener('submit', async event => {
    event.preventDefault();
    const name = signupName.value.trim();
    if (busy || !name) return;
    if (name.length < 2 || name.length > 80) { signupStatus.textContent = 'Enter a company name of 2 to 80 characters.'; return; }
    busy = true; render(); signupStatus.textContent = 'Creating your company…';
    try {
      await invoke('saas_signup', {company: name});
      signupName.value = '';
      signupStatus.textContent = 'Your company is ready. Your free 14-day Business trial has started: add this computer in Devices.';
    } catch (error) { signupStatus.textContent = String(error); }
    finally { busy = false; render(); }
  });
  function accountChanged(value: Account): void {
    const key = value.phase + '|' + (value.phase === 'authenticated' && value.account ? JSON.stringify(value.account) : '');
    account = value;
    if (key === accountKey) return;
    accountKey = key; tenant = null; overview = null; memberList = []; inviteList = []; membersStatus.textContent = ''; problem.textContent = '';
    render(); void load();
  }
  document.addEventListener('mixel-account-state', event => accountChanged((event as CustomEvent<Account>).detail));
  document.addEventListener('mixel-company-refresh', () => { if (tenant) void load(); });
  // Trial days and usage stay current.
  window.setInterval(() => { if (tenant && !document.hidden && !busy) void load(); }, 60000);
  render();
})();
