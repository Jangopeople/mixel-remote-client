import { useEffect, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useI18n, type MessageKey } from '../i18n';
import { useTenant } from '../components/AppShell';
import { ConfirmDialog, ErrorNotice, Meter, Notice, Spinner, formatDate } from '../components/ui';
import { MfaSetup } from '../components/MfaSetup';
import { Icon } from '../components/Icon';
import { PlanTable } from './Public';
import { statusBadge } from './Dashboard';
import { useAuth } from '../lib/auth';
import { supabase } from '../lib/supabase';
import { formatMoney, periodTotal, planByCode, type PriceRow } from '../lib/plans';
import { billingApi, type BillingState } from '../lib/api';
import { isManager } from '../lib/logic';
import { useLoad } from '../lib/useLoad';
import { api } from '../lib/api';
import { DeleteMyAccount } from './DataDeletion';

type Interval = 'year' | 'month';
type PaidPlan = 'solo' | 'team' | 'business';

function PlanChooser({ state, prices, onDone }: { state: BillingState; prices: PriceRow[]; onDone: () => void }) {
  const { t, locale } = useI18n();
  const { tenant } = useTenant();
  const subscribed = state.has_subscription;
  const [plan, setPlan] = useState<PaidPlan>((['solo', 'team', 'business'].includes(state.plan) ? state.plan : 'business') as PaidPlan);
  const [interval, setInterval] = useState<Interval>(state.billing_interval ?? 'year');
  const [extra, setExtra] = useState(state.extra_technicians ?? 0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [changed, setChanged] = useState(false);
  const total = periodTotal(prices, plan, interval, extra);
  const included = planByCode(plan)?.technicians ?? 0;
  const per = t(`billing.per.${interval}` as MessageKey);
  const trialEnd = state.status === 'trial' && state.trial_ends_at && Date.parse(state.trial_ends_at) - Date.now() > 49 * 3600 * 1000
    ? formatDate(state.trial_ends_at, locale) : null;
  const seat = prices.find((row) => row.kind === 'seat' && row.billing_interval === interval);
  const lang = locale.slice(0, 2);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setChanged(false);
    try {
      const choice = { plan, interval, extra_technicians: extra };
      if (subscribed) {
        await billingApi.change(tenant.tenant_id, choice);
        setChanged(true);
        setBusy(false);
        onDone();
      } else {
        const session = await billingApi.checkout(tenant.tenant_id, choice);
        window.location.assign(session.url);
      }
    } catch (failure) {
      setError(failure);
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit} noValidate data-testid="plan-chooser">
      <div className="panel-heading">
        <div><h2>{subscribed ? t('billing.change') : t('billing.choosePlan')}</h2><p>{subscribed ? t('billing.changeText') : t('billing.vat')}</p></div>
        <div className="billing-toggle" role="group" aria-label={t('billing.subscription')}>
          {(['year', 'month'] as Interval[]).map((value) => (
            <button key={value} type="button" aria-pressed={interval === value} onClick={() => setInterval(value)}>{t(`billing.interval.${value}` as MessageKey)}</button>
          ))}
        </div>
      </div>
      <div className="grid-3">
        {(['solo', 'team', 'business'] as PaidPlan[]).map((code) => {
          const row = periodTotal(prices, code, interval, 0);
          const info = planByCode(code)!;
          return (
            <label key={code} className={`plan${plan === code ? ' plan-featured' : ''}`} style={{ cursor: 'pointer' }} data-choice={code}>
              <span style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <input type="radio" name="plan" checked={plan === code} onChange={() => { setPlan(code); setError(null); }} />
                <strong style={{ fontSize: 16, color: 'var(--text)' }}>{info.name}</strong>
              </span>
              <span className="plan-price" style={{ fontSize: 22 }}>{row ? formatMoney(row.total, lang) : '—'}<small> / {per}</small></span>
              <span className="field-hint">{t('pricing.technicians')}: {info.technicians} · {t('pricing.devices')}: {new Intl.NumberFormat(locale).format(info.devices ?? 0)} · {t('pricing.sessions')}: {info.sessions}</span>
            </label>
          );
        })}
      </div>
      <div className="field" style={{ maxWidth: 260, marginTop: 18 }}>
        <label htmlFor="extra-technicians">{t('billing.extra')}</label>
        <input id="extra-technicians" type="number" min={0} max={1000} value={extra} onChange={(event) => setExtra(Math.max(0, Math.min(1000, Number(event.target.value) || 0)))} />
        {seat && <p className="field-hint">{t('billing.extraHint', { included, price: formatMoney(Number(seat.amount_chf), lang), interval: per })}</p>}
      </div>
      {total && (
        <p data-testid="billing-summary" style={{ fontWeight: 600 }}>
          {!subscribed && trialEnd
            ? t('billing.summaryTrial', { date: trialEnd, amount: formatMoney(total.total, lang), interval: per })
            : t('billing.summaryNow', { amount: formatMoney(total.total, lang), interval: per })}
        </p>
      )}
      <ErrorNotice error={error} />
      {changed && <Notice kind="success"><p>{t('billing.changed')}</p></Notice>}
      <button type="submit" disabled={busy || !total} data-testid="billing-submit">
        <Icon name={subscribed ? 'check' : 'lock'} />{busy && !subscribed ? t('billing.redirecting') : subscribed ? t('billing.changeSubmit') : t('billing.checkout')}
      </button>
      {!subscribed && <p className="field-hint" style={{ marginTop: 10 }}>{t('billing.stripeNote')}</p>}
      <p className="field-hint">{t('billing.enterprise')} <a href="https://mixel.ch/kontakt">{t('billing.contact')}</a></p>
    </form>
  );
}

export function Billing() {
  const { t, locale } = useI18n();
  const { tenant, overview, reload } = useTenant();
  const [params, setParams] = useSearchParams();
  const manager = isManager(overview.role);
  const plan = planByCode(overview.plan);
  const number = (value: number) => new Intl.NumberFormat(locale).format(value);
  const state = useLoad(() => (manager ? billingApi.state(tenant.tenant_id) : Promise.resolve(null)), `${tenant.tenant_id}|${manager}`);
  const prices = useLoad(async () => (await billingApi.prices()) ?? [], 'prices');
  const [portalBusy, setPortalBusy] = useState(false);
  const [portalError, setPortalError] = useState<unknown>(null);
  const result = params.get('billing');

  useEffect(() => {
    if (result !== 'success') return;
    // The webhook updates the plan a moment after Stripe returns here.
    const timers = [3000, 8000, 15000].map((delay) => window.setTimeout(() => { void reload(); void state.reload(); }, delay));
    return () => timers.forEach((timer) => window.clearTimeout(timer));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result]);

  const billing = state.data;
  return (
    <section>
      <div className="page-heading"><div><h1>{t('billing.title')}</h1><p>{t('billing.vat')}</p></div></div>
      {result === 'success' && (
        <div className="panel" data-testid="billing-success" style={{ borderColor: 'var(--success)' }}>
          <h2><Icon name="check" /> {t('billing.successTitle')}</h2><p>{t('billing.success')}</p>
          <button type="button" className="secondary button-small" onClick={() => setParams({}, { replace: true })}>{t('common.close')}</button>
        </div>
      )}
      {result === 'cancelled' && (
        <div className="panel" data-testid="billing-cancelled">
          <h2>{t('billing.cancelledTitle')}</h2><p>{t('billing.cancelled')}</p>
          <button type="button" className="secondary button-small" onClick={() => setParams({}, { replace: true })}>{t('common.close')}</button>
        </div>
      )}
      <div className="grid-2">
        <div className="panel">
          <div className="panel-heading">
            <div>
              <div className="stat-label">{t('billing.current')}</div>
              <h2 style={{ fontSize: 26, marginTop: 4 }} data-testid="billing-plan">{plan?.name ?? overview.plan}</h2>
            </div>
            <span className={statusBadge(overview.status)}><span className="dot" />{t(`status.${overview.status}` as MessageKey)}</span>
          </div>
          {overview.status === 'trial' && !billing?.has_subscription && <p>{t('billing.trialInfo', { date: formatDate(overview.trial_ends_at, locale) })}</p>}
          {billing && (
            <dl className="kv" style={{ marginTop: 12 }}>
              <dt>{t('billing.subscription')}</dt>
              <dd data-testid="billing-subscription">{billing.has_subscription
                ? `${t(`billing.interval.${billing.billing_interval ?? 'year'}` as MessageKey)} · ${billing.subscription_status ?? ''}`
                : t('billing.noSubscription')}</dd>
              {billing.current_period_end && !billing.cancel_at && <><dt /><dd>{t('billing.periodEnd', { date: formatDate(billing.current_period_end, locale) })}</dd></>}
              {billing.cancel_at && <><dt /><dd>{t('billing.cancelsAt', { date: formatDate(billing.cancel_at, locale) })}</dd></>}
              {billing.extra_technicians > 0 && <><dt>{t('billing.extra')}</dt><dd>{billing.extra_technicians}</dd></>}
            </dl>
          )}
          {billing?.devices_delete_after && <Notice kind="warning"><p>{t('billing.deleteAfter', { date: formatDate(billing.devices_delete_after, locale) })}</p></Notice>}
          {billing?.has_customer && (
            <div style={{ marginTop: 16 }}>
              <button type="button" className="secondary" disabled={portalBusy} data-testid="billing-portal" onClick={async () => {
                setPortalBusy(true);
                setPortalError(null);
                try {
                  window.location.assign((await billingApi.portal(tenant.tenant_id)).url);
                } catch (failure) {
                  setPortalError(failure);
                  setPortalBusy(false);
                }
              }}><Icon name="card" />{t('billing.portal')}</button>
              <p className="field-hint" style={{ marginTop: 8 }}>{t('billing.portalText')}</p>
              <ErrorNotice error={portalError} />
            </div>
          )}
          {!manager && <Notice><p>{t('billing.ownersOnly')}</p></Notice>}
          <ErrorNotice error={state.error} onRetry={() => void state.reload()} />
        </div>
        <div className="panel">
          <h2>{t('billing.limits')}</h2>
          <div className="check-list" style={{ marginTop: 10 }}>
            {([
              ['dash.technicians', overview.usage.technicians, overview.limits.technicians],
              ['dash.devices', overview.usage.devices, overview.limits.devices],
            ] as [MessageKey, number, number][]).map(([key, used, limit]) => (
              <div key={key} className="check-row" style={{ flexDirection: 'column', gap: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%' }}>
                  <strong>{t(key)}</strong><span>{t('dash.ofLimit', { used: number(used), limit: number(limit) })}</span>
                </div>
                <div style={{ width: '100%' }}><Meter used={used} limit={limit} label={t(key)} /></div>
              </div>
            ))}
            <div className="check-row"><div><strong>{t('dash.sessions')}</strong></div><span>{number(overview.limits.sessions)}</span></div>
          </div>
        </div>
      </div>
      {manager && billing && prices.data && prices.data.length > 0 && (
        <PlanChooser state={billing} prices={prices.data} onDone={() => { window.setTimeout(() => { void reload(); void state.reload(); }, 4000); }} />
      )}
      {manager && !billing && state.loading && <Spinner />}
      <div className="panel">
        <h2 style={{ marginBottom: 12 }}>{t('billing.compare')}</h2>
        <PlanTable cta={false} />
      </div>
    </section>
  );
}

export function Account() {
  const { t } = useI18n();
  const { session, assurance, refreshAssurance } = useAuth();
  const { tenant, overview } = useTenant();
  // The last owner cannot delete their account (the company needs an owner): say so up front.
  const owners = useLoad(async () => (overview.role === 'owner'
    ? (await api.members(tenant.tenant_id)).filter((member) => member.role === 'owner').length : 0), `${tenant.tenant_id}|${overview.role}`);
  const factors = useLoad(async () => {
    const { data, error } = await supabase.auth.mfa.listFactors();
    if (error) throw error;
    return data.totp.filter((factor) => factor.status === 'verified');
  }, session?.user.id ?? '');
  const [setup, setSetup] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const user = session?.user;
  const active = (factors.data ?? []).length > 0;
  return (
    <section>
      <div className="page-heading"><div><h1>{t('account.title')}</h1></div></div>
      <div className="panel">
        <h2>{t('account.profile')}</h2>
        <dl className="kv" style={{ marginTop: 12 }}>
          <dt>{t('common.name')}</dt><dd>{String(user?.user_metadata?.name ?? '') || '—'}</dd>
          <dt>{t('common.email')}</dt><dd data-testid="account-email">{user?.email}</dd>
        </dl>
      </div>
      <div className="panel" data-testid="account-mfa">
        <div className="panel-heading">
          <div><h2>{t('account.mfa')}</h2></div>
          {active && <span className="badge badge-success"><Icon name="shield" />{t('common.yes')}</span>}
        </div>
        {factors.loading && !factors.data ? <Spinner /> : active ? (
          <>
            <Notice kind="success"><p>{t('account.mfaOn')}</p></Notice>
            <button type="button" className="button-danger" disabled={assurance.current !== 'aal2'} onClick={() => setRemoving(factors.data![0].id)}>{t('account.mfaRemove')}</button>
            {assurance.current !== 'aal2' && <p className="field-hint">{t('account.mfaNeedsFresh')}</p>}
          </>
        ) : (
          <>
            <p className="field-hint">{t('account.mfaOff')}</p>
            {setup ? <MfaSetup onDone={() => { setSetup(false); void factors.reload(); }} />
              : <button type="button" onClick={() => setSetup(true)} data-testid="mfa-open">{t('account.mfaSetup')}</button>}
          </>
        )}
      </div>
      <div className="panel">
        <h2>{t('account.session')}</h2>
        <p className="field-hint">{assurance.current === 'aal2' ? t('account.sessionLevel') : t('account.sessionLevelBasic')}</p>
      </div>
      <DeleteMyAccount company={overview.name} soleOwner={overview.role === 'owner' && (owners.data ?? 1) <= 1} />
      {removing && (
        <ConfirmDialog title={t('account.mfaRemoveTitle')} text={t('account.mfaRemoveText')} confirmLabel={t('account.mfaRemove')}
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            const { error } = await supabase.auth.mfa.unenroll({ factorId: removing });
            if (error) throw error;
            await supabase.auth.refreshSession();
            await refreshAssurance();
            await factors.reload();
          }} />
      )}
    </section>
  );
}
