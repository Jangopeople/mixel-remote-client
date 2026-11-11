import { createRequire } from 'node:module';
const require = createRequire('/Volumes/MAC_Mini_Ext/Project_Mixel/mixel-ism/node_modules/.pnpm/ws@8.21.0/node_modules/ws/');
const { WebSocket } = require('ws');
for (const origin of ['https://ism.mixel.ch', 'https://ism.mixel.mu', 'https://evil.example']) {
  await new Promise(resolve => {
    const socket = new WebSocket('wss://rs.mixel.ch:9444/v1/ws', { origin });
    socket.on('open', () => { console.log(origin, '-> connection accepted'); socket.close(); resolve(); });
    socket.on('unexpected-response', (_request, response) => { console.log(origin, '-> refused with', response.statusCode); resolve(); });
    socket.on('error', () => resolve());
  });
}
