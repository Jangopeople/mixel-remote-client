// Live check of the legal features (SQL 027) against the real backend with a throwaway QA company:
// the legal drafts (noindex), sign-up with the Terms accepted, a member deleting their own account,
// the owner's data export, and deleting the company (devices, members, accounts, Stripe customer).
//   QA_RUN=l$(date +%H%M%S) QA_ADMIN_ENV=/path/qa-admin.env QA_SQL="ssh mixel-vps docker exec -i ism-postgres psql -U postgres -d ism -At" \
//   QA_STRIPE="ssh mixel-vps docker exec -i mixel-remote-billing node -" QA_SHOTS=/path/shots PORTAL_URL=https://remote.mixel.ch \
//   npx playwright test tests/e2e/legal.spec.ts
// Nothing is paid: the Stripe step only opens a Checkout session (which creates the Stripe customer).
import { expect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdirSync, readFileSync } from 'node:fs';
import { createUser, enrolComputer, passwordSession, rest, signupCode } from './qa';
import { shot } from './shots';

const RUN = (process.env.QA_RUN ?? `l${Date.now().toString(36)}`).toLowerCase();
const SHOTS = process.env.QA_SHOTS ?? 'qa-output/screenshots';
const PASSWORD = `Qa-${RUN}-Legal-2026!`;
const COMPANY = `QA Legal ${RUN}`;
const OWNER = `delivered+qa-legal-${RUN}-owner@resend.dev`;
const TECH = `qa-legal-${RUN}-tech@example.com`;
const MEMBER = `qa-legal-${RUN}-member@example.com`;
const state: { tenant?: string; owner?: string; customer?: string; devices?: string[]; profiles?: string[]; accounts?: string[] } = {};

mkdirSync(SHOTS, { recursive: true });
test.describe.configure({ mode: 'serial' });

function sql(query: string): string {
  if (!/^[\w\s'(),.=*<>:;+@%|-]+$/.test(query.replace(/\n/g, ' '))) throw new Error('unexpected characters in QA SQL');
  return execFileSync('sh', ['-c', process.env.QA_SQL ?? ''], { input: query, encoding: 'utf8' }).trim();
}
const uuidList = (ids: string[]) => ids.map((id) => `'${id.replace(/[^0-9a-f-]/g, '')}'`).join(',') || `'00000000-0000-0000-0000-000000000000'`;

/** Stands in for the invitation e-mail (QA addresses are never mailed): stores the hash of a fresh secret. */
function invitationSecret(email: string): string {
  if (!email.startsWith(`qa-legal-${RUN}-`) || !email.endsWith('@example.com')) throw new Error('not a QA invitation');
  const secret = `mri_${randomBytes(32).toString('base64url')}`;
  const out = sql(`update remote_saas.invites set token_hash = sha256(convert_to('${secret}', 'UTF8')), mail_state = 'sent', mail_sent_at = now(),
    mail_claim = null, mail_claimed_at = null where email = '${email}' and accepted_at is null and revoked_at is null returning id;`);
  if (!/[0-9a-f-]{36}/.test(out)) throw new Error(`no open invitation for ${email}`);
  return secret;
}

async function signIn(page: Page, email: string) {
  await page.goto('/signin');
  await page.getByLabel('Email address', { exact: true }).fill(email);
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page).toHaveURL(/\/app/);
}

test('legal drafts: four documents in four languages, marked as drafts and not indexed', async ({ page, request }) => {
  for (const doc of ['terms', 'privacy', 'dpa', 'subprocessors']) {
    const response = await request.get(`/legal/${doc}`);
    expect(response.status()).toBe(200);
    expect(response.headers()['x-robots-tag']).toBe('noindex, nofollow');
  }
  await page.goto('/legal/terms?lang=en');
  await expect(page.getByTestId('legal-draft')).toContainText('Draft — not in force');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Terms of Service for Mixel Remote');
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex, nofollow');
  await shot(page, 'legal-01-terms-en');
  await page.goto('/legal/privacy?lang=de');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Datenschutzerklärung für Mixel Remote');
  await expect(page.getByTestId('legal-draft')).toContainText('Entwurf');
  await page.goto('/legal/dpa?lang=fr');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Accord de sous-traitance');
  await page.goto('/legal/subprocessors?lang=it');
  await expect(page.getByTestId('subprocessor-table')).toContainText('Hetzner Online GmbH');
  await expect(page.getByTestId('subprocessor-table')).toContainText('Resend');
  await shot(page, 'legal-02-subprocessors-it');
  await page.goto('/?lang=en');
  await expect(page.locator('.site-footer')).toContainText('Terms of Service');
  await expect(page.locator('.site-footer')).toContainText('Sub-processors');
});

test('sign-up needs the Terms accepted; version and time are stored', async ({ page }) => {
  await page.goto('/signup?lang=en');
  await page.getByLabel('Company name').fill(COMPANY);
  await page.getByLabel('Your name').fill('QA Legal Owner');
  await page.getByLabel('Work email').fill(OWNER);
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Create account' }).click();
  await expect(page.getByText('Accept the Terms of Service and the Data Processing Agreement to continue.')).toBeVisible();
  await shot(page, 'legal-03-signup-terms-required');
  await page.getByTestId('terms-check').locator('input').check();
  await page.getByRole('button', { name: 'Create account' }).click();
  await expect(page.getByLabel('6-digit code')).toBeVisible();
  await page.getByLabel('6-digit code').fill(await signupCode(OWNER, PASSWORD));
  await page.getByRole('button', { name: 'Verify' }).click();
  await expect(page).toHaveURL(/\/app\?welcome=1/);
  await expect(page.getByTestId('company-name')).toHaveText(COMPANY);
  await expect(page.locator('.app-footer')).toContainText('Data Processing Agreement');
  await page.goto('/app/settings');
  await expect(page.getByTestId('terms-accepted')).toContainText(OWNER);
  await expect(page.getByTestId('terms-accepted')).toContainText('2026-11-13-draft');
  await shot(page, 'legal-04-settings-terms');
  await page.context().storageState({ path: `${SHOTS}/../legal-owner-state.json` });
  state.tenant = sql(`select id from remote_saas.tenants where name = '${COMPANY}';`);
  expect(state.tenant).toMatch(/^[0-9a-f-]{36}$/);
  expect(sql(`select string_agg(document || ':' || version || ':' || source, ',' order by document) from remote_saas.terms_acceptances
    where tenant_id = '${state.tenant}' and accepted_at > now() - interval '10 minutes';`)).toBe('dpa:2026-11-13-draft:portal,terms:2026-11-13-draft:portal');
});

test('the company gets members, two computers and a Stripe customer', async ({ request }) => {
  const owner = await passwordSession(OWNER, PASSWORD);
  const tenant = state.tenant!;
  const link = await rest('/rpc/remote_saas_create_deploy_link', owner.access_token,
    { tenant, link_label: 'QA legal', valid_days: 1, computers: 2, in_group: null });
  await enrolComputer(link, `QA Legal PC 1 ${RUN}`);
  await enrolComputer(link, `QA Legal PC 2 ${RUN}`);
  for (const [email, role] of [[TECH, 'technician'], [MEMBER, 'member']] as const) {
    await rest('/rpc/remote_saas_invite_member', owner.access_token, { tenant, invitee_email: email, member_role: role, locale: 'en' });
    await createUser(email, PASSWORD, `QA Legal ${role}`);
    const person = await passwordSession(email, PASSWORD);
    expect(await rest('/rpc/remote_saas_accept_invite', person.access_token, { invite_token: invitationSecret(email) })).toBe(tenant);
  }
  // A pending invitation of the owner: revoked when the company is deleted.
  await rest('/rpc/remote_saas_invite_member', owner.access_token, { tenant, invitee_email: `qa-legal-${RUN}-later@example.com`, member_role: 'member', locale: 'en' });
  // Stripe Checkout creates the customer (nothing is paid; the session expires).
  const checkout = await request.post('/api/billing/checkout', {
    headers: { authorization: `Bearer ${owner.access_token}` },
    data: { tenant_id: tenant, plan: 'team', interval: 'month', return_path: '/app/billing' },
  });
  expect(checkout.status(), await checkout.text()).toBe(200);
  expect((await checkout.json()).url).toMatch(/^https:\/\/checkout\.stripe\.com\//);
  state.customer = sql(`select stripe_customer_id from remote_saas.billing where tenant_id = '${tenant}';`);
  expect(state.customer).toMatch(/^cus_[A-Za-z0-9]+$/);
  state.devices = sql(`select d.id from public.devices d join remote_saas.tenants t on t.client_id = d.client_id where t.id = '${tenant}';`).split('\n').filter(Boolean);
  expect(state.devices).toHaveLength(2);
});

test('a member deletes their own account in the portal', async ({ browser }) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await signIn(page, MEMBER);
  await page.goto('/app/account');
  await page.getByTestId('delete-account').click();
  await expect(page.getByTestId('confirm-delete')).toBeDisabled();
  await page.getByTestId('type-to-confirm').fill(MEMBER.toUpperCase());
  await shot(page, 'legal-05-delete-account');
  await page.getByTestId('confirm-delete').click();
  await expect(page).toHaveURL(/\/deleted\?kind=account/);
  await expect(page.getByTestId('deleted-page')).toContainText('Account deleted');
  await context.close();
  expect(sql(`select count(*) from public.users where email = '${MEMBER}';`)).toBe('0');
  expect(sql(`select count(*) from auth.users where email = '${MEMBER}';`)).toBe('0');
  expect(sql(`select count(*) from remote_saas.members where tenant_id = '${state.tenant}';`)).toBe('2');
});

test('the owner exports the company data (JSON and CSV)', async ({ browser }) => {
  const context = await browser.newContext({ storageState: `${SHOTS}/../legal-owner-state.json`, acceptDownloads: true });
  const page = await context.newPage();
  await page.goto('/app/settings');
  await expect(page.getByTestId('data-panel')).toBeVisible();
  const [json] = await Promise.all([page.waitForEvent('download'), page.getByTestId('export-json').click()]);
  const data = JSON.parse(readFileSync(await json.path(), 'utf8'));
  expect(json.suggestedFilename()).toMatch(/^mixel-remote-qa-legal-[a-z0-9-]+-export-\d{4}-\d{2}-\d{2}\.json$/);
  expect(data.company.name).toBe(COMPANY);
  expect(data.members.map((m: { email: string }) => m.email).sort()).toEqual([OWNER, TECH].sort());
  expect(data.devices).toHaveLength(2);
  expect(data.terms_acceptances).toHaveLength(2);
  expect(data.audit_log.some((entry: { action: string }) => entry.action === 'member.account_deleted')).toBe(true);
  const [csv] = await Promise.all([page.waitForEvent('download'), page.getByTestId('export-devices').click()]);
  const devices = readFileSync(await csv.path(), 'utf8');
  expect(devices.startsWith('﻿name,os,group,remote_id')).toBe(true);
  expect(devices).toContain(`QA Legal PC 1 ${RUN}`);
  await expect(page.getByTestId('export-done')).toBeVisible();
  await shot(page, 'legal-06-export');
  await context.close();
});

test('the owner deletes the company: devices, members, accounts, invitations and the Stripe customer are gone', async ({ browser }) => {
  state.profiles = sql(`select m.user_id from remote_saas.members m where m.tenant_id = '${state.tenant}';`).split('\n').filter(Boolean);
  state.accounts = sql(`select u.auth_user_id from public.users u where u.id in (${uuidList(state.profiles)});`).split('\n').filter(Boolean);
  const org = sql(`select organization_id || ' ' || client_id from remote_saas.tenants where id = '${state.tenant}';`).split(' ');
  const context = await browser.newContext({ storageState: `${SHOTS}/../legal-owner-state.json` });
  const page = await context.newPage();
  await page.goto('/app/settings');
  await page.getByTestId('delete-company').click();
  await page.getByTestId('type-to-confirm').fill(COMPANY.slice(0, -1));
  await expect(page.getByTestId('confirm-delete')).toBeDisabled();
  await page.getByTestId('type-to-confirm').fill(COMPANY);
  await shot(page, 'legal-07-delete-company');
  await page.getByTestId('confirm-delete').click();
  await expect(page).toHaveURL(/\/deleted\?kind=company/);
  await expect(page.getByTestId('deleted-page')).toContainText('2 devices removed, 2 members');
  await shot(page, 'legal-08-deleted');
  await context.close();

  const tenant = state.tenant!;
  expect(sql(`select count(*) from remote_saas.tenants where id = '${tenant}';`)).toBe('0');
  expect(sql(`select count(*) from public.organizations where id = '${org[0]}';`)).toBe('0');
  expect(sql(`select count(*) from public.clients where id = '${org[1]}';`)).toBe('0');
  expect(sql(`select count(*) from public.devices where id in (${uuidList(state.devices!)});`)).toBe('0');
  expect(sql(`select count(*) from public.remote_device_enrollments where device_id in (${uuidList(state.devices!)});`)).toBe('0');
  expect(sql(`select count(*) from public.users where id in (${uuidList(state.profiles)});`)).toBe('0');
  expect(sql(`select count(*) from auth.users where id in (${uuidList(state.accounts)});`)).toBe('0');
  expect(sql(`select count(*) from remote_saas.invites where email like 'qa-legal-${RUN}-%';`)).toBe('0');
  expect(sql(`select count(*) from remote_saas.audit_log where tenant_id = '${tenant}';`)).toBe('0');
  expect(sql(`select devices_removed || ' ' || members_removed || ' ' || accounts_deleted || ' ' || invites_revoked || ' ' || coalesce(stripe_customer_id, '')
    from remote_saas.closures where tenant_id = '${tenant}';`)).toBe(`2 2 2 1 ${state.customer}`);
  // The billing service cancels and deletes the Stripe customer from the queue within seconds.
  await expect.poll(() => sql(`select stripe_state from remote_saas.closures where tenant_id = '${tenant}';`), { timeout: 90_000, intervals: [2000] }).toBe('done');
  const stripe = execFileSync('sh', ['-c', process.env.QA_STRIPE ?? ''], {
    encoding: 'utf8',
    input: `fetch('https://api.stripe.com/v1/customers/${state.customer}', { headers: { authorization: 'Bearer ' + process.env.STRIPE_SECRET_KEY } })
      .then((r) => r.json()).then((c) => console.log(JSON.stringify({ deleted: c.deleted === true, id: c.id })));`,
  });
  expect(JSON.parse(stripe)).toEqual({ deleted: true, id: state.customer });
});
