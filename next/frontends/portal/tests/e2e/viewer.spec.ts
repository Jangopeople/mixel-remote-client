// The browser viewer (ism.mixel.ch/remote-next/) with Mixel Remote company accounts, using the QA tenant
// of the portal run (qa-run.json). VIEWER_URL: the viewer page, e.g. https://ism.mixel.ch/remote-next/ or
// a local test server. Skipped unless VIEWER_URL is set.
import { expect, test, type Page } from '@playwright/test';
import { shot } from './shots';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { createUser, passwordSession, rest, secondStep, type GoTrueSession } from './qa';

const VIEWER = process.env.VIEWER_URL;
const SHOTS = process.env.QA_SHOTS ?? 'qa-output/screenshots';
test.skip(!VIEWER, 'VIEWER_URL not set');
test.describe.configure({ mode: 'serial' });

const run = () => JSON.parse(readFileSync(`${SHOTS}/../qa-run.json`, 'utf8')) as { run: string; owner: string; tech: string; secret: string };
const password = (id: string) => `Qa-${id}-Portal-2026!`;
const used = new Set<string>();
const SECRETS = `${SHOTS}/../qa-totp.json`;

/** aal2 session for a QA account; the authenticator secret is enrolled once and kept in qa-totp.json. */
async function aal2(email: string, pw: string): Promise<GoTrueSession> {
  const secrets: Record<string, string> = existsSync(SECRETS) ? JSON.parse(readFileSync(SECRETS, 'utf8')) : {};
  if (email === run().owner && !secrets[email]) secrets[email] = run().secret;
  const result = await secondStep(await passwordSession(email, pw), used, secrets[email] || undefined);
  secrets[email] = result.secret;
  writeFileSync(SECRETS, JSON.stringify(secrets, null, 2));
  return result.session;
}

async function openViewer(page: Page, session: GoTrueSession) {
  const stored = { ...session, expires_at: Math.floor(Date.now() / 1000) + session.expires_in };
  await page.goto(new URL('/favicon.ico', VIEWER).toString()).catch(() => undefined);
  await page.evaluate((value) => localStorage.setItem('sb-ism-auth-token', value), JSON.stringify(stored));
  await page.goto(VIEWER!);
}

test('technician: registers the browser, reaches the connection service, lists the devices', async ({ page }) => {
  const qa = run();
  await openViewer(page, await aal2(qa.tech, password(qa.run)));
  await expect(page.locator('#account')).toContainText(`QA Portal ${qa.run}`);
  await expect(page.locator('#register')).toBeVisible();
  await page.locator('#register-button').click();
  await expect(page.locator('#connect')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('#own-id')).toHaveText(/^\d{3} \d{3} \d{3}$/);
  await expect(page.locator('#devices')).toContainText('QA Reception', { timeout: 30_000 });
  await expect(page.locator('#member-note')).toBeHidden();
  await expect(page.locator('#status')).toHaveText('');
  await page.screenshot({ path: `${SHOTS}/40-viewer-technician.png`, fullPage: true });

  // Presence end to end: the browser is now an enrolled device connected to the broker. The broker
  // reports it within 30 s and the owner's portal shows it Online; after the viewer closes it turns
  // Offline with "last seen".
  if (process.env.PORTAL_URL) {
    const ownId = (await page.locator('#own-id').textContent())!.replace(/\s/g, '');
    const portal = await page.context().browser()!.newContext({ storageState: `${SHOTS}/../owner-state.json` });
    const owner = await portal.newPage();
    const row = owner.locator('[data-testid="devices-table"] tbody tr', { hasText: ownId.replace(/(\d{3})(\d{3})(\d{3})/, '$1 $2 $3') });
    await expect(async () => {
      await owner.goto(`${process.env.PORTAL_URL}/app/devices`);
      await expect(row.getByTestId('device-status')).toContainText('Online', { timeout: 3000 });
    }).toPass({ timeout: 90_000, intervals: [10_000] });
    await shot(owner, '44-portal-device-online');
    await page.close();
    await expect(async () => {
      await owner.goto(`${process.env.PORTAL_URL}/app/devices`);
      await expect(row.getByTestId('device-status')).toContainText('Last seen', { timeout: 3000 });
    }).toPass({ timeout: 120_000, intervals: [10_000] });
    await expect(row.getByTestId('device-status')).toContainText('Turned off, asleep or without internet');
    await shot(owner, '45-portal-device-offline');
    await portal.close();
  }
});

test('member: sees their devices without Connect', async ({ page }) => {
  const qa = run();
  const owner = await aal2(qa.owner, password(qa.run));
  const tenants = await rest('/rpc/remote_saas_my_tenants', owner.access_token, {});
  const email = `qa-portal-${qa.run}-viewer-member@example.com`;
  const secret = await rest('/rpc/remote_saas_invite_member', owner.access_token, { tenant: tenants[0].tenant_id, invitee_email: email, member_role: 'member' });
  await createUser(email, password(qa.run), 'QA Viewer Member');
  const member = await passwordSession(email, password(qa.run));
  await rest('/rpc/remote_saas_accept_invite', member.access_token, { invite_token: secret });
  await openViewer(page, await aal2(email, password(qa.run)));
  await expect(page.locator('#account')).toContainText(`QA Portal ${qa.run}`);
  await expect(page.locator('#member-note')).toBeVisible();
  await expect(page.locator('#register')).toBeHidden();
  await expect(page.locator('#connect-form')).toBeHidden();
  await expect(page.locator('#no-devices')).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/41-viewer-member.png`, fullPage: true });
});

test('company 2-step rule: without the second step the viewer asks for the code', async ({ page }) => {
  const qa = run();
  const signedIn = await passwordSession(qa.tech, password(qa.run));
  await openViewer(page, signedIn);
  await expect(page.locator('#mfa')).toBeVisible();
  await expect(page.locator('#mfa-why')).toContainText('Your company requires a second step');
  await page.screenshot({ path: `${SHOTS}/42-viewer-mfa.png`, fullPage: true });
});
