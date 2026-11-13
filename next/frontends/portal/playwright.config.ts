import { defineConfig } from '@playwright/test';

// Live end-to-end run against the real ISM backend with a throwaway QA tenant (see tests/e2e).
export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 10 * 60 * 1000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: process.env.QA_OUTPUT ? `${process.env.QA_OUTPUT}/test-results` : 'test-results',
  use: {
    baseURL: process.env.PORTAL_URL ?? 'http://localhost:5174',
    viewport: { width: 1360, height: 900 },
    locale: 'en-US',
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
