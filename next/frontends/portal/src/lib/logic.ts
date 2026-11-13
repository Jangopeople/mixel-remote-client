// Pure client logic of the portal (no network, no DOM). Unit tested in tests/unit.

export type Role = 'owner' | 'admin' | 'technician' | 'member';
export type TenantStatus = 'active' | 'trial' | 'past_due' | 'read_only' | 'closed';

/** Sign-in method codes as stored in remote_saas.auth_settings.methods. */
export type Method = 'password' | 'magic_link' | 'google' | 'azure' | 'apple' | 'mixel_id' | 'sso';
export const ALL_METHODS: readonly Method[] = ['password', 'magic_link', 'mixel_id', 'google', 'azure', 'apple', 'sso'];
/** The methods a tenant gets by default (SQL 018). */
export const DEFAULT_METHODS: readonly Method[] = ['password', 'magic_link', 'google', 'azure', 'apple', 'mixel_id'];

export interface Features {
  groups?: boolean;
  access_rights?: boolean;
  sso?: boolean;
  audit_log?: boolean;
  policies?: boolean;
  require_mfa?: boolean;
  private_use_only?: boolean;
  [key: string]: boolean | undefined;
}

/** GoTrue /auth/v1/settings "external" flags. */
export interface GoTrueSettings {
  external: Record<string, boolean>;
  saml_enabled?: boolean;
  disable_signup?: boolean;
}

/** Which methods the sign-in service can actually offer right now. */
export function availableMethods(settings: GoTrueSettings | null): Set<Method> {
  const available = new Set<Method>();
  if (!settings) {
    // Without the settings (offline) only the always-on e-mail methods are offered.
    available.add('password');
    available.add('magic_link');
    return available;
  }
  const ext = settings.external ?? {};
  if (ext.email) {
    available.add('password');
    available.add('magic_link');
  }
  if (ext.keycloak) available.add('mixel_id');
  if (ext.google) available.add('google');
  if (ext.azure) available.add('azure');
  if (ext.apple) available.add('apple');
  if (settings.saml_enabled) available.add('sso');
  return available;
}

/** The methods to show on the sign-in page: allowed by the company (if known) and available. */
export function signInMethods(allowed: readonly string[] | null, available: Set<Method>): Method[] {
  return ALL_METHODS.filter((method) => available.has(method) && (allowed === null || allowed.includes(method)));
}

/**
 * The methods to save from the settings form. Methods that are not available yet keep their
 * stored value (they cannot be chosen in the form), so saving never changes them silently.
 */
export function methodsToSave(stored: readonly string[], chosen: ReadonlySet<Method>, available: Set<Method>): Method[] {
  return ALL_METHODS.filter((method) => (available.has(method) ? chosen.has(method) : stored.includes(method)));
}

// ---------------------------------------------------------------------------------------------
// Roles and permissions (mirrors the SECURITY DEFINER checks in SQL 019/020)

export const ROLE_ORDER: readonly Role[] = ['owner', 'admin', 'technician', 'member'];

export function isManager(role: Role | null | undefined): boolean {
  return role === 'owner' || role === 'admin';
}

export function canSeeTeam(role: Role | null | undefined): boolean {
  return role === 'owner' || role === 'admin' || role === 'technician';
}

export function canManageDevices(role: Role | null | undefined): boolean {
  return role === 'owner' || role === 'admin' || role === 'technician';
}

/** Roles the acting member may assign to a member who currently has `current`. */
export function assignableRoles(me: Role, current: Role): Role[] {
  if (me === 'owner') return [...ROLE_ORDER];
  if (me === 'admin' && current !== 'owner') return ['admin', 'technician', 'member'];
  return [];
}

export function canRemoveMember(me: Role, myId: string, target: { user_id: string; role: Role }, owners: number): boolean {
  if (target.role === 'owner' && owners <= 1) return false;
  if (target.user_id === myId) return true;
  if (me === 'owner') return true;
  return me === 'admin' && target.role !== 'owner';
}

/** Writes are allowed while active, in trial or past due (remote_saas.actor 'write'). */
export function isWritable(status: TenantStatus | null | undefined): boolean {
  return status === 'active' || status === 'trial' || status === 'past_due';
}

// ---------------------------------------------------------------------------------------------
// Trial and usage

const DAY = 24 * 60 * 60 * 1000;

/** Whole days left in the trial (rounded up, never negative). */
export function trialDaysLeft(trialEndsAt: string | null | undefined, now: Date = new Date()): number {
  if (!trialEndsAt) return 0;
  const end = Date.parse(trialEndsAt);
  if (Number.isNaN(end)) return 0;
  return Math.max(0, Math.ceil((end - now.getTime()) / DAY));
}

export interface Usage {
  used: number;
  limit: number;
  percent: number;
  level: 'ok' | 'high' | 'full';
}

export function usage(used: number, limit: number): Usage {
  const safeLimit = Math.max(0, limit);
  const percent = safeLimit === 0 ? (used > 0 ? 100 : 0) : Math.min(100, Math.round((used / safeLimit) * 100));
  const level = used >= safeLimit ? 'full' : percent >= 80 ? 'high' : 'ok';
  return { used, limit: safeLimit, percent, level };
}

// ---------------------------------------------------------------------------------------------
// Validation

const EMAIL = /^[^@\s\p{Cc}]+@[^@\s\p{Cc}]+\.[^@\s\p{Cc}]+$/u;
const DOMAIN = /^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f]/;

export function normalizeEmail(value: string): string {
  return value.trim().toLowerCase();
}

export function isEmail(value: string): boolean {
  const clean = normalizeEmail(value);
  return clean.length >= 3 && clean.length <= 254 && EMAIL.test(clean);
}

export function isCompanyName(value: string): boolean {
  const clean = value.trim();
  return clean.length >= 2 && clean.length <= 80 && !CONTROL.test(clean);
}

export function isName(value: string, max: number): boolean {
  const clean = value.trim();
  return clean.length >= 1 && clean.length <= max && !CONTROL.test(clean);
}

export function normalizeDomain(value: string): string {
  return value.trim().toLowerCase().replace(/^@/, '').replace(/\.$/, '');
}

export function isDomain(value: string): boolean {
  const clean = normalizeDomain(value);
  return clean.length <= 253 && DOMAIN.test(clean);
}

export interface PasswordCheck {
  ok: boolean;
  tooShort: boolean;
  needsVariety: boolean;
}

/** At least 10 characters with letters and digits or symbols. */
export function checkPassword(value: string): PasswordCheck {
  const tooShort = value.length < 10;
  const hasLetter = /\p{L}/u.test(value);
  const hasOther = /[^\p{L}]/u.test(value);
  const needsVariety = !(hasLetter && hasOther);
  return { ok: !tooShort && !needsVariety, tooShort, needsVariety };
}

export function isOtpCode(value: string): boolean {
  return /^\d{6}$/.test(value.replace(/\s/g, ''));
}

// ---------------------------------------------------------------------------------------------
// Formatting

/** 123456789 -> "123 456 789". */
export function formatConnectionId(id: number | null | undefined): string {
  if (id === null || id === undefined) return '';
  return String(id).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

export function osLabel(os: string | null | undefined): string {
  switch (os) {
    case 'macos': return 'macOS';
    case 'windows': return 'Windows';
    case 'linux': return 'Linux';
    case 'ios': return 'iOS';
    case 'android': return 'Android';
    default: return os ? os : '';
  }
}

// ---------------------------------------------------------------------------------------------
// Links. Secrets travel in the URL fragment, which browsers never send to a server.

// Same format as the desktop app (INVITE_URL in saas_commands.rs): /invite#<secret>.
export function inviteLink(origin: string, token: string): string {
  return `${origin.replace(/\/$/, '')}/invite#${encodeURIComponent(token)}`;
}

/** The invitation secret from "#<secret>" (also accepts "#token=<secret>"). */
export function tokenFromHash(hash: string): string | null {
  const raw = hash.replace(/^#/, '');
  let token: string | null = raw.includes('=') ? new URLSearchParams(raw).get('token') : raw;
  try {
    token = token ? decodeURIComponent(token) : null;
  } catch {
    return null;
  }
  if (!token || token.length < 20 || token.length > 100 || !/^[A-Za-z0-9_-]+$/.test(token)) return null;
  return token;
}

/** Only same-site relative paths are followed after sign-in. */
export function safeNext(next: string | null | undefined, fallback = '/app'): string {
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.includes('\\')) return fallback;
  return next;
}

/** PowerShell line that installs Mixel Remote and enrols the computer with a deploy token. */
export function windowsDeployCommand(token: string, installerUrl: string): string {
  // Single quotes only, so the line survives being wrapped in "..." by deployment tools
  // (same command as the desktop app's rollout_create).
  const quote = (value: string) => value.replace(/'/g, "''");
  return `$t='${quote(token)}'; $f=Join-Path $env:TEMP 'Mixel-Remote-Setup.exe'; Invoke-WebRequest -UseBasicParsing '${quote(installerUrl)}' -OutFile $f; Start-Process $f '/S' -Wait; $p=Start-Process (Join-Path $env:LOCALAPPDATA 'Mixel Remote\\mixel-remote.exe') -ArgumentList '--signin-enroll',$t -Wait -PassThru; exit $p.ExitCode`;
}

export type LinkState = 'active' | 'revoked' | 'expired' | 'used_up';

export function deployLinkState(link: { revoked_at: string | null; expires_at: string; uses: number; max_uses: number }, now: Date = new Date()): LinkState {
  if (link.revoked_at) return 'revoked';
  if (Date.parse(link.expires_at) <= now.getTime()) return 'expired';
  if (link.uses >= link.max_uses) return 'used_up';
  return 'active';
}

// ---------------------------------------------------------------------------------------------
// Errors from the RPCs (SQL raises English messages with SQLSTATE codes).

export type ErrorKey =
  | 'error.not_allowed'
  | 'error.read_only'
  | 'error.mfa_required'
  | 'error.method_not_allowed'
  | 'error.limit_technicians'
  | 'error.limit_devices'
  | 'error.already_member'
  | 'error.account_has_company'
  | 'error.email_in_use'
  | 'error.invite_invalid'
  | 'error.invite_wrong_email'
  | 'error.confirm_email'
  | 'error.sign_in_first'
  | 'error.too_many'
  | 'error.group_exists'
  | 'error.needs_team'
  | 'error.needs_business'
  | 'error.keep_method'
  | 'error.mfa_self_first'
  | 'error.invalid_credentials'
  | 'error.code_invalid'
  | 'error.rate_limited'
  | 'error.weak_password'
  | 'error.network'
  | 'error.one_owner'
  | 'error.invalid_input'
  | 'error.email_send'
  | 'error.plan_too_small'
  | 'error.already_subscribed'
  | 'error.payment_failed'
  | 'error.billing_unavailable'
  | 'error.unknown';

const MESSAGE_KEYS: [RegExp, ErrorKey][] = [
  [/^plan_too_small/i, 'error.plan_too_small'],
  [/^already_subscribed/i, 'error.already_subscribed'],
  [/^payment_failed/i, 'error.payment_failed'],
  [/^(stripe_unavailable|billing_unavailable)/i, 'error.billing_unavailable'],
  [/two-step sign-in is required/i, 'error.mfa_required'],
  [/sign-in method is not allowed/i, 'error.method_not_allowed'],
  [/read-only/i, 'error.read_only'],
  [/technician limit/i, 'error.limit_technicians'],
  [/device limit|more computers than your plan/i, 'error.limit_devices'],
  [/already a member/i, 'error.already_member'],
  [/already belongs to (a|another) company/i, 'error.account_has_company'],
  [/already used by another account|already registered/i, 'error.email_in_use'],
  [/sign in with the invited email/i, 'error.invite_wrong_email'],
  [/invitation invalid or expired/i, 'error.invite_invalid'],
  [/confirm your email/i, 'error.confirm_email'],
  [/^sign in first/i, 'error.sign_in_first'],
  [/too many/i, 'error.too_many'],
  [/group with this name exists/i, 'error.group_exists'],
  [/need(s)? the team plan/i, 'error.needs_team'],
  [/needs the business plan/i, 'error.needs_business'],
  [/keep the sign-in method/i, 'error.keep_method'],
  [/set up and use two-step sign-in yourself/i, 'error.mfa_self_first'],
  [/at least one owner/i, 'error.one_owner'],
  [/invalid login credentials/i, 'error.invalid_credentials'],
  [/token has expired or is invalid|otp.*(expired|invalid)|invalid totp|invalid mfa/i, 'error.code_invalid'],
  [/error sending .*email|sending email/i, 'error.email_send'],
  [/rate limit|for security purposes, you can only request/i, 'error.rate_limited'],
  [/password should|weak password/i, 'error.weak_password'],
  [/failed to fetch|networkerror|network request failed|load failed/i, 'error.network'],
  [/not allowed|permission denied|unavailable/i, 'error.not_allowed'],
  [/^(enter|invalid|choose)/i, 'error.invalid_input'],
];

export interface ApiErrorLike {
  message?: string;
  code?: string;
}

export function errorKey(error: unknown): ErrorKey {
  const message = typeof error === 'string' ? error : (error as ApiErrorLike | null)?.message ?? '';
  for (const [pattern, key] of MESSAGE_KEYS) if (pattern.test(message)) return key;
  const code = (error as ApiErrorLike | null)?.code;
  if (code === '42501') return 'error.not_allowed';
  if (code === '25006') return 'error.read_only';
  if (code === '54000') return 'error.too_many';
  if (code === '22023') return 'error.invalid_input';
  return 'error.unknown';
}

/** Gate errors that block the whole workspace rather than one action. */
export function gateFor(error: unknown): 'mfa' | 'method' | null {
  const key = errorKey(error);
  if (key === 'error.mfa_required') return 'mfa';
  if (key === 'error.method_not_allowed') return 'method';
  return null;
}
