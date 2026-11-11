// Local test server for the browser viewer: serves the viewer on http://localhost:5173/remote-next/ and
// passes the WebSocket and the ticket request on to the live services without an Origin header.
import http from 'node:http'; import https from 'node:https'; import fs from 'node:fs'; import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire('/Volumes/MAC_Mini_Ext/Project_Mixel/mixel-ism/node_modules/.pnpm/ws@8.21.0/node_modules/ws/');
const { WebSocketServer, WebSocket } = require('ws');
const root = '/Volumes/MAC_Mini_Ext/Project_Mixel/mixel-remote-client/.worktrees/managed-app/next/frontends/web/viewer';
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.wasm': 'application/wasm', '.png': 'image/png', '.webmanifest': 'application/manifest+json' };
const inject = `<script>window.MIXEL_REMOTE_CONFIG={signaling:'ws://localhost:5173/ws',ticketUrl:'http://localhost:5173/v1/device-ticket',meetings:'http://localhost:8097/meet/api/rooms'}</script>`;
const server = http.createServer((request, response) => {
  if (request.method === 'POST' && request.url === '/v1/device-ticket') {
    const chunks = []; request.on('data', chunk => chunks.push(chunk)); request.on('end', () => {
      const upstream = https.request('https://ism-api.mixel.ch/v1/device-ticket', { method: 'POST', headers: { authorization: request.headers.authorization || '', 'content-type': 'application/json' } }, answer => {
        const body = []; answer.on('data', chunk => body.push(chunk)); answer.on('end', () => { console.log('ticket', answer.statusCode); response.writeHead(answer.statusCode, { 'content-type': 'application/json' }); response.end(Buffer.concat(body)); });
      });
      upstream.on('error', () => { response.writeHead(502); response.end(); }); upstream.end(Buffer.concat(chunks));
    });
    return;
  }
  if (request.url === '/dev-session-aal1.json') { response.writeHead(200, { 'content-type': 'application/json' }); response.end(fs.readFileSync(new URL('./dev-session-aal1.json', import.meta.url))); return; }
  if (request.url === '/dev-totp') { import('node:child_process').then(({ execFileSync }) => { response.writeHead(200, { 'content-type': 'text/plain' }); response.end(execFileSync('ssh', ['mixel-vps', 'sudo python3 /tmp/lab-aal1.py totp']).toString().trim()); }); return; }
  if (request.url === '/dev-login') { response.writeHead(200, { 'content-type': 'text/html' }); response.end(`<script>localStorage.setItem('sb-ism-auth-token', ${JSON.stringify(fs.readFileSync(new URL('./dev-session.json', import.meta.url)).toString())}); location.replace('/remote-next/');</script>`); return; }
  if (request.url === '/dev-session.json') { response.writeHead(200, { 'content-type': 'application/json' }); response.end(fs.readFileSync(new URL('./dev-session.json', import.meta.url))); return; }
  let file = decodeURIComponent(request.url.split('?')[0]).replace(/^\/remote-next/, '') || '/';
  if (file.endsWith('/')) file += 'index.html';
  const full = path.join(root, path.normalize(file));
  if (!full.startsWith(root) || !fs.existsSync(full)) { response.writeHead(404); response.end('not found'); return; }
  let body = fs.readFileSync(full);
  if (full.endsWith('index.html')) body = Buffer.from(body.toString().replace('<script type="module"', inject + '<script type="module"'));
  response.writeHead(200, { 'content-type': types[path.extname(full)] || 'application/octet-stream', 'cache-control': 'no-store' }); response.end(body);
});
const wss = new WebSocketServer({ server, path: '/ws' });
wss.on('connection', client => {
  const upstream = new WebSocket('wss://rs.mixel.ch:9444/v1/ws'); const queue = [];
  client.on('message', (data, binary) => { const text = binary ? data : data.toString(); console.log('>', String(text).slice(-140)); upstream.readyState === 1 ? upstream.send(text) : queue.push(text); });
  upstream.on('open', () => queue.splice(0).forEach(item => upstream.send(item)));
  upstream.on('message', (data, binary) => { const text = binary ? data : data.toString(); console.log('<', String(text).slice(-140)); client.send(text); });
  upstream.on('close', (code) => { console.log('upstream closed', code); client.close(); }); client.on('close', () => upstream.close());
  upstream.on('error', error => { console.log('upstream error', error.message); client.close(); });
});
server.listen(5173, '127.0.0.1', () => console.log('viewer test server on http://localhost:5173/remote-next/'));
