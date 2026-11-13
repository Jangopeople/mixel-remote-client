import { describe, expect, it } from 'vitest';
import {
  assignableRoles, availableMethods, canRemoveMember, checkPassword, deployLinkState, errorKey, formatConnectionId, gateFor,
  inviteLink, isCompanyName, isDomain, isEmail, isOtpCode, isWritable, methodsToSave, normalizeDomain, safeNext, signInMethods,
  relativeTime, sinceTime, tokenFromHash, trialDaysLeft, usage, windowsDeployCommand,
  browserOs, isDeployToken, linuxDeployCommand, macDeployCommand,
} from '../../src/lib/logic';

describe('sign-in methods', () => {
  const settings = { external: { email: true, keycloak: true, google: false, azure: false, apple: false }, saml_enabled: false };
  it('offers only configured providers', () => {
    expect([...availableMethods(settings)].sort()).toEqual(['magic_link', 'mixel_id', 'password']);
    expect([...availableMethods(null)].sort()).toEqual(['magic_link', 'password']);
    expect(availableMethods({ external: { email: true, google: true }, saml_enabled: true }).has('sso')).toBe(true);
  });
  it('intersects company rules with availability', () => {
    const available = availableMethods(settings);
    expect(signInMethods(null, available)).toEqual(['password', 'magic_link', 'mixel_id']);
    expect(signInMethods(['password', 'google'], available)).toEqual(['password']);
    expect(signInMethods(['google'], available)).toEqual([]);
  });
  it('keeps unavailable methods as stored when saving', () => {
    const available = availableMethods(settings);
    const stored = ['password', 'magic_link', 'google', 'azure', 'apple', 'mixel_id'];
    expect(methodsToSave(stored, new Set(['password']), available)).toEqual(['password', 'google', 'azure', 'apple']);
    expect(methodsToSave(['password'], new Set(['password', 'mixel_id', 'google']), available)).toEqual(['password', 'mixel_id']);
  });
});

describe('roles', () => {
  it('limits role changes like the database', () => {
    expect(assignableRoles('owner', 'owner')).toEqual(['owner', 'admin', 'technician', 'member']);
    expect(assignableRoles('admin', 'technician')).toEqual(['admin', 'technician', 'member']);
    expect(assignableRoles('admin', 'owner')).toEqual([]);
    expect(assignableRoles('technician', 'member')).toEqual([]);
  });
  it('never removes the last owner', () => {
    const owner = { user_id: 'a', role: 'owner' as const };
    expect(canRemoveMember('owner', 'a', owner, 1)).toBe(false);
    expect(canRemoveMember('owner', 'a', owner, 2)).toBe(true);
    expect(canRemoveMember('admin', 'b', owner, 2)).toBe(false);
    expect(canRemoveMember('admin', 'b', { user_id: 'c', role: 'technician' }, 1)).toBe(true);
    expect(canRemoveMember('technician', 'b', { user_id: 'c', role: 'member' }, 1)).toBe(false);
    expect(canRemoveMember('member', 'b', { user_id: 'b', role: 'member' }, 1)).toBe(true);
  });
  it('allows writes only while active, trial or past due', () => {
    expect(isWritable('trial')).toBe(true);
    expect(isWritable('past_due')).toBe(true);
    expect(isWritable('read_only')).toBe(false);
    expect(isWritable('closed')).toBe(false);
  });
});

describe('trial and usage', () => {
  const now = new Date('2026-11-13T12:00:00Z');
  it('rounds trial days up and never below zero', () => {
    expect(trialDaysLeft('2026-11-27T12:00:00Z', now)).toBe(14);
    expect(trialDaysLeft('2026-11-13T13:00:00Z', now)).toBe(1);
    expect(trialDaysLeft('2026-11-01T00:00:00Z', now)).toBe(0);
    expect(trialDaysLeft(null, now)).toBe(0);
    expect(trialDaysLeft('nonsense', now)).toBe(0);
  });
  it('grades usage', () => {
    expect(usage(1, 15)).toMatchObject({ percent: 7, level: 'ok' });
    expect(usage(12, 15)).toMatchObject({ percent: 80, level: 'high' });
    expect(usage(15, 15)).toMatchObject({ percent: 100, level: 'full' });
    expect(usage(20, 15).percent).toBe(100);
    expect(usage(0, 0)).toMatchObject({ percent: 0, level: 'full' });
  });
});

describe('validation', () => {
  it('validates emails, names, domains, passwords and codes', () => {
    expect(isEmail(' Anna@Example.CH ')).toBe(true);
    expect(isEmail('anna@example')).toBe(false);
    expect(isEmail('a b@example.ch')).toBe(false);
    expect(isCompanyName('A')).toBe(false);
    expect(isCompanyName('Muster AG')).toBe(true);
    expect(isCompanyName('Bad\u0007Name')).toBe(false);
    expect(normalizeDomain(' @Example.CH. ')).toBe('example.ch');
    expect(isDomain('example.ch')).toBe(true);
    expect(isDomain('sub.example.co.uk')).toBe(true);
    expect(isDomain('-bad.ch')).toBe(false);
    expect(isDomain('localhost')).toBe(false);
    expect(checkPassword('short1')).toMatchObject({ ok: false, tooShort: true });
    expect(checkPassword('onlyletterslong')).toMatchObject({ ok: false, needsVariety: true });
    expect(checkPassword('Grüezi-2026!').ok).toBe(true);
    expect(isOtpCode('123 456')).toBe(true);
    expect(isOtpCode('12345')).toBe(false);
  });
});

describe('links and formatting', () => {
  it('formats connection IDs', () => {
    expect(formatConnectionId(123456789)).toBe('123 456 789');
    expect(formatConnectionId(null)).toBe('');
  });
  it('puts invitation secrets into the fragment and reads them back', () => {
    const token = 'mri_' + 'a'.repeat(43);
    const link = inviteLink('https://remote.mixel.ch/', token);
    expect(link).toBe(`https://remote.mixel.ch/invite#${token}`);
    expect(tokenFromHash(new URL(link).hash)).toBe(token);
    expect(tokenFromHash(`#token=${token}`)).toBe(token);
    expect(tokenFromHash('#token=short')).toBeNull();
    expect(tokenFromHash('#mri_<script>aaaaaaaaaaaaaaaaaaaa')).toBeNull();
    expect(tokenFromHash('')).toBeNull();
  });
  it('follows only same-site paths after sign-in', () => {
    expect(safeNext('/app/team')).toBe('/app/team');
    expect(safeNext('//evil.example')).toBe('/app');
    expect(safeNext('/\\evil.example')).toBe('/app');
    expect(safeNext('https://evil.example')).toBe('/app');
    expect(safeNext(null)).toBe('/app');
  });
  it('does not follow paths the URL parser turns into another site (tab, newline, other whitespace)', () => {
    // new URL('/\t/evil.example', origin) is https://evil.example/: browsers drop tabs and newlines.
    for (const next of ['/\t/evil.example', '/\n/evil.example', '/\r/evil.example', '/\t\\evil.example', '/ /evil.example', '/\u0000/x']) {
      expect(safeNext(next), JSON.stringify(next)).toBe('/app');
    }
    expect(safeNext('/app/devices?filter=online#top')).toBe('/app/devices?filter=online#top');
    expect(safeNext('/invite')).toBe('/invite');
  });
  it('builds a single-quoted deploy command', () => {
    const command = windowsDeployCommand("mrt_abc'def", 'https://store-api.mixel.ch/x/Mixel-Remote.exe');
    expect(command).toContain("$t='mrt_abc''def'");
    expect(command).toContain("'https://store-api.mixel.ch/x/Mixel-Remote.exe'");
    expect(command).toContain("--signin-enroll");
    expect(command).not.toContain('"');
  });
  it('derives deploy link states', () => {
    const now = new Date('2026-11-13T12:00:00Z');
    const base = { revoked_at: null, expires_at: '2026-12-01T00:00:00Z', uses: 0, max_uses: 5 };
    expect(deployLinkState(base, now)).toBe('active');
    expect(deployLinkState({ ...base, revoked_at: '2026-11-12T00:00:00Z' }, now)).toBe('revoked');
    expect(deployLinkState({ ...base, expires_at: '2026-11-01T00:00:00Z' }, now)).toBe('expired');
    expect(deployLinkState({ ...base, uses: 5 }, now)).toBe('used_up');
  });
});

describe('errors', () => {
  it('maps database messages to texts', () => {
    expect(errorKey({ message: 'Two-step sign-in is required by this company', code: '42501' })).toBe('error.mfa_required');
    expect(errorKey({ message: 'This sign-in method is not allowed by this company' })).toBe('error.method_not_allowed');
    expect(errorKey({ message: 'Technician limit of your plan reached', code: '54000' })).toBe('error.limit_technicians');
    expect(errorKey({ message: 'More computers than your plan has room for' })).toBe('error.limit_devices');
    expect(errorKey({ message: 'This Mixel Remote account is read-only', code: '25006' })).toBe('error.read_only');
    expect(errorKey({ message: 'Invalid login credentials' })).toBe('error.invalid_credentials');
    expect(errorKey({ message: 'Token has expired or is invalid' })).toBe('error.code_invalid');
    expect(errorKey({ message: 'Failed to fetch' })).toBe('error.network');
    expect(errorKey({ message: 'Error sending confirmation email' })).toBe('error.email_send');
    expect(errorKey({ message: 'something new', code: '42501' })).toBe('error.not_allowed');
    expect(errorKey({ message: 'something new' })).toBe('error.unknown');
    expect(gateFor({ message: 'Two-step sign-in is required by this company' })).toBe('mfa');
    expect(gateFor({ message: 'Not allowed for this company' })).toBeNull();
  });
});

describe('presence times', () => {
  const now = new Date('2026-11-13T12:00:00Z');
  it('says how long ago, in the chosen language', () => {
    expect(relativeTime('2026-11-13T11:59:40Z', 'en', now)).toBe('1 minute ago');
    expect(sinceTime('2026-11-13T10:05:00Z', 'en-CH', new Date('2026-11-13T12:00:00Z'))).toMatch(/\d{1,2}:05/);
    expect(sinceTime('2026-11-10T10:05:00Z', 'de-CH', now)).toMatch(/2026/);
    expect(relativeTime('2026-11-13T11:35:00Z', 'en', now)).toBe('25 minutes ago');
    expect(relativeTime('2026-11-13T09:00:00Z', 'en', now)).toBe('3 hours ago');
    expect(relativeTime('2026-11-12T10:00:00Z', 'en', now)).toBe('yesterday');
    expect(relativeTime('2026-11-13T09:00:00Z', 'de', now)).toBe('vor 3 Stunden');
    expect(relativeTime('2026-11-13T09:00:00Z', 'fr', now)).toBe('il y a 3 heures');
    expect(relativeTime('2026-11-13T09:00:00Z', 'it', now)).toBe('3 ore fa');
    expect(relativeTime(null, 'en', now)).toBe('');
    expect(relativeTime('nonsense', 'en', now)).toBe('');
  });
});

describe('Mac and Linux rollout', () => {
  const token = 'mrt_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_';
  it('pipes the install script to root with the token as a single-quoted argument', () => {
    expect(macDeployCommand(token)).toBe(`curl -fsSL https://remote.mixel.ch/install/macos.sh | sudo sh -s -- '${token}'`);
    expect(linuxDeployCommand(token)).toBe(`curl -fsSL https://remote.mixel.ch/install/linux.sh | sudo sh -s -- '${token}'`);
  });
  it('never builds a shell line from anything but a deploy token', () => {
    expect(isDeployToken(token)).toBe(true);
    for (const bad of ["mrt_abcdefghijklmnop'; rm -rf ~", 'mri_abcdefghijklmnopqrstuvwxyz', 'mrt_short', `mrt_${'a'.repeat(120)}`]) {
      expect(isDeployToken(bad)).toBe(false);
      expect(() => macDeployCommand(bad)).toThrow();
      expect(() => linuxDeployCommand(bad)).toThrow();
    }
  });
  it('preselects the command of the browser\'s system', () => {
    expect(browserOs('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15')).toBe('macos');
    expect(browserOs('Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0')).toBe('linux');
    expect(browserOs('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')).toBe('windows');
    expect(browserOs('Mozilla/5.0 (Linux; Android 14; Pixel 8)')).toBe('windows');
    expect(browserOs('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)')).toBe('windows');
  });
});

describe('invitation e-mails', () => {
  it('explains the resend limits', () => {
    expect(errorKey({ message: 'Wait a minute before sending this invitation again', code: '54000' })).toBe('error.resend_wait');
    expect(errorKey({ message: 'This invitation was sent too often; revoke it and invite again', code: '54000' })).toBe('error.resend_limit');
    expect(errorKey({ message: 'Too many invitations today', code: '54000' })).toBe('error.too_many');
  });
});
