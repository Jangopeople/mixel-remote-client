import { useCallback, useEffect, useState } from 'react';
import { useI18n, type MessageKey } from '../i18n';
import { Icon } from '../components/Icon';
import { PublicLayout } from '../components/PublicLayout';
import { DAYS, lastDays, overall, uptime, type Level, type StatusComponent, type StatusReport } from '../lib/status';

const BADGE: Record<Level | 'unknown', string> = {
  operational: 'badge-success', degraded: 'badge-warning', outage: 'badge-danger', unknown: '',
};

export function Status() {
  const { t, locale } = useI18n();
  const [report, setReport] = useState<StatusReport | null>(null);
  const [failed, setFailed] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const load = useCallback(() => {
    fetch('/api/status', { headers: { accept: 'application/json' }, cache: 'no-store' })
      .then((response) => (response.ok ? response.json() as Promise<StatusReport> : Promise.reject(new Error('status'))))
      .then((value) => { setReport(value); setFailed(false); })
      .catch(() => setFailed(true))
      .finally(() => setNow(Date.now()));
  }, []);
  useEffect(() => {
    load();
    const timer = window.setInterval(load, 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const level = failed ? 'unknown' : overall(report, now);
  const loading = !report && !failed;
  const dateTime = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' });
  const shortDate = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeZone: 'UTC' });
  const percent = new Intl.NumberFormat(locale, { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  const minutes = report?.updated_at ? Math.max(0, Math.round((now - Date.parse(report.updated_at)) / 60000)) : null;
  const name = (component: StatusComponent) => {
    const key = `status.component.${component.id}` as MessageKey;
    const text = t(key);
    return text === key ? component.name : text;
  };

  return (
    <PublicLayout>
      <section className="section">
        <div className="container status-page">
          <div className="section-heading">
            <h1>{t('status.title')}</h1>
            <p>{t('status.lead')}</p>
          </div>
          {loading ? (
            <p className="field-hint" role="status">{t('common.loading')}</p>
          ) : (
            <div className={`status-banner status-banner-${level}`} role="status" data-status={level}>
              <Icon name={level === 'operational' ? 'check' : 'alert'} />
              <div>
                <strong>{t(`status.overall.${level}` as MessageKey)}</strong>
                <p>
                  {level === 'unknown'
                    ? t('status.unknownText')
                    : minutes !== null && t('status.updated', { minutes })}
                </p>
              </div>
            </div>
          )}
          {report?.components && report.components.length > 0 && (
            <ul className="status-list" aria-label={t('status.components')}>
              {report.components.map((component) => {
                const days = lastDays(component.history, new Date(now));
                const average = uptime(component.history);
                const shown: Level | 'unknown' = level === 'unknown' ? 'unknown' : component.status;
                return (
                  <li key={component.id} className="panel status-item" data-component={component.id} data-status={shown}>
                    <div className="status-item-head">
                      <h2>{name(component)}</h2>
                      <span className={`badge ${BADGE[shown]}`}>{t(`status.level.${shown}` as MessageKey)}</span>
                    </div>
                    {component.since && shown !== 'operational' && shown !== 'unknown' && (
                      <p className="field-hint">{t('status.since', { time: dateTime.format(new Date(component.since)) })}</p>
                    )}
                    <div className="status-bars" aria-hidden="true">
                      {days.map((day, index) => (
                        <span key={index} className="status-bar" data-level={day ? (day.worst === 'operational' ? 'operational' : day.worst) : 'none'}
                          title={day ? `${shortDate.format(new Date(`${day.date}T12:00:00Z`))}: ${day.uptime === null ? '–' : `${percent.format(day.uptime)} %`}` : t('status.noData')} />
                      ))}
                    </div>
                    <div className="status-bars-legend">
                      <span className="status-days-full">{t('status.daysAgo', { days: DAYS })}</span>
                      <span className="status-days-short">{t('status.daysAgo', { days: DAYS / 2 })}</span>
                      <span>{average === null ? t('status.noData') : t('status.uptime', { value: percent.format(average) })}</span>
                      <span>{t('status.today')}</span>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          <p className="field-hint status-footnote">{t('status.footnote')}</p>
        </div>
      </section>
    </PublicLayout>
  );
}
