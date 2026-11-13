// Mixel Remote browser viewer: a technician's controller for phones, tablets and any browser.
// It runs inside the ISM web app's origin and uses that app's sign-in. All identity checks and
// the end-to-end encryption of the control channel run in the shared Rust code (WASM).
import init, { Device } from './pkg/web_client.js';
import { t, applyTexts } from './texts.js';

const CONFIG = Object.assign({
  identity: 'https://ism-api.mixel.ch',
  apiKey: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiIsImlzcyI6InN1cGFiYXNlIiwiaWF0IjoxNzE0MTU4MDAwLCJleHAiOjE4NzI5MTA4MDB9.yqgm8Szodb6LBnZ2eU1y9aoqXI7xIqLxTNow-Wwc7iA',
  signaling: 'wss://rs.mixel.ch:9444/v1/ws',
  ticketUrl: 'https://ism-api.mixel.ch/v1/device-ticket',
  authorityKeys: '2da4e754d0ab4a620e90b7d6a0afadc563a1177293be4abe0e66e639574ad907',
  storageKey: 'sb-ism-auth-token',
  signIn: '/',
  meetings: 'https://rs.mixel.ch/meet/api/rooms',
}, window.MIXEL_REMOTE_CONFIG || {});
// The phone app supplies its own network calls and sign-in; in a browser these are the standard ones.
const http = (url, options) => (CONFIG.fetch || fetch)(url, options);

const $ = id => document.getElementById(id);
const now = () => Math.floor(Date.now() / 1000);
const hex = bytes => Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
const unhex = text => Uint8Array.from(text.match(/../g).map(pair => parseInt(pair, 16)));
const ascii = text => new TextEncoder().encode(text);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
class Problem extends Error {}

// ---------- sign-in session of the ISM app (shared storage, refreshed under the same lock) ----------
function storedSession() {
  try {
    const value = JSON.parse(localStorage.getItem(CONFIG.storageKey) || 'null');
    return value && typeof value.access_token === 'string' && typeof value.refresh_token === 'string' ? value : null;
  } catch { return null; }
}
function claims(token) {
  try {
    const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(decodeURIComponent(escape(atob(part.padEnd(Math.ceil(part.length / 4) * 4, '=')))));
  } catch { return {}; }
}
async function accessToken(minimumSeconds = 120) {
  let session = storedSession();
  if (!session) throw new Problem(t('Please sign in to Mixel ISM first.'));
  if ((claims(session.access_token).exp || 0) - now() >= minimumSeconds) return session.access_token;
  const refresh = async () => {
    session = storedSession();
    if (!session) throw new Problem(t('Please sign in to Mixel ISM first.'));
    if ((claims(session.access_token).exp || 0) - now() >= minimumSeconds) return session.access_token;
    const response = await http(`${CONFIG.identity}/auth/v1/token?grant_type=refresh_token`, {
      method: 'POST', headers: { apikey: CONFIG.apiKey, 'content-type': 'application/json' },
      body: JSON.stringify({ refresh_token: session.refresh_token }),
    });
    if (!response.ok) throw new Problem(t('Your sign-in has expired. Please sign in to Mixel ISM again.'));
    const fresh = await response.json();
    if (!fresh.expires_at) fresh.expires_at = now() + (fresh.expires_in || 3600);
    localStorage.setItem(CONFIG.storageKey, JSON.stringify(Object.assign({}, session, fresh)));
    return fresh.access_token;
  };
  return navigator.locks ? navigator.locks.request(`lock:${CONFIG.storageKey}`, refresh) : refresh();
}
async function api(path, options = {}) {
  const token = await accessToken();
  const response = await http(CONFIG.identity + path, {
    method: options.body === undefined ? 'GET' : 'POST',
    headers: Object.assign({ apikey: CONFIG.apiKey, authorization: `Bearer ${token}`, 'content-type': 'application/json' }, options.headers || {}),
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (!response.ok) throw new Problem(options.failure || t('The Mixel account service refused the request.'));
  return response.json();
}

// ---------- who is signed in (same rules as the desktop app) ----------
async function principal() {
  const token = await accessToken();
  const tokenClaims = claims(token);
  // A sign-in that was ended elsewhere (sign-out, removed account) shows up here first.
  const user = await api('/auth/v1/user', { failure: CONFIG.signInAction ? t('Your sign-in has ended. Please sign in again.') : t('Your sign-in has expired. Please sign in to Mixel ISM again.') });
  if (!user.id || user.id !== tokenClaims.sub) throw new Problem(t('Please sign in to Mixel ISM first.'));
  const personal = await api('/rest/v1/rpc/remote_personal_account').catch(() => []);
  if (Array.isArray(personal) && personal[0] && personal[0].profile_id) {
    return { profile: personal[0].profile_id, tenant: personal[0].profile_id, staff: false, personal: true, name: personal[0].display_name || user.email };
  }
  const rows = await api(`/rest/v1/users?auth_user_id=eq.${user.id}&is_active=eq.true&select=id,organization_id,role,name&limit=2`);
  if (rows.length !== 1) throw new Problem(t('This account cannot use Mixel Remote.'));
  const row = rows[0];
  const staff = row.organization_id === null && (row.role === 'admin' || row.role === 'agent');
  if (staff && tokenClaims.aal !== 'aal2') {
    // Team accounts need the second step. Offer it here, like the desktop app does.
    const factor = (user.factors || []).find(entry => entry.status === 'verified' && entry.factor_type === 'totp');
    if (!factor) throw new Problem(t('Set up an authenticator app for your account in Mixel ISM, then open this page again.'));
    const problem = new Problem(t('Enter the code from your authenticator app.')); problem.factor = factor.id; problem.factorName = typeof factor.friendly_name === 'string' ? factor.friendly_name.slice(0, 60) : ''; throw problem;
  }
  if (!staff) return companyPrincipal(row, user, tokenClaims);
  return { profile: row.id, tenant: null, staff: true, personal: false, name: row.name || user.email };
}
// A member of a Mixel Remote company (remote.mixel.ch). The database decides what they reach: owners
// and admins every device of the company, technicians the devices of their groups and those in no
// group, members only devices they added. Members cannot start sessions (the connection policy
// admits owners, admins and technicians only), so they see their devices without Connect.
async function companyPrincipal(row, user, tokenClaims) {
  const companies = await api('/rest/v1/rpc/remote_saas_my_tenants', { body: {} }).catch(() => []);
  const company = Array.isArray(companies) ? companies[0] : null;
  if (!company || company.organization_id !== row.organization_id) throw new Problem(t('Remote control in the browser is for Mixel team members, Mixel Remote company accounts and personal accounts.'));
  // The company's sign-in rules (required second step, allowed methods) are checked by the database.
  const token = await accessToken();
  const response = await http(`${CONFIG.identity}/rest/v1/rpc/remote_saas_tenant_overview`, {
    method: 'POST', headers: { apikey: CONFIG.apiKey, authorization: `Bearer ${token}`, 'content-type': 'application/json' },
    body: JSON.stringify({ tenant: company.tenant_id }),
  });
  if (!response.ok) {
    const failure = await response.json().catch(() => ({}));
    const message = String(failure && failure.message || '');
    if (/two-step sign-in is required/i.test(message)) {
      const factor = (user.factors || []).find(entry => entry.status === 'verified' && entry.factor_type === 'totp');
      if (!factor || tokenClaims.aal === 'aal2') throw new Problem(t('Your company requires 2-step login. Set it up at remote.mixel.ch, then open this page again.'));
      const problem = new Problem(t('Enter the code from your authenticator app.')); problem.factor = factor.id; problem.company = true; problem.factorName = typeof factor.friendly_name === 'string' ? factor.friendly_name.slice(0, 60) : ''; throw problem;
    }
    if (/sign-in method is not allowed/i.test(message)) throw new Problem(t('Your company does not allow the sign-in method you used. Sign out and sign in another way.'));
    if (/read-only/i.test(message)) throw new Problem(t('Your company’s Mixel Remote account is read-only.'));
    throw new Problem(t('The Mixel account service refused the request.'));
  }
  const overview = await response.json();
  return { profile: row.id, tenant: row.organization_id, staff: false, personal: false, company: { id: company.tenant_id, name: company.name, role: overview.role }, name: `${row.name || user.email} · ${company.name}` };
}

// ---------- this browser as a registered device ----------
const deviceKey = profile => `mixel.remote.web.device.v1:${profile}`;
let device = null, enrollment = null, who = null;
function browserName() {
  const agent = navigator.userAgent;
  const system = /iPad|iPhone/.test(agent) ? (/iPad/.test(agent) ? 'iPad' : 'iPhone') : /Android/.test(agent) ? 'Android' : /Mac/.test(agent) ? 'Mac' : /Windows/.test(agent) ? 'Windows' : 'Linux';
  return { name: CONFIG.deviceName ? `${t(CONFIG.deviceName)} (${system})` : `${t('Browser')} (${system})`, os: /iPad|iPhone|Mac/.test(agent) ? 'macos' : /Windows/.test(agent) ? 'windows' : 'linux' };
}
async function loadDevice() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(deviceKey(who.profile)) || 'null'); } catch { saved = null; }
  device = new Device(saved && saved.seed ? unhex(saved.seed) : undefined);
  if (!saved) localStorage.setItem(deviceKey(who.profile), JSON.stringify({ seed: hex(device.seed()) }));
  const key = hex(device.public_key());
  let rows;
  if (who.personal) {
    rows = (await api('/rest/v1/rpc/remote_personal_devices')).filter(row => row.public_key === `\\x${key}`);
  } else {
    rows = await api(`/rest/v1/remote_device_enrollments?public_key=eq.%5Cx${key}&select=device_id,connection_id,generation,public_key,revoked_at&limit=2`);
  }
  const removed = rows.some(row => row.revoked_at);
  rows = rows.filter(row => !row.revoked_at);
  enrollment = rows.length === 1 ? rows[0] : null;
  if (!enrollment && removed) {
    // This registration was removed. A removed key is never accepted again, so start over with a new one.
    device = new Device();
    localStorage.setItem(deviceKey(who.profile), JSON.stringify({ seed: hex(device.seed()) }));
  }
  return enrollment;
}
async function register(company, name) {
  const { os } = browserName();
  const failure = t('This browser could not be registered.');
  const enrol = async () => {
    const key = `\\x${hex(device.public_key())}`;
    if (who.personal) return api('/rest/v1/rpc/remote_register_personal_device', { body: { device_name: name, device_os: os, key_material: key }, failure });
    if (who.company) return api('/rest/v1/rpc/remote_saas_add_this_device', { body: { tenant: who.company.id, device_name: name, device_os: os, key_material: key }, failure });
    const target = await api('/rest/v1/rpc/remote_add_device', { body: { target_client: company, device_name: name, device_os: os }, failure });
    return api('/rest/v1/rpc/remote_enroll_device', { body: { target, key_material: key }, headers: { accept: 'application/vnd.pgrst.object+json' }, failure });
  };
  try { await enrol(); }
  catch {
    // A key whose registration was removed earlier is never accepted again: try once more with a new key.
    device = new Device();
    localStorage.setItem(deviceKey(who.profile), JSON.stringify({ seed: hex(device.seed()) }));
    await enrol();
  }
  if (!(await loadDevice())) throw new Problem(failure);
}
async function ticket() {
  const token = await accessToken(300);
  const proof = device.delegation_proof(enrollment.device_id, who.profile, who.tenant, enrollment.connection_id, enrollment.generation, now());
  const response = await http(CONFIG.ticketUrl, { method: 'POST', headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' }, body: proof });
  if (!response.ok) throw new Problem(t('Mixel did not confirm this browser. Please try again.'));
  return device.accept_ticket(await response.text(), proof, CONFIG.authorityKeys, who.staff, claims(token).exp, now());
}

// ---------- connection service ----------
let socket = null, online = false, lastRenew = 0, pendingRequest = null, active = null, wanted = false;
const waiters = [];
function send(message) { if (socket && socket.readyState === 1) socket.send(JSON.stringify(message)); }
// One presence question at a time, so every asker gets the answer to its own question.
let presenceQueue = Promise.resolve();
function presence(hosts) {
  const answer = presenceQueue.then(async () => {
    if (!online || !hosts.length) return [];
    send({ type: 'presence', hosts });
    return (await nextMessage(['presence'], 10).catch(() => ({ online: [] }))).online;
  });
  presenceQueue = answer.catch(() => {});
  return answer;
}
function nextMessage(types, seconds) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { waiters.splice(waiters.indexOf(entry), 1); reject(new Problem(t('The connection service did not answer.'))); }, seconds * 1000);
    const entry = { types, resolve: value => { clearTimeout(timer); resolve(value); }, reject: value => { clearTimeout(timer); reject(value); } };
    waiters.push(entry);
  });
}
async function connectService() {
  if (online) return;
  status(t('Connecting…'));
  const encoded = await ticket();
  lastRenew = now();
  socket = CONFIG.socket ? CONFIG.socket(CONFIG.signaling) : new WebSocket(CONFIG.signaling);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = () => reject(new Problem(t('The connection service cannot be reached.'))); });
  socket.onmessage = event => { if (typeof event.data === 'string') dispatch(JSON.parse(event.data)); };
  socket.onclose = () => { online = false; waiters.splice(0).forEach(entry => entry.reject(new Problem(t('The connection was lost.')))); if (active) endSession(t('The connection was lost.')); else if (wanted) status(t('Not connected. Reload the page to connect again.')); renderHome(); };
  send({ type: 'authenticate_device', token: encoded, device: enrollment.device_id });
  const challenge = await nextMessage(['device_challenge'], 20);
  send({ type: 'prove_device', signature: Array.from(device.prove_login(JSON.stringify(challenge.challenge))) });
  const ready = await nextMessage(['ready', 'error'], 10);
  if (ready.type !== 'ready' || ready.host !== enrollment.connection_id) throw new Problem(t('Another window already uses this browser registration. Close it and try again.'));
  online = true; wanted = true;
  status('');
}
function dispatch(message) {
  const index = waiters.findIndex(entry => entry.types.includes(message.type) && (!entry.session || entry.session === message.session));
  if (index >= 0) { waiters.splice(index, 1)[0].resolve(message); return; }
  if (message.type === 'signal' && active && message.session === active.session) active.onSignal(message.payload);
  else if (message.type === 'closed' && active && message.session === active.session) endSession(message.reason === 'requested' ? t('The session was ended.') : t('The remote computer closed the session.'));
  else if (message.type === 'closed' && pendingRequest === message.session) { /* handled by the request waiter */ }
  else if (message.type === 'peer_renewed' || message.type === 'renewed') { /* identity unchanged; nothing to show */ }
}
setInterval(async () => {
  if (!online || !device) return;
  try {
    if (now() - lastRenew >= 600 || device.ticket_expires() - now() <= 150) {
      lastRenew = now();
      send({ type: 'renew', token: await ticket() });
    }
    if (active && active.connectedAt && now() - active.connectedAt >= 700 && !active.restarting) active.restart();
    if (!active && ++homeTicks % 2 === 0) renderHome();
  } catch (problem) { console.warn(problem); }
}, 15000);
let homeTicks = 0, wakeLock = null;
async function keepAwake(on) {
  // A phone must not go to sleep in the middle of a session.
  try {
    if (on && navigator.wakeLock && !wakeLock) wakeLock = await navigator.wakeLock.request('screen');
    if (!on && wakeLock) { await wakeLock.release(); wakeLock = null; }
  } catch { wakeLock = null; }
}

// ---------- devices and requests ----------
async function directory(query) {
  if (who.personal) return (await api('/rest/v1/rpc/remote_personal_devices')).filter(row => !row.revoked_at).map(row => ({ id: row.connection_id, name: row.name, os: row.os }));
  if (who.company) {
    const rows = await api('/rest/v1/rpc/remote_saas_devices', { body: { tenant: who.company.id, in_group: null, search: query ? query.slice(0, 80) : null } });
    return rows.filter(row => row.connection_id && !row.revoked).slice(0, 51).map(row => ({ id: row.connection_id, name: row.name, os: row.os }));
  }
  const filter = query ? `&devices.name=ilike.*${encodeURIComponent(query.replace(/[*,()]/g, ''))}*` : '';
  const rows = await api(`/rest/v1/remote_device_enrollments?select=device_id,connection_id,generation,devices!inner(id,name,os)&revoked_at=is.null&order=connection_id.asc&limit=51${filter}`);
  return rows.map(row => ({ id: row.connection_id, name: row.devices.name, os: row.devices.os }));
}
async function hostKey(id) {
  if (who.personal) {
    const row = (await api('/rest/v1/rpc/remote_personal_devices')).find(entry => entry.connection_id === id && !entry.revoked_at);
    if (row) return unhex(row.public_key.slice(2));
  }
  const rows = await api(`/rest/v1/remote_device_enrollments?connection_id=eq.${id}&select=device_id,connection_id,generation,public_key,revoked_at&limit=2`);
  if (rows.length !== 1 || rows[0].revoked_at) throw new Problem(t('This Remote ID is not known to your account.'));
  return unhex(rows[0].public_key.slice(2));
}
async function request(id, code, unattended) {
  if (!enrollment) throw new Problem(t('Register first, then connect.'));
  await connectService();
  // A phone with Mixel Quick Support: claim its ID for this team member, then wait until it is online.
  if (who && who.staff && !unattended) {
    const phone = await api('/rest/v1/rpc/remote_quick_phone_claim', { body: { connection: id } }).catch(error => { throw new Problem(t('This phone could not be prepared for the session. Sign in again and try once more.') + (error && error.message ? ` (${error.message})` : '')); });
    if (phone === true) {
      status(t('Phone found. Waiting for it to come online… On an iPhone the person taps “Start sharing my screen”.'));
      let reachable = false;
      for (let round = 0; round < 60 && !reachable; round++) {
        reachable = (await presence([id])).includes(id);
        if (!reachable) await new Promise(resolve => setTimeout(resolve, 2000));
      }
      if (!reachable) throw new Problem(t('The phone did not come online. Ask the person to keep Mixel Quick Support open, then choose Connect again.'));
    }
  }
  const key = await hostKey(id);
  status(unattended ? t('Connecting…') : t('Waiting for the other person to accept…'));
  send(unattended ? { type: 'request_unattended', host: id } : code ? { type: 'request_with_code', host: id, code } : { type: 'request', host: id });
  const requested = await nextMessage(['requested', 'error'], 20);
  if (requested.type === 'error') throw new Problem(requested.code === 'busy' ? t('The remote computer is busy.') : t('The remote computer is not available. Ask the other person to make it available.'));
  pendingRequest = requested.session;
  const accepted = await nextMessage(['accepted', 'closed', 'error'], 100).finally(() => { pendingRequest = null; });
  if (accepted.type !== 'accepted' || accepted.session !== requested.session) throw new Problem(accepted.reason === 'rejected' ? t('The request was declined.') : t('The request was not answered.'));
  await startSession(accepted, key, id, unattended);
}

// ---------- one session ----------
async function startSession(accepted, key, hostId, unattended) {
  const session = { session: accepted.session, hostId, key, unattended, hello: null, candidates: [], pc: null, control: null, permissions: null, displays: 0, display: 0, displayAll: false, actions: [], connectedAt: 0, restarting: false, pending: null };
  session.onSignal = payload => onSignal(session, payload);
  session.restart = () => restart(session);
  window.__mixelRenew = () => session.restart(); // lab: renew the link now instead of after 12 minutes
  active = session;
  showSession(true);
  await negotiate(session, accepted);
}
async function negotiate(session, accepted) {
  const context = device.session(accepted.session, Uint8Array.from(accepted.nonce), JSON.stringify(accepted.controller_identity), JSON.stringify(accepted.host_identity), session.key, session.hostId, session.unattended, CONFIG.authorityKeys, now());
  send({ type: 'ice', session: accepted.session });
  const lease = await nextMessage(['ice', 'error'], 20);
  if (lease.type !== 'ice' || lease.session !== accepted.session) throw new Problem(t('The connection service did not provide a relay.'));
  const pc = new RTCPeerConnection({ iceServers: lease.lease.servers.map(server => ({ urls: server.urls, username: server.username, credential: server.credential })), iceTransportPolicy: 'relay' });
  session.pc = pc; session.hello = session.hello && session.helloFor === accepted.session ? session.hello : null; session.candidates = [];
  const transceiver = pc.addTransceiver('video', { direction: 'recvonly' });
  const codecs = (RTCRtpReceiver.getCapabilities('video') || { codecs: [] }).codecs.filter(codec => codec.mimeType.toLowerCase() === 'video/h264' && /packetization-mode=1/.test(codec.sdpFmtpLine || ''));
  if (!codecs.length) throw new Problem(t('This browser cannot show the remote screen (H.264 video is missing).'));
  if (transceiver.setCodecPreferences) transceiver.setCodecPreferences(codecs);
  // A remote desktop wants every picture at once, not smooth playback: no extra buffering.
  try { transceiver.receiver.jitterBufferTarget = 0; transceiver.receiver.playoutDelayHint = 0; } catch { /* not supported everywhere */ }
  const channel = pc.createDataChannel('mixel-control-v1', { ordered: true });
  channel.binaryType = 'arraybuffer';
  pc.ontrack = event => { $('screen').srcObject = event.streams[0] || new MediaStream([event.track]); $('screen').play().catch(() => {}); };
  let offerSent = false;
  pc.onicecandidate = event => { if (event.candidate && offerSent) send({ type: 'signal', session: accepted.session, payload: { kind: 'ice', value: JSON.stringify(event.candidate.toJSON()) } }); };
  pc.onconnectionstatechange = () => { if (['failed', 'closed'].includes(pc.connectionState) && active === session && session.pc === pc && !session.restarting) endSession(t('The connection was lost.')); };
  await pc.setLocalDescription(await pc.createOffer());
  await Promise.race([new Promise(resolve => { pc.onicegatheringstatechange = () => { if (pc.iceGatheringState === 'complete') resolve(); }; if (pc.iceGatheringState === 'complete') resolve(); }), sleep(3000)]);
  const local = pc.localDescription.sdp;
  if (!/typ relay/.test(local)) throw new Problem(t('No relay connection is possible from this network.'));
  const pending = device.begin(context, fingerprint(local));
  send({ type: 'signal', session: accepted.session, payload: { kind: 'identity', value: JSON.parse(pending.hello()) } });
  send({ type: 'signal', session: accepted.session, payload: { kind: 'offer', value: `${local.trimEnd()}\r\na=x-mixel-recording:1\r\na=x-mixel-input-result:1\r\na=x-mixel-displays:1\r\na=x-mixel-actions:1\r\na=x-mixel-features:hd,sound\r\n` } });
  offerSent = true;
  session.session = accepted.session;
  session.pending = { pending, channel, opened: new Promise(resolve => { channel.onopen = resolve; }) };
  session.flush && session.flush();
  const deadline = Date.now() + 30000;
  while (!(session.answered && session.hello)) { if (Date.now() > deadline || active !== session) throw new Problem(t('The remote computer did not answer in time.')); await sleep(50); }
  await Promise.race([session.pending.opened, sleep(30000).then(() => { throw new Problem(t('The remote computer did not answer in time.')); })]);
  const observed = await remoteFingerprint(pc);
  const control = pending.finish(JSON.stringify(session.hello), observed);
  session.control = control;
  const first = await new Promise((resolve, reject) => { channel.onmessage = event => resolve(event.data); setTimeout(() => reject(new Problem(t('The remote computer did not answer in time.'))), 6000); });
  const permissions = control.open(new Uint8Array(first));
  const text = new TextDecoder().decode(permissions.slice(0, 20));
  if (!text.startsWith('mixel-permissions-v')) throw new Problem(t('The remote computer sent an unexpected answer.'));
  session.permissions = { control: permissions[20] === 1, clipboard: permissions[21] === 1 };
  if (!session.permissions.control) { session.displays = 0; session.actions = []; }
  channel.onmessage = event => { try { onRecord(session, control.open(new Uint8Array(event.data))); } catch { endSession(t('The connection was lost.')); } };
  session.channel = channel; session.connectedAt = now(); session.restarting = false; session.answered = false;
  renderToolbar(session);
  status('');
  keyframeLoop(session, pc);
}
function fingerprint(sdp) {
  const lines = [...sdp.matchAll(/^a=fingerprint:sha-256 ([0-9A-Fa-f:]+)\s*$/gm)].map(match => match[1].toLowerCase());
  if (!lines.length || lines.some(line => line !== lines[0])) throw new Problem(t('The connection could not be secured.'));
  return unhex(lines[0].replace(/:/g, ''));
}
async function remoteFingerprint(pc) {
  try {
    const certificates = pc.sctp && pc.sctp.transport && pc.sctp.transport.getRemoteCertificates ? pc.sctp.transport.getRemoteCertificates() : [];
    if (certificates.length) return new Uint8Array(await crypto.subtle.digest('SHA-256', certificates[0]));
  } catch { /* fall through */ }
  // The browser has verified the DTLS certificate against this line; the host signs its own value, too.
  return fingerprint(pc.remoteDescription.sdp);
}
async function onSignal(session, payload) {
  if (payload.kind === 'identity') { session.hello = payload.value; session.helloFor = session.session; return; }
  if (!session.pc) return;
  if (payload.kind === 'answer') {
    const sdp = payload.value;
    const count = /^a=x-mixel-display-count:(\d+):(\d+)\s*$/m.exec(sdp);
    session.displays = count && +count[1] >= 2 && +count[1] <= 16 && +count[2] < +count[1] ? +count[1] : 0;
    session.display = session.displays ? +count[2] : 0;
    session.displayAll = session.displays >= 3 && /^a=x-mixel-display-all:1\s*$/m.test(sdp);
    const actions = /^a=x-mixel-host-actions:(.*)$/m.exec(sdp);
    session.actions = actions ? actions[1].trim().split(',') : [];
    await session.pc.setRemoteDescription({ type: 'answer', sdp });
    for (const candidate of session.candidates.splice(0)) await session.pc.addIceCandidate(candidate).catch(() => {});
    session.answered = true;
  } else if (payload.kind === 'ice') {
    const candidate = JSON.parse(payload.value);
    if (session.pc.remoteDescription) await session.pc.addIceCandidate(candidate).catch(() => {}); else session.candidates.push(candidate);
  }
}
async function restart(session) {
  // The viewing permission of one transport lasts 15 minutes: replace it in time, keep the picture.
  session.restarting = true;
  try {
    send({ type: 'restart', session: session.session });
    const restarted = await nextMessage(['restarted', 'error'], 20);
    if (restarted.type !== 'restarted' || restarted.previous !== session.session) throw new Problem(t('The connection was lost.'));
    const old = session.pc; session.control = null; session.channel = null; session.hello = null; session.answered = false;
    session.session = restarted.session;
    await negotiate(session, restarted);
    old.close();
  } catch (problem) { endSession(problem instanceof Problem ? problem.message : t('The connection was lost.')); }
}
const SOUND = ascii('mixel-sound-v1:');
function onRecord(session, plain) {
  const kind = new TextDecoder().decode(plain.subarray(0, 14)).replace(/[^a-z-]/g, '.'); recordKinds[kind] = (recordKinds[kind] || 0) + 1;
  // Sound frames are played; input acknowledgments and "screen unchanged" notes need nothing here.
  if (plain.length === SOUND.length + 483 && SOUND.every((byte, index) => plain[index] === byte)) { soundReceived++; playSound(plain.subarray(SOUND.length)); }
  void session;
}
// Remote sound: mono, 24 kHz, IMA ADPCM, 40 ms per frame = 3 header bytes + 480 bytes
// with two samples each (same format as the desktop app).
const STEP = [7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767];
const STEP_INDEX = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8];
let audio = null, audioNext = 0, soundOn = true;
function unlockSound() {
  // Browsers only allow sound after a tap or click: prepare it when the user connects.
  try { const Context = window.AudioContext || window.webkitAudioContext; if (!audio && Context) audio = new Context(); if (audio && audio.state === 'suspended') audio.resume(); } catch { audio = null; }
}
function playSound(frame) {
  if (!audio || !soundOn || frame[2] > 88) return;
  let predictor = (frame[0] << 8) | frame[1]; if (predictor & 0x8000) predictor -= 0x10000;
  let index = frame[2], position = 0;
  const buffer = audio.createBuffer(1, 960, 24000), samples = buffer.getChannelData(0);
  for (let offset = 3; offset < 483; offset++) for (const code of [frame[offset] & 15, frame[offset] >> 4]) {
    const size = STEP[index];
    let difference = size >> 3;
    if (code & 4) difference += size; if (code & 2) difference += size >> 1; if (code & 1) difference += size >> 2;
    predictor = Math.max(-32768, Math.min(32767, code & 8 ? predictor - difference : predictor + difference));
    index = Math.max(0, Math.min(88, index + STEP_INDEX[code]));
    samples[position++] = predictor / 32768;
  }
  const time = audio.currentTime;
  if (audioNext < time + 0.05 || audioNext > time + 0.6) audioNext = time + 0.08;
  const source = audio.createBufferSource(); source.buffer = buffer; source.connect(audio.destination); source.start(audioNext);
  audioNext += 0.04;
  soundFrames++;
}
let soundFrames = 0, soundReceived = 0;
const recordKinds = {};
// For support and tests: how many sound frames arrived and were handed to the speakers.
window.__mixelSound = () => ({ received: soundReceived, played: soundFrames, output: audio ? audio.state : 'none', records: recordKinds });
async function keyframeLoop(session, pc) {
  let decoded = -1, losses = 0;
  while (active === session && session.pc === pc) {
    try {
      let frames = 0, lost = 0;
      (await pc.getStats()).forEach(report => { if (report.type === 'inbound-rtp' && report.kind === 'video') { frames = report.framesDecoded || 0; lost = (report.pliCount || 0) + (report.nackCount || 0) + (report.packetsLost || 0); } });
      if (frames === 0 || lost !== losses) record(session, ascii('mixel-video-keyframe-v1'));
      losses = lost; decoded = frames;
    } catch { /* stats unavailable */ }
    await sleep(decoded > 0 ? 1500 : 1000);
  }
}
function record(session, plain) {
  if (session.control && session.channel && session.channel.readyState === 'open') session.channel.send(session.control.seal(plain));
}
function sendInput(build) {
  const session = active;
  if (!session || !session.control || !session.permissions || !session.permissions.control || !session.channel || session.channel.readyState !== 'open') return;
  try { session.channel.send(build(session.control)); } catch { endSession(t('The connection was lost.')); }
}
function endSession(message) {
  const session = active;
  if (!session) return;
  active = null;
  try { sendInputRelease(session); } catch { /* closing anyway */ }
  send({ type: 'close', session: session.session });
  try { session.pc && session.pc.close(); } catch { /* closed */ }
  $('screen').srcObject = null;
  setZoom(1);
  showSession(false);
  status(message || '');
  renderHome();
}
function sendInputRelease(session) {
  if (session.control && session.channel && session.channel.readyState === 'open' && session.permissions && session.permissions.control) session.channel.send(session.control.release_all());
}

// ---------- pointer, touch and keyboard ----------
const screen = () => $('screen');
function position(event) {
  const video = screen(), box = video.getBoundingClientRect();
  const scale = Math.min(box.width / (video.videoWidth || 1), box.height / (video.videoHeight || 1));
  const width = (video.videoWidth || 1) * scale, height = (video.videoHeight || 1) * scale;
  return { x: event.clientX - box.left - (box.width - width) / 2, y: event.clientY - box.top - (box.height - height) / 2, width, height };
}
let moveQueued = null;
function move(event) {
  const at = position(event);
  moveQueued = at;
  requestAnimationFrame(() => { if (moveQueued) { const p = moveQueued; moveQueued = null; sendInput(control => control.pointer(active ? active.display : 0, p.x, p.y, p.width, p.height)); } });
}
function moveNow(event) { const p = position(event); moveQueued = null; sendInput(control => control.pointer(active ? active.display : 0, p.x, p.y, p.width, p.height)); }
const touches = new Map();
let touchMode = null, longPress = 0, lastTap = 0, rightClickNext = false;
function pointerDown(event) {
  event.preventDefault();
  if (typed) resetTyping();
  if (event.pointerType === 'mouse') { moveNow(event); sendInput(control => control.button(event.button === 2 ? 2 : event.button === 1 ? 3 : 1, true)); return; }
  touches.set(event.pointerId, { x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY, time: Date.now() });
  if (touches.size === 1) {
    touchMode = 'tap'; moveNow(event);
    clearTimeout(longPress);
    longPress = setTimeout(() => { if (touchMode === 'tap') { touchMode = 'done'; click(2); if (navigator.vibrate) navigator.vibrate(15); } }, 550);
  } else { clearTimeout(longPress); if (touchMode === 'drag') sendInput(control => control.button(1, false)); touchMode = 'scroll'; }
}
const clampWheel = value => Math.max(-120, Math.min(120, Math.round(value)));
function click(button) { sendInput(control => control.button(button, true)); sendInput(control => control.button(button, false)); }
function pointerMove(event) {
  if (event.pointerType === 'mouse') { move(event); return; }
  const touch = touches.get(event.pointerId);
  if (!touch) return;
  event.preventDefault();
  const dx = event.clientX - touch.x, dy = event.clientY - touch.y;
  if (touchMode === 'scroll' && zoom > 1) {
    // Zoomed in: two fingers move the picture, they do not scroll the remote computer.
    pan(dx, dy); touch.x = event.clientX; touch.y = event.clientY;
    return;
  }
  if (touchMode === 'scroll') {
    // The content follows the fingers; the host counts in mouse-wheel units (120 = one notch).
    if (Math.abs(dx) + Math.abs(dy) >= 4) { sendInput(control => control.wheel(clampWheel(-dx * 2), clampWheel(dy * 2))); touch.x = event.clientX; touch.y = event.clientY; }
    return;
  }
  if (touchMode === 'tap' && Math.hypot(event.clientX - touch.startX, event.clientY - touch.startY) > 8) {
    clearTimeout(longPress); touchMode = 'drag'; sendInput(control => control.button(1, true));
  }
  if (touchMode === 'drag') move(event);
}
function pointerUp(event) {
  if (event.pointerType === 'mouse') { moveNow(event); sendInput(control => control.button(event.button === 2 ? 2 : event.button === 1 ? 3 : 1, false)); return; }
  if (!touches.has(event.pointerId)) return;
  event.preventDefault();
  touches.delete(event.pointerId);
  clearTimeout(longPress);
  if (touchMode === 'tap') {
    const button = rightClickNext ? 2 : 1; rightClickNext = false; $('tool-right').setAttribute('aria-pressed', 'false');
    click(button);
    if (button === 1 && Date.now() - lastTap < 350) click(1);
    lastTap = Date.now();
  } else if (touchMode === 'drag') { moveNow(event); sendInput(control => control.button(1, false)); }
  if (touches.size === 0) touchMode = null;
}
let zoom = 1, panX = 0, panY = 0;
function pan(dx, dy) {
  const stage = $('stage').getBoundingClientRect(), limitX = stage.width * (zoom - 1) / 2, limitY = stage.height * (zoom - 1) / 2;
  panX = Math.max(-limitX, Math.min(limitX, panX + dx)); panY = Math.max(-limitY, Math.min(limitY, panY + dy));
  screen().style.transform = zoom === 1 ? '' : `translate(${panX}px, ${panY}px) scale(${zoom})`;
}
function setZoom(value) { zoom = value; if (zoom === 1) { panX = 0; panY = 0; } pan(0, 0); $('tool-zoom').textContent = `${zoom}×`; }
const USAGE = { Enter: 0x28, Escape: 0x29, Backspace: 0x2a, Tab: 0x2b, Space: 0x2c, Minus: 0x2d, Equal: 0x2e, BracketLeft: 0x2f, BracketRight: 0x30, Backslash: 0x31, Semicolon: 0x33, Quote: 0x34, Backquote: 0x35, Comma: 0x36, Period: 0x37, Slash: 0x38, CapsLock: 0x39, PrintScreen: 0x46, ScrollLock: 0x47, Pause: 0x48, Insert: 0x49, Home: 0x4a, PageUp: 0x4b, Delete: 0x4c, End: 0x4d, PageDown: 0x4e, ArrowRight: 0x4f, ArrowLeft: 0x50, ArrowDown: 0x51, ArrowUp: 0x52, NumLock: 0x53, IntlBackslash: 0x64, ContextMenu: 0x65, ControlLeft: 0xe0, ShiftLeft: 0xe1, AltLeft: 0xe2, MetaLeft: 0xe3, ControlRight: 0xe4, ShiftRight: 0xe5, AltRight: 0xe6, MetaRight: 0xe7 };
function usage(code) {
  if (/^Key[A-Z]$/.test(code)) return 4 + code.charCodeAt(3) - 65;
  if (/^Digit[1-9]$/.test(code)) return 0x1e + (+code[5]) - 1;
  if (code === 'Digit0') return 0x27;
  if (/^F([1-9]|1[0-2])$/.test(code)) return 0x3a + (+code.slice(1)) - 1;
  return USAGE[code] || 0;
}
function keyEvent(event, down) {
  if (!active || document.activeElement === $('typing')) return;
  const code = usage(event.code);
  if (!code) return;
  event.preventDefault();
  sendInput(control => control.key(code, down));
}
function tapKey(...codes) { for (const code of codes) sendInput(control => control.key(code, true)); for (const code of codes.reverse()) sendInput(control => control.key(code, false)); }

// ---------- screens ----------
function status(text) { $('status').textContent = text; $('session-status').textContent = text; }
function showSession(visible) {
  document.body.classList.toggle('in-session', visible);
  $('home').hidden = visible; $('session').hidden = !visible;
  keepAwake(visible);
  if (!visible && document.fullscreenElement) document.exitFullscreen().catch(() => {});
}
function renderToolbar(session) {
  $('tool-sas').hidden = !session.actions.includes('sas');
  $('tool-sound').hidden = !session.actions.includes('sound');
  $('tool-sound').textContent = soundOn ? t('Sound: on') : t('Sound: off');
  const displays = $('tool-displays'); displays.replaceChildren(); displays.hidden = session.displays < 2;
  for (let index = 0; index < session.displays; index++) {
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = session.displayAll && index === session.displays - 1 ? t('All') : String(index + 1);
    button.setAttribute('aria-pressed', String(index === session.display));
    button.onclick = () => { session.display = index; record(session, Uint8Array.from([...ascii('mixel-display-select-v1'), index])); renderToolbar(session); };
    displays.appendChild(button);
  }
  $('view-only').hidden = session.permissions.control;
  for (const id of ['tool-keyboard', 'tool-right', 'tool-keys']) $(id).hidden = !session.permissions.control;
}
const viewOnlyMember = () => !!(who && who.company && who.company.role === 'member');
async function renderHome() {
  const member = viewOnlyMember();
  $('register').hidden = !!enrollment || !who || member; $('connect').hidden = !enrollment && !member;
  $('support').hidden = !(who && who.staff && enrollment);
  $('member-note').hidden = !member; $('connect-form').hidden = member; $('own-line').hidden = member;
  if ((!enrollment && !member) || active) return;
  if (enrollment) $('own-id').textContent = String(enrollment.connection_id).replace(/(\d{3})(?=\d)/g, '$1 ');
  try {
    const devices = (await directory($('search').value.trim())).filter(entry => !enrollment || entry.id !== enrollment.connection_id);
    let reachable = [];
    if (online && devices.length) reachable = await presence(devices.slice(0, 64).map(entry => entry.id));
    const list = $('devices'); list.replaceChildren();
    for (const entry of devices.slice(0, 50)) {
      const item = document.createElement('button');
      item.type = 'button'; item.className = 'device';
      const name = document.createElement('strong'); name.textContent = entry.name;
      const detail = document.createElement('span'); detail.textContent = `${String(entry.id).replace(/(\d{3})(?=\d)/g, '$1 ')} · ${reachable.includes(entry.id) ? t('online') : t('offline')}`;
      if (reachable.includes(entry.id)) item.classList.add('online');
      item.append(name, detail);
      // One tap connects; the one-time password and unattended choice above still apply.
      if (member) item.disabled = true;
      else item.onclick = () => { $('remote-id').value = String(entry.id); $('connect-form').requestSubmit(); };
      list.appendChild(item);
    }
    $('no-devices').hidden = devices.length > 0;
  } catch (problem) { console.warn(problem); }
}
async function run(task) {
  try { await task(); }
  catch (problem) {
    console.error(problem);
    const message = problem instanceof Problem ? problem.message : t('That did not work. Please try again.');
    if (active) endSession(message); else status(message);
  }
}

// Meetings: any signed-in account can start one; guests join with the invitation link alone.
async function startMeeting() {
  const button = $('meet-button');
  button.disabled = true;
  try {
    const response = await http(CONFIG.meetings, { method: 'POST', headers: { authorization: `Bearer ${await accessToken()}`, 'content-type': 'application/json' }, body: JSON.stringify({ title: who ? who.name : '' }) }).catch(() => null);
    if (!response || !response.ok) throw new Problem(response && response.status === 429 ? t('You have started many meetings. Please try again later.') : t('The meeting could not be started. Please try again.'));
    const room = await response.json();
    $('meet-open').href = room.host; $('meet-invite').value = room.invite; $('meet-result').hidden = false;
    status('');
  } finally { button.disabled = false; }
}
let started = false;
async function start() {
  if (!started) { applyTexts(); if (CONFIG.ready) await CONFIG.ready; await init(); started = true; }
  if (CONFIG.deviceName) {
    // Inside the phone app it is the phone that is registered, not a browser.
    $('register-title').textContent = t('Register this device');
    $('register-hint').textContent = t('To connect from here, this device is registered once as one of your devices. It gets its own Remote ID.');
    $('own-label').textContent = t('This device:');
  }
  $('signin-link').href = CONFIG.signIn;
  if (CONFIG.signInAction) { $('signin-link').textContent = t('Sign in with Mixel ID'); $('signin-link').onclick = event => { event.preventDefault(); run(CONFIG.signInAction); }; }
  $('signout').hidden = true;
  try { who = await principal(); }
  catch (problem) {
    if (problem.factor) {
      mfaFactor = problem.factor;
      // Say which entry of the authenticator app is meant; people have several.
      $('mfa-label').textContent = problem.factorName ? `${t('Open your authenticator app and enter the 6-digit code shown for:')} ${problem.factorName}` : t('Enter the code from your authenticator app.');
      $('signout').hidden = !CONFIG.signInAction;
      if (problem.company) $('mfa-why').textContent = t('Your company requires a second step. Enter the code of your Mixel Remote account.');
      $('mfa').hidden = false; $('mfa-code').focus(); return;
    }
    $('signin').hidden = false;
    const text = problem instanceof Problem ? problem.message : t('Please sign in to Mixel ISM first.');
    $('signin-text').textContent = CONFIG.signInAction && !storedSession() ? (CONFIG.signInFailed && CONFIG.signInFailed() ? t('The sign-in was not completed. Please try again.') : t('Sign in with your Mixel ID to control your computers from here.')) : text;
    $('signout').hidden = !(CONFIG.signInAction && storedSession());
    return;
  }
  $('account').textContent = who.name;
  $('signout').hidden = !CONFIG.signInAction;
  $('meet').hidden = false;
  await loadDevice();
  if (viewOnlyMember()) { renderHome(); return; }
  if (!enrollment) {
    $('register-name').value = browserName().name;
    if (who.staff) {
      const companies = await api('/rest/v1/rpc/remote_enrollment_companies');
      for (const company of companies) { const option = document.createElement('option'); option.value = company.client_id; option.textContent = company.client_name; $('register-company').appendChild(option); }
      $('register-company-row').hidden = false;
    }
  } else { await run(connectService); }
  renderHome();
}
let mfaFactor = null;
$('mfa-form').onsubmit = event => {
  event.preventDefault();
  const code = $('mfa-code').value.replace(/\D/g, '');
  if (!/^(\d{6}|\d{8})$/.test(code)) { status(t('Enter the code from your authenticator app.')); return; }
  $('mfa-button').disabled = true;
  run(async () => {
    const challenge = await api(`/auth/v1/factors/${mfaFactor}/challenge`, { body: {}, failure: t('The code was not accepted. Please try again.') });
    const fresh = await api(`/auth/v1/factors/${mfaFactor}/verify`, { body: { challenge_id: challenge.id, code }, failure: t('The code was not accepted. Please try again.') });
    if (!fresh.access_token || !fresh.refresh_token) throw new Problem(t('The code was not accepted. Please try again.'));
    if (!fresh.expires_at) fresh.expires_at = now() + (fresh.expires_in || 3600);
    const save = () => localStorage.setItem(CONFIG.storageKey, JSON.stringify(Object.assign({}, storedSession() || {}, fresh)));
    if (navigator.locks) await navigator.locks.request(`lock:${CONFIG.storageKey}`, save); else save();
    $('mfa').hidden = true; $('mfa-code').value = ''; status('');
    await start();
  }).finally(() => { $('mfa-button').disabled = false; });
};
$('register-button').onclick = () => run(async () => {
  $('register-button').disabled = true;
  try { await register($('register-company').value, $('register-name').value.trim() || browserName().name); await connectService(); renderHome(); }
  finally { $('register-button').disabled = false; }
});
$('connect-form').onsubmit = event => {
  event.preventDefault();
  const id = +$('remote-id').value.replace(/\D/g, ''), code = $('remote-code').value.replace(/\s/g, '').toUpperCase();
  if (!(id >= 100000000 && id <= 999999999)) { status(t('Enter the 9-digit Remote ID.')); return; }
  if (code && !/^[0-9A-F]{12}$/.test(code)) { status(t('The one-time password has 12 characters.')); return; }
  $('connect-button').disabled = true; unlockSound();
  run(() => request(id, code, $('remote-unattended').checked)).finally(() => { $('connect-button').disabled = false; });
};
$('status').onclick = () => { $('status').textContent = ''; }; // tap the notice to put it away
$('meet-button').onclick = () => run(startMeeting);
$('meet-open').onclick = event => { if (CONFIG.openExternal) { event.preventDefault(); run(() => CONFIG.openExternal($('meet-open').href)); } };
// The phone app has its own sign-in, so it also needs its own sign-out.
$('signout').onclick = () => run(async () => {
  const session = storedSession();
  if (active) endSession('');
  if (session) await http(`${CONFIG.identity}/auth/v1/logout?scope=local`, { method: 'POST', headers: { apikey: CONFIG.apiKey, authorization: `Bearer ${session.access_token}` } }).catch(() => null);
  localStorage.removeItem(CONFIG.storageKey);
  if (socket) { wanted = false; socket.close(); }
  location.reload();
});
$('meet-copy').onclick = () => run(async () => {
  const link = $('meet-invite').value;
  try { await navigator.clipboard.writeText(link); } catch { $('meet-invite').select(); document.execCommand('copy'); }
  status(t('Invitation copied.'));
});
$('search').oninput = () => { clearTimeout(start.search); start.search = setTimeout(renderHome, 350); };
$('tool-end').onclick = () => endSession(t('The session was ended.'));
$('tool-sas').onclick = () => active && record(active, ascii('mixel-action-v1:sas'));
$('tool-sound').onclick = () => { if (!active) return; soundOn = !soundOn; unlockSound(); record(active, ascii(`mixel-action-v1:sound:${soundOn ? 'on' : 'off'}`)); renderToolbar(active); };
$('tool-right').onclick = () => { rightClickNext = !rightClickNext; $('tool-right').setAttribute('aria-pressed', String(rightClickNext)); };
$('tool-full').onclick = () => { const target = $('session'); (document.fullscreenElement ? document.exitFullscreen() : target.requestFullscreen ? target.requestFullscreen() : Promise.resolve()).catch(() => {}); };
$('tool-zoom').onclick = () => setZoom(zoom >= 3 ? 1 : zoom + 1);
$('tool-keyboard').onclick = () => { resetTyping(); $('typing').focus(); };
$('tool-keys').onclick = () => { $('keys').hidden = !$('keys').hidden; };
for (const button of document.querySelectorAll('#keys button')) button.onclick = () => tapKey(...button.dataset.keys.split(',').map(Number));
// Typing field (on-screen keyboards): send exactly what changed since the last event, so
// autocorrect, word suggestions and dictation arrive correctly on the remote computer.
let typed = '';
function resetTyping() { $('typing').value = ''; typed = ''; }
$('typing').addEventListener('beforeinput', event => {
  if (event.inputType === 'insertLineBreak' || event.inputType === 'insertParagraph') { event.preventDefault(); tapKey(0x28); resetTyping(); }
  else if (event.inputType === 'deleteContentBackward' && event.target.value === '') { event.preventDefault(); tapKey(0x2a); }
});
$('typing').addEventListener('input', event => {
  const before = Array.from(typed), now = Array.from(event.target.value);
  let same = 0;
  while (same < before.length && same < now.length && before[same] === now[same]) same++;
  for (let index = same; index < before.length; index++) tapKey(0x2a);
  const added = now.slice(same).join('');
  if (added) sendInput(control => control.text(added));
  typed = event.target.value;
  if (!event.isComposing && typed.length > 200) resetTyping();
});
$('typing').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); tapKey(0x28); resetTyping(); } });
$('typing').addEventListener('blur', resetTyping);
const video = screen();
video.addEventListener('pointerdown', pointerDown); video.addEventListener('pointermove', pointerMove);
video.addEventListener('pointerup', pointerUp); video.addEventListener('pointercancel', pointerUp);
video.addEventListener('contextmenu', event => event.preventDefault());
video.addEventListener('wheel', event => { event.preventDefault(); const unit = event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? 400 : 1; sendInput(control => control.wheel(clampWheel(-event.deltaX * unit), clampWheel(-event.deltaY * unit))); }, { passive: false });
window.addEventListener('keydown', event => keyEvent(event, true));
window.addEventListener('keyup', event => keyEvent(event, false));
window.addEventListener('blur', () => active && sendInputRelease(active));
window.addEventListener('pagehide', () => { if (active) endSession(''); });
// After signing in to ISM in another tab or window, coming back here continues without a reload.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && !who && !$('signin').hidden && storedSession()) { $('signin').hidden = true; start().catch(() => { $('signin').hidden = false; }); }
});
start().catch(problem => { console.error(problem); status(problem instanceof Problem ? problem.message : t('That did not work. Please try again.')); });
