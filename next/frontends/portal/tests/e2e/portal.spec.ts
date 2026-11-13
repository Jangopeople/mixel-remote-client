// Live end-to-end run of the portal against the real backend (ism-api.mixel.ch) with a throwaway QA
// tenant. Users are created with the GoTrue admin API; the owner signs up through the UI. Clean-up:
// tests/e2e/cleanup.sql (database) and the admin API (accounts), see README.md.
import { expect, test, type Page } from '@playwright/test';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { anonKey, createUser, enrolComputer, freshTotp, invitationLink, signupCode } from './qa';
import { shot } from './shots';

const RUN = process.env.QA_RUN ?? Date.now().toString(36);
const SHOTS = process.env.QA_SHOTS ?? 'qa-output/screenshots';
const PASSWORD = `Qa-${RUN}-Portal-2026!`;
const COMPANY = `QA Portal ${RUN}`;
// Resend's test inbox: accepted and simulated, no real email is delivered.
const OWNER = `delivered+qa-portal-${RUN}-owner@resend.dev`;
const TECH = `qa-portal-${RUN}-tech@example.com`;
const MEMBER = `qa-portal-${RUN}-member@example.com`;
const usedCodes = new Set<string>();
let secret = existsSync(`${SHOTS}/../owner-totp.txt`) ? readFileSync(`${SHOTS}/../owner-totp.txt`, 'utf8').trim() : '';
let slug = '';

mkdirSync(SHOTS, { recursive: true });


async function signIn(page: Page, email: string, path = '/signin') {
  await page.goto(path);
  await page.getByLabel('Email address', { exact: true }).fill(email);
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
}

test.describe.configure({ mode: 'serial' });

test('public pages', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Help anyone');
  await expect(page.locator('[data-plan="business"]')).toContainText('89');
  await shot(page, '01-landing');
  await page.goto('/pricing');
  await expect(page.locator('[data-plan="team"]')).toContainText('39');
  await page.getByRole('button', { name: 'Billed monthly' }).click();
  await expect(page.locator('[data-plan="team"]')).toContainText('46.80');
  await expect(page.locator('[data-plan="enterprise"]')).toContainText('On request');
  await shot(page, '02-pricing');
  await page.goto('/download');
  await expect(page.getByRole('link', { name: /Open the Mixel Store/ })).toHaveAttribute('href', 'https://store.mixel.ch/app/mixel-remote');
  await shot(page, '03-download');
  await page.goto('/signin');
  await shot(page, '04-signin');
  await page.goto('/signup');
  await shot(page, '05-signup');
  // Languages: German, French and Italian texts load and the choice is kept.
  await page.goto('/');
  for (const [label, text] of [['Deutsch', 'Jedem helfen'], ['Français', 'Aider chacun'], ['Italiano', 'Aiutare chiunque'], ['English', 'Help anyone']]) {
    await page.getByTestId('preferences').click();
    await page.getByRole('menuitemradio', { name: label }).click();
    await expect(page.getByRole('heading', { level: 1 })).toContainText(text);
    if (label === 'Deutsch') await shot(page, '06-landing-de');
  }
  // Mobile layout.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/pricing');
  await shot(page, '07-pricing-mobile');
  await page.setViewportSize({ width: 1360, height: 900 });
});

test('sign up creates a company with a 14-day Business trial', async ({ page }) => {
  await page.goto('/signup');
  await page.getByLabel('Company name').fill(COMPANY);
  await page.getByLabel('Your name').fill('QA Owner');
  await page.getByLabel('Work email').fill(OWNER);
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Create account' }).click();
  await expect(page.getByLabel('6-digit code')).toBeVisible();
  await shot(page, '08-signup-code');
  await page.getByLabel('6-digit code').fill(await signupCode(OWNER, PASSWORD));
  await page.getByRole('button', { name: 'Verify' }).click();
  await expect(page).toHaveURL(/\/app\?welcome=1/);
  await expect(page.getByTestId('company-name')).toHaveText(COMPANY);
  await expect(page.getByTestId('plan-name')).toHaveText('Business');
  await expect(page.getByTestId('plan-status')).toHaveText('Trial');
  await expect(page.getByTestId('trial-days')).toContainText(/1[34] days left/);
  await expect(page.getByTestId('usage-technicians')).toContainText('1 / 15');
  await expect(page.getByTestId('usage-devices')).toContainText(/0 \/ 2.?000/);
  await shot(page, '09-dashboard');
  await page.context().storageState({ path: `${SHOTS}/../owner-state.json` });
});

test('owner manages groups, deploy links and devices', async ({ browser }) => {
  const context = await browser.newContext({ storageState: `${SHOTS}/../owner-state.json` });
  const page = await context.newPage();
  await page.goto('/app/groups');
  await page.getByRole('button', { name: 'New group' }).click();
  await page.getByRole('dialog').getByLabel('Group name').fill('QA Office');
  await page.getByRole('dialog').getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('[data-group="QA Office"]')).toBeVisible();

  await page.goto('/app/deploy');
  await page.getByLabel('Label', { exact: true }).fill('QA rollout');
  await page.getByLabel('Number of computers').fill('3');
  await page.getByLabel('Place new devices in').selectOption({ label: 'QA Office' });
  await page.getByRole('button', { name: 'Create' }).click();
  const token = (await page.getByTestId('deploy-token').textContent())!.trim();
  expect(token).toMatch(/^mrt_/);
  // The command of the browser's own system is preselected; start with Windows.
  await page.getByTestId('deploy-os').getByRole('button', { name: 'Windows', exact: true }).click();
  await expect(page.getByTestId('deploy-command')).toHaveValue(/--signin-enroll/);
  await expect(page.getByTestId('deploy-command')).toHaveValue(/store-api\.mixel\.ch.*\.exe/);
  await shot(page, '10-deploy-created');
  // The same link on Macs and Linux computers: one line that pipes the install script to root.
  for (const [os, script] of [['macOS', 'macos'], ['Linux', 'linux']]) {
    await page.getByTestId('deploy-os').getByRole('button', { name: os, exact: true }).click();
    await expect(page.getByTestId('deploy-command')).toHaveValue(`curl -fsSL https://remote.mixel.ch/install/${script}.sh | sudo sh -s -- '${token}'`);
    await shot(page, `10-deploy-created-${script}`);
  }
  await page.getByTestId('deploy-os').getByRole('button', { name: 'Windows', exact: true }).click();
  await page.getByRole('button', { name: 'Close' }).click();
  // Two computers run the command.
  await enrolComputer(token, 'QA-PC-1');
  await enrolComputer(token, 'QA-PC-2');
  await page.reload();
  await expect(page.locator('[data-link="QA rollout"]')).toContainText('2 / 3');
  await shot(page, '11-deploy');

  await page.goto('/app/devices');
  await expect(page.getByTestId('devices-table').locator('tbody tr')).toHaveCount(2);
  await expect(page.locator('[data-device="QA-PC-1"]')).toContainText('QA Office');
  // Status from the connection service: these computers enrolled but never connected.
  await expect(page.locator('[data-device="QA-PC-1"] [data-testid="device-status"]')).toContainText(/Not seen yet|Status unknown/);
  await shot(page, '12-devices');
  // Rename.
  await page.locator('[data-device="QA-PC-1"]').getByRole('button', { name: 'Rename' }).click();
  await page.getByRole('dialog').getByLabel('Name', { exact: true }).fill('QA Reception');
  await page.getByRole('dialog').getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('[data-device="QA Reception"]')).toBeVisible();
  // Move out of the group.
  await page.locator('[data-device="QA-PC-2"]').getByRole('button', { name: 'Move to group' }).click();
  await page.getByRole('dialog').getByLabel('Group', { exact: true }).selectOption({ label: 'No group' });
  await page.getByRole('dialog').getByRole('button', { name: 'Move to group' }).click();
  await expect(page.locator('[data-device="QA-PC-2"]')).toContainText('No group');
  // Add-devices help.
  await page.getByRole('button', { name: 'Add devices' }).first().click();
  await expect(page.getByRole('dialog')).toContainText('Add this computer');
  await shot(page, '13-devices-add');
  await page.getByRole('dialog').getByRole('button', { name: 'Close' }).click();
  await expect(page.getByTestId('open-viewer')).toHaveAttribute('href', 'https://ism.mixel.ch/login?next=%2Fremote-next%2F');
  await context.close();
});

test('owner invites a technician and a member; they accept', async ({ browser }) => {
  const owner = await browser.newContext({ storageState: `${SHOTS}/../owner-state.json` });
  const page = await owner.newPage();
  const links: Record<string, string> = {};
  await page.goto('/app/team');
  // The member's invitation is created through the RPC directly, as the desktop app does; it
  // returns the invitation id only (the secret exists only in the e-mail).
  const idFromRpc = await page.evaluate(async ({ email, anon }) => {
    const session = JSON.parse(localStorage.getItem('mixel-remote-portal-auth') ?? '{}');
    const headers = { apikey: anon, authorization: `Bearer ${session.access_token}`, 'content-type': 'application/json' };
    const tenants = await (await fetch('https://ism-api.mixel.ch/rest/v1/rpc/remote_saas_my_tenants', { method: 'POST', headers, body: '{}' })).json();
    const response = await fetch('https://ism-api.mixel.ch/rest/v1/rpc/remote_saas_invite_member', {
      method: 'POST', headers, body: JSON.stringify({ tenant: tenants[0].tenant_id, invitee_email: email, member_role: 'member', locale: 'de' }),
    });
    return response.json();
  }, { email: MEMBER, anon: anonKey() });
  expect(String(idFromRpc)).toMatch(/^[0-9a-f-]{36}$/);
  for (const [email, role] of [[TECH, 'Technician'], [`qa-portal-${RUN}-revoke@example.com`, 'Admin']]) {
    await page.getByLabel('Email address', { exact: true }).fill(email);
    await page.getByLabel('Role', { exact: true }).selectOption({ label: role });
    await page.getByRole('button', { name: 'Send invitation' }).click();
    await expect(page.getByTestId('invite-created')).toContainText(`Invitation sent to ${email}`);
    await expect(page.getByTestId('invite-link')).toHaveCount(0);
  }
  // QA addresses are never mailed: the service marks them "Not delivered" (a real address shows "Sent").
  await expect(page.locator(`[data-invite="${TECH}"]`).getByTestId('invite-mail-state')).toHaveText(/Not delivered|Sent/, { timeout: 30_000 });
  for (const email of [TECH, MEMBER]) links[email] = invitationLink(email, RUN);
  await shot(page, '14-team-invites');
  // Revoke one invitation.
  await page.locator(`[data-invite="qa-portal-${RUN}-revoke@example.com"]`).getByRole('button', { name: 'Revoke' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Revoke' }).click();
  await expect(page.locator(`[data-invite="qa-portal-${RUN}-revoke@example.com"]`)).toHaveCount(0);

  await createUser(TECH, PASSWORD, 'QA Technician');
  await createUser(MEMBER, PASSWORD, 'QA Member');
  for (const [email, file] of [[TECH, 'tech'], [MEMBER, 'member']]) {
    const context = await browser.newContext();
    const invitee = await context.newPage();
    await invitee.goto(new URL(links[email]).pathname + new URL(links[email]).hash);
    await expect(invitee.getByRole('heading', { name: 'Join your team on Mixel Remote' })).toBeVisible();
    if (file === 'tech') await shot(invitee, '15-invite');
    await invitee.getByLabel('Email address', { exact: true }).fill(email);
    await invitee.getByRole('button', { name: 'Continue' }).click();
    await invitee.getByLabel('Password', { exact: true }).fill(PASSWORD);
    await invitee.getByRole('button', { name: 'Sign in', exact: true }).click();
    await invitee.getByTestId('accept-invite').click();
    await expect(invitee).toHaveURL(/\/app\?joined=1/);
    await expect(invitee.getByTestId('company-name')).toHaveText(COMPANY);
    await context.storageState({ path: `${SHOTS}/../${file}-state.json` });
    await context.close();
  }
  await page.reload();
  await expect(page.locator(`[data-member="${TECH}"]`)).toContainText('Technician');
  await expect(page.locator(`[data-member="${MEMBER}"]`)).toContainText('Member');
  await shot(page, '16-team');
  // Technician access to the group.
  await page.goto('/app/groups');
  await page.locator('[data-group="QA Office"]').getByRole('button', { name: 'Access' }).click();
  await page.getByRole('dialog').getByLabel(/QA Technician/).check();
  await page.getByRole('dialog').getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('[data-group="QA Office"]')).toContainText('QA Technician');
  await shot(page, '17-groups');
  await owner.close();
});

test('technician and member see what the database allows', async ({ browser }) => {
  const tech = await browser.newContext({ storageState: `${SHOTS}/../tech-state.json` });
  const page = await tech.newPage();
  await page.goto('/app/devices');
  // Technician: QA Reception (group granted) and QA-PC-2 (no group).
  await expect(page.getByTestId('devices-table').locator('tbody tr')).toHaveCount(2);
  await expect(page.getByRole('link', { name: 'Deploy' })).toHaveCount(0);
  await shot(page, '18-devices-technician');
  await page.goto('/app/team');
  await expect(page.getByText('Only owners and admins can invite people')).toBeVisible();
  await tech.close();

  const member = await browser.newContext({ storageState: `${SHOTS}/../member-state.json` });
  const memberPage = await member.newPage();
  await memberPage.goto('/app/devices');
  await expect(memberPage.getByText('No devices yet')).toBeVisible();
  await expect(memberPage.getByRole('link', { name: 'Team' })).toHaveCount(0);
  await shot(memberPage, '19-devices-member');
  await member.close();
});

test('settings: sign-in methods, domains, audit log; billing', async ({ browser }) => {
  const context = await browser.newContext({ storageState: `${SHOTS}/../owner-state.json` });
  const page = await context.newPage();
  await page.goto('/app/settings');
  await expect(page.getByTestId('settings-company')).toHaveValue(COMPANY);
  // Rename the company (and back): the company chip follows, the audit log records it.
  await page.getByTestId('settings-company').fill(`${COMPANY} Renamed`);
  await page.getByTestId('company-rename').getByRole('button', { name: 'Rename' }).click();
  await expect(page.getByTestId('company-name')).toHaveText(`${COMPANY} Renamed`);
  await expect(page.getByText('Company renamed.')).toBeVisible();
  await page.getByTestId('settings-company').fill(COMPANY);
  await page.getByTestId('company-rename').getByRole('button', { name: 'Rename' }).click();
  await expect(page.getByTestId('company-name')).toHaveText(COMPANY);
  slug = (await page.locator('dd code').first().textContent())!.trim();
  // Google, Microsoft, Apple and SSO are not configured: shown as coming soon, not selectable.
  for (const method of ['google', 'azure', 'apple', 'sso']) {
    await expect(page.locator(`[data-method="${method}"]`)).toContainText('Coming soon');
    await expect(page.locator(`[data-method="${method}"] input`)).toBeDisabled();
  }
  await page.locator('[data-method="mixel_id"] input').uncheck();
  await page.getByTestId('signin-settings').getByRole('button', { name: 'Save' }).click();
  await expect(page.getByTestId('signin-settings')).toContainText('Saved');
  // Domain.
  await page.getByLabel('Add domain').fill(`qa-${RUN}.example.com`);
  await page.getByTestId('domains').getByRole('button', { name: 'Add domain' }).click();
  await expect(page.locator(`[data-domain="qa-${RUN}.example.com"]`)).toContainText('Pending verification');
  await expect(page.locator(`[data-domain="qa-${RUN}.example.com"]`)).toContainText('mixel-remote-verify=');
  await page.reload();
  await expect(page.getByTestId('audit-log')).toContainText('settings.sign_in');
  await expect(page.getByTestId('audit-log')).toContainText('member.join');
  await expect(page.getByTestId('audit-log')).toContainText('tenant.rename');
  await shot(page, '20-settings');

  // The company sign-in page shows only the allowed methods.
  const visitor = await browser.newContext();
  const signin = await visitor.newPage();
  await signin.goto(`/signin?company=${slug}`);
  await expect(signin.getByText(`Sign in to ${COMPANY}.`).first()).toBeVisible();
  await signin.getByLabel('Email address', { exact: true }).fill('someone@example.com');
  await signin.getByRole('button', { name: 'Continue' }).click();
  await expect(signin.getByTestId('signin-methods')).toBeVisible();
  await expect(signin.getByTestId('mixel-id')).toHaveCount(0);
  await expect(signin.getByTestId('send-code')).toBeVisible();
  await shot(signin, '21-signin-company');
  await visitor.close();

  await page.goto('/app/billing');
  await expect(page.getByTestId('billing-plan')).toHaveText('Business');
  await expect(page.getByTestId('plan-chooser')).toBeVisible();
  await shot(page, '22-billing');
  await context.close();
});

test('2-step login: set up, require for the company, sign in with the code', async ({ browser }) => {
  const context = await browser.newContext({ storageState: `${SHOTS}/../owner-state.json` });
  const page = await context.newPage();
  await page.goto('/app/account');
  await page.getByTestId('mfa-open').click();
  await page.getByTestId('mfa-start').click();
  secret = (await page.getByTestId('mfa-secret').textContent())!.trim();
  writeFileSync(`${SHOTS}/../owner-totp.txt`, secret);
  await shot(page, '23-account-mfa-setup');
  await page.getByLabel('6-digit code').fill(await freshTotp(secret, usedCodes));
  await page.getByRole('button', { name: 'Turn on' }).click();
  await expect(page.getByTestId('account-mfa')).toContainText('2-step login is on');
  await shot(page, '24-account');

  await page.goto('/app/settings');
  await page.getByLabel('Require 2-step login').check();
  await page.getByTestId('signin-settings').getByRole('button', { name: 'Save' }).click();
  await expect(page.getByTestId('signin-settings')).toContainText('Saved');

  // Sign out and back in: the second step is asked.
  await page.getByRole('link', { name: 'Sign out' }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('link', { name: 'Sign in' })).toBeVisible();
  await signIn(page, OWNER);
  await expect(page.getByTestId('mfa-challenge')).toBeVisible();
  await shot(page, '25-signin-mfa');
  await page.getByLabel('6-digit code').fill(await freshTotp(secret, usedCodes));
  await page.getByRole('button', { name: 'Verify' }).click();
  await expect(page).toHaveURL(/\/app$/);
  await expect(page.getByTestId('company-name')).toHaveText(COMPANY);
  await context.storageState({ path: `${SHOTS}/../owner-state.json` });

  // The technician has no authenticator yet: the portal asks them to set one up.
  const tech = await browser.newContext({ storageState: `${SHOTS}/../tech-state.json` });
  const techPage = await tech.newPage();
  await techPage.goto('/app');
  await expect(techPage.getByRole('heading', { name: 'Your company requires 2-step login' })).toBeVisible();
  await shot(techPage, '26-gate-mfa');
  await tech.close();
  await context.close();
});

test('billing: checkout and customer portal open Stripe (nothing is paid)', async ({ browser }) => {
  const context = await browser.newContext({ storageState: `${SHOTS}/../owner-state.json` });
  const page = await context.newPage();
  await page.goto('/app/billing');
  await expect(page.getByTestId('plan-chooser')).toBeVisible();
  await expect(page.getByTestId('billing-subscription')).toHaveText('No paid plan yet.');
  // Solo has one technician seat; this company has two technicians, so Solo is refused.
  await page.locator('[data-choice="solo"] input').check();
  await page.getByTestId('billing-submit').click();
  await expect(page.getByText('This plan is too small for your current team or devices.')).toBeVisible();
  await page.locator('[data-choice="team"] input').check();
  await page.getByTestId('plan-chooser').getByRole('button', { name: 'Monthly', exact: true }).click();
  await expect(page.getByTestId('billing-summary')).toContainText(/Free until .*, then CHF 46\.80 per month\./);
  await page.getByLabel('Extra technicians').fill('1');
  await expect(page.getByTestId('billing-summary')).toContainText('CHF 56.40 per month');
  await page.getByLabel('Extra technicians').fill('0');
  await shot(page, '29-billing-choose');
  await page.getByTestId('billing-submit').click();
  await page.waitForURL(/checkout\.stripe\.com/, { timeout: 30_000 });
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${SHOTS}/30-stripe-checkout.png` });
  // Back without paying: Stripe's cancel URL.
  await page.goto('/app/billing?billing=cancelled');
  await expect(page.getByTestId('billing-cancelled')).toBeVisible();
  await shot(page, '31-billing-cancelled');
  await page.goto('/app/billing?billing=success');
  await expect(page.getByTestId('billing-success')).toBeVisible();
  await shot(page, '32-billing-success');
  // Checkout created the Stripe customer: the customer portal opens.
  await page.goto('/app/billing');
  await page.getByTestId('billing-portal').click();
  await page.waitForURL(/billing\.stripe\.com/, { timeout: 30_000 });
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${SHOTS}/33-stripe-portal.png` });
  await context.close();
});

test('remove a device and a member', async ({ browser }) => {
  const context = await browser.newContext({ storageState: `${SHOTS}/../owner-state.json` });
  const page = await context.newPage();
  await page.goto('/app/devices');
  await page.locator('[data-device="QA-PC-2"]').getByRole('button', { name: 'Remove' }).click();
  await expect(page.getByRole('dialog')).toContainText('Remove “QA-PC-2”?');
  await shot(page, '27-devices-remove');
  await page.getByRole('dialog').getByRole('button', { name: 'Remove' }).click();
  await expect(page.locator('[data-device="QA-PC-2"]')).toHaveCount(0);
  await expect(page.getByTestId('devices-table').locator('tbody tr')).toHaveCount(1);

  await page.goto('/app/team');
  await page.locator(`[data-member="${MEMBER}"]`).getByRole('button', { name: 'Remove' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Remove' }).click();
  await expect(page.locator(`[data-member="${MEMBER}"]`)).toHaveCount(0);
  await page.goto('/app');
  await shot(page, '28-dashboard-after');
  writeFileSync(`${SHOTS}/../qa-run.json`, JSON.stringify({ run: RUN, slug, owner: OWNER, tech: TECH, member: MEMBER, secret }, null, 2));
  await context.close();
});
