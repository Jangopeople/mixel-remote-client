// A second technician in a running session, and switching sides.
// Technician: "Invite a colleague" sends an invitation (a colleague of the company, or the Remote ID of
// their computer); invitations from colleagues appear with Join. Person at the computer: allows or
// declines a colleague (viewing, or viewing and control) and sees who is connected. Both sides:
// "Switch sides" shows the other screen instead, in the same session. Where the other app is too old,
// the Mixel Meet links ("Invite a colleague", "Show my screen") stay as they were.
(() => {
  function element<T extends HTMLElement>(id: string): T { const value = document.getElementById(id); if (!value) throw Error(`Missing colleague control ${id}`); return value as T; }
  const invoke = window.__TAURI__.core.invoke;
  type Colleague = {session: string; connection: number; control: boolean; state: 'asking' | 'connecting' | 'active'};
  type JoinRequest = {session: string; connection: number; inviter_connection: number; control: boolean; unattended: boolean};
  type Invitation = {invitation: string; host: number; inviter: string; inviter_connection: number; control: boolean; unattended: boolean; expires_at: number};
  type Member = {user_id: string; name: string; role: string};
  type State = {phase: string; session: string | null; sharing: boolean; control: boolean; peer: number | null; colleagues?: Colleague[]; join_request?: JoinRequest | null; invitations?: Invitation[]; invite_message?: string | null; joined_session?: boolean; switched?: boolean; peer_features?: string[]; host_actions?: string[]};
  const formatId = (id: number): string => String(id).replace(/(\d{3})(?=\d)/g, '$1 ');
  const request = element<HTMLDivElement>('colleague-request'), requestText = element<HTMLParagraphElement>('colleague-request-text');
  const list = element<HTMLDivElement>('colleague-list'), items = element<HTMLUListElement>('colleague-items');
  const invitations = element<HTMLDivElement>('colleague-invitations');
  const hostSwitch = element<HTMLDivElement>('host-switch'), hostSwitchButton = element<HTMLButtonElement>('host-switch-button');
  const switchButton = element<HTMLButtonElement>('remote-switch');
  const panel = element<HTMLDivElement>('invite-panel'), member = element<HTMLSelectElement>('invite-member'), device = element<HTMLInputElement>('invite-device');
  const askControl = element<HTMLInputElement>('invite-control'), send = element<HTMLButtonElement>('invite-send'), inviteStatus = element<HTMLParagraphElement>('invite-status');
  const limits = element<HTMLParagraphElement>('remote-host-limits');
  let state: State | null = null, members: Member[] = [], me = '', busy = false, shownMessage = '';
  // The app reads its state four times a second: rebuild the colleague and invitation rows only when
  // they change, or a click that falls between two reads lands on a removed button and is lost.
  let shownColleagues = '', shownOffers = '';
  const seenOffers = new Set<string>();
  // The state is read four times a second: change a button's label only when its text changes. A
  // replaced text node under the pointer makes WebKitGTK (Linux) drop the click, and a translated
  // label would be reset to English on every read.
  const labels = new WeakMap<HTMLElement, string>();
  const label = (target: HTMLElement, text: string): void => { if (labels.get(target) !== text) { labels.set(target, text); target.textContent = text; } };
  const names = (): Map<string, string> => new Map(members.map(person => [person.user_id, person.name]));
  const supports = (name: string): boolean => (state?.peer_features ?? []).includes(name);
  const active = (): boolean => state?.phase === 'active' && !!state.session;
  async function run(command: string, args: Record<string, unknown>): Promise<boolean> {
    if (busy) return false;
    busy = true; render();
    try { await invoke(command, args); return true; }
    catch (value) { inviteStatus.textContent = String(value); return false; }
    finally { busy = false; render(); }
  }
  function render(): void {
    const current = state;
    // Person at the computer: a colleague asks to join.
    const asking = current?.join_request && current.sharing && active() ? current.join_request : null;
    request.hidden = !asking;
    if (asking) {
      requestText.textContent = `A colleague of your technician (Remote ID ${formatId(asking.connection)}) wants to see this screen too${asking.control ? ' and asks for keyboard and mouse' : ''}. Your technician invited them.`;
      element<HTMLButtonElement>('colleague-control').hidden = !asking.control;
    }
    for (const id of ['colleague-decline', 'colleague-view', 'colleague-control']) element<HTMLButtonElement>(id).disabled = busy;
    // Person at the computer: who else is connected.
    const others = current?.sharing && active() ? (current.colleagues ?? []).filter(item => item.state !== 'asking') : [];
    list.hidden = !others.length;
    const colleaguesKey = JSON.stringify([busy, others.map(item => [item.session, item.connection, item.control, item.state])]);
    if (colleaguesKey !== shownColleagues) { shownColleagues = colleaguesKey; items.replaceChildren(...others.map(item => {
      const row = document.createElement('li'), label = document.createElement('span'), remove = document.createElement('button');
      label.textContent = `Technician ${formatId(item.connection)} · ${item.state === 'active' ? (item.control ? 'viewing and control' : 'viewing') : 'connecting'}`;
      remove.type = 'button'; remove.className = 'button-quiet'; remove.textContent = 'Remove'; remove.disabled = busy;
      remove.setAttribute('aria-label', `Remove technician ${formatId(item.connection)}`);
      remove.addEventListener('click', () => { void run('remote_remove_colleague', {session: item.session}); });
      row.append(label, remove);
      return row;
    })); }
    // Switching sides: the person at the computer and the technician both have the button.
    const canSwitch = active() && supports('switch') && !current?.joined_session && !(current?.colleagues ?? []).length;
    hostSwitch.hidden = !(canSwitch && current?.sharing);
    label(hostSwitchButton, current?.switched ? 'Switch back' : 'Switch sides');
    switchButton.hidden = !(canSwitch && !current?.sharing);
    label(switchButton, current?.switched ? 'Switch back' : 'Switch sides');
    switchButton.disabled = hostSwitchButton.disabled = busy;
    // Technician: invitations from colleagues.
    const now = Date.now() / 1000;
    const offers = (current?.invitations ?? []).filter(offer => offer.expires_at > now);
    invitations.hidden = !offers.length;
    const offersKey = JSON.stringify([busy, state?.phase === 'ready', offers.map(offer => [offer.invitation, offer.host, offer.inviter, offer.inviter_connection, offer.control, names().get(offer.inviter) ?? null])]);
    if (offersKey !== shownOffers) { shownOffers = offersKey; invitations.replaceChildren(...offers.map(offer => {
      const row = document.createElement('div'), text = document.createElement('p'), join = document.createElement('button'), later = document.createElement('button');
      const who = names().get(offer.inviter) ?? `A colleague (Remote ID ${formatId(offer.inviter_connection)})`;
      text.textContent = `${who} invites you to join their session on computer ${formatId(offer.host)}${offer.control ? ' with keyboard and mouse' : ''}.`;
      join.type = later.type = 'button'; join.className = 'button-primary'; later.className = 'button-secondary';
      join.textContent = 'Join'; later.textContent = 'Not now';
      join.disabled = later.disabled = busy || state?.phase !== 'ready';
      join.title = state?.phase !== 'ready' ? 'Finish the current session first' : '';
      join.addEventListener('click', () => { void run('remote_join', {invitation: offer.invitation}); });
      later.addEventListener('click', () => { void run('remote_dismiss_invitation', {invitation: offer.invitation}); });
      row.append(text, join, later);
      return row;
    })); }
    // A new invitation sits below the connection cards: bring it into view while this app is free.
    const fresh = offers.filter(offer => !seenOffers.has(offer.invitation));
    for (const offer of fresh) seenOffers.add(offer.invitation);
    if (fresh.length && state?.phase === 'ready') invitations.scrollIntoView?.({block: 'nearest', behavior: 'smooth'});
    // Technician: the invitation panel closes when the session ends.
    if (!active() || current?.sharing) panel.hidden = true;
    send.disabled = busy;
    const message = current?.invite_message ?? '';
    if (message && message !== shownMessage) { shownMessage = message; inviteStatus.textContent = message; }
    // A Wayland desktop cannot cover the screen, block input or show a pointer for other programs.
    const wayland = active() && !current?.sharing && (current?.host_actions ?? []).includes('wayland');
    limits.hidden = !wayland;
    limits.textContent = wayland ? 'This Linux computer uses Wayland: the privacy screen, blocking its keyboard and mouse and showing your pointer are not possible there. Ask the person to sign in with “Ubuntu on Xorg” for these.' : '';
  }
  function fillMembers(): void {
    const keep = member.value;
    const choices = members.filter(person => person.user_id !== me && ['owner', 'admin', 'technician'].includes(person.role));
    const first = document.createElement('option'); first.value = ''; first.textContent = choices.length ? 'Choose a colleague…' : 'No colleagues listed';
    member.replaceChildren(first, ...choices.map(person => { const option = document.createElement('option'); option.value = person.user_id; option.textContent = person.name; return option; }));
    member.value = choices.some(person => person.user_id === keep) ? keep : '';
  }
  document.addEventListener('mixel-remote-state', event => {
    state = (event as CustomEvent<State>).detail;
    render();
  });
  document.addEventListener('mixel-company-members', event => {
    const detail = (event as CustomEvent<{members: Member[]; me: string}>).detail;
    members = detail.members ?? []; me = detail.me ?? '';
    fillMembers(); render();
  });
  // "Invite a colleague": the native invitation when the computer supports it, else a meeting link.
  document.addEventListener('mixel-invite-colleague', () => {
    if (!supports('join') || state?.joined_session) { document.dispatchEvent(new CustomEvent('mixel-invite-meeting')); return; }
    panel.hidden = false; inviteStatus.textContent = ''; shownMessage = state?.invite_message ?? '';
    fillMembers();
    member.focus();
  });
  element<HTMLButtonElement>('invite-close').addEventListener('click', () => { panel.hidden = true; });
  element<HTMLButtonElement>('invite-meet').addEventListener('click', () => { panel.hidden = true; document.dispatchEvent(new CustomEvent('mixel-invite-meeting')); });
  send.addEventListener('click', () => {
    if (!state?.session) return;
    const digits = device.value.replace(/\s/g, '');
    const user = member.value || null;
    if (!user && !/^\d{9}$/.test(digits)) { inviteStatus.textContent = 'Choose a colleague or enter the 9-digit Remote ID of their computer.'; return; }
    inviteStatus.textContent = 'Sending the invitation…';
    // The answer to a second invitation can be the same text as the first one: show it again.
    void run('remote_invite', {session: state.session, user, device: user ? null : Number(digits), control: askControl.checked}).then(sent => { if (sent) { shownMessage = ''; render(); } });
  });
  const answer = (accept: boolean, control: boolean): void => {
    const asking = state?.join_request;
    if (asking) void run('remote_decide_join', {session: asking.session, accept, control});
  };
  element<HTMLButtonElement>('colleague-decline').addEventListener('click', () => answer(false, false));
  element<HTMLButtonElement>('colleague-view').addEventListener('click', () => answer(true, false));
  element<HTMLButtonElement>('colleague-control').addEventListener('click', () => answer(true, true));
  const switchSides = (): void => { if (state?.session) void run('remote_switch_sides', {session: state.session}); };
  switchButton.addEventListener('click', switchSides);
  hostSwitchButton.addEventListener('click', switchSides);
  setInterval(render, 5000);
  render();
})();
