// Connects the shared viewer to the phone app: network calls, the connection service and
// sign-in go through the app instead of the browser. Loaded before viewer.js.
(() => {
  const { invoke } = window.__TAURI__.core;
  const KEY = 'sb-ism-auth-token';
  const API_KEY = ''; // filled in by build.mjs from the viewer's configuration
  const sockets = new Map();
  let nextSocket = 1, failed = false;
  const listening = window.__TAURI__.event.listen('ws', event => { const socket = sockets.get(event.payload.id); if (socket) socket.receive(event.payload); });

  class AppSocket {
    constructor(url) {
      this.id = nextSocket++; this.readyState = 0;
      this.onopen = this.onmessage = this.onclose = this.onerror = null;
      sockets.set(this.id, this);
      listening.then(() => invoke('ws_open', { id: this.id, url })).catch(() => { this.receive({ kind: 'error' }); this.receive({ kind: 'close' }); });
    }
    receive(event) {
      if (event.kind === 'open') { this.readyState = 1; if (this.onopen) this.onopen({}); }
      else if (event.kind === 'message') { if (this.onmessage) this.onmessage({ data: event.data }); }
      else if (event.kind === 'error') { if (this.onerror) this.onerror({}); }
      else if (event.kind === 'close' && this.readyState !== 3) { this.readyState = 3; sockets.delete(this.id); if (this.onclose) this.onclose({}); }
    }
    send(text) { invoke('ws_send', { id: this.id, data: String(text) }).catch(() => {}); }
    close() { if (this.readyState >= 2) return; this.readyState = 2; invoke('ws_close', { id: this.id }).catch(() => {}); }
  }

  async function appFetch(url, options = {}) {
    const body = options.body == null ? null : typeof options.body === 'string' ? options.body : new TextDecoder().decode(options.body);
    const reply = await invoke('native_fetch', { url: String(url), method: options.method || 'GET', headers: options.headers || {}, body });
    return { ok: reply.status >= 200 && reply.status < 300, status: reply.status, text: async () => reply.body, json: async () => JSON.parse(reply.body) };
  }
  const save = session => {
    if (!session.expires_at) session.expires_at = Math.floor(Date.now() / 1000) + (session.expires_in || 3600);
    localStorage.setItem(KEY, JSON.stringify(session));
  };
  // Runs before the viewer starts: take over a sign-in that has just come back.
  const ready = invoke('sign_in_result').then(async outcome => {
    if (outcome && outcome.session) save(outcome.session); else if (outcome && outcome.failed) failed = true;
    if (!localStorage.getItem(KEY)) { try { save(await invoke('lab_session')); } catch { /* store builds have no lab sign-in */ } }
  }).catch(() => {});

  window.MIXEL_REMOTE_CONFIG = {
    apiKey: API_KEY,
    fetch: appFetch,
    socket: url => new AppSocket(url),
    ready,
    deviceName: 'Mixel Remote app',
    signInFailed: () => failed,
    signInAction: async () => { location.href = await invoke('sign_in_begin', { apiKey: API_KEY }); },
    openExternal: url => invoke('open_external', { url }),
  };
})();
