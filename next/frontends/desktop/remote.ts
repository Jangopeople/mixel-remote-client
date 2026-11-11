(() => {
  const invoke = window.__TAURI__.core.invoke;
  function element<T extends HTMLElement>(id: string): T {
    const value = document.getElementById(id);
    if (!value) throw Error(`Missing remote-support control ${id}`);
    return value as T;
  }
  type Status = {controller_only?:boolean; platform?: 'macos'|'windows'|'linux'; phase: string; message: string; host: number | null; session: string | null; consent: string | null; peer: number | null; requester: string | null; received: number; sharing: boolean; control: boolean; clipboard: boolean; files: boolean; input_access: boolean; code_required: boolean; support_code: string | null; online?: number[]; pairing: {request_id:string; provider_name:string; customer_name:string} | null; pairing_message: string | null; unattended_enabled: boolean; unattended_pending: 'enabling' | 'disabling_after_enable' | 'disabling' | null; unattended_session: boolean; displays?: number; display?: number; host_actions?: string[]; signin_id?: number | null; host_macs?: string[]};
  type RecordingView = {phase: 'ready' | 'waiting_for_video' | 'recording' | 'saving' | 'saved' | 'saved_partial'} | {phase:'error'; message:string};
  type RemoteStatus = Status & {recording_permitted?:boolean; recording?:RecordingView; previous_recording?:RecordingView};
  type Source = {id: string; name: string; full_display: boolean};
  const ownId = element<HTMLParagraphElement>('remote-own-id');
  const copyId = element<HTMLButtonElement>('remote-copy-id');
  const register = element<HTMLButtonElement>('remote-register');
  const offline = element<HTMLButtonElement>('remote-disconnect');
  const connect = element<HTMLButtonElement>('remote-connect');
  const form = element<HTMLFormElement>('remote-connect-form');
  const supportDetails = element<HTMLDetailsElement>('support-connection');
  const host = element<HTMLInputElement>('remote-host');
  const password = element<HTMLInputElement>('remote-password');
  const codeControls = element<HTMLDivElement>('remote-code-controls');
  const codeInfo = element<HTMLParagraphElement>('remote-code-info');
  const ownCode = element<HTMLParagraphElement>('remote-own-code');
  const generateCode = element<HTMLButtonElement>('remote-generate-code');
  const clearCode = element<HTMLButtonElement>('remote-clear-code');
  const pairing = element<HTMLDivElement>('remote-pairing');
  const checkPairing = element<HTMLButtonElement>('remote-check-pairing');
  const pairingInfo = element<HTMLParagraphElement>('remote-pairing-info');
  const confirmPairing = element<HTMLButtonElement>('remote-confirm-pairing');
  const unattendedSettings = element<HTMLDetailsElement>('remote-unattended-settings');
  const unattendedState = element<HTMLParagraphElement>('remote-unattended-state');
  const loadUnattended = element<HTMLButtonElement>('remote-load-unattended-displays');
  const unattendedDisplay = element<HTMLSelectElement>('remote-unattended-display');
  const unattendedConfirm = element<HTMLInputElement>('remote-unattended-confirm');
  const unattendedControl = element<HTMLInputElement>('remote-unattended-control');
  const enableUnattended = element<HTMLButtonElement>('remote-enable-unattended');
  const disableUnattended = element<HTMLButtonElement>('remote-disable-unattended');
  const requestUnattended = element<HTMLInputElement>('remote-request-unattended');
  const status = element<HTMLParagraphElement>('remote-status');
  const error = element<HTMLParagraphElement>('remote-error');
  const invitation = element<HTMLDivElement>('remote-invitation');
  const requester = element<HTMLParagraphElement>('remote-requester');
  const source = element<HTMLSelectElement>('remote-source');
  const refreshSources = element<HTMLButtonElement>('remote-refresh-sources');
  const screenSettings = element<HTMLButtonElement>('remote-screen-settings');
  const confirmation = element<HTMLLabelElement>('remote-display-confirmation');
  const confirm = element<HTMLInputElement>('remote-confirm-display');
  const allowControl = element<HTMLInputElement>('remote-allow-control');
  const allowChat = element<HTMLInputElement>('remote-allow-chat');
  const allowRecording = element<HTMLInputElement>('remote-allow-recording');
  const recordingButton = element<HTMLButtonElement>('remote-record');
  const recordingMessage = element<HTMLElement>('remote-recording-message');
  const recordingResult = element<HTMLParagraphElement>('remote-recording-result');
  const allowFiles = element<HTMLInputElement>('remote-allow-files');
  const allowClipboard = element<HTMLInputElement>('remote-allow-clipboard');
  const clipboardControls = element<HTMLDivElement>('remote-clipboard');
  const pushClipboard = element<HTMLButtonElement>('remote-push-clipboard');
  const pullClipboard = element<HTMLButtonElement>('remote-pull-clipboard');
  const clipboardResult = element<HTMLParagraphElement>('remote-clipboard-result');
  const inputSettings = element<HTMLButtonElement>('remote-input-settings');
  const inputPermission = element<HTMLParagraphElement>('remote-input-permission');
  const controlHelp = element<HTMLParagraphElement>('remote-control-help');
  const textForm = element<HTMLDivElement>('remote-text-form');
  const textValue = element<HTMLInputElement>('remote-send-text-value');
  const sendText = element<HTMLButtonElement>('remote-send-text');
  const accept = element<HTMLButtonElement>('remote-accept');
  const reject = element<HTMLButtonElement>('remote-reject');
  const close = element<HTMLButtonElement>('remote-close');
  const restart = element<HTMLButtonElement>('remote-restart');
  const displaySwitch = element<HTMLSpanElement>('remote-displays');
  let displayKey = '';
  const sas = element<HTMLButtonElement>('remote-sas');
  const reboot = element<HTMLButtonElement>('remote-reboot');
  // After "Restart computer": wait for the computer (or its sign-in screen) and reconnect.
  let reconnect: {host: number; signin: number | null; unattended: boolean; until: number; since: number; wentOffline: boolean; lastTry: number; session: string} | null = null;
  let rebootArmed = false, lastPresence = 0, lastRequestUnattended = false;
  function hasAction(name: string): boolean {
    return !!current && current.phase === 'active' && !current.sharing && current.control && (current.host_actions ?? []).includes(name);
  }
  // Remember each computer's network addresses (seen in a session) to wake it later.
  const macKey = 'mixel.remote.macs.v1';
  function rememberMacs(): void {
    if (!current || current.phase !== 'active' || current.sharing || !current.peer || !(current.host_macs ?? []).length) return;
    try {
      const known = JSON.parse(localStorage.getItem(macKey) ?? '{}') as Record<string, string[]>;
      const next = JSON.stringify(current.host_macs);
      if (JSON.stringify(known[String(current.peer)]) === next) return;
      known[String(current.peer)] = current.host_macs ?? [];
      localStorage.setItem(macKey, JSON.stringify(known));
    } catch { /* not remembered */ }
  }
  // Devices asks to wake a computer: also send the signal through the connected computer's network.
  document.addEventListener('mixel-wake', event => {
    const macs = (event as CustomEvent<string[]>).detail;
    if (!hasAction('wol') || !current?.session) return;
    for (const mac of macs.slice(0, 4)) void invoke('remote_input', {session: current.session, event: {kind: 'action', name: `wol:${mac}`}}).catch(() => {});
  });
  function renderActions(): void {
    rememberMacs();
    sas.hidden = !hasAction('sas');
    reboot.hidden = !hasAction('restart');
    sas.disabled = reboot.disabled = busy;
    reboot.textContent = rebootArmed ? 'Click again to restart' : 'Restart computer';
    // Done once a new session (not the one that asked for the restart) is active.
    if (reconnect && current && current.phase === 'active') { if (current.session !== reconnect.session) reconnect = null; return; }
    if (reconnect && current && !['negotiating', 'outgoing', 'deciding'].includes(current.phase)) {
      const now = Date.now();
      if (now > reconnect.until) { reconnect = null; error.textContent = 'The remote computer did not come back within 10 minutes.'; return; }
      if (now - lastPresence > 5000) {
        lastPresence = now;
        void invoke('remote_presence', {hosts: [reconnect.host, ...(reconnect.signin ? [reconnect.signin] : [])]}).catch(() => {});
      }
      const online = current.online ?? [];
      // Reconnect only after the computer was seen going offline (the list may still show it
      // from before the restart), then retry every 10 s until it answers.
      if (!online.includes(reconnect.host)) reconnect.wentOffline = true;
      const ready = reconnect.wentOffline || now - reconnect.since > 120000;
      // Unattended hosts may not appear in the online list: after a minute, just try.
      const target = !ready ? null : online.includes(reconnect.host) ? reconnect.host
        : (reconnect.signin && online.includes(reconnect.signin) && now - reconnect.since > 45000 ? reconnect.signin
          : (reconnect.unattended && now - reconnect.since > 60000 ? reconnect.host : null));
      status.textContent = 'The remote computer is restarting. Mixel Remote reconnects when it is back…';
      if (target && current.phase === 'ready' && !busy && now - reconnect.lastTry > 15000) {
        reconnect.lastTry = now;
        void action('remote_request', {host: target, password: null, unattended: reconnect.unattended || target === reconnect.signin});
      }
    }
  }
  sas.addEventListener('click', () => {
    if (current?.session) void action('remote_input', {session: current.session, event: {kind: 'action', name: 'sas'}});
  });
  reboot.addEventListener('click', () => {
    if (!current?.session || !current.peer) return;
    if (!rebootArmed) { rebootArmed = true; renderActions(); setTimeout(() => { rebootArmed = false; renderActions(); }, 5000); return; }
    rebootArmed = false;
    const now = Date.now();
    reconnect = {host: current.peer, signin: current.signin_id ?? null, unattended: lastRequestUnattended || current.unattended_session, until: now + 600000, since: now, wentOffline: false, lastTry: 0, session: current.session};
    void action('remote_input', {session: current.session, event: {kind: 'action', name: 'restart'}});
  });
  function renderDisplays(): void {
    const count = current && current.phase === 'active' && !current.sharing && current.control ? (current.displays ?? 0) : 0;
    const shown = current?.display ?? 0;
    displaySwitch.hidden = count < 2;
    const key = `${count}:${shown}:${busy}`;
    if (key === displayKey) return;
    displayKey = key;
    displaySwitch.replaceChildren();
    for (let index = 0; index < count; index++) {
      const button = document.createElement('button');
      button.className = 'button-quiet';
      button.type = 'button';
      button.textContent = `Display ${index + 1}`;
      button.setAttribute('aria-pressed', String(index === shown));
      button.disabled = busy || index === shown;
      button.addEventListener('click', () => {
        if (current?.session) void action('remote_input', {session: current.session, event: {kind: 'select_display', index}});
      });
      displaySwitch.appendChild(button);
    }
  }
  const metrics = element<HTMLParagraphElement>('remote-video-metrics');
  const canvas = element<HTMLCanvasElement>('remote-canvas');
  const context = canvas.getContext('2d');
  let current: RemoteStatus | undefined, sources: Source[] = [], busy = false, polling = false, readVersion = 0;
  let operationVersion = 0, stopping = false, stoppedConsent = '';
  let workspaceFocused = true;
  let authenticated=false,accountKey='',accountVersion=0;
  let viewing = '', videoReady = '', videoGeneration = 0;
  let recordingPending = false, recordingError = '', recordingConsent = '';
  type InputEvent = {kind: 'key'; usage: number; down: boolean} | {kind: 'pointer'; x: number; y: number} | {kind: 'button_click'; button: number; down: boolean; clicks: number} | {kind: 'wheel'; horizontal: number; vertical: number} | {kind: 'text'; text: string} | {kind: 'release_all'};
  const inputQueue: {session: string; event: InputEvent}[] = [];
  const clickCounts=new Map<number,number>();
  let inputSending = false;
  function locallyStopped(): boolean { return !!current?.consent && current.consent === stoppedConsent; }
  function controllable(): boolean { return workspaceFocused && authenticated && !busy && !stopping && !locallyStopped() && current?.phase === 'active' && current.control && !current.sharing && !!current.session && videoReady === current.session; }
  function mayRelease(session: string): boolean { return authenticated && !stopping && !locallyStopped() && current?.phase === 'active' && current.control && !current.sharing && current.session === session; }
  async function flushInput(): Promise<void> {
    if (inputSending) return;
    inputSending = true;
    try {
      while (inputQueue.length) {
        const command = inputQueue.shift()!;
        if (command.event.kind === 'release_all' ? !mayRelease(command.session) : !controllable() || current?.session !== command.session) continue;
        try {
          const posted = await invoke<boolean>('remote_input', command);
          if (!posted && current?.session === command.session) {
            if (command.event.kind === 'text') error.textContent = 'The remote computer could not type this text.';
            else inputQueue.length = 0;
          }
        }
        catch (value) { if(current?.session===command.session)inputQueue.length = 0; await videoFailure(value, command.session); break; }
      }
    } finally { inputSending = false; if(inputQueue.length)void flushInput(); }
  }
  function enqueueInput(event: InputEvent): void {
    const session = current?.session;
    if (!session || (event.kind === 'release_all' ? !mayRelease(session) : !controllable())) return;
    const previous = inputQueue[inputQueue.length - 1];
    if (event.kind === 'pointer' && previous?.event.kind === 'pointer' && previous.session === session) previous.event = event;
    else if (inputQueue.length >= 64) { inputQueue.length = 0; void videoFailure('Input delivery stalled; the session has been stopped', session); return; }
    else inputQueue.push({session, event});
    void flushInput();
  }
  function formatId(id: number | null): string { return id === null ? '— — —' : String(id).replace(/(\d{3})(\d{3})(\d{3})/, '$1 $2 $3'); }
  function connectionMessage(closing: boolean): string {
    if (closing) return 'Ending the session…';
    switch(current?.phase) {
      case 'offline': return current?.controller_only
        ? authenticated ? 'Connect this controller to the Mixel network.' : 'Sign in to access managed devices.'
        : authenticated ? 'Make this computer available to start or receive a connection.' : 'Sign in to get your connection ID.';
      case 'ready': return current.message || 'Ready for a connection.';
      case 'outgoing': return 'Waiting for the other person to accept your request.';
      case 'connecting': case 'deciding': case 'negotiating': return 'Connecting securely…';
      case 'active': return current.sharing ? 'Sharing your selected screen. You can end the session at any time.' : current.control ? 'Connected. Keyboard and mouse control is enabled.' : 'Connected in view-only mode.';
      default: return current?.message ?? '';
    }
  }
  function clearVideo(): void {
    wheelX=wheelY=0;clickCounts.clear();
    ++videoGeneration; viewing = ''; videoReady = '';
    context?.clearRect(0, 0, canvas.width, canvas.height);
    inputQueue.length = 0;
    canvas.hidden = true; metrics.textContent = 'No retained remote video';
    recordingButton.disabled = true;
    document.dispatchEvent(new CustomEvent('mixel-video-visibility',{detail:{visible:false}}));
  }
  function recordingText(view: RecordingView | undefined): string {
    switch (view?.phase) {
      case 'waiting_for_video': return 'Waiting for a complete video frame…';
      case 'recording': return 'Recording video';
      case 'saving': return 'Saving recording…';
      case 'saved': return 'Recording saved';
      case 'saved_partial': return 'The recording stopped early. The playable portion was saved.';
      case 'error': return view.message;
      default: return '';
    }
  }
  function renderRecording(closing: boolean): void {
    const active = authenticated && !closing && current?.phase === 'active';
    const view = active && !current?.sharing ? current?.recording : undefined;
    const running = view?.phase === 'recording' || view?.phase === 'waiting_for_video';
    recordingButton.hidden = !view;
    recordingButton.disabled = !view || recordingPending || view.phase === 'saving' || videoReady !== current?.session;
    recordingButton.textContent = running ? 'Stop recording' : 'Record';
    const text = active && current?.sharing && current.recording_permitted
      ? 'You allowed the other person to save a video of this session.'
      : view ? recordingError || recordingText(view) : '';
    if (recordingMessage.textContent !== text) recordingMessage.textContent = text;
    recordingMessage.hidden = !text;
    const result = authenticated && !active ? recordingText(current?.previous_recording) : '';
    if (recordingResult.textContent !== result) recordingResult.textContent = result;
    recordingResult.hidden = !result;
  }
  let controllerLayout = false;
  function render(): void {
    if (!current) return;
    if (current.controller_only && !controllerLayout) {
      controllerLayout = true;
      document.body.dataset.controllerOnly = 'true';
      const actions = element<HTMLDivElement>('controller-connection-actions');
      register.textContent = 'Connect to Mixel Remote';
      const channel = element('release-channel-label');
      channel.textContent = 'TestFlight';
      channel.hidden = false;
      element('remote-workspace-intro').textContent = 'Connect securely to a managed device.';
      element('remote-partner-help').textContent = 'Enter the 9-digit ID shown by the Mixel Remote host.';
      element('device-settings-intro').textContent = 'Manage the device identity used to connect from this computer.';
      actions.append(register, offline);
      actions.hidden = false;
    }
    const mac = current.platform === undefined || current.platform === 'macos';
    supportDetails.hidden = !!current.controller_only || !mac;
    document.body.dataset.remotePlatform = current.platform ?? 'macos';
    const closing = stopping || (locallyStopped() && ['incoming','outgoing','deciding','negotiating','active','closing'].includes(current.phase));
    const displayPhase = closing ? 'closing' : current.phase;
    document.dispatchEvent(new CustomEvent('mixel-remote-state',{detail:{phase:displayPhase,controllerOnly:!!current.controller_only,host:current.controller_only?null:current.host,session:!closing&&authenticated?current.session:null,consent:current.consent,files:!closing&&authenticated&&current.files,clipboard:!closing&&authenticated&&current.clipboard,sharing:current.sharing,control:current.control,stopping,online:current.online??[],peer:current.peer}}));
    status.textContent = connectionMessage(closing);
    status.dataset.phase=displayPhase;
    ownId.textContent = formatId(authenticated ? current.host : null);
    copyId.disabled=!authenticated || !current.host;
    const disconnected = ['offline', 'error'].includes(current.phase);
    register.disabled = busy || !authenticated || !disconnected;
    offline.disabled = busy || disconnected;
    connect.disabled = busy || !authenticated || current.phase !== 'ready';
    host.disabled = requestUnattended.disabled = busy || !authenticated || current.phase !== 'ready';
    password.disabled = host.disabled || requestUnattended.checked;
    const unattendedEnabling = current.unattended_pending === 'enabling';
    const unattendedChanging = Boolean(current.unattended_pending);
    const canDisableUnattended = current.unattended_enabled || unattendedEnabling;
    unattendedSettings.hidden = !authenticated || (!canDisableUnattended && !unattendedChanging && current.phase !== 'ready');
    unattendedState.textContent = unattendedEnabling
      ? 'Turning on. Waiting for the connection service.'
      : unattendedChanging
        ? 'Sharing is off. Confirming with the connection service.'
        : current.unattended_enabled
          ? 'On. Approved users can connect while this computer stays signed in.'
          : 'Off. Choose a display and confirm before enabling.';
    const changeUnattended = !authenticated || busy || current.phase !== 'ready' || canDisableUnattended || unattendedChanging;
    loadUnattended.disabled = unattendedConfirm.disabled = changeUnattended;
    unattendedDisplay.disabled = changeUnattended || !unattendedDisplay.value;
    unattendedControl.disabled = changeUnattended || !current.input_access || unattendedDisplay.value === 'qa-relay-fixture';
    enableUnattended.hidden = !!current.unattended_enabled;
    enableUnattended.disabled = changeUnattended || current.code_required || !unattendedDisplay.value || !unattendedConfirm.checked;
    disableUnattended.hidden = !canDisableUnattended;
    disableUnattended.disabled = !authenticated || busy;
    codeControls.hidden = !authenticated || !current.host;
    generateCode.disabled = clearCode.disabled = changeUnattended;
    clearCode.hidden = !current.code_required;
    ownCode.textContent = authenticated ? current.support_code ?? '' : '';
    codeInfo.textContent = current.support_code
      ? 'Share this password with your technician. It lasts five minutes and allows one request.'
      : current.code_required ? 'Generate a new password before receiving another request.' : '';
    pairing.hidden = !authenticated || current.phase !== 'ready';
    pairingInfo.textContent = !authenticated ? '' : current.pairing
      ? `Pair this computer with ${current.pairing.provider_name} for ${current.pairing.customer_name}?`
      : current.pairing_message ?? '';
    confirmPairing.hidden = !current.pairing;
    checkPairing.disabled = confirmPairing.disabled = busy || !authenticated || current.phase !== 'ready';
    invitation.hidden = current.phase !== 'incoming' || current.unattended_session;
    requester.textContent = `Device ${formatId(current.peer)} requests to connect. Its enrolled identity has been verified with Mixel.`;
    const selected = sources.find(item => item.id === source.value);
    confirmation.hidden = !selected?.full_display;
    accept.disabled = busy || current.phase !== 'incoming' || !selected || (!!selected.full_display && !confirm.checked);
    reject.disabled = busy || current.phase !== 'incoming';
    source.disabled = confirm.disabled = busy || current.phase !== 'incoming';
    const controlAvailable = current.phase === 'incoming' && !!selected?.full_display && current.input_access;
    allowControl.disabled = busy || !controlAvailable;
    if (!controlAvailable) allowControl.checked = false;
    accept.textContent = allowControl.checked ? 'Accept with control' : 'Accept view only';
    inputPermission.textContent = !selected?.full_display
      ? 'Choose a display to allow keyboard and mouse control.'
      : !current.input_access
        ? mac ? 'Enable Accessibility in macOS to allow keyboard and mouse control.' : 'Keyboard and mouse control are unavailable in this desktop session.'
        : allowControl.checked ? 'Keyboard and mouse control will be allowed for this session.' : 'Select Keyboard & mouse to allow control for this session.';
    allowChat.disabled = busy || current.phase !== 'incoming';
    if (current.phase !== 'incoming') allowChat.checked = false;
    allowRecording.disabled = busy || current.phase !== 'incoming';
    if (recordingConsent !== (current.consent ?? '') || current.phase !== 'incoming') {
      recordingConsent = current.consent ?? '';
      allowRecording.checked = false;
    }
    renderRecording(closing);
    allowFiles.disabled = busy || current.phase !== 'incoming' || selected?.id === 'qa-relay-fixture';
    if (allowFiles.disabled) allowFiles.checked = false;
    allowClipboard.disabled = busy || current.phase !== 'incoming' || selected?.id === 'qa-relay-fixture';
    if (allowClipboard.disabled) allowClipboard.checked = false;
    clipboardControls.hidden = closing || !authenticated || current.phase !== 'active' || current.sharing || !current.clipboard;
    if (clipboardControls.hidden) clipboardResult.textContent = '';
    pushClipboard.disabled = pullClipboard.disabled = busy || clipboardControls.hidden;
    inputSettings.hidden = !mac || !selected?.full_display || current.input_access;
    controlHelp.hidden = textForm.hidden = !controllable();
    close.disabled = stopping || !current.consent || !['incoming', 'outgoing', 'deciding', 'negotiating', 'active', 'closing'].includes(current.phase);
    restart.hidden = current.phase !== 'active' || current.sharing;
    restart.disabled = closing || busy || restart.hidden;
    renderDisplays();
    renderActions();
    if (!closing && authenticated && current.phase === 'active' && !current.sharing && current.session) {
      if (viewing !== current.session) void receive(current.session);
    } else if (viewing) clearVideo();
  }
  async function read(force = false): Promise<void> {
    if ((polling || busy || stopping) && !force) return;
    polling = true; const version = ++readVersion;
    try {
      const status = await invoke<RemoteStatus>('remote_status');
      if (version === readVersion) {
        if (current?.session !== status.session) recordingError = '';
        const freshInvitation = status.phase === 'incoming' && current?.phase !== 'incoming';
        const becameReady = status.phase === 'ready' && current?.phase !== 'ready';
        current = status;
        render();
        if (freshInvitation && !status.controller_only) void loadSources();
        if (becameReady) void restoreUnattended();
      }
    }
    catch (value) { if (version === readVersion) { error.textContent = String(value); clearVideo(); } }
    finally { if (version === readVersion) polling = false; }
  }
  async function action(command: string, args?: Record<string, unknown>): Promise<void> {
    if (busy || stopping || (locallyStopped() && command !== 'remote_disconnect')) return;
    const operation = ++operationVersion;
    ++readVersion; busy = true; error.textContent = ''; render();
    try { await invoke(command, args); }
    catch (value) { if (operation === operationVersion) error.textContent = String(value); }
    finally { if (operation === operationVersion) { busy = false; await read(true); render(); } }
  }
  async function stopSession(): Promise<void> {
    const consent = current?.consent;
    if (!consent || stopping) return;
    const operation = ++operationVersion;
    ++readVersion; stoppedConsent = consent; stopping = true; busy = false;
    clearVideo(); clipboardResult.textContent = ''; error.textContent = ''; render();
    try { await invoke('remote_stop_session', {consent}); }
    catch (value) { if (operation === operationVersion) error.textContent = `Could not confirm that the session ended. Try End session again. ${String(value)}`; }
    finally { if (operation === operationVersion) { stopping = false; await read(true); render(); } }
  }
  async function loadSources(): Promise<void> {
    refreshSources.disabled = true;
    try {
      sources = (await invoke<Source[]>('capture_targets')).filter(item=>item.full_display || item.id === 'qa-relay-fixture');
      source.replaceChildren();
      // Prefer a real display when macOS has already authorized recording.
      sources.sort((a, b) => Number(b.full_display) - Number(a.full_display));
      for (const item of sources) { const option = document.createElement('option'); option.value = item.id; option.textContent = item.name; source.appendChild(option); }
      screenSettings.hidden = current?.platform === 'windows' || current?.platform === 'linux' || sources.some(item => item.full_display);
      confirm.checked = false; render();
    } catch (value) { error.textContent = String(value); }
    finally { refreshSources.disabled = false; }
  }
  async function videoFailure(value: unknown, session: string): Promise<void> {
    if(current?.session!==session || stopping || locallyStopped())return;
    clearVideo(); error.textContent = String(value);
    await invoke('remote_close', {session}).catch(() => {});
  }
  async function receive(session: string): Promise<void> {
    clearVideo(); viewing = session; const generation = videoGeneration;
    let sequence = 0, rendered = 0, lastTimestamp = -1;
    const started = performance.now();
    try {
      if (!context) throw Error('The remote screen renderer is unavailable');
      while (generation === videoGeneration) {
        const value = await invoke<ArrayBuffer | number[] | Uint8Array>('remote_next_video', {session});
        const bytes = value instanceof ArrayBuffer ? new Uint8Array(value) : new Uint8Array(value);
        if (generation !== videoGeneration) { bytes.fill(0); break; }
        if (!bytes.length) { await new Promise(resolve => setTimeout(resolve, 20)); continue; }
        let rgba: Uint8ClampedArray<ArrayBuffer> | undefined;
        try {
          if (bytes.length <= 32 || bytes[0] !== 77 || bytes[1] !== 82 || bytes[2] !== 70 || bytes[3] !== 49) throw Error('Invalid native video frame');
          const header = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
          const width = header.getUint32(4, true), height = header.getUint32(8, true), stride = header.getUint32(12, true);
          const next = Number(header.getBigUint64(16, true)), timestamp = Number(header.getBigUint64(24, true));
          if (width < 2 || height < 2 || width > 1280 || height > 720 || stride < width * 4 || bytes.length !== 32 + stride * height || bytes.length > 16_777_248 || !Number.isSafeInteger(next) || next <= sequence || !Number.isSafeInteger(timestamp) || timestamp <= lastTimestamp) throw Error('Invalid native video dimensions or sequence');
          rgba = new Uint8ClampedArray(width * height * 4);
          for (let y = 0; y < height; ++y) for (let x = 0; x < width; ++x) {
            const source = 32 + y * stride + x * 4, destination = (y * width + x) * 4;
            rgba[destination] = bytes[source + 2]; rgba[destination + 1] = bytes[source + 1]; rgba[destination + 2] = bytes[source]; rgba[destination + 3] = 255;
          }
          if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
          context.putImageData(new ImageData(rgba, width, height), 0, 0);
          canvas.hidden = false; videoReady = session; sequence = next; lastTimestamp = timestamp; ++rendered;
          if (rendered === 1) document.dispatchEvent(new CustomEvent('mixel-video-visibility',{detail:{visible:true}}));
          metrics.textContent = `${width} × ${height} · ${(rendered * 1000 / Math.max(1, performance.now() - started)).toFixed(0)} fps`;
        } finally { bytes.fill(0); rgba?.fill(0); }
        await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      }
    } catch (value) { if (generation === videoGeneration) await videoFailure(value, session); }
  }
  register.addEventListener('click', () => { void action('remote_register'); });
  copyId.addEventListener('click', async () => {
    copyId.disabled=true;
    try{await invoke('copy_connection_id');copyId.textContent='Copied';}
    catch(value){error.textContent=String(value);}
    finally{window.setTimeout(()=>{copyId.textContent='Copy ID';copyId.disabled=!authenticated||!current?.host;},1500);}
  });
  offline.addEventListener('click', () => { clearVideo(); void action('remote_disconnect'); });
  generateCode.addEventListener('click', () => { void action('remote_support_code', {required:true}); });
  clearCode.addEventListener('click', () => { void action('remote_support_code', {required:false}); });
  checkPairing.addEventListener('click', () => { void action('remote_pairing', {confirmation:null}); });
  confirmPairing.addEventListener('click', () => {
    if (current?.pairing) void action('remote_pairing', {confirmation:current.pairing.request_id});
  });
  loadUnattended.addEventListener('click', async () => {
    const version = operationVersion;
    try {
      const displays = await invoke<Source[]>('capture_targets');
      if (version !== operationVersion || !authenticated || current?.phase !== 'ready') return;
      unattendedDisplay.replaceChildren();
      for (const display of displays.filter((item) => item.id !== 'wayland:portal' && (item.full_display || item.id === 'qa-relay-fixture'))) {
        const option = document.createElement('option');
        option.value = display.id;
        option.textContent = display.name;
        unattendedDisplay.appendChild(option);
      }
      unattendedConfirm.checked = false;
      if (!unattendedDisplay.value) {
        error.textContent = displays.some(item => item.id === 'wayland:portal')
          ? 'Wayland requires local confirmation for each sharing session.'
          : 'An available display is required before enabling unattended sharing.';
      }
      render();
    } catch (value) {
      if (version === operationVersion) error.textContent = String(value);
    }
  });
  unattendedConfirm.addEventListener('change', render);
  unattendedDisplay.addEventListener('change', () => { unattendedConfirm.checked = false; render(); });
  // Remember the owner's unattended choice on this computer, so a restored
  // start-up (stay signed in / start with computer) turns it back on.
  const unattendedKey = 'mixel.remote.unattended.v1';
  function savedUnattended(): {source: string; control: boolean} | null {
    try {
      const value = JSON.parse(localStorage.getItem(unattendedKey) ?? 'null');
      return value && typeof value.source === 'string' && typeof value.control === 'boolean' ? value : null;
    } catch { return null; }
  }
  let unattendedRestored = false;
  async function restoreUnattended(): Promise<void> {
    const saved = savedUnattended();
    if (unattendedRestored || !saved || !current || current.unattended_enabled || current.unattended_pending) return;
    try {
      const startup = await invoke<{restored: boolean; launched_at_startup: boolean}>('startup_settings');
      if (!startup.restored && !startup.launched_at_startup) return;
      const displays = await invoke<Source[]>('capture_targets');
      if (!displays.some(item => item.id === saved.source && item.full_display)) return;
      unattendedRestored = true;
      await action('remote_unattended', {source: saved.source, control: saved.control});
    } catch { /* stays attended-only until the owner enables it again */ }
  }
  enableUnattended.addEventListener('click', () => {
    const source = unattendedDisplay.value;
    if (!(/^(display:[0-9]+|dxgi:[0-9]+:[0-9]+:.{1,128}|x11:[0-9]+:[0-9]+)$/.test(source) || source === 'qa-relay-fixture') || !unattendedConfirm.checked) { error.textContent = 'Choose a display and confirm it before enabling unattended sharing.'; return; }
    const control = unattendedControl.checked && !unattendedControl.disabled;
    unattendedConfirm.checked = false;
    try { localStorage.setItem(unattendedKey, JSON.stringify({source, control})); } catch { /* not remembered */ }
    void action('remote_unattended', {source, control});
  });
  disableUnattended.addEventListener('click', () => {
    try { localStorage.removeItem(unattendedKey); } catch { /* nothing remembered */ }
    void action('remote_unattended', {source:null, control:false});
  });
  requestUnattended.addEventListener('change', () => {
    if (requestUnattended.checked) password.value = '';
    render();
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    const digits = host.value.replace(/ /g, '');
    if (!/^[1-9][0-9]{8}$/.test(digits)) { error.textContent = 'Enter a nine-digit connection ID'; return; }
    const supplied = password.value;
    password.value = '';
    lastRequestUnattended = requestUnattended.checked;
    void action('remote_request', {host: Number(digits), password: supplied || null, unattended: requestUnattended.checked});
  });
  const supportForm = element<HTMLFormElement>('support-connect-form');
  const supportHost = element<HTMLInputElement>('support-host');
  const supportConnect = element<HTMLButtonElement>('support-connect');
  const supportStatus = element<HTMLParagraphElement>('support-connect-status');
  supportForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (!current || supportDetails.hidden || supportConnect.disabled) return;
    const digits = supportHost.value.replace(/ /g, '');
    if (!/^[1-9][0-9]{8}$/.test(digits)) {
      supportStatus.textContent = 'Enter the nine-digit ID shown in Mixel Remote Support.';
      return;
    }
    supportConnect.disabled = true;
    supportStatus.textContent = 'Verifying and opening Mixel Remote Support…';
    try {
      await invoke('connect_support_id', {id: digits});
      supportStatus.textContent = 'Mixel Remote Support was opened. Follow its prompts to connect.';
    } catch (value) {
      supportStatus.textContent = String(value);
    } finally {
      supportConnect.disabled = false;
    }
  });
  for (const [button, accepted] of [[accept, true], [reject, false]] as const) button.addEventListener('click', () => {
    if (!current?.session) return;
    void action('remote_decide', {decision:{session: current.session, accept: accepted, source: source.value, confirm: confirm.checked, control: allowControl.checked, clipboard: allowClipboard.checked, files: allowFiles.checked, chat: allowChat.checked, recording: allowRecording.checked}});
  });
  recordingButton.addEventListener('click', async () => {
    const session = current?.session;
    const phase = current?.recording?.phase;
    if (!session || !phase || recordingButton.disabled) return;
    recordingPending = true;
    recordingError = '';
    render();
    try {
      const command = phase === 'recording' || phase === 'waiting_for_video' ? 'remote_record_stop' : 'remote_record_start';
      await invoke(command, {session});
    } catch (error) {
      if (current?.session === session) recordingError = String(error);
    } finally {
      recordingPending = false;
      await read(true);
      render();
    }
  });
  for (const [button,push] of [[pushClipboard,true],[pullClipboard,false]] as const) button.addEventListener('click', async () => {
    if (!current?.session || busy || stopping || locallyStopped()) return;
    const operation=++operationVersion,session=current.session; ++readVersion; busy=true; clipboardResult.textContent='Transferring plain text…'; error.textContent=''; render();
    try { await invoke('remote_clipboard',{session,push}); if (operation===operationVersion&&current?.session===session) clipboardResult.textContent=push?'Remote clipboard updated.':'Your clipboard was updated.'; }
    catch (value) { if(operation===operationVersion)clipboardResult.textContent=String(value); }
    finally { if(operation===operationVersion){busy=false; await read(true); render();} }
  });
  close.addEventListener('click', () => { void stopSession(); });
  restart.addEventListener('click', () => { if (current?.session) void action('remote_restart', {session: current.session}); });
  source.addEventListener('change', () => { confirm.checked = false; allowControl.checked = false; render(); });
  confirm.addEventListener('change', render);
  allowControl.addEventListener('change', render);
  refreshSources.addEventListener('click', () => { void loadSources(); });
  screenSettings.addEventListener('click', async () => { await action('request_screen_access'); await loadSources(); });
  inputSettings.addEventListener('click', () => { void action('request_input_access'); });
  sendText.addEventListener('click', () => { const text = textValue.value; textValue.value = ''; if (text) enqueueInput({kind: 'text', text}); });
  function usage(code: string): number | undefined {
    if (/^Key[A-Z]$/.test(code)) return code.charCodeAt(3) - 65 + 4;
    if (/^Digit[1-9]$/.test(code)) return Number(code[5]) + 29;
    if (/^F([1-9]|1[0-2])$/.test(code)) return Number(code.slice(1)) + 57;
    const keys: Record<string, number> = {Digit0: 39, Enter: 40, Escape: 41, Backspace: 42, Tab: 43, Space: 44, Minus: 45, Equal: 46, BracketLeft: 47, BracketRight: 48, Backslash: 49, Semicolon: 51, Quote: 52, Backquote: 53, Comma: 54, Period: 55, Slash: 56, CapsLock: 57, PrintScreen: 70, ScrollLock: 71, Pause: 72, Insert: 73, Home: 74, PageUp: 75, Delete: 76, End: 77, PageDown: 78, ArrowRight: 79, ArrowLeft: 80, ArrowDown: 81, ArrowUp: 82, NumLock: 83, NumpadDivide: 84, NumpadMultiply: 85, NumpadSubtract: 86, NumpadAdd: 87, NumpadEnter: 88, Numpad1: 89, Numpad2: 90, Numpad3: 91, Numpad4: 92, Numpad5: 93, Numpad6: 94, Numpad7: 95, Numpad8: 96, Numpad9: 97, Numpad0: 98, NumpadDecimal: 99, IntlBackslash: 100, ContextMenu: 101, ControlLeft: 224, ShiftLeft: 225, AltLeft: 226, MetaLeft: 227, ControlRight: 228, ShiftRight: 229, AltRight: 230, MetaRight: 231};
    return keys[code];
  }
  for (const name of ['keydown', 'keyup'] as const) canvas.addEventListener(name, event => {
    if (!controllable() || event.isComposing) return;
    const key = usage(event.code); if (key === undefined) return;
    event.preventDefault(); enqueueInput({kind: 'key', usage: key, down: name === 'keydown'});
  });
  canvas.addEventListener('compositionend', event => { if (event.data) enqueueInput({kind: 'text', text: event.data}); });
  function imagePoint(event: {clientX:number;clientY:number}, dragging = false): {x:number;y:number} | undefined {
    const bounds = canvas.getBoundingClientRect();
    if (bounds.width <= 0 || bounds.height <= 0 || !canvas.width || !canvas.height) return;
    const scale = Math.min(bounds.width / canvas.width, bounds.height / canvas.height);
    const width = canvas.width * scale, height = canvas.height * scale;
    const x = (event.clientX - bounds.left - (bounds.width - width) / 2) / width;
    const y = (event.clientY - bounds.top - (bounds.height - height) / 2) / height;
    if (!dragging && (x < 0 || x > 1 || y < 0 || y > 1)) return;
    const normalized = (value: number): number => Math.round(Math.max(0, Math.min(1, value)) * 65535);
    return {x: normalized(x), y: normalized(y)};
  }
  function pointer(event: {clientX:number;clientY:number}, dragging = false): boolean {
    const point = imagePoint(event, dragging);
    if (!point) return false;
    enqueueInput({kind: 'pointer', ...point});
    return true;
  }
  canvas.addEventListener('pointermove', event => { if (controllable()) { event.preventDefault(); pointer(event, clickCounts.size > 0); } });
  // MouseEvent.detail contains the user's OS/browser click count; using
  // receipt timing on the host would turn a double click into two single clicks
  // when reliable input acknowledgments cross a high-latency connection.
  for(const name of ['pointerdown','pointerup'] as const)canvas.addEventListener(name,event=>{
    if(!controllable())return;
    if(name==='pointerdown'){if(imagePoint(event)){canvas.focus();canvas.setPointerCapture(event.pointerId);}}
    else{
      const button=[1,3,2,4,5][event.button],clicks=clickCounts.get(button);
      if(button&&clicks){pointer(event,true);enqueueInput({kind:'button_click',button,down:false,clicks});clickCounts.delete(button);}
      if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);
    }
  });
  canvas.addEventListener('mousedown',event=>{
    if(!controllable())return;
    const button=[1,3,2,4,5][event.button];if(!button)return;
    event.preventDefault();canvas.focus();
    if (!pointer(event)) return;
    const clicks=Math.min(3,Math.max(1,event.detail));clickCounts.set(button,clicks);
    enqueueInput({kind:'button_click',button,down:true,clicks});
  });
  let wheelX=0,wheelY=0;
  canvas.addEventListener('wheel', event => {
    if (!controllable() || !imagePoint(event)) return;
    event.preventDefault();
    const scale=event.deltaMode===WheelEvent.DOM_DELTA_PIXEL?1/40:event.deltaMode===WheelEvent.DOM_DELTA_PAGE?12:1;
    wheelX=Math.max(-120,Math.min(120,wheelX-event.deltaX*scale));wheelY=Math.max(-120,Math.min(120,wheelY-event.deltaY*scale));
    const horizontal=Math.trunc(wheelX),vertical=Math.trunc(wheelY);wheelX-=horizontal;wheelY-=vertical;
    if(horizontal||vertical)enqueueInput({kind:'wheel',horizontal,vertical});
  }, {passive: false});
  canvas.addEventListener('contextmenu', event => { if (controllable()) event.preventDefault(); });
  for (const name of ['blur', 'pointercancel'] as const) canvas.addEventListener(name, () => { clickCounts.clear();wheelX=wheelY=0;enqueueInput({kind: 'release_all'}); });
  window.addEventListener('blur', () => { clickCounts.clear();wheelX=wheelY=0;enqueueInput({kind: 'release_all'}); });
  document.addEventListener('mixel-workspace-leaving', () => { clickCounts.clear();wheelX=wheelY=0;enqueueInput({kind:'release_all'}); });
  document.addEventListener('mixel-workspace-focus', event => { workspaceFocused = !!(event as CustomEvent<{enabled:boolean}>).detail.enabled; });
  window.addEventListener('pagehide', () => { clearVideo(); });
  type Account={phase:string;account:{profile:string;organization:string|null;staff:boolean}|null};
  function accountChanged(value:Account):void{
    const key=value.phase==='authenticated'&&value.account?JSON.stringify(value.account):'';
    if(key!==accountKey){accountKey=key;authenticated=!!key;++operationVersion;busy=false;stopping=false;++readVersion;polling=false;password.value='';requestUnattended.checked=false;unattendedDisplay.replaceChildren();unattendedConfirm.checked=false;unattendedControl.checked=false;ownCode.textContent='';clearVideo();}
    render();
  }
  document.addEventListener('mixel-account-state',event=>{++accountVersion;accountChanged((event as CustomEvent<Account>).detail);});
  const initialAccountVersion=accountVersion;
  void invoke<Account>('account_status').then(value=>{if(accountVersion===initialAccountVersion)accountChanged(value);}).catch(()=>{if(accountVersion===initialAccountVersion)accountChanged({phase:'signed_out',account:null});});
  void read(); window.setInterval(() => { void read(); }, 250);
})();
