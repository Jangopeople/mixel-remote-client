// Remote printing. Settings: add or remove the "Mixel Remote Printer" on this computer (Windows).
// Session: a notice when the remote computer sent a print job, to print or open it here.
(() => {
  function element<T extends HTMLElement>(id: string): T { const value = document.getElementById(id); if (!value) throw Error(`Missing printing control ${id}`); return value as T; }
  const invoke = window.__TAURI__.core.invoke;
  const panel = element<HTMLDivElement>('printing-panel'), current = element<HTMLParagraphElement>('printing-current');
  const enable = element<HTMLButtonElement>('printing-enable'), disable = element<HTMLButtonElement>('printing-disable'), status = element<HTMLParagraphElement>('printing-status');
  let busy = false;
  async function refresh(): Promise<void> {
    try {
      const value = await invoke<{supported: boolean; installed: boolean}>('remote_printing_status');
      panel.hidden = !value.supported;
      current.hidden = !value.installed; enable.hidden = value.installed; disable.hidden = !value.installed;
    } catch { panel.hidden = true; }
    enable.disabled = disable.disabled = busy;
  }
  async function change(enabled: boolean): Promise<void> {
    busy = true; status.textContent = 'Waiting for administrator approval…'; void refresh();
    try { await invoke('remote_printing_set', {enabled}); status.textContent = enabled ? 'Remote printing is on.' : 'Remote printing is off.'; }
    catch (value) { status.textContent = String(value); }
    busy = false; void refresh();
  }
  enable.addEventListener('click', () => { void change(true); });
  disable.addEventListener('click', () => { void change(false); });
  void refresh();

  const notice = element<HTMLDivElement>('print-notice'), noticeStatus = element<HTMLSpanElement>('print-notice-status');
  const printButton = element<HTMLButtonElement>('print-notice-print'), openButton = element<HTMLButtonElement>('print-notice-open'), closeButton = element<HTMLButtonElement>('print-notice-close');
  let jobs: string[] = [], handled = 0, session: string | null = null, working = false;
  function render(): void {
    const waiting = jobs.length - handled;
    notice.hidden = waiting <= 0 && !noticeStatus.textContent;
    printButton.hidden = openButton.hidden = waiting <= 0;
    printButton.disabled = openButton.disabled = working;
  }
  document.addEventListener('mixel-remote-state', (event) => {
    const detail = (event as CustomEvent<{session: string | null; prints?: string[]}>).detail;
    if (detail.session !== session) { session = detail.session; handled = 0; noticeStatus.textContent = ''; }
    jobs = detail.prints ?? [];
    if (handled > jobs.length) handled = jobs.length;
    render();
  });
  async function act(open: boolean): Promise<void> {
    const path = jobs[handled]; if (!path || working) return;
    working = true; noticeStatus.textContent = ''; render();
    try {
      const result = await invoke<string>('print_job', {path, open});
      handled += 1;
      if (!open && result === 'opened') noticeStatus.textContent = 'Opened instead: choose Print in the program that shows it.';
    } catch (value) { noticeStatus.textContent = String(value); }
    working = false; render();
  }
  printButton.addEventListener('click', () => { void act(false); });
  openButton.addEventListener('click', () => { void act(true); });
  closeButton.addEventListener('click', () => { handled += 1; noticeStatus.textContent = ''; render(); });
})();
