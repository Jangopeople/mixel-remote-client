import { defineConfig, loadEnv, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

// In development, serve the worker's /api/windows-installer from the same code (worker/index.ts).
function workerApi(): Plugin {
  return {
    name: 'mixel-worker-api',
    configureServer(server) {
      server.middlewares.use('/api/windows-installer', async (_request, response) => {
        const worker = (await server.ssrLoadModule('/worker/index.ts')) as { windowsInstaller: () => Promise<Response> };
        const answer = await worker.windowsInstaller();
        response.statusCode = answer.status;
        response.setHeader('content-type', 'application/json; charset=utf-8');
        response.end(await answer.text());
      });
    },
  };
}

// A production build without the ISM key renders a blank page (2026-11-13 outage): refuse to build.
function requireIsmKey(mode: string): void {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  if (mode === 'production' && !env.VITE_ISM_ANON_KEY) {
    throw new Error('VITE_ISM_ANON_KEY is missing: create .env.local first (see README.md "Deploy").');
  }
}

export default defineConfig(({ mode }) => {
  if (process.env.VITEST === undefined) requireIsmKey(mode);
  return {
  plugins: [react(), workerApi()],
  build: { outDir: 'dist', sourcemap: false, target: 'es2022' },
  // Billing API on the live origin in development (same path as in production).
  server: { port: 5174, strictPort: true, proxy: { '/api/billing': { target: 'https://remote.mixel.ch', changeOrigin: true } } },
  test: {
    include: ['tests/unit/**/*.test.ts'],
    environment: 'jsdom',
  },
  } as never;
});
