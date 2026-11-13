// remote.mixel.ch: serves the static portal (Workers static assets) and one helper endpoint.
// /api/windows-installer returns the current Windows installer of the stable update channel, so
// the portal can show a ready-to-run deploy command without the browser calling the storage API.

interface Env {
  ASSETS: Fetcher;
}

const MANIFEST_URL =
  'https://store-api.mixel.ch/storage/v1/object/public/remote-updates/stable/windows-x64/latest.json';
const FILE_BASE = 'https://store-api.mixel.ch/storage/v1/object/public/remote-updates/';

interface Manifest {
  product: string;
  platform: string;
  version: string;
  file: string;
  size: number;
}

function json(body: unknown, status = 200, maxAge = 300): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': `public, max-age=${maxAge}`,
      'x-content-type-options': 'nosniff',
    },
  });
}

export async function windowsInstaller(fetcher: typeof fetch = fetch): Promise<Response> {
  const response = await fetcher(MANIFEST_URL, { cf: { cacheTtl: 300, cacheEverything: true } } as RequestInit);
  if (!response.ok) return json({ error: 'unavailable' }, 502, 30);
  const text = await response.text();
  if (text.length > 8192) return json({ error: 'invalid' }, 502, 30);
  try {
    const envelope = JSON.parse(text) as { manifest?: string };
    const manifest = JSON.parse(atob(envelope.manifest ?? '')) as Manifest;
    // The app verifies the signature and the installer's code signature itself; here we only
    // accept a plain relative file path of the expected product and platform.
    if (manifest.product !== 'mixel-remote' || manifest.platform !== 'windows'
        || !/^files\/[A-Za-z0-9._/-]+\.exe$/.test(manifest.file) || manifest.file.includes('..')) {
      return json({ error: 'invalid' }, 502, 30);
    }
    return json({ version: manifest.version, url: FILE_BASE + manifest.file, size: manifest.size });
  } catch {
    return json({ error: 'invalid' }, 502, 30);
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === '/api/windows-installer') {
      if (request.method !== 'GET') return json({ error: 'method' }, 405, 0);
      return windowsInstaller();
    }
    // The billing service (Stripe Checkout, portal, webhook) stays on the origin: nginx on
    // mixel-vps proxies /api/billing/ to it. A subrequest to the route's own host goes to the origin.
    if (url.pathname.startsWith('/api/billing/')) return fetch(request);
    if (url.pathname.startsWith('/api/')) return json({ error: 'not_found' }, 404, 0);
    return env.ASSETS.fetch(request);
  },
} satisfies ExportedHandler<Env>;
