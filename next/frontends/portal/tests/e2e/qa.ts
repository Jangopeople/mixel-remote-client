// Helpers for the live QA run: GoTrue admin API (service role from QA_ADMIN_ENV), TOTP, device enrolment.
import { execFileSync } from 'node:child_process';
import { createHmac, randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';

export const ISM = 'https://ism-api.mixel.ch';

function env(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set`);
  return value;
}
function fileValue(path: string, key: string): string {
  const line = readFileSync(path, 'utf8').split('\n').find((entry) => entry.startsWith(`${key}=`));
  if (!line) throw new Error(`${key} missing in ${path}`);
  return line.slice(key.length + 1).trim();
}
export const anonKey = () => fileValue('.env.local', 'VITE_ISM_ANON_KEY');
const serviceKey = () => fileValue(env('QA_ADMIN_ENV'), 'ISM_SERVICE_ROLE_KEY');

async function admin(path: string, init: RequestInit = {}) {
  const key = serviceKey();
  const response = await fetch(`${ISM}/auth/v1/admin${path}`, {
    ...init,
    headers: { apikey: key, authorization: `Bearer ${key}`, 'content-type': 'application/json', ...(init.headers ?? {}) },
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`admin ${path}: ${response.status} ${text.slice(0, 200)}`);
  return text ? JSON.parse(text) : null;
}

export async function createUser(email: string, password: string, name: string): Promise<string> {
  const user = await admin('/users', { method: 'POST', body: JSON.stringify({ email, password, email_confirm: true, user_metadata: { name } }) });
  return user.id as string;
}

/** The 6-digit code GoTrue would have emailed for a pending sign-up (no email is sent by this call). */
export async function signupCode(email: string, password: string): Promise<string> {
  const link = await admin('/generate_link', { method: 'POST', body: JSON.stringify({ type: 'signup', email, password }) });
  const code = link.email_otp ?? link.properties?.email_otp;
  if (!/^\d{6}$/.test(String(code))) throw new Error('no email_otp in generate_link');
  return String(code);
}

export async function deleteUser(id: string) {
  await admin(`/users/${id}`, { method: 'DELETE' });
}

export async function findUsers(prefix: string): Promise<{ id: string; email: string }[]> {
  const found: { id: string; email: string }[] = [];
  for (let page = 1; page < 50; page++) {
    const result = await admin(`/users?page=${page}&per_page=200`);
    const users = (result.users ?? []) as { id: string; email: string }[];
    found.push(...users.filter((user) => user.email?.startsWith(prefix)));
    if (users.length < 200) break;
  }
  return found;
}

/** A computer running the deploy command: enrol with the token (anonymous RPC, like the installer). */
export async function enrolComputer(token: string, name: string): Promise<{ connection_id: number }> {
  const response = await fetch(`${ISM}/rest/v1/rpc/remote_enroll_with_token`, {
    method: 'POST',
    headers: { apikey: anonKey(), 'content-type': 'application/json' },
    body: JSON.stringify({ token, device_name: name, device_os: 'windows', key_material: `\\x${randomBytes(32).toString('hex')}` }),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`enrol: ${response.status} ${text}`);
  return JSON.parse(text)[0];
}

function base32(secret: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const char of secret.replace(/=+$/, '').toUpperCase()) bits += alphabet.indexOf(char).toString(2).padStart(5, '0');
  const bytes = [];
  for (let index = 0; index + 8 <= bits.length; index += 8) bytes.push(parseInt(bits.slice(index, index + 8), 2));
  return Buffer.from(bytes);
}

/** RFC 6238 TOTP (SHA-1, 30 s, 6 digits), as authenticator apps compute it. */
export function totp(secret: string, at = Date.now()): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 1000 / 30)));
  const digest = createHmac('sha1', base32(secret)).update(counter).digest();
  const offset = digest[digest.length - 1] & 0xf;
  const value = (digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return String(value).padStart(6, '0');
}

/** Wait for the start of a fresh 30-second window (a TOTP code is accepted once per window). */
export async function freshTotp(secret: string, used: Set<string>): Promise<string> {
  for (;;) {
    const code = totp(secret);
    const left = 30 - (Math.floor(Date.now() / 1000) % 30);
    if (!used.has(code) && left > 4) {
      used.add(code);
      return code;
    }
    await new Promise((resolve) => setTimeout(resolve, (left + 1) * 1000));
  }
}

export interface GoTrueSession { access_token: string; refresh_token: string; expires_in: number; expires_at?: number; user: { id: string; email: string } }

async function gotrue(path: string, body: unknown, token?: string): Promise<any> { // eslint-disable-line @typescript-eslint/no-explicit-any
  const response = await fetch(`${ISM}/auth/v1${path}`, {
    method: 'POST',
    headers: { apikey: anonKey(), 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`gotrue ${path}: ${response.status} ${text.slice(0, 200)}`);
  return text ? JSON.parse(text) : null;
}

export async function passwordSession(email: string, password: string): Promise<GoTrueSession> {
  return gotrue('/token?grant_type=password', { email, password });
}

/** An aal2 session: verifies with the given TOTP secret, or enrols a new authenticator and returns its secret. */
export async function secondStep(session: GoTrueSession, used: Set<string>, secret?: string): Promise<{ session: GoTrueSession; secret: string }> {
  let factorId: string;
  if (secret) {
    const user = await (await fetch(`${ISM}/auth/v1/user`, { headers: { apikey: anonKey(), authorization: `Bearer ${session.access_token}` } })).json();
    factorId = user.factors.find((factor: { status: string }) => factor.status === 'verified').id;
  } else {
    const enrolled = await gotrue('/factors', { factor_type: 'totp', friendly_name: `QA ${Date.now()}`, issuer: 'Mixel Remote QA' }, session.access_token);
    factorId = enrolled.id;
    secret = enrolled.totp.secret as string;
  }
  const challenge = await gotrue(`/factors/${factorId}/challenge`, {}, session.access_token);
  const verified = await gotrue(`/factors/${factorId}/verify`, { challenge_id: challenge.id, code: await freshTotp(secret!, used) }, session.access_token);
  return { session: verified, secret: secret! };
}

export async function rest(path: string, token: string, body: unknown): Promise<any> { // eslint-disable-line @typescript-eslint/no-explicit-any
  const response = await fetch(`${ISM}/rest/v1${path}`, {
    method: 'POST', headers: { apikey: anonKey(), authorization: `Bearer ${token}`, 'content-type': 'application/json' }, body: JSON.stringify(body),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`rest ${path}: ${response.status} ${text.slice(0, 200)}`);
  return text ? JSON.parse(text) : null;
}

/**
 * Invitation e-mails go out from the mail service (SQL 026); QA addresses (example.com) are never
 * mailed. This stands in for the e-mail: it stores the hash of a fresh secret for the QA run's open
 * invitation of `email` and returns the link the e-mail would contain. Needs QA_SQL, a command
 * that runs SQL on the ISM database, e.g. "ssh mixel-vps docker exec -i ism-postgres psql -U postgres -d ism -At".
 */
export function invitationLink(email: string, run: string): string {
  if (!/^qa-portal-[a-z0-9]+-[a-z-]+@example\.com$/.test(email) || !email.includes(`-${run}-`)) throw new Error('not a QA invitation');
  const secret = `mri_${randomBytes(32).toString('base64url')}`;
  const sql = `update remote_saas.invites set token_hash = sha256(convert_to('${secret}', 'UTF8')), mail_state = 'sent', mail_sent_at = now(),
    mail_claim = null, mail_claimed_at = null where email = '${email}' and accepted_at is null and revoked_at is null returning id;`;
  const out = execFileSync('sh', ['-c', env('QA_SQL')], { input: sql, encoding: 'utf8' });
  if (!/[0-9a-f-]{36}/.test(out)) throw new Error(`no open invitation for ${email}`);
  return `https://remote.mixel.ch/invite#${secret}`;
}
