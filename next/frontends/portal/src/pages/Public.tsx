import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useI18n, type MessageKey } from '../i18n';
import { EVERY_PLAN, EXTRA_TECHNICIAN_CHF, PLANS, formatChf, shownMonthly, type Billing, type Plan, type PriceRow } from '../lib/plans';
import { billingApi } from '../lib/api';
import { Icon, type IconName } from '../components/Icon';
import { PublicLayout, STORE_URL } from '../components/PublicLayout';

function limitText(value: number | null, locale: string, custom: string): string {
  return value === null ? custom : new Intl.NumberFormat(locale).format(value);
}

function PlanCard({ plan, billing, cta = true, prices }: { plan: Plan; billing: Billing; cta?: boolean; prices: PriceRow[] | null }) {
  const { t, locale } = useI18n();
  const price = shownMonthly(plan, billing, prices);
  const featured = plan.code === 'business';
  return (
    <article className={`plan${featured ? ' plan-featured' : ''}`} data-plan={plan.code}>
      {featured && <span className="plan-tag">{t('pricing.popular')}</span>}
      <div>
        <h3>{plan.name}</h3>
        <div className="plan-price" style={{ marginTop: 10 }}>
          {price === null ? t('common.onRequest') : price === 0 ? t('common.free') : (
            <>
              <small>{t('common.chf')} </small>{formatChf(price, locale.slice(0, 2))}<small> {t('common.perMonth')}</small>
            </>
          )}
        </div>
        <p className="plan-price-sub">{price !== null && price > 0 ? t(billing === 'yearly' ? 'pricing.yearly' : 'pricing.monthly') : ' '}</p>
      </div>
      <dl>
        <div><dt>{t('pricing.technicians')}</dt><dd>{limitText(plan.technicians, locale, t('common.unlimited'))}</dd></div>
        <div><dt>{t('pricing.devices')}</dt><dd>{limitText(plan.devices, locale, t('common.unlimited'))}</dd></div>
        <div><dt>{t('pricing.sessions')}</dt><dd>{limitText(plan.sessions, locale, t('common.unlimited'))}</dd></div>
      </dl>
      <ul>
        {(plan.code === 'enterprise' ? ['sso', 'audit_log', 'policies', 'require_mfa'] : plan.extras).map((extra) => (
          <li key={extra}><Icon name="check" />{t(`feature.${extra}` as MessageKey)}</li>
        ))}
      </ul>
      {cta && (plan.code === 'personal' ? (
        <a className="button button-secondary" href={STORE_URL}>{t('pricing.personalCta')}</a>
      ) : plan.code === 'enterprise' ? (
        <a className="button button-secondary" href="https://mixel.ch/kontakt">{t('pricing.contact')}</a>
      ) : (
        <Link className={`button${featured ? '' : ' button-secondary'}`} to="/signup">{t('pricing.choose')}</Link>
      ))}
    </article>
  );
}

export function PlanTable({ cta = true }: { cta?: boolean }) {
  const { t } = useI18n();
  const [billing, setBilling] = useState<Billing>('yearly');
  const [prices, setPrices] = useState<PriceRow[] | null>(null);
  useEffect(() => {
    billingApi.prices().then((rows) => setPrices(rows && rows.length ? rows : null)).catch(() => setPrices(null));
  }, []);
  return (
    <div>
      <div className="billing-toggle" role="group" aria-label={t('nav.pricing')}>
        <button type="button" aria-pressed={billing === 'yearly'} onClick={() => setBilling('yearly')}>{t('pricing.yearly')}</button>
        <button type="button" aria-pressed={billing === 'monthly'} onClick={() => setBilling('monthly')}>{t('pricing.monthly')}</button>
      </div>
      <p className="field-hint">{t('pricing.monthlyNote')}</p>
      <div className="plan-grid">
        {PLANS.map((plan) => <PlanCard key={plan.code} plan={plan} billing={billing} cta={cta} prices={prices} />)}
      </div>
      <div className="pricing-notes">
        <div>{t('pricing.included')}:</div>
        <div className="included">
          {EVERY_PLAN.map((feature) => <span key={feature} className="badge"><Icon name="check" />{t(`feature.${feature}` as MessageKey)}</span>)}
        </div>
        <p>{t('pricing.extraTechnician', { price: billing === 'yearly' ? EXTRA_TECHNICIAN_CHF : '9.60' })}</p>
        <p>{t('pricing.trial')}</p>
        <p>{t('pricing.personalNote')}</p>
      </div>
    </div>
  );
}

const FEATURES: { icon: IconName; title: MessageKey; text: MessageKey }[] = [
  { icon: 'zap', title: 'landing.f1.title', text: 'landing.f1.text' },
  { icon: 'monitor', title: 'landing.f2.title', text: 'landing.f2.text' },
  { icon: 'key', title: 'landing.f3.title', text: 'landing.f3.text' },
  { icon: 'shield', title: 'landing.f4.title', text: 'landing.f4.text' },
  { icon: 'layers', title: 'landing.f5.title', text: 'landing.f5.text' },
  { icon: 'flag', title: 'landing.f6.title', text: 'landing.f6.text' },
];

function useHashScroll() {
  const location = useLocation();
  useEffect(() => {
    if (!location.hash) {
      window.scrollTo(0, 0);
      return;
    }
    document.getElementById(location.hash.slice(1))?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [location]);
}

export function Landing() {
  const { t } = useI18n();
  useHashScroll();
  return (
    <PublicLayout>
      <section className="hero">
        <div className="container hero-grid">
          <div>
            <p className="eyebrow">{t('landing.eyebrow')}</p>
            <h1>{t('landing.title')}</h1>
            <p className="lead">{t('landing.lead')}</p>
            <div className="hero-actions">
              <Link className="button button-large" to="/signup">{t('landing.ctaTrial')}<Icon name="arrow" /></Link>
              <Link className="button button-secondary button-large" to="/pricing">{t('landing.ctaPricing')}</Link>
            </div>
            <p className="hero-note">{t('landing.trialNote')}</p>
          </div>
          <div className="hero-card" aria-hidden="true">
            <div className="mock-heading">
              <strong>{t('shell.devices')}</strong>
              <span className="badge badge-success"><span className="dot" />Business</span>
            </div>
            {[['Reception PC', 'Windows', '482 913 307'], ['MacBook — Design', 'macOS', '771 204 958'], ['Server room', 'Linux', '305 662 841'], ['Workshop laptop', 'Windows', '913 045 226']].map(([name, os, id]) => (
              <div key={id} className="mock-row">
                <Icon name="monitor" />
                <div><div style={{ fontWeight: 600 }}>{name}</div><div className="field-hint">{os}</div></div>
                <span className="mock-id">{id}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="section" id="features">
        <div className="container">
          <div className="feature-grid">
            {FEATURES.map((feature) => (
              <article key={feature.title} className="feature">
                <div className="feature-icon"><Icon name={feature.icon} /></div>
                <h3>{t(feature.title)}</h3>
                <p>{t(feature.text)}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="section section-alt">
        <div className="container">
          <div className="section-heading"><h2>{t('landing.how.title')}</h2></div>
          <ol className="steps">
            <li>{t('landing.how.1')}</li>
            <li>{t('landing.how.2')}</li>
            <li>{t('landing.how.3')}</li>
          </ol>
        </div>
      </section>

      <section className="section" id="pricing">
        <div className="container">
          <div className="section-heading">
            <h2>{t('pricing.title')}</h2>
            <p>{t('pricing.lead')}</p>
          </div>
          <PlanTable />
        </div>
      </section>

      <section className="section section-alt" id="download">
        <div className="container">
          <div className="section-heading">
            <h2>{t('landing.download.title')}</h2>
            <p>{t('landing.download.text')}</p>
          </div>
          <a className="button button-large" href={STORE_URL}><Icon name="download" />{t('landing.download.cta')}</a>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="cta-band">
            <div>
              <h2>{t('signup.title')}</h2>
              <p>{t('landing.trialNote')}</p>
            </div>
            <Link className="button button-large" to="/signup">{t('landing.ctaTrial')}</Link>
          </div>
        </div>
      </section>
    </PublicLayout>
  );
}

export function Pricing() {
  const { t } = useI18n();
  useHashScroll();
  return (
    <PublicLayout>
      <section className="section">
        <div className="container">
          <div className="section-heading">
            <h1>{t('pricing.title')}</h1>
            <p>{t('pricing.lead')}</p>
          </div>
          <PlanTable />
        </div>
      </section>
    </PublicLayout>
  );
}

export function Download() {
  const { t } = useI18n();
  useHashScroll();
  return (
    <PublicLayout>
      <section className="section">
        <div className="container">
          <div className="section-heading">
            <h1>{t('download.title')}</h1>
            <p>{t('download.lead')}</p>
          </div>
          <div className="grid-2">
            <div className="panel">
              <div className="feature-icon"><Icon name="download" /></div>
              <h2>Mixel Remote</h2>
              <p className="field-hint">{t('download.platforms')}</p>
              <a className="button button-large" href={STORE_URL} style={{ marginTop: 16 }}>
                <Icon name="download" />{t('download.store')}
              </a>
            </div>
            <div className="panel">
              <h2>{t('download.afterTitle')}</h2>
              <ul className="ol-steps">
                <li>{t('download.after1')}</li>
                <li>{t('download.after2')}</li>
              </ul>
              <p style={{ marginTop: 16 }}><Link to="/signup">{t('landing.ctaTrial')} →</Link></p>
            </div>
          </div>
        </div>
      </section>
    </PublicLayout>
  );
}

export function NotFound() {
  const { t } = useI18n();
  return (
    <PublicLayout>
      <section className="section">
        <div className="container empty">
          <Icon name="search" />
          <h1>{t('notFound.title')}</h1>
          <p>{t('notFound.text')}</p>
          <Link className="button" to="/">{t('notFound.home')}</Link>
        </div>
      </section>
    </PublicLayout>
  );
}
