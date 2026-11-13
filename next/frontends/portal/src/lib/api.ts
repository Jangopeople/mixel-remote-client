// Typed wrappers for the remote_saas RPCs (SQL 019/020). Every call goes through PostgREST with
// the signed-in user's JWT; the database checks membership, role, plan and sign-in policy.
import { supabase, ISM_URL, anonKey } from './supabase';
import type { Features, GoTrueSettings, Method, Role, TenantStatus } from './logic';

export class ApiError extends Error {
  code?: string;
  constructor(message: string, code?: string) {
    super(message);
    this.code = code;
  }
}

async function rpc<T>(name: string, args: Record<string, unknown> = {}): Promise<T> {
  const { data, error } = await supabase.rpc(name, args);
  if (error) throw new ApiError(error.message, error.code);
  return data as T;
}

export interface MyTenant {
  tenant_id: string;
  organization_id: string;
  name: string;
  slug: string;
  role: Role;
  plan: string;
  status: TenantStatus;
  trial_ends_at: string | null;
}

export interface Overview {
  tenant_id: string;
  organization_id: string;
  name: string;
  slug: string;
  role: Role;
  plan: string;
  status: TenantStatus;
  trial_ends_at: string | null;
  features: Features;
  limits: { technicians: number; devices: number; sessions: number };
  usage: { technicians: number; devices: number; members: number };
}

export interface Member { user_id: string; name: string; email: string; role: Role; created_at: string }
export interface Invite { id: string; email: string; role: Role; created_at: string; expires_at: string }
export interface AuthSettings { methods: Method[]; require_mfa: boolean; sso_provider_id: string | null; updated_at: string }
export interface SignInOptions { company: string; slug: string; methods: Method[]; require_mfa: boolean; sso_provider_id: string | null }
export interface Domain { domain: string; verification_token: string; verified_at: string | null }
export interface Device {
  device_id: string; name: string; os: string; group_id: string | null; group_name: string | null;
  connection_id: number | null; generation: number | null; enrolled_at: string | null; revoked: boolean | null;
  added_via: string | null;
}
export interface Group { group_id: string; name: string; devices: number; technicians: string[] }
export interface DeployLink {
  id: string; label: string; group_id: string | null; created_at: string; expires_at: string;
  max_uses: number; uses: number; revoked_at: string | null;
}
export interface AuditEntry {
  id: number; actor_id: string | null; actor_name: string | null; action: string; target: string | null;
  detail: Record<string, unknown>; occurred_at: string;
}
export interface SignupResult { tenant_id: string; slug: string; plan: string; status: string; trial_ends_at: string }

export const api = {
  signup: (companyName: string) => rpc<SignupResult>('remote_saas_signup', { company_name: companyName }),
  myTenants: () => rpc<MyTenant[]>('remote_saas_my_tenants'),
  overview: (tenant: string) => rpc<Overview>('remote_saas_tenant_overview', { tenant }),

  members: (tenant: string) => rpc<Member[]>('remote_saas_members', { tenant }),
  invite: (tenant: string, email: string, role: Role) =>
    rpc<string>('remote_saas_invite_member', { tenant, invitee_email: email, member_role: role }),
  invites: (tenant: string) => rpc<Invite[]>('remote_saas_invites', { tenant }),
  revokeInvite: (tenant: string, invite: string) => rpc<boolean>('remote_saas_revoke_invite', { tenant, invite }),
  acceptInvite: (token: string) => rpc<string>('remote_saas_accept_invite', { invite_token: token }),
  removeMember: (tenant: string, member: string) => rpc<boolean>('remote_saas_remove_member', { tenant, member }),
  setMemberRole: (tenant: string, member: string, role: Role) =>
    rpc<boolean>('remote_saas_set_member_role', { tenant, member, member_role: role }),

  authSettings: (tenant: string) => rpc<AuthSettings>('remote_saas_get_auth_settings', { tenant }),
  setAuthSettings: (tenant: string, methods: Method[], requireMfa: boolean) =>
    rpc<AuthSettings>('remote_saas_set_auth_settings', { tenant, sign_in_methods: methods, mfa_required: requireMfa }),
  signInOptions: async (identifier: string): Promise<SignInOptions | null> =>
    (await rpc<SignInOptions[]>('remote_saas_sign_in_options', { identifier }))[0] ?? null,

  domains: (tenant: string) => rpc<Domain[]>('remote_saas_domains', { tenant }),
  addDomain: (tenant: string, domain: string) => rpc<string>('remote_saas_add_domain', { tenant, email_domain: domain }),
  removeDomain: (tenant: string, domain: string) => rpc<boolean>('remote_saas_remove_domain', { tenant, email_domain: domain }),

  devices: (tenant: string, group: string | null = null, search: string | null = null) =>
    rpc<Device[]>('remote_saas_devices', { tenant, in_group: group, search }),
  renameDevice: (tenant: string, device: string, name: string) =>
    rpc<string>('remote_saas_rename_device', { tenant, device, device_name: name }),
  moveDevice: (tenant: string, device: string, group: string | null) =>
    rpc<boolean>('remote_saas_move_device', { tenant, device, to_group: group }),
  removeDevice: (tenant: string, device: string) => rpc<boolean>('remote_saas_remove_device', { tenant, device }),

  groups: (tenant: string) => rpc<Group[]>('remote_saas_groups', { tenant }),
  createGroup: (tenant: string, name: string) => rpc<string>('remote_saas_create_group', { tenant, group_name: name }),
  renameGroup: (tenant: string, group: string, name: string) =>
    rpc<boolean>('remote_saas_rename_group', { tenant, target: group, group_name: name }),
  deleteGroup: (tenant: string, group: string) => rpc<boolean>('remote_saas_delete_group', { tenant, target: group }),
  setGroupAccess: (tenant: string, group: string, technicians: string[]) =>
    rpc<number>('remote_saas_set_group_access', { tenant, target: group, technicians }),

  createDeployLink: (tenant: string, label: string, days: number, computers: number, group: string | null) =>
    rpc<string>('remote_saas_create_deploy_link', { tenant, link_label: label, valid_days: days, computers, in_group: group }),
  deployLinks: (tenant: string) => rpc<DeployLink[]>('remote_saas_deploy_links', { tenant }),
  revokeDeployLink: (tenant: string, link: string) => rpc<boolean>('remote_saas_revoke_deploy_link', { tenant, link }),

  auditLog: (tenant: string, beforeId: number | null = null) =>
    rpc<AuditEntry[]>('remote_saas_audit_log', { tenant, before_id: beforeId }),
};

/** Public sign-in service settings: which external providers are configured. */
export async function goTrueSettings(): Promise<GoTrueSettings | null> {
  try {
    const response = await fetch(`${ISM_URL}/auth/v1/settings`, { headers: { apikey: anonKey } });
    if (!response.ok) return null;
    return (await response.json()) as GoTrueSettings;
  } catch {
    return null;
  }
}

export interface Installer { version: string; url: string; size: number }

/** The current signed Windows installer (served by this site's worker from the update channel). */
export async function windowsInstaller(): Promise<Installer | null> {
  try {
    const response = await fetch('/api/windows-installer');
    if (!response.ok) return null;
    return (await response.json()) as Installer;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------------------------
// Billing (Stripe): SQL 023/024 for the state and prices, the billing service for the actions.

export interface Price { kind: 'plan' | 'seat'; plan: string | null; billing_interval: 'year' | 'month'; amount_chf: number; per_month_chf: number }
export interface BillingState {
  tenant_id: string; role: string; plan: string; status: string; trial_ends_at: string | null; extra_technicians: number;
  billing_interval: 'year' | 'month' | null; subscription_status: string | null; current_period_end: string | null;
  cancel_at: string | null; cancel_at_period_end: boolean; has_customer: boolean; has_subscription: boolean;
  read_only_since: string | null; devices_delete_after: string | null; usage: { technicians: number; devices: number };
}
export interface CheckoutRequest { plan: 'solo' | 'team' | 'business'; interval: 'year' | 'month'; extra_technicians: number }

export const billingApi = {
  prices: async () => (await supabase.rpc('mixel_remote_prices')).data as Price[] | null,
  state: (tenant: string) => rpc<BillingState>('remote_saas_billing', { tenant }),
  checkout: (tenant: string, choice: CheckoutRequest) =>
    billingCall<{ url: string; trial_end: string | null }>('checkout', { tenant_id: tenant, ...choice, return_path: '/app/billing' }),
  portal: (tenant: string) => billingCall<{ url: string }>('portal', { tenant_id: tenant, return_path: '/app/billing' }),
  change: (tenant: string, choice: CheckoutRequest) => billingCall<{ subscription: string; status: string }>('change', { tenant_id: tenant, ...choice }),
};

async function billingCall<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new ApiError('Sign in first');
  let response: Response;
  try {
    response = await fetch(`/api/billing/${path}`, {
      method: 'POST',
      headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError('Failed to fetch');
  }
  const answer = await response.json().catch(() => ({}));
  if (!response.ok) {
    const code = String(answer.error ?? 'billing_unavailable');
    throw new ApiError(`${code}: ${answer.message ?? ''}`, response.status === 401 || response.status === 403 ? '42501' : undefined);
  }
  return answer as T;
}
