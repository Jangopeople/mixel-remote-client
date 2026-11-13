import { Link, useSearchParams } from 'react-router-dom';
import { useI18n, type MessageKey } from '../i18n';
import { useTenant } from '../components/AppShell';
import { Meter, Notice, formatDate } from '../components/ui';
import { Icon, type IconName } from '../components/Icon';
import { STORE_URL, WEB_VIEWER_URL } from '../components/PublicLayout';
import { canSeeTeam, isManager, trialDaysLeft } from '../lib/logic';
import { planByCode } from '../lib/plans';

export function statusBadge(status: string): string {
  if (status === 'active') return 'badge badge-success';
  if (status === 'trial') return 'badge badge-brand';
  return 'badge badge-warning';
}

export function Dashboard() {
  const { t, locale } = useI18n();
  const { overview } = useTenant();
  const [params] = useSearchParams();
  const days = trialDaysLeft(overview.trial_ends_at);
  const plan = planByCode(overview.plan);
  const number = (value: number) => new Intl.NumberFormat(locale).format(value);
  const stats: { key: MessageKey; used: number | null; limit: number }[] = [
    { key: 'dash.technicians', used: overview.usage.technicians, limit: overview.limits.technicians },
    { key: 'dash.devices', used: overview.usage.devices, limit: overview.limits.devices },
    { key: 'dash.members', used: overview.usage.members, limit: 0 },
    { key: 'dash.sessions', used: null, limit: overview.limits.sessions },
  ];
  const steps: { to: string; external?: boolean; icon: IconName; title: MessageKey; text: MessageKey; show: boolean }[] = [
    { to: '/app/devices?add=1', icon: 'monitor', title: 'dash.nextDevices', text: 'dash.nextDevicesText', show: true },
    { to: '/app/team', icon: 'users', title: 'dash.nextTeam', text: 'dash.nextTeamText', show: isManager(overview.role) },
    { to: '/app/settings', icon: 'shield', title: 'dash.nextSecurity', text: 'dash.nextSecurityText', show: isManager(overview.role) },
    { to: STORE_URL, external: true, icon: 'download', title: 'dash.nextApp', text: 'dash.nextAppText', show: true },
    { to: WEB_VIEWER_URL, external: true, icon: 'globe', title: 'viewer.open', text: overview.role === 'member' ? 'viewer.memberNote' : 'viewer.text', show: true },
  ];

  return (
    <section>
      <div className="page-heading">
        <div>
          <h1>{t('dash.welcome', { company: overview.name })}</h1>
          <p>{plan?.name ?? overview.plan} · {t(`role.${overview.role}` as MessageKey)}</p>
        </div>
      </div>
      {params.get('welcome') && <Notice kind="success"><p>{t('dash.trialLeft', { days })}</p></Notice>}
      {params.get('joined') && <Notice kind="success"><p>{t('invite.done', { company: overview.name })}</p></Notice>}

      <div className="grid-3">
        <div className="stat">
          <div className="stat-label">{t('dash.plan')}</div>
          <div className="stat-value" data-testid="plan-name">{plan?.name ?? overview.plan}</div>
          <span className={statusBadge(overview.status)} data-testid="plan-status"><span className="dot" />{t(`status.${overview.status}` as MessageKey)}</span>
        </div>
        {overview.status === 'trial' && (
          <div className="stat">
            <div className="stat-label">{t('status.trial')}</div>
            <div className="stat-value" data-testid="trial-days">{t('dash.trialLeft', { days })}</div>
            <span className="field-hint">{t('dash.trialEnds', { date: formatDate(overview.trial_ends_at, locale) })}</span>
          </div>
        )}
        <div className="stat">
          <div className="stat-label">{t('shell.billing')}</div>
          <p className="field-hint">{t('billing.dashText')}</p>
          <Link to="/app/billing">{t('billing.compare')} →</Link>
        </div>
      </div>

      <h2 style={{ margin: '28px 0 14px' }}>{t('dash.usage')}</h2>
      <div className="grid-4">
        {stats.map((stat) => (
          <div className="stat" key={stat.key} data-testid={`usage-${stat.key.split('.')[1]}`}>
            <div className="stat-label">{t(stat.key)}</div>
            {stat.used === null ? (
              <div className="stat-value"><small>{t('dash.upTo', { limit: number(stat.limit) })}</small></div>
            ) : stat.limit > 0 ? (
              <>
                <div className="stat-value">{number(stat.used)} <small>/ {number(stat.limit)}</small></div>
                <Meter used={stat.used} limit={stat.limit} label={t(stat.key)} />
              </>
            ) : (
              <div className="stat-value">{number(stat.used)}</div>
            )}
          </div>
        ))}
      </div>

      <h2 style={{ margin: '28px 0 14px' }}>{t('dash.next')}</h2>
      <div className="next-steps">
        {steps.filter((step) => step.show && (step.to !== '/app/team' || canSeeTeam(overview.role))).map((step) => {
          const body = (
            <>
              <div className="feature-icon" style={{ margin: 0 }}><Icon name={step.icon} /></div>
              <div><strong>{t(step.title)}</strong><p>{t(step.text)}</p></div>
            </>
          );
          return step.external
            ? <a key={step.to} className="next-step" href={step.to}>{body}</a>
            : <Link key={step.to} className="next-step" to={step.to}>{body}</Link>;
        })}
      </div>
    </section>
  );
}
