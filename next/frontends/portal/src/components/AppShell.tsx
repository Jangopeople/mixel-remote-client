import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { Link, NavLink, Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useI18n, type MessageKey } from '../i18n';
import { useAuth } from '../lib/auth';
import { api, type MyTenant, type Overview, type SignInOptions } from '../lib/api';
import { canSeeTeam, gateFor, isManager, trialDaysLeft } from '../lib/logic';
import { AuthLayout } from '../pages/Auth';
import { Brand, ErrorNotice, PreferencesMenu, Spinner } from './ui';
import { MfaChallenge, methodLabelKey } from './auth';
import { MfaSetup } from './MfaSetup';
import { Icon, type IconName } from './Icon';
import { LegalLinks } from './legal';
import { DeleteMyAccount } from '../pages/DataDeletion';
import { useLoad } from '../lib/useLoad';

interface TenantContext {
  tenant: MyTenant;
  overview: Overview;
  reload: () => Promise<void>;
}

const Context = createContext<TenantContext | null>(null);

export function useTenant(): TenantContext {
  const value = useContext(Context);
  if (!value) throw new Error('useTenant outside the app shell');
  return value;
}

/** Signed in (and second step done), else to the sign-in page. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { session, loading, needsSecondStep, signOut } = useAuth();
  const location = useLocation();
  if (loading) return <Spinner />;
  if (!session) return <Navigate to={`/signin?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  if (needsSecondStep) {
    return <AuthLayout><MfaChallenge onDone={() => undefined} onCancel={() => void signOut()} /></AuthLayout>;
  }
  return <>{children}</>;
}

type Gate = { kind: 'none' } | { kind: 'mfa' } | { kind: 'method'; options: SignInOptions | null; company: string };

export function TenantProvider() {
  const { t } = useI18n();
  const { session, signOut } = useAuth();
  const navigate = useNavigate();
  const [tenant, setTenant] = useState<MyTenant | null | undefined>(undefined);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [gate, setGate] = useState<Gate | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const tenants = await api.myTenants();
      const first = tenants[0] ?? null;
      setTenant(first);
      if (!first) return;
      try {
        setOverview(await api.overview(first.tenant_id));
        setGate(null);
      } catch (failure) {
        const kind = gateFor(failure);
        if (kind === 'mfa') setGate({ kind: 'mfa' });
        else if (kind === 'method') setGate({ kind: 'method', options: await api.signInOptions(first.slug).catch(() => null), company: first.name });
        else throw failure;
      }
    } catch (failure) {
      setError(failure);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, session?.user.id]);

  if (error) {
    return <AuthLayout><ErrorNotice error={error} onRetry={() => void load()} /></AuthLayout>;
  }
  if (tenant === undefined) return <Spinner />;
  if (tenant === null) {
    return (
      <AuthLayout>
        <h1>{t('gate.noTenantTitle')}</h1>
        <p className="lead">{t('gate.noTenantText', { email: session?.user.email ?? '' })}</p>
        <div className="stack">
          <button type="button" className="button-block" onClick={() => navigate('/signup/finish')}>{t('signup.finishTitle')}</button>
          <button type="button" className="button-quiet" onClick={() => void signOut()}>{t('auth.signOut')}</button>
        </div>
        <div style={{ marginTop: 24 }}><DeleteMyAccount company={null} soleOwner={false} /></div>
      </AuthLayout>
    );
  }
  if (gate?.kind === 'mfa') {
    return (
      <AuthLayout>
        <h1>{t('gate.mfaTitle')}</h1>
        <p className="lead">{t('gate.mfaText')}</p>
        <MfaSetup onDone={() => void load()} />
        <p className="auth-footer"><button type="button" className="button-quiet" onClick={() => void signOut()}>{t('auth.signOut')}</button></p>
      </AuthLayout>
    );
  }
  if (gate?.kind === 'method') {
    const methods = (gate.options?.methods ?? []).map((method) => t(methodLabelKey(method))).join(', ');
    return (
      <AuthLayout>
        <h1>{t('gate.methodTitle')}</h1>
        <p className="lead">{t('gate.methodText', { company: gate.company, methods })}</p>
        <button type="button" className="button-block" onClick={async () => {
          await signOut();
          navigate(`/signin?company=${encodeURIComponent(tenant.slug)}`);
        }}>{t('auth.signOut')}</button>
      </AuthLayout>
    );
  }
  if (!overview) return <Spinner />;
  return (
    <Context.Provider value={{ tenant, overview, reload: load }}>
      <Shell />
    </Context.Provider>
  );
}

interface NavItem { to: string; icon: IconName; label: MessageKey; show: boolean }

function Shell() {
  const { t, locale } = useI18n();
  const { overview } = useTenant();
  const { session, signOut } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [location.pathname]);

  const role = overview.role;
  const terms = useLoad(() => api.termsStatus(overview.tenant_id), `${overview.tenant_id}|${location.pathname === '/app/settings'}`);
  const groups = !!overview.features.groups;
  const items: NavItem[] = [
    { to: '/app', icon: 'home', label: 'shell.dashboard', show: true },
    { to: '/app/devices', icon: 'monitor', label: 'shell.devices', show: true },
    // Everyone who can connect has sessions; the page itself explains a plan without reports.
    { to: '/app/reports', icon: 'chart', label: 'shell.reports', show: true },
    { to: '/app/groups', icon: 'folder', label: 'shell.groups', show: groups && canSeeTeam(role) },
    { to: '/app/deploy', icon: 'rocket', label: 'shell.deploy', show: isManager(role) },
    { to: '/app/team', icon: 'users', label: 'shell.team', show: canSeeTeam(role) },
    { to: '/app/settings', icon: 'settings', label: 'shell.settings', show: true },
    { to: '/app/billing', icon: 'card', label: 'shell.billing', show: true },
  ];
  const current = items.find((item) => item.to === location.pathname) ?? (location.pathname === '/app/account' ? { label: 'shell.account' as MessageKey } : null);
  const days = trialDaysLeft(overview.trial_ends_at);
  const email = session?.user.email ?? '';
  const initial = (email[0] ?? '?').toUpperCase();

  return (
    <div className="app-shell">
      <a className="skip-link" href="#app-main">{t('nav.skip')}</a>
      {open && <div className="sidebar-scrim" onClick={() => setOpen(false)} />}
      <aside className="sidebar" data-open={open} aria-label={t('shell.menu')}>
        <Brand to="/app" />
        <div className="sidebar-label">{t('shell.workspace')}</div>
        <div className="company-chip">
          <strong data-testid="company-name">{overview.name}</strong>
          <span>{overview.plan[0].toUpperCase() + overview.plan.slice(1)} · {t(`role.${role}` as MessageKey)}</span>
        </div>
        <nav className="app-nav">
          {items.filter((item) => item.show).map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === '/app'}>
              <Icon name={item.icon} />
              <span>{t(item.label)}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom app-nav">
          <NavLink to="/app/account"><Icon name="user" /><span>{t('shell.account')}</span></NavLink>
          <a href="/" onClick={async (event) => {
            event.preventDefault();
            await signOut();
            navigate('/');
          }}><Icon name="logout" /><span>{t('auth.signOut')}</span></a>
        </div>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <button type="button" className="button-quiet menu-toggle" aria-label={t('shell.menu')} aria-expanded={open} onClick={() => setOpen(true)}>
            <Icon name="menu" />
          </button>
          <span className="topbar-title">{current ? t(current.label) : ''}</span>
          <div className="topbar-actions">
            <PreferencesMenu />
            <Link to="/app/account" className="avatar" aria-label={t('shell.account')} title={email}>{initial}</Link>
          </div>
        </header>
        {overview.status === 'trial' && (
          <div className="banner banner-trial" data-testid="trial-banner">
            <Icon name="info" />
            <span>{days <= 1 ? t('shell.trialLastDay') : t('shell.trialBanner', { days })}</span>
            <Link to="/app/billing" style={{ marginLeft: 'auto', fontWeight: 600 }}>{t('shell.billing')}</Link>
          </div>
        )}
        {overview.status === 'read_only' && <div className="banner banner-warning" role="alert"><Icon name="alert" /><span>{t('shell.readOnly')}</span></div>}
        {overview.status === 'past_due' && <div className="banner banner-warning" role="alert"><Icon name="alert" /><span>{t('shell.pastDue')}</span></div>}
        {terms.data && !terms.data.up_to_date && isManager(role) && (
          <div className="banner banner-warning" role="alert" data-testid="terms-banner">
            <Icon name="info" /><span>{t('terms.banner', { company: overview.name, version: terms.data.current_version })}</span>
            <Link to="/app/settings#terms" style={{ marginLeft: 'auto', fontWeight: 600 }}>{t('terms.review')}</Link>
          </div>
        )}
        <main id="app-main" className="app-content" lang={locale.slice(0, 2)}>
          <Outlet />
        </main>
        <footer className="app-footer" aria-label={t('footer.legal')}>
          <span>© {new Date().getFullYear()} Mixel IT and Corporate Services GmbH</span>
          <LegalLinks />
        </footer>
      </div>
    </div>
  );
}
