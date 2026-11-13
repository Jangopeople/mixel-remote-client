import { describe, expect, it } from 'vitest';
import { statusProxy, windowsInstaller } from '../../worker/index';

const envelope = (manifest: object) => JSON.stringify({ manifest: btoa(JSON.stringify(manifest)), signature: 'x' });
const respond = (body: string, status = 200) => (async () => new Response(body, { status })) as unknown as typeof fetch;

describe('worker /api/windows-installer', () => {
  it('returns the installer of the signed channel file', async () => {
    const response = await windowsInstaller(respond(envelope({ product: 'mixel-remote', platform: 'windows', version: '0.1.20', file: 'files/0.1.20/windows-x64/ab/Mixel-Remote-Windows-x64.exe', size: 10 })));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ version: '0.1.20', size: 10, url: 'https://store-api.mixel.ch/storage/v1/object/public/remote-updates/files/0.1.20/windows-x64/ab/Mixel-Remote-Windows-x64.exe' });
  });
  it('refuses unexpected files and failures', async () => {
    expect((await windowsInstaller(respond(envelope({ product: 'mixel-remote', platform: 'windows', version: '1', file: 'https://evil.example/x.exe', size: 1 })))).status).toBe(502);
    expect((await windowsInstaller(respond(envelope({ product: 'other', platform: 'windows', version: '1', file: 'files/x.exe', size: 1 })))).status).toBe(502);
    expect((await windowsInstaller(respond(envelope({ product: 'mixel-remote', platform: 'windows', version: '1', file: 'files/../x.exe', size: 1 })))).status).toBe(502);
    expect((await windowsInstaller(respond('not json'))).status).toBe(502);
    expect((await windowsInstaller(respond('', 404))).status).toBe(502);
  });
});

describe('worker /api/status', () => {
  const request = new Request('https://remote.mixel.ch/api/status');
  const origin = (body: string, status = 200) => (async () => new Response(body, { status })) as unknown as typeof fetch;
  const good = { updated_at: '2026-11-13T11:00:00+00:00', status: 'degraded', secret: 'x', components: [
    { id: 'relay', name: 'Relay (TURN)', status: 'degraded', since: '2026-11-13T10:50:00+00:00', detail: 'internal', history: [{ date: '2026-11-13', uptime: 99.5, worst: 'degraded', runs: 3 }] },
  ] };
  it('passes only the known fields of the monitor on', async () => {
    const response = await statusProxy(request, origin(JSON.stringify(good)));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: 'degraded', updated_at: good.updated_at, components: [
      { id: 'relay', name: 'Relay (TURN)', status: 'degraded', since: '2026-11-13T10:50:00+00:00', history: [{ date: '2026-11-13', uptime: 99.5, worst: 'degraded' }] },
    ] });
  });
  it('says the monitor is unreachable instead of failing', async () => {
    for (const fetcher of [origin('', 502), origin('not json'), origin(JSON.stringify({ status: 'fine' })),
      (async () => { throw new Error('down'); }) as unknown as typeof fetch]) {
      const body = await (await statusProxy(request, fetcher)).json() as { status: string; reason: string };
      expect(body).toMatchObject({ status: 'unknown', reason: 'monitor_unreachable' });
    }
  });
});
