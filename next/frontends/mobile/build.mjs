// Puts the phone app's pages together: the shared viewer plus the bridge to the app.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const viewer = path.join(here, '..', 'web', 'viewer');
const dist = path.join(here, 'dist');
fs.rmSync(dist, { recursive: true, force: true });
fs.mkdirSync(path.join(dist, 'pkg'), { recursive: true });
for (const file of ['viewer.js', 'viewer.css', 'texts.js', 'icon-192.png', 'pkg/web_client.js', 'pkg/web_client_bg.wasm']) fs.copyFileSync(path.join(viewer, file), path.join(dist, file));

// One source for the public key: the viewer's own configuration.
const key = /apiKey: '([^']+)'/.exec(fs.readFileSync(path.join(viewer, 'viewer.js'), 'utf8'))[1];
const bridge = fs.readFileSync(path.join(here, 'bridge.js'), 'utf8').replace(/const API_KEY = '[^']*';/, `const API_KEY = '${key}';`);
if (!bridge.includes(key)) throw new Error('The public key could not be placed in bridge.js');
fs.writeFileSync(path.join(dist, 'bridge.js'), bridge);

let page = fs.readFileSync(path.join(viewer, 'index.html'), 'utf8');
const before = page;
page = page.replace(/<link rel="manifest"[^>]*>\n/, '').replace(/<link rel="apple-touch-icon"[^>]*>\n/, '')
  .replace(/<meta name="(mobile-web-app-capable|apple-mobile-web-app-capable|apple-mobile-web-app-title)"[^>]*>\n/g, '')
  .replace('<p data-t>Remote control in your browser</p>', '<p data-t>Control your computers from here</p>')
  .replace('<script type="module" src="viewer.js"></script>', '<script src="bridge.js"></script>\n<script type="module" src="viewer.js"></script>');
if (page === before || !page.includes('bridge.js')) throw new Error('index.html has changed; update build.mjs');
fs.writeFileSync(path.join(dist, 'index.html'), page);
console.log(`mobile pages: ${fs.readdirSync(dist).length} entries in ${dist}`);
