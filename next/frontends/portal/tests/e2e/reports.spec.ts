// Live check of favorites, recent connections and session reports (SQL 029) against the real backend
// with a throwaway QA company that the spec creates and deletes again:
//   QA_RUN=p$(date +%H%M%S) QA_ADMIN_ENV=/path/qa-admin.env QA_SQL="ssh mixel-vps docker exec -i ism-postgres psql -U postgres -d ism -At" \
//   QA_SHOTS=/path/shots PORTAL_URL=https://remote.mixel.ch npx playwright test tests/e2e/reports.spec.ts
// Session rows are written by the connection broker; the spec seeds two of them with tests/e2e/reports-seed.sql
// (or the file in QA_REPORTS_SEED) through QA_SQL. Everything the run creates is removed in afterAll:
// the company (remote_saas_delete_company: devices, members, accounts), its closure row and reserved IDs.
import { expect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync } from 'node:fs';
import { createUser, deleteUser, enrolComputer, findUsers, passwordSession, rest } from './qa';
import { shot } from './shots';
import { LEGAL_VERSION } from '../../src/legal/types';

const RUN = (process.env.QA_RUN ?? `p${Date.now().toString(36)}`).toLowerCase().replace(/[^a-z0-9]/g, '');
const SHOTS = process.env.QA_SHOTS ?? 'qa-output/screenshots';
const SEED = process.env.QA_REPORTS_SEED ?? 'tests/e2e/reports-seed.sql';
const PASSWORD = `Qa-${RUN}-Reports-2026!`;
const COMPANY = `QA Reports ${RUN}`;
const OWNER = `qa-reports-${RUN}-owner@example.com`;
const PC1 = `QA Reports PC 1 ${RUN}`;
const PC2 = `QA Reports PC 2 ${RUN}`;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const state: { tenant?: string; profile?: string; devices?: { device_id: string; name: string; connection_id: number }[] } = {};

mkdirSync(SHOTS, { recursive: true });
test.describe.configure({ mode: 'serial' });

/** Runs SQL on the ISM database (QA_SQL); extra psql variables only with validated values. */
function sql(query: string, vars: Record<string, string> = {}): string {
  if (!process.env.QA_SQL) throw new Error('QA_SQL is not set');
  for (const [name, value] of Object.entries(vars)) {
    if (!/^[a-z_]+$/.test(name) || !(UUID.test(value) || /^\d{9}$/.test(value) || /^[a-z0-9]+$/.test(value))) throw new Error(`unexpected QA SQL variable ${name}`);
  }
  const command = [process.env.QA_SQL, ...Object.entries(vars).map(([name, value]) => `-v ${name}=${value}`)].join(' ');
  return execFileSync('sh', ['-c', command], { input: query, encoding: 'utf8' }).trim();
}

async function owner() {
  return (await passwordSession(OWNER, PASSWORD)).access_token;
}

async function signIn(page: Page) {
  await page.goto('/signin?lang=en');
  await page.getByLabel('Email address', { exact: true }).fill(OWNER);
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page).toHaveURL(/\/app/);
}

test.afterAll(async () => {
  // Reserved IDs of the QA devices, then the company with its devices, members and accounts.
  if (state.tenant) {
    sql(`delete from remote_private.connection_reservations where device_id in
      (select d.id from public.devices d join remote_saas.tenants t on t.client_id = d.client_id where t.id = :'tenant');`, { tenant: state.tenant });
    try {
      await rest('/rpc/remote_saas_delete_company', await owner(), { tenant: state.tenant, confirm_name: COMPANY });
    } catch (error) {
      console.warn(`QA company ${COMPANY} not deleted through the RPC: ${String(error).slice(0, 200)}; run tests/e2e/cleanup.sql style clean-up by hand`);
    }
    sql(`delete from remote_saas.closures where tenant_id = :'tenant';`, { tenant: state.tenant });
  }
  for (const user of await findUsers(`qa-reports-${RUN}-`)) await deleteUser(user.id);
});

test('a QA company with two computers and two seeded sessions', async () => {
  await createUser(OWNER, PASSWORD, 'QA Reports Owner');
  const token = await owner();
  const signup = await rest('/rpc/remote_saas_signup', token, { company_name: COMPANY, accept_terms: true, terms_version: LEGAL_VERSION, source: 'portal' });
  state.tenant = (Array.isArray(signup) ? signup[0] : signup).tenant_id;
  expect(state.tenant).toMatch(UUID);
  const link = await rest('/rpc/remote_saas_create_deploy_link', token, { tenant: state.tenant, link_label: 'QA reports', valid_days: 1, computers: 2, in_group: null });
  await enrolComputer(link, PC1);
  await enrolComputer(link, PC2);
  const devices = (await rest('/rpc/remote_saas_devices', token, { tenant: state.tenant, in_group: null, search: null })) as { device_id: string; name: string; connection_id: number }[];
  state.devices = [PC1, PC2].map((name) => devices.find((device) => device.name === name)!);
  expect(state.devices.every((device) => device && UUID.test(device.device_id) && /^\d{9}$/.test(String(device.connection_id)))).toBe(true);
  state.profile = sql(`select id from public.users where email = '${OWNER}';`);
  expect(state.profile).toMatch(UUID);
  const [a, b] = state.devices;
  sql(readFileSync(SEED, 'utf8'), {
    tenant: state.tenant!, technician: state.profile, device_a: a.device_id, conn_a: String(a.connection_id), device_b: b.device_id, conn_b: String(b.connection_id), run: RUN,
  });
  const overview = await rest('/rpc/remote_saas_tenant_overview', token, { tenant: state.tenant });
  expect(overview.features.reports, 'the Business trial includes reports').toBe(true);
  const rows = await rest('/rpc/remote_saas_session_report', token, { tenant: state.tenant });
  expect(rows.map((row: { device_name: string }) => row.device_name), 'seeded sessions, newest first').toEqual([PC1, PC2]);
  const recent = await rest('/rpc/remote_saas_recent_devices', token, { tenant: state.tenant, max_count: 10 });
  expect(recent.map((row: { device_id: string }) => row.device_id)).toEqual([a.device_id, b.device_id]);
});

test('Devices: a starred device moves to Favorites, the recently connected ones follow', async ({ page }) => {
  await signIn(page);
  await page.goto('/app/devices');
  await expect(page.getByTestId('devices-table')).toBeVisible();
  // Before starring: both computers are recent connections (PC 1 first, newest).
  await expect(page.getByTestId('devices-section-recent').locator('tr[data-device]')).toHaveCount(2);
  await expect(page.getByTestId('devices-section-recent').locator('tr[data-device]').first()).toHaveAttribute('data-device', PC1);
  const star = page.locator(`tr[data-device="${PC2}"]`).getByTestId('favorite-toggle');
  await expect(star).toHaveAttribute('aria-pressed', 'false');
  await expect(star).toHaveAccessibleName(`Add ${PC2} to favorites`);
  await star.click();
  await expect(page.getByTestId('devices-section-favorites').locator('tr[data-device]')).toHaveCount(1);
  await expect(page.locator('tbody[data-testid^="devices-section-"] tr[data-device]').first()).toHaveAttribute('data-device', PC2);
  await expect(page.locator(`tr[data-device="${PC2}"]`).getByTestId('favorite-toggle')).toHaveAttribute('aria-pressed', 'true');
  // Stored on the server: still on top after a reload.
  await page.reload();
  await expect(page.locator('tbody[data-testid^="devices-section-"] tr[data-device]').first()).toHaveAttribute('data-device', PC2);
  await expect(page.getByTestId('devices-section-recent').locator('tr[data-device]')).toHaveCount(1);
  await shot(page, 'reports-01-devices-favorites');
  const favorites = await rest('/rpc/remote_saas_favorites', await owner(), { tenant: state.tenant });
  expect(favorites.map((row: { device_id: string }) => row.device_id)).toEqual([state.devices![1].device_id]);
});

test('Reports: filter, edit a note, export CSV and PDF', async ({ browser }) => {
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  await signIn(page);
  await page.locator('.sidebar .app-nav').getByRole('link', { name: 'Reports' }).click();
  await expect(page).toHaveURL(/\/app\/reports$/);
  await expect(page.getByTestId('report-row')).toHaveCount(2);
  await expect(page.getByTestId('report-totals')).toHaveText('2 sessions · total 1:35:00');
  await shot(page, 'reports-02-all');
  // Last day only: the session two days ago drops out.
  const today = new Date();
  const day = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  await page.getByLabel('From').fill(day(new Date(today.getTime() - 86400000)));
  await expect(page.getByTestId('report-row')).toHaveCount(1);
  await page.getByLabel('From').fill(day(new Date(today.getTime() - 30 * 86400000)));
  await expect(page.getByTestId('report-row')).toHaveCount(2);
  await page.getByLabel('Device').selectOption({ label: PC2 });
  await expect(page.getByTestId('report-row')).toHaveCount(1);
  await expect(page.getByTestId('report-row')).toContainText('Unattended');
  await page.getByLabel('Device').selectOption({ label: 'All devices' });
  await page.getByLabel('Technician').selectOption(state.profile!);
  await expect(page.getByTestId('report-row')).toHaveCount(2);

  // A note on the PC 1 session, with characters the PDF must encode.
  const NOTE = `Drucker neu installiert – Grüsse aus Zürich ${RUN}`;
  const row = page.getByTestId('report-row').filter({ hasText: PC1 });
  await row.getByTestId('note-edit').click();
  await row.getByTestId('note-input').fill(NOTE);
  await shot(page, 'reports-03-note-editing');
  await row.getByTestId('note-save').click();
  await expect(row.getByTestId('note-text')).toHaveText(NOTE);
  const stored = await rest('/rpc/remote_saas_session_report', await owner(), { tenant: state.tenant, device: state.devices![0].device_id });
  expect(stored[0].note).toBe(NOTE);

  const [csv] = await Promise.all([page.waitForEvent('download'), page.getByTestId('report-export-csv').click()]);
  expect(csv.suggestedFilename()).toMatch(/^mixel-remote-qa-reports-[a-z0-9-]+-sessions-\d{4}-\d{2}-\d{2}-\d{4}-\d{2}-\d{2}\.csv$/);
  const text = readFileSync(await csv.path(), 'utf8');
  expect(text.startsWith('﻿Technician,Email,Device,Remote ID,Start,End,Duration,Duration (seconds),Type,Joined as colleague,Note,Session ID\r\n')).toBe(true);
  expect(text).toContain(`,${OWNER},${PC1},${state.devices![0].connection_id},`);
  expect(text).toContain(',0:25:00,1500,Attended,No,');
  expect(text).toContain(NOTE);
  expect(text).toContain(',1:10:00,4200,Unattended,No,');

  const [pdf] = await Promise.all([page.waitForEvent('download'), page.getByTestId('report-export-pdf').click()]);
  expect(pdf.suggestedFilename()).toMatch(/\.pdf$/);
  const file = readFileSync(await pdf.path(), 'latin1');
  expect(file.startsWith('%PDF-1.4\n')).toBe(true);
  expect(file.trimEnd().endsWith('%%EOF')).toBe(true);
  expect(file).toContain('/Count 1');
  expect(file).toContain(`(${COMPANY})`);
  expect(file).toContain(`(${PC1})`);
  // The note may wrap in its column: check its encoded words.
  for (const part of ['Drucker neu installiert', '\\226', 'Gr\\374sse', 'Z\\374rich']) expect(file).toContain(part);
  expect(file).toContain('(2 sessions \\267 total 1:35:00)');
  await shot(page, 'reports-04-after-export');
  await context.close();
});
