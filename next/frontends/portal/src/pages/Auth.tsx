import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { useI18n, type MessageKey } from '../i18n';
import { supabase } from '../lib/supabase';
import { api, type SignInOptions } from '../lib/api';
import { useAuth } from '../lib/auth';
import {
  checkPassword, errorKey, isCompanyName, isEmail, isName, normalizeEmail, safeNext, signInMethods, tokenFromHash, type Method,
} from '../lib/logic';
import { Brand, ErrorNotice, Notice, PreferencesMenu, Spinner } from '../components/ui';
import { EmailCodeForm, MfaChallenge, MixelIdButton, methodLabelKey, useAvailableMethods } from '../components/auth';
import { TermsCheckbox } from '../components/legal';
import { LEGAL_VERSION } from '../legal/types';

const PENDING_SIGNUP = 'mixel-remote-portal-pending-signup';
const PENDING_INVITE = 'mixel-remote-portal-pending-invite';

function session(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}
function setSession(key: string, value: string | null) {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    /* storage unavailable */
  }
}

export function AuthLayout({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  return (
    <>
      <a className="skip-link" href="#main">{t('nav.skip')}</a>
      <main id="main" className="auth-page">
        <div className="auth-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <Brand />
            <PreferencesMenu />
          </div>
          {children}
        </div>
      </main>
    </>
  );
}

// -------------------------------------------------------------------------------------------------
// Sign in: email first, then the methods the company allows (by verified domain or ?company=slug).

interface SignInFlowProps {
  next: string;
  /** Known company options (from ?company=slug). */
  initialOptions?: SignInOptions | null;
  onSignedIn: () => void;
  /** Hide the lookup and offer the standard methods (invitation page). */
  standardOnly?: boolean;
}

export function SignInFlow({ next, initialOptions = null, onSignedIn, standardOnly = false }: SignInFlowProps) {
  const { t } = useI18n();
  const available = useAvailableMethods();
  const [step, setStep] = useState<'email' | 'methods' | 'code'>('email');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [options, setOptions] = useState<SignInOptions | null>(initialOptions);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => setOptions(initialOptions), [initialOptions]);

  const methods = available ? signInMethods(options?.methods ?? null, available) : [];

  async function lookup(event: FormEvent) {
    event.preventDefault();
    if (!isEmail(email)) {
      setError({ message: 'Invalid email' });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (!standardOnly && !initialOptions) setOptions(await api.signInOptions(normalizeEmail(email)));
      setStep('methods');
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  async function withPassword(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const { error: failure } = await supabase.auth.signInWithPassword({ email: normalizeEmail(email), password });
    setBusy(false);
    if (failure) {
      setError(failure);
      return;
    }
    onSignedIn();
  }

  async function sendCode() {
    const { error: failure } = await supabase.auth.signInWithOtp({ email: normalizeEmail(email), options: { shouldCreateUser: false } });
    if (failure) throw failure;
  }

  if (step === 'code') {
    return <EmailCodeForm email={normalizeEmail(email)} type="email" onVerified={onSignedIn} onResend={sendCode} onBack={() => setStep('methods')} />;
  }

  if (step === 'email') {
    return (
      <form onSubmit={lookup} noValidate>
        {options && <Notice kind="brand"><p>{t('signin.companyLead', { company: options.company })}</p></Notice>}
        <div className="field">
          <label htmlFor="signin-email">{t('common.email')}</label>
          <input id="signin-email" type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} autoFocus required />
        </div>
        {error ? <p className="field-error" role="alert">{t('auth.emailInvalid')}</p> : null}
        <button type="submit" className="button-block" disabled={busy}>{t('signin.next')}</button>
      </form>
    );
  }

  return (
    <div data-testid="signin-methods">
      <div className="email-chip">
        <span>{normalizeEmail(email)}</span>
        <button type="button" className="button-quiet button-small" onClick={() => { setStep('email'); setError(null); }}>{t('signin.changeEmail')}</button>
      </div>
      {options && <Notice kind="brand"><p>{t('signin.companyLead', { company: options.company })}{options.require_mfa ? ' ' + t('signin.companyMfa', { company: options.company }) : ''}</p></Notice>}
      {!available && <Spinner />}
      {available && methods.length === 0 && <Notice kind="warning"><p>{t('signin.noMethods')}</p></Notice>}
      {methods.includes('password') && (
        <form onSubmit={withPassword} noValidate>
          <div className="field">
            <label htmlFor="signin-password">{t('common.password')}</label>
            <input id="signin-password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} autoFocus required />
          </div>
          <ErrorNotice error={error} />
          <button type="submit" className="button-block" disabled={busy || !password}>{t('signin.submit')}</button>
        </form>
      )}
      {methods.includes('password') && (methods.includes('magic_link') || methods.includes('mixel_id')) && <div className="divider">{t('common.or')}</div>}
      <div className="stack">
        {methods.includes('magic_link') && (
          <button type="button" className="secondary button-block" disabled={busy} data-testid="send-code"
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await sendCode();
                setStep('code');
              } catch (failure) {
                setError(failure);
              } finally {
                setBusy(false);
              }
            }}>
            {t('auth.sendCode')}
          </button>
        )}
        {methods.includes('mixel_id') && <MixelIdButton next={next} />}
        {!methods.includes('password') && <ErrorNotice error={error} />}
      </div>
    </div>
  );
}

export function SignIn() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { session: current, needsSecondStep, refreshAssurance, signOut, loading } = useAuth();
  const next = safeNext(params.get('next'));
  const slug = params.get('company');
  const [options, setOptions] = useState<SignInOptions | null>(null);
  const [mfa, setMfa] = useState(false);

  useEffect(() => {
    if (!slug) return;
    api.signInOptions(slug).then(setOptions).catch(() => setOptions(null));
  }, [slug]);

  const signedIn = useCallback(async () => {
    const level = await refreshAssurance();
    if (level.current === 'aal1' && level.next === 'aal2') setMfa(true);
    else navigate(next, { replace: true });
  }, [navigate, next, refreshAssurance]);

  if (loading) return <AuthLayout><Spinner /></AuthLayout>;
  if (current && (mfa || needsSecondStep)) {
    return (
      <AuthLayout>
        <MfaChallenge onDone={() => navigate(next, { replace: true })} onCancel={() => void signOut()} />
      </AuthLayout>
    );
  }
  if (current) return <Navigate to={next} replace />;

  return (
    <AuthLayout>
      <h1>{t('signin.title')}</h1>
      <p className="lead">{options ? t('signin.companyLead', { company: options.company }) : t('signin.lead')}</p>
      <SignInFlow next={next} initialOptions={options} onSignedIn={signedIn} />
      <p className="auth-footer">{t('signin.noAccount')} <Link to="/signup">{t('signin.createOne')}</Link></p>
    </AuthLayout>
  );
}

// -------------------------------------------------------------------------------------------------
// Sign up: company, account and the first sign-in method. Then remote_saas_signup (14-day trial).

export function SignUp() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const available = useAvailableMethods();
  const { session: current, loading } = useAuth();
  const [company, setCompany] = useState(() => JSON.parse(session(PENDING_SIGNUP) ?? '{}').company ?? '');
  const [accepted, setAccepted] = useState(false);
  const [termsError, setTermsError] = useState(false);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [method, setMethod] = useState<Method>('password');
  const [step, setStep] = useState<'form' | 'code' | 'creating'>('form');
  const [codeType, setCodeType] = useState<'email' | 'signup'>('signup');
  const [errors, setErrors] = useState<Partial<Record<'company' | 'name' | 'email' | 'password', MessageKey>>>({});
  const [error, setError] = useState<unknown>(null);
  const [exists, setExists] = useState(false);
  const [busy, setBusy] = useState(false);

  const createCompany = useCallback(async () => {
    setStep('creating');
    setError(null);
    try {
      await api.signup(company.trim(), LEGAL_VERSION);
      setSession(PENDING_SIGNUP, null);
      navigate('/app?welcome=1', { replace: true });
    } catch (failure) {
      setError(failure);
      setStep('form');
    }
  }, [company, navigate]);

  if (loading) return <AuthLayout><Spinner /></AuthLayout>;
  if (current && step === 'form') return <Navigate to="/signup/finish" replace />;

  const methods: Method[] = (['password', 'magic_link', 'mixel_id'] as Method[]).filter((item) => available?.has(item) ?? item !== 'mixel_id');

  function validate(): boolean {
    const next: typeof errors = {};
    if (!isCompanyName(company)) next.company = 'signup.companyInvalid';
    if (method !== 'mixel_id') {
      if (!isName(name, 80)) next.name = 'signup.nameInvalid';
      if (!isEmail(email)) next.email = 'auth.emailInvalid';
    }
    if (method === 'password') {
      const check = checkPassword(password);
      if (check.tooShort) next.password = 'auth.passwordShort';
      else if (check.needsVariety) next.password = 'auth.passwordVariety';
    }
    setErrors(next);
    setTermsError(!accepted);
    return Object.keys(next).length === 0 && accepted;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setExists(false);
    setError(null);
    if (!validate()) return;
    setBusy(true);
    try {
      const cleanEmail = normalizeEmail(email);
      if (method === 'password') {
        const { data, error: failure } = await supabase.auth.signUp({
          email: cleanEmail, password, options: { data: { name: name.trim() }, emailRedirectTo: `${window.location.origin}/signup/finish` },
        });
        if (failure) throw failure;
        if (data.user && (data.user.identities ?? []).length === 0) {
          setExists(true);
          return;
        }
        if (data.session) {
          await createCompany();
          return;
        }
        setCodeType('signup');
        setStep('code');
      } else if (method === 'magic_link') {
        const { error: failure } = await supabase.auth.signInWithOtp({ email: cleanEmail, options: { shouldCreateUser: true, data: { name: name.trim() } } });
        if (failure) throw failure;
        setCodeType('email');
        setStep('code');
      }
    } catch (failure) {
      if (errorKey(failure) === 'error.email_in_use') setExists(true);
      else setError(failure);
    } finally {
      setBusy(false);
    }
  }

  if (step === 'creating') return <AuthLayout><Spinner label={t('signup.creating')} /><p className="field-hint" style={{ textAlign: 'center' }}>{t('signup.creating')}</p></AuthLayout>;

  if (step === 'code') {
    return (
      <AuthLayout>
        <h1>{t('signup.title')}</h1>
        <p className="lead">{company.trim()}</p>
        <EmailCodeForm
          email={normalizeEmail(email)}
          type={codeType}
          onVerified={() => void createCompany()}
          onResend={async () => {
            const { error: failure } = codeType === 'signup'
              ? await supabase.auth.resend({ type: 'signup', email: normalizeEmail(email) })
              : await supabase.auth.signInWithOtp({ email: normalizeEmail(email), options: { shouldCreateUser: true } });
            if (failure) throw failure;
          }}
          onBack={() => setStep('form')}
        />
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <h1>{t('signup.title')}</h1>
      <p className="lead">{t('signup.lead')}</p>
      <form onSubmit={submit} noValidate data-testid="signup-form">
        <div className="field">
          <label htmlFor="signup-company">{t('signup.company')}</label>
          <input id="signup-company" autoComplete="organization" value={company} maxLength={80} onChange={(event) => setCompany(event.target.value)} autoFocus />
          {errors.company && <p className="field-error">{t(errors.company)}</p>}
        </div>
        <fieldset className="method-choice" style={{ border: 0, padding: 0 }}>
          <legend className="field-hint" style={{ marginBottom: 8, fontWeight: 560, color: 'var(--text-soft)', fontSize: 13.5 }}>{t('signup.method')}</legend>
          {methods.map((item) => (
            <label key={item}>
              <input type="radio" name="method" value={item} checked={method === item} onChange={() => setMethod(item)} />
              {t(methodLabelKey(item))}
            </label>
          ))}
        </fieldset>
        {method !== 'mixel_id' && (
          <>
            <div className="field">
              <label htmlFor="signup-name">{t('signup.yourName')}</label>
              <input id="signup-name" autoComplete="name" value={name} maxLength={80} onChange={(event) => setName(event.target.value)} />
              {errors.name && <p className="field-error">{t(errors.name)}</p>}
            </div>
            <div className="field">
              <label htmlFor="signup-email">{t('signup.workEmail')}</label>
              <input id="signup-email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} />
              {errors.email && <p className="field-error">{t(errors.email)}</p>}
            </div>
          </>
        )}
        {method === 'password' && (
          <div className="field">
            <label htmlFor="signup-password">{t('common.password')}</label>
            <input id="signup-password" type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} />
            {errors.password ? <p className="field-error">{t(errors.password)}</p> : <p className="field-hint">{t('auth.passwordHint')}</p>}
          </div>
        )}
        <TermsCheckbox checked={accepted} onChange={(value) => { setAccepted(value); if (value) setTermsError(false); }} error={termsError} />
        {exists && (
          <Notice kind="warning">
            <p>{t('signup.exists')} <Link to="/signin?next=%2Fsignup%2Ffinish">{t('nav.signIn')}</Link></p>
          </Notice>
        )}
        <ErrorNotice error={error} />
        {method === 'mixel_id' ? (
          <>
            <MixelIdButton next="/signup/finish" onBefore={() => {
              if (!validate()) return false;
              setSession(PENDING_SIGNUP, JSON.stringify({ company: company.trim(), accepted: true }));
              return true;
            }} />
            <p className="field-hint" style={{ marginTop: 8 }}>{t('signup.mixelIdNote')}</p>
          </>
        ) : (
          <button type="submit" className="button-block" disabled={busy}>{t('signup.create')}</button>
        )}
      </form>
      <p className="auth-footer">{t('signup.haveAccount')} <Link to="/signin">{t('nav.signIn')}</Link></p>
    </AuthLayout>
  );
}

/** Signed in without a company (Mixel ID sign-up, or an existing account): name the company. */
export function SignupFinish() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { session: current, loading, needsSecondStep, signOut } = useAuth();
  const [company, setCompany] = useState(() => JSON.parse(session(PENDING_SIGNUP) ?? '{}').company ?? '');
  const [accepted, setAccepted] = useState(() => JSON.parse(session(PENDING_SIGNUP) ?? '{}').accepted === true);
  const [termsError, setTermsError] = useState(false);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [invalid, setInvalid] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!current || needsSecondStep) return;
    api.myTenants()
      .then((tenants) => (tenants.length ? navigate('/app', { replace: true }) : setChecking(false)))
      .catch(() => setChecking(false));
  }, [current, needsSecondStep, navigate]);

  if (loading) return <AuthLayout><Spinner /></AuthLayout>;
  if (!current) return <Navigate to="/signup" replace />;
  if (needsSecondStep) return <AuthLayout><MfaChallenge onDone={() => undefined} onCancel={() => void signOut()} /></AuthLayout>;
  if (checking) return <AuthLayout><Spinner /></AuthLayout>;

  return (
    <AuthLayout>
      <h1>{t('signup.finishTitle')}</h1>
      <p className="lead">{t('signup.finishLead', { email: current.user.email ?? '' })}</p>
      <form noValidate onSubmit={async (event) => {
        event.preventDefault();
        setTermsError(!accepted);
        if (!isCompanyName(company) || !accepted) {
          setInvalid(!isCompanyName(company));
          return;
        }
        setInvalid(false);
        setBusy(true);
        setError(null);
        try {
          await api.signup(company.trim(), LEGAL_VERSION);
          setSession(PENDING_SIGNUP, null);
          navigate('/app?welcome=1', { replace: true });
        } catch (failure) {
          setError(failure);
          setBusy(false);
        }
      }}>
        <div className="field">
          <label htmlFor="finish-company">{t('signup.company')}</label>
          <input id="finish-company" autoComplete="organization" value={company} maxLength={80} onChange={(event) => setCompany(event.target.value)} autoFocus />
          {invalid && <p className="field-error">{t('signup.companyInvalid')}</p>}
        </div>
        <TermsCheckbox checked={accepted} onChange={(value) => { setAccepted(value); if (value) setTermsError(false); }} error={termsError} />
        <ErrorNotice error={error} />
        <button type="submit" className="button-block" disabled={busy}>{t('signup.finishCreate')}</button>
      </form>
      <p className="field-hint" style={{ marginTop: 16 }}>{t('signup.orJoin')}</p>
      <p className="auth-footer"><button type="button" className="button-quiet" onClick={() => void signOut()}>{t('auth.signOut')}</button></p>
    </AuthLayout>
  );
}

/** OAuth return (PKCE): supabase-js exchanges the code on load; then continue. */
export function AuthCallback() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const next = safeNext(params.get('next'));
  const failed = params.get('error_description') ?? params.get('error');
  const { session: current, loading } = useAuth();

  useEffect(() => {
    if (failed || loading) return;
    if (current) {
      navigate(next, { replace: true });
      return;
    }
    // The code exchange may still be running; give it a moment before falling back.
    const timer = window.setTimeout(async () => {
      const { data } = await supabase.auth.getSession();
      navigate(data.session ? next : '/signin', { replace: true });
    }, 2500);
    return () => window.clearTimeout(timer);
  }, [current, loading, failed, navigate, next]);

  return (
    <AuthLayout>
      {failed ? (
        <>
          <ErrorNotice error={{ message: failed }} />
          <Link className="button button-block" to="/signin">{t('nav.signIn')}</Link>
        </>
      ) : <Spinner />}
    </AuthLayout>
  );
}

// -------------------------------------------------------------------------------------------------
// Invitation: /invite#token=… (the secret stays in the fragment, never sent to a server).

function CreateAccount({ onSignedIn }: { onSignedIn: () => void }) {
  const { t } = useI18n();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [step, setStep] = useState<'form' | 'code'>('form');
  const [problem, setProblem] = useState<MessageKey | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (step === 'code') {
    return <EmailCodeForm email={normalizeEmail(email)} type="signup" onVerified={onSignedIn}
      onResend={async () => {
        const { error: failure } = await supabase.auth.resend({ type: 'signup', email: normalizeEmail(email) });
        if (failure) throw failure;
      }}
      onBack={() => setStep('form')} />;
  }
  return (
    <form noValidate onSubmit={async (event) => {
      event.preventDefault();
      const check = checkPassword(password);
      const issue: MessageKey | null = !isName(name, 80) ? 'signup.nameInvalid' : !isEmail(email) ? 'auth.emailInvalid'
        : check.tooShort ? 'auth.passwordShort' : check.needsVariety ? 'auth.passwordVariety' : null;
      setProblem(issue);
      if (issue) return;
      setBusy(true);
      setError(null);
      const { data, error: failure } = await supabase.auth.signUp({ email: normalizeEmail(email), password, options: { data: { name: name.trim() } } });
      setBusy(false);
      if (failure) {
        setError(failure);
        return;
      }
      if (data.user && (data.user.identities ?? []).length === 0) {
        setError({ message: 'already registered' });
        return;
      }
      if (data.session) onSignedIn();
      else setStep('code');
    }}>
      <div className="field">
        <label htmlFor="invite-name">{t('signup.yourName')}</label>
        <input id="invite-name" autoComplete="name" value={name} onChange={(event) => setName(event.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="invite-email">{t('common.email')}</label>
        <input id="invite-email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="invite-password">{t('common.password')}</label>
        <input id="invite-password" type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} />
        <p className="field-hint">{t('auth.passwordHint')}</p>
      </div>
      {problem && <p className="field-error" role="alert">{t(problem)}</p>}
      <ErrorNotice error={error} />
      <button type="submit" className="button-block" disabled={busy}>{t('signup.create')}</button>
    </form>
  );
}

export function Invite() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();
  const { session: current, loading, needsSecondStep, refreshAssurance, signOut } = useAuth();
  const [token] = useState<string | null>(() => {
    const fromHash = tokenFromHash(location.hash);
    if (fromHash) {
      setSession(PENDING_INVITE, fromHash);
      // Keep the secret out of the address bar and history.
      window.history.replaceState(null, '', '/invite');
      return fromHash;
    }
    return session(PENDING_INVITE);
  });
  const [mode, setMode] = useState<'signin' | 'create'>('signin');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (loading) return <AuthLayout><Spinner /></AuthLayout>;
  if (!token) {
    return (
      <AuthLayout>
        <h1>{t('invite.title')}</h1>
        <Notice kind="warning"><p>{t('invite.missing')}</p></Notice>
      </AuthLayout>
    );
  }
  if (current && needsSecondStep) {
    return <AuthLayout><MfaChallenge onDone={() => undefined} onCancel={() => void signOut()} /></AuthLayout>;
  }
  if (current) {
    return (
      <AuthLayout>
        <h1>{t('invite.title')}</h1>
        <p className="lead">{t('invite.signedInAs', { email: current.user.email ?? '' })}</p>
        <ErrorNotice error={error} />
        <div className="stack">
          <button type="button" className="button-block" disabled={busy} data-testid="accept-invite" onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              await api.acceptInvite(token);
              setSession(PENDING_INVITE, null);
              navigate('/app?joined=1', { replace: true });
            } catch (failure) {
              setError(failure);
              setBusy(false);
            }
          }}>{busy ? t('invite.accepting') : t('invite.accept')}</button>
          <button type="button" className="button-quiet" onClick={() => void signOut()}>{t('invite.wrongAccount')}</button>
        </div>
      </AuthLayout>
    );
  }
  return (
    <AuthLayout>
      <h1>{t('invite.title')}</h1>
      <p className="lead">{t('invite.lead')}</p>
      {mode === 'signin' ? (
        <SignInFlow next="/invite" standardOnly onSignedIn={() => void refreshAssurance()} />
      ) : (
        <CreateAccount onSignedIn={() => void refreshAssurance()} />
      )}
      <p className="auth-footer">
        <button type="button" className="button-quiet" onClick={() => setMode(mode === 'signin' ? 'create' : 'signin')}>
          {mode === 'signin' ? t('invite.createAccount') : t('invite.haveAccount')}
        </button>
      </p>
    </AuthLayout>
  );
}
