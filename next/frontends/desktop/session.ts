(() => {
  const invoke = window.__TAURI__.core.invoke;
  const open = document.querySelector<HTMLButtonElement>('#open-viewer')!;
  const accept = document.querySelector<HTMLButtonElement>('#accept-session')!;
  const reject = document.querySelector<HTMLButtonElement>('#reject-session')!;
  const close = document.querySelector<HTMLButtonElement>('#close-session')!;
  const status = document.querySelector<HTMLParagraphElement>('#session-status')!;
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
      const state = await invoke<{request: number; phase: string; message: string; received: number}>('local_session_status');
      request = state.request;
      status.textContent = `${state.phase} · ${state.message} · received frames ${state.received}`;
      accept.disabled = reject.disabled = state.phase !== 'pending';
      close.disabled = !['pending', 'negotiating', 'active'].includes(state.phase);
    } catch (error) { status.textContent = String(error); }
    setTimeout(() => { void refresh(); }, 200);
  }
  void refresh();
})();
