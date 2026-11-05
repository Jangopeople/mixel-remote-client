(() => {
  const invoke = window.__TAURI__.core.invoke;
  const open = document.querySelector<HTMLButtonElement>('#open-viewer')!;
  const accept = document.querySelector<HTMLButtonElement>('#accept-session')!;
  const reject = document.querySelector<HTMLButtonElement>('#reject-session')!;
  const close = document.querySelector<HTMLButtonElement>('#close-session')!;
  const status = document.querySelector<HTMLParagraphElement>('#session-status')!;
  const inputAccess = document.querySelector<HTMLButtonElement>('#request-input-access')!;
  const inputHelp = document.querySelector<HTMLParagraphElement>('#input-access-help')!;
  inputAccess.addEventListener('click', () => { void invoke('request_input_access').catch(error => { status.textContent = String(error); }); });
  let request = 0;
  open.addEventListener('click', () => { void invoke('open_local_viewer').catch(error => { status.textContent = String(error); }); });
  for (const [button, accepted] of [[accept, true], [reject, false]] as const) {
    button.addEventListener('click', async () => {
      accept.disabled = reject.disabled = true;
      try { await invoke('decide_local_session', { request, accept: accepted }); }
      catch (error) { status.textContent = String(error); }
    });
  }
  close.addEventListener('click', () => { void invoke('close_local_session').catch(error => { status.textContent = String(error); }); });
  async function refresh(): Promise<void> {
    try {
      const state = await invoke<{request: number; phase: string; message: string; received: number; control: boolean; injected: number; input_access: boolean}>('local_session_status');
      request = state.request;
      inputAccess.disabled = state.input_access;
      inputHelp.hidden = state.input_access;
      status.textContent = `${state.phase} · ${state.message} · ${state.control ? 'fixture keyboard requested' : 'view only'} · received frames ${state.received} · input acknowledgements ${state.injected} · OS input access ${state.input_access ? 'granted' : 'required'}`;
      accept.disabled = reject.disabled = state.phase !== 'pending';
      close.disabled = !['pending', 'negotiating', 'active'].includes(state.phase);
    } catch (error) { status.textContent = String(error); }
    setTimeout(() => { void refresh(); }, 200);
  }
  void refresh();
})();
