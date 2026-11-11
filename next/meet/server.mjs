// Mixel Meet: rooms, lobby and signaling for small meetings (screen sharing, voice, video, chat).
// Media flows directly between the participants' browsers (WebRTC, encrypted end to end between
// each pair, through Mixel's relay). This service only introduces them to each other.
import http from 'node:http';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WebSocketServer } from 'ws';

const env = name => process.env[name] || '';
const PORT = Number(env('MEET_PORT') || 8097);
const PUBLIC = path.join(path.dirname(fileURLToPath(import.meta.url)), 'public');
const BASE = env('MEET_PUBLIC_URL') || 'https://rs.mixel.ch/meet/';
const ISM_URL = env('MEET_ISM_URL') || 'https://ism-api.mixel.ch';
const ISM_KEY = env('MEET_ISM_KEY');
const TURN_SECRET = env('MEET_TURN_SECRET');
const TURN_URLS = (env('MEET_TURN_URLS') || '').split(',').filter(Boolean);
const RELAY_ONLY = env('MEET_RELAY_ONLY') !== '0';
const CREATOR_ORIGINS = (env('MEET_CREATOR_ORIGINS') || 'https://ism.mixel.ch,https://ism.mixel.mu').split(',');
const MAX_PEOPLE = 8, MAX_WAITING = 20, MAX_ROOMS = 500, ROOM_IDLE_MS = 12 * 3600 * 1000, MAX_MESSAGE = 65536;

/** @type {Map<string, any>} */
const rooms = new Map();
const created = new Map(); // user id -> timestamps of created rooms (rate limit)
const now = () => Date.now();
const token = bytes => crypto.randomBytes(bytes).toString('hex');
const roomId = () => { const letters = 'abcdefghjkmnpqrstuvwxyz23456789'; return Array.from(crypto.randomBytes(12), byte => letters[byte % letters.length]).join('').replace(/(.{4})(.{4})(.{4})/, '$1-$2-$3'); };
const digest = text => crypto.createHash('sha256').update(text).digest('hex');
const cleanName = value => typeof value === 'string' ? value.replace(/[\u0000-\u001f\u007f]/g, '').trim().slice(0, 40) : '';

function iceServers(room, peer) {
  if (!TURN_SECRET || !TURN_URLS.length) return [];
  const username = `${Math.floor(now() / 1000) + 6 * 3600}:meet-${room}-${peer}`;
  return [{ urls: TURN_URLS, username, credential: crypto.createHmac('sha1', TURN_SECRET).update(username).digest('base64') }];
}
function send(socket, message) { if (socket.readyState === 1) socket.send(JSON.stringify(message)); }
function broadcast(room, message, except) { for (const person of room.people.values()) if (person.id !== except) send(person.socket, message); }
function hosts(room) { return [...room.people.values()].filter(person => person.role === 'host'); }
function closeRoom(id, reason) {
  const room = rooms.get(id); if (!room) return;
  rooms.delete(id);
  for (const person of [...room.people.values(), ...room.waiting.values()]) { send(person.socket, { type: 'ended', reason }); person.socket.close(); }
}
setInterval(() => { for (const [user, times] of created) if (times.every(time => now() - time > 3600 * 1000)) created.delete(user); for (const [id, room] of rooms) if (now() - room.active > ROOM_IDLE_MS || (!room.people.size && !room.waiting.size && now() - room.active > 3600 * 1000)) closeRoom(id, 'expired'); }, 60000).unref();

async function accountOf(bearer) {
  if (!bearer || bearer.length > 16000 || !ISM_KEY) return null;
  try {
    const response = await fetch(`${ISM_URL}/auth/v1/user`, { headers: { apikey: ISM_KEY, authorization: `Bearer ${bearer}` }, signal: AbortSignal.timeout(8000) });
    if (!response.ok) return null;
    const user = await response.json();
    return typeof user.id === 'string' ? user : null;
  } catch { return null; }
}
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.png': 'image/png', '.webmanifest': 'application/manifest+json', '.svg': 'image/svg+xml' };
const SECURITY = {
  'content-security-policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob: mediastream:; connect-src 'self' wss://rs.mixel.ch; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
  'x-content-type-options': 'nosniff', 'referrer-policy': 'no-referrer', 'x-robots-tag': 'noindex, nofollow',
  'permissions-policy': 'camera=(self), microphone=(self), display-capture=(self), geolocation=()', 'cache-control': 'no-cache',
};
const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, 'http://localhost');
  const origin = request.headers.origin || '';
  const cors = CREATOR_ORIGINS.includes(origin) ? { 'access-control-allow-origin': origin, vary: 'Origin' } : {};
  if (url.pathname === '/meet/healthz') { response.writeHead(200, { 'content-type': 'text/plain' }); response.end(`ok ${rooms.size}`); return; }
  if (url.pathname === '/meet/api/rooms') {
    if (request.method === 'OPTIONS') { response.writeHead(cors['access-control-allow-origin'] ? 204 : 403, { ...cors, 'access-control-allow-methods': 'POST', 'access-control-allow-headers': 'authorization, content-type', 'access-control-max-age': '600' }); response.end(); return; }
    if (request.method !== 'POST') { response.writeHead(405, cors); response.end(); return; }
    let body = ''; let tooLarge = false;
    request.on('data', chunk => { body += chunk; if (body.length > 2048) { tooLarge = true; request.destroy(); } });
    request.on('end', async () => {
      if (tooLarge) return;
      const account = await accountOf((request.headers.authorization || '').replace(/^Bearer /, ''));
      if (!account) { response.writeHead(401, cors); response.end(); return; }
      const recent = (created.get(account.id) || []).filter(time => now() - time < 3600 * 1000);
      if (recent.length >= 12 || rooms.size >= MAX_ROOMS) { response.writeHead(429, cors); response.end(); return; }
      created.set(account.id, [...recent, now()]);
      let title = ''; try { title = cleanName(JSON.parse(body || '{}').title); } catch { title = ''; }
      const id = roomId(), hostKey = token(16);
      rooms.set(id, { id, title, hostKey: digest(hostKey), creator: account.id, active: now(), people: new Map(), waiting: new Map(), locked: false });
      response.writeHead(200, { ...cors, 'content-type': 'application/json', 'cache-control': 'no-store' });
      response.end(JSON.stringify({ room: id, invite: `${BASE}#${id}`, host: `${BASE}#${id}.${hostKey}` }));
    });
    return;
  }
  if (request.method !== 'GET' || !url.pathname.startsWith('/meet/')) { response.writeHead(404); response.end(); return; }
  let file = url.pathname.slice('/meet/'.length) || 'index.html';
  const full = path.join(PUBLIC, path.normalize(file));
  if (!full.startsWith(PUBLIC + path.sep) || !TYPES[path.extname(full)] || !fs.existsSync(full)) { response.writeHead(404, SECURITY); response.end('Not found'); return; }
  response.writeHead(200, { ...SECURITY, 'content-type': TYPES[path.extname(full)] });
  fs.createReadStream(full).pipe(response);
});

const sockets = new WebSocketServer({ noServer: true, maxPayload: MAX_MESSAGE });
server.on('upgrade', (request, socket, head) => {
  const url = new URL(request.url, 'http://localhost');
  // Only the meeting page itself may open the signaling connection.
  const allowed = new URL(BASE).origin;
  if (url.pathname !== '/meet/ws' || (request.headers.origin && request.headers.origin !== allowed && !env('MEET_DEV'))) { socket.destroy(); return; }
  sockets.handleUpgrade(request, socket, head, client => sockets.emit('connection', client));
});
sockets.on('connection', socket => {
  let room = null, me = null, count = 0, windowStart = now();
  socket.alive = true;
  socket.on('pong', () => { socket.alive = true; });
  const joinTimer = setTimeout(() => { if (!me) socket.close(); }, 10000);
  function admit(person) {
    room.waiting.delete(person.id);
    if (room.people.size >= MAX_PEOPLE) { send(person.socket, { type: 'denied', reason: 'full' }); person.socket.close(); return; }
    person.admitted = true;
    const peers = [...room.people.values()].map(other => ({ id: other.id, name: other.name, role: other.role, state: other.state }));
    room.people.set(person.id, person);
    send(person.socket, { type: 'welcome', id: person.id, role: person.role, title: room.title, peers, ice: iceServers(room.id, person.id), relayOnly: RELAY_ONLY, locked: room.locked, waiting: person.role === 'host' ? [...room.waiting.values()].map(other => ({ id: other.id, name: other.name })) : [] });
    broadcast(room, { type: 'peer-joined', id: person.id, name: person.name, role: person.role, state: person.state }, person.id);
  }
  socket.on('message', (raw, binary) => {
    if (binary) { socket.close(); return; }
    if (now() - windowStart > 1000) { windowStart = now(); count = 0; }
    if (++count > 60) { socket.close(); return; }
    let message; try { message = JSON.parse(raw.toString()); } catch { socket.close(); return; }
    if (!message || typeof message.type !== 'string') return;
    if (!me) {
      if (message.type !== 'join' || typeof message.room !== 'string') { socket.close(); return; }
      room = rooms.get(message.room);
      const name = cleanName(message.name);
      if (!room || !name) { send(socket, { type: 'denied', reason: room ? 'name' : 'unknown' }); socket.close(); return; }
      clearTimeout(joinTimer);
      const isHost = typeof message.host_key === 'string' && crypto.timingSafeEqual(Buffer.from(digest(message.host_key)), Buffer.from(room.hostKey));
      me = { id: token(6), name, role: isHost ? 'host' : 'guest', socket, admitted: false, state: { mic: false, cam: false, screen: false } };
      room.active = now();
      if (isHost) { admit(me); return; }
      if (room.locked || room.waiting.size >= MAX_WAITING) { send(socket, { type: 'denied', reason: room.locked ? 'locked' : 'full' }); socket.close(); return; }
      room.waiting.set(me.id, me);
      send(socket, { type: 'waiting', title: room.title, host: hosts(room).length > 0 });
      for (const host of hosts(room)) send(host.socket, { type: 'knock', id: me.id, name: me.name });
      return;
    }
    if (!me.admitted || !rooms.has(room.id)) return;
    room.active = now();
    const host = me.role === 'host';
    switch (message.type) {
      case 'signal': { const target = room.people.get(message.to); if (target && message.data && JSON.stringify(message.data).length < 60000) send(target.socket, { type: 'signal', from: me.id, data: message.data }); break; }
      case 'state': { me.state = { mic: !!message.mic, cam: !!message.cam, screen: !!message.screen, camStream: String(message.camStream || '').slice(0, 80), screenStream: String(message.screenStream || '').slice(0, 80) }; broadcast(room, { type: 'state', id: me.id, state: me.state }, me.id); break; }
      case 'chat': { const text = typeof message.text === 'string' ? message.text.replace(/[\u0000-\u0008\u000b-\u001f]/g, '').trim().slice(0, 1000) : ''; if (text) broadcast(room, { type: 'chat', id: me.id, name: me.name, text, at: now() }); break; }
      case 'ice': send(socket, { type: 'ice', ice: iceServers(room.id, me.id) }); break;
      case 'admit': case 'deny': { const person = host && room.waiting.get(message.id); if (!person) break; if (message.type === 'admit') admit(person); else { room.waiting.delete(person.id); send(person.socket, { type: 'denied', reason: 'declined' }); person.socket.close(); } for (const other of hosts(room)) send(other.socket, { type: 'knock-done', id: message.id }); break; }
      case 'remove': { const person = host && room.people.get(message.id); if (person && person.role !== 'host') { send(person.socket, { type: 'ended', reason: 'removed' }); person.socket.close(); } break; }
      case 'lock': if (host) { room.locked = !!message.on; broadcast(room, { type: 'locked', on: room.locked }); } break;
      case 'end': if (host) closeRoom(room.id, 'ended'); break;
      default: break;
    }
  });
  socket.on('close', () => {
    clearTimeout(joinTimer);
    if (!room || !me || !rooms.has(room.id)) return;
    if (room.waiting.delete(me.id)) { for (const host of hosts(room)) send(host.socket, { type: 'knock-done', id: me.id }); return; }
    if (room.people.delete(me.id)) broadcast(room, { type: 'peer-left', id: me.id });
    room.active = now();
  });
});
setInterval(() => { for (const socket of sockets.clients) { if (!socket.alive) { socket.terminate(); continue; } socket.alive = false; socket.ping(); } }, 25000).unref();
server.listen(PORT, env('MEET_BIND') || '127.0.0.1', () => console.log(`Mixel Meet listening on ${env('MEET_BIND') || '127.0.0.1'}:${PORT}`));
