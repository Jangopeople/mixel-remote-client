// Settings › This computer: "Remove this computer from Mixel Remote". The removal is signed with
// this computer's own device key (device_self_remove), so it needs no sign-in and works offline:
// without a connection it is saved and sent as soon as Mixel Remote is reachable
// (device_self_remove_flush, at start and every 30 seconds). Afterwards the computer is no longer set up.
(() => {
  function element<T extends HTMLElement>(id: string): T { const node = document.getElementById(id); if (!node) throw Error(`Missing this-computer control ${id}`); return node as T; }
  const invoke = window.__TAURI__.core.invoke;
  type Saved = {device: string; connection_id: number; generation: number; name: string; tenant?: string};
  type Remote = {phase: string; host: number | null};
  type Company = {tenant: {tenant_id: string; name: string} | null};
  type Outcome = 'removed' | 'queued' | 'stale';
  type Dialog = {open(options: {title: string; text?: string; confirm: string; danger?: boolean; run: (values: Record<string, string>) => Promise<HTMLElement | void>}): void};
  const SAVED = 'mixel.remote.this-computer.v1', PENDING = 'mixel.remote.self-removal.v1';
  const panel = element<HTMLElement>('this-computer-panel'), detail = element<HTMLElement>('this-computer-detail'), pendingLine = element<HTMLElement>('this-computer-pending');
  const remove = element<HTMLButtonElement>('this-computer-remove'), status = element<HTMLElement>('this-computer-status');
  let remote: Remote = {phase: 'offline', host: null}, company: Company = {tenant: null}, busy = false, flushing = false;
  const formatId = (value: number): string => String(value).replace(/(\d{3})(\d{3})(\d{3})/, '$1 $2 $3');
  const read = <T>(key: string): T | null => { try { return JSON.parse(localStorage.getItem(key) ?? 'null') as T | null; } catch { return null; } };
  const write = (key: string, value: unknown): void => { try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, JSON.stringify(value)); } catch { /* not kept */ } };
  function saved(): Saved | null {
    const value = read<Saved>(SAVED);
    return value && typeof value.device === 'string' && Number.isInteger(value.connection_id) && Number.isInteger(value.generation) ? value : null;
  }
  const pending = (): boolean => read<boolean>(PENDING) === true;
  function render(): void {
    const registration = saved();
    panel.hidden = !(pending() || registration || (company.tenant && remote.host));
    pendingLine.hidden = !pending();
    remove.hidden = pending();
    remove.disabled = busy;
    detail.textContent = registration
      ? `Registered as ${registration.name} · Connection ID ${formatId(registration.connection_id)}`
      : remote.host ? `Connection ID ${formatId(remote.host)}` : '';
  }
  function finished(outcome: Outcome | null): void {
    if (outcome === 'removed') {
      write(SAVED, null); write(PENDING, null);
      status.textContent = 'This computer was removed from Mixel Remote. It is no longer set up; add it again in Devices to use it.';
      document.dispatchEvent(new CustomEvent('mixel-self-remove-done'));
      document.dispatchEvent(new CustomEvent('mixel-device-removed'));
      document.dispatchEvent(new CustomEvent('mixel-company-refresh'));
    } else if (outcome === 'queued') {
      write(PENDING, true);
      status.textContent = 'No connection to Mixel Remote right now. This computer will be removed as soon as it is online.';
    } else if (outcome === 'stale') {
      write(SAVED, null); write(PENDING, null);
      status.textContent = 'This computer’s registration changed in the meantime, so nothing was removed. Open Devices to see its current registration.';
      document.dispatchEvent(new CustomEvent('mixel-self-remove-done'));
    } else if (pending()) {
      // Nothing is saved any more (sent from another start of the app).
      write(PENDING, null);
    }
    render();
  }
  async function flush(): Promise<void> {
    if (flushing || busy) return;
    flushing = true;
    try { finished(await invoke<Outcome | null>('device_self_remove_flush')); }
    catch (error) {
      // A definite refusal: the request was dropped; this computer stays as it is.
      write(PENDING, null); status.textContent = `The removal was refused: ${String(error)}`; render();
      document.dispatchEvent(new CustomEvent('mixel-self-remove-done'));
    } finally { flushing = false; }
  }
  async function offline(): Promise<void> {
    const now = await invoke<Remote>('remote_status').catch(() => remote);
    if (['offline', 'error'].includes((now ?? remote).phase)) return;
    await invoke('remote_disconnect');
    for (let tries = 0; tries < 20; tries++) {
      const value = await invoke<Remote>('remote_status').catch(() => null);
      if (!value || ['offline', 'error'].includes(value.phase)) return;
      await new Promise(resolve => window.setTimeout(resolve, 250));
    }
  }
  remove.addEventListener('click', () => {
    if (busy || pending()) return;
    const name = company.tenant?.name;
    (window as unknown as {mixelDialog: Dialog}).mixelDialog.open({
      title: 'Remove this computer from Mixel Remote?',
      text: `This computer is removed${name ? ` from ${name}` : ''} at once: its registration is revoked, running sessions end and nobody can connect to it any more. Without internet, the removal is saved and sent as soon as this computer is online. Afterwards this computer is no longer set up; you can add it again.`,
      confirm: 'Remove this computer', danger: true,
      run: async () => {
        busy = true; render();
        try {
          document.dispatchEvent(new CustomEvent('mixel-self-remove-start'));
          await offline();
          const registration = saved();
          const outcome = await invoke<Outcome>('device_self_remove', {registration: registration ? {device: registration.device, connection_id: registration.connection_id, generation: registration.generation} : null, confirmed: true});
          busy = false; finished(outcome);
        } catch (error) {
          busy = false; render();
          document.dispatchEvent(new CustomEvent('mixel-self-remove-done'));
          throw error;
        }
      },
    });
  });
  document.addEventListener('mixel-remote-state', event => { const value = (event as CustomEvent<Remote>).detail; if (value.phase !== remote.phase || value.host !== remote.host) { remote = value; render(); } });
  document.addEventListener('mixel-company-state', event => { company = (event as CustomEvent<Company>).detail ?? {tenant: null}; render(); });
  document.addEventListener('mixel-this-computer', render);
  if (typeof window.addEventListener === 'function') window.addEventListener('online', () => { if (pending()) void flush(); });
  window.setInterval(() => { if (pending()) void flush(); }, 30000);
  render();
  // At start: send a removal saved earlier (it also tells whether one is still waiting).
  void flush();
})();
