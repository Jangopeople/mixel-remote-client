// Runs the real service against a stand-in sign-in server and walks through a whole meeting.
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import WebSocket from 'ws';

const here = path.dirname(fileURLToPath(import.meta.url));
const PORT = 18097, ISM_PORT = 18098, BASE = `http://127.0.0.1:${PORT}/meet/`;
let service, ism;

test.before(async () => {
  ism = http.createServer((request, response) => {
    const good = request.headers.authorization === 'Bearer good-token' && request.headers.apikey === 'test-key';
    response.writeHead(good ? 200 : 401, { 'content-type': 'application/json' });
    response.end(JSON.stringify(good ? { id: 'user-1' } : {}));
  });
  await new Promise(resolve => ism.listen(ISM_PORT, '127.0.0.1', resolve));
  service = spawn(process.execPath, [path.join(here, '..', 'server.mjs')], {
    env: { ...process.env, MEET_PORT: String(PORT), MEET_PUBLIC_URL: BASE, MEET_ISM_URL: `http://127.0.0.1:${ISM_PORT}`, MEET_ISM_KEY: 'test-key', MEET_TURN_SECRET: 'secret', MEET_TURN_URLS: 'turn:relay.test:3479?transport=udp', MEET_CREATOR_ORIGINS: 'https://ism.test' },
    stdio: ['ignore', 'pipe', 'inherit'],
  });
  await new Promise(resolve => service.stdout.once('data', resolve));
});
test.after(() => { service.kill(); ism.close(); });

function person(origin = `http://127.0.0.1:${PORT}`) {
  const socket = new WebSocket(`ws://127.0.0.1:${PORT}/meet/ws`, { origin });
  const inbox = [], waiters = [];
  socket.on('message', raw => { const message = JSON.parse(raw.toString()); const index = waiters.findIndex(waiter => waiter.type === message.type); if (index >= 0) waiters.splice(index, 1)[0].resolve(message); else inbox.push(message); });
  return {
    socket,
    open: () => new Promise((resolve, reject) => { socket.once('open', resolve); socket.once('error', reject); }),
    say: message => socket.send(JSON.stringify(message)),
    next: type => { const index = inbox.findIndex(message => message.type === type); if (index >= 0) return Promise.resolve(inbox.splice(index, 1)[0]); return new Promise((resolve, reject) => { const timer = setTimeout(() => reject(new Error(`no ${type}`)), 3000); waiters.push({ type, resolve: message => { clearTimeout(timer); resolve(message); } }); }); },
    closed: () => new Promise(resolve => socket.readyState === 3 ? resolve() : socket.once('close', resolve)),
  };
}
const create = (token, origin = 'https://ism.test') => fetch(`${BASE}api/rooms`, { method: 'POST', headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json', origin }, body: JSON.stringify({ title: 'Weekly\u0007 call' }) });

test('pages are served with strict headers and nothing outside the folder', async () => {
  const page = await fetch(BASE);
  assert.equal(page.status, 200);
  assert.match(page.headers.get('content-security-policy'), /default-src 'self'/);
  assert.equal((await fetch(`${BASE}..%2fserver.mjs`)).status, 404);
  assert.equal((await fetch(`${BASE}../package.json`)).status, 404);
  assert.match(await (await fetch(`${BASE}healthz`)).text(), /^ok \d+$/);
});

test('only a signed-in account can create a meeting', async () => {
  assert.equal((await create('bad-token')).status, 401);
  const preflight = await fetch(`${BASE}api/rooms`, { method: 'OPTIONS', headers: { origin: 'https://ism.test' } });
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get('access-control-allow-origin'), 'https://ism.test');
  assert.equal((await fetch(`${BASE}api/rooms`, { method: 'OPTIONS', headers: { origin: 'https://other.test' } })).status, 403);
  const made = await create('good-token');
  assert.equal(made.status, 200);
  const room = await made.json();
  assert.match(room.room, /^[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}$/);
  assert.equal(room.invite, `${BASE}#${room.room}`);
  assert.ok(room.host.startsWith(`${room.invite}.`));
});

test('the signaling connection is refused from another website', async () => {
  const stranger = person('https://other.test');
  await assert.rejects(stranger.open());
});

test('a whole meeting: lobby, admit, signaling, chat, lock, remove, end', async () => {
  const room = await (await create('good-token')).json();
  const hostKey = room.host.split('.').pop();

  const guest = person(); await guest.open();
  guest.say({ type: 'join', room: room.room, name: ' Ben\u0000 ' });
  assert.equal((await guest.next('waiting')).host, false);

  const host = person(); await host.open();
  host.say({ type: 'join', room: room.room, name: 'Anna', host_key: hostKey });
  const welcome = await host.next('welcome');
  assert.equal(welcome.role, 'host');
  assert.equal(welcome.title, 'Weekly call');
  assert.equal(welcome.waiting.length, 1);
  assert.equal(welcome.waiting[0].name, 'Ben');
  assert.match(welcome.ice[0].username, /^\d+:meet-/);

  // A guest cannot let people in, lock or end the meeting; only the host can.
  host.say({ type: 'admit', id: welcome.waiting[0].id });
  const guestWelcome = await guest.next('welcome');
  assert.equal(guestWelcome.role, 'guest');
  assert.equal(guestWelcome.peers[0].name, 'Anna');
  assert.equal((await host.next('peer-joined')).name, 'Ben');

  guest.say({ type: 'signal', to: welcome.id, data: { description: { type: 'offer', sdp: 'x' } } });
  const signal = await host.next('signal');
  assert.equal(signal.from, guestWelcome.id);
  assert.equal(signal.data.description.sdp, 'x');

  guest.say({ type: 'state', mic: true, cam: false, screen: true });
  assert.deepEqual((await host.next('state')).state, { mic: true, cam: false, screen: true, camStream: '', screenStream: '' });

  guest.say({ type: 'chat', text: '  hello  ' });
  assert.equal((await host.next('chat')).text, 'hello');

  guest.say({ type: 'lock', on: true }); guest.say({ type: 'end' });
  host.say({ type: 'lock', on: true });
  assert.equal((await guest.next('locked')).on, true);

  const late = person(); await late.open();
  late.say({ type: 'join', room: room.room, name: 'Chris' });
  assert.equal((await late.next('denied')).reason, 'locked');
  await late.closed();

  const wrong = person(); await wrong.open();
  wrong.say({ type: 'join', room: room.room, name: 'Eve', host_key: 'not-the-key' });
  assert.equal((await wrong.next('denied')).reason, 'locked');

  host.say({ type: 'lock', on: false });
  await guest.next('locked');
  const third = person(); await third.open();
  third.say({ type: 'join', room: room.room, name: 'Dora' });
  await third.next('waiting');
  const knock = await host.next('knock');
  host.say({ type: 'deny', id: knock.id });
  assert.equal((await third.next('denied')).reason, 'declined');

  host.say({ type: 'remove', id: guestWelcome.id });
  assert.equal((await guest.next('ended')).reason, 'removed');
  await guest.closed();
  assert.equal((await host.next('peer-left')).id, guestWelcome.id);

  host.say({ type: 'end' });
  assert.equal((await host.next('ended')).reason, 'ended');
  const after = person(); await after.open();
  after.say({ type: 'join', room: room.room, name: 'Late' });
  assert.equal((await after.next('denied')).reason, 'unknown');
});
