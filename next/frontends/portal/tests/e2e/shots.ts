import type { Page } from '@playwright/test';

const SHOTS = process.env.QA_SHOTS ?? 'qa-output/screenshots';

/** Full-page screenshots in light and dark mode. */
export async function shot(page: Page, name: string) {
  await page.waitForLoadState('networkidle').catch(() => undefined);
  for (const theme of ['light', 'dark'] as const) {
    await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme);
    await page.waitForTimeout(150);
    await page.screenshot({ path: `${SHOTS}/${name}-${theme}.png`, fullPage: true });
  }
  await page.evaluate(() => document.documentElement.removeAttribute('data-theme'));
}
