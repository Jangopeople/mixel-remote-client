import { useState, type ReactNode } from 'react';
import { Link, NavLink } from 'react-router-dom';
import { useI18n } from '../i18n';
import { useAuth } from '../lib/auth';
import { Brand, PreferencesMenu } from './ui';
import { Icon } from './Icon';

export const STORE_URL = 'https://store.mixel.ch/app/mixel-remote';

export function PublicLayout({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const { session } = useAuth();
  const [open, setOpen] = useState(false);
  return (
    <>
      <a className="skip-link" href="#main">{t('nav.skip')}</a>
      <header className="site-header">
        <div className="site-header-inner">
          <Brand />
          <nav className="site-nav" data-open={open} aria-label="Main" onClick={() => setOpen(false)}>
            <Link to="/#features">{t('nav.features')}</Link>
            <NavLink to="/pricing">{t('nav.pricing')}</NavLink>
            <NavLink to="/download">{t('nav.download')}</NavLink>
          </nav>
          <div className="header-actions">
            <PreferencesMenu />
            {session ? (
              <Link className="button button-small" to="/app">{t('nav.openPortal')}</Link>
            ) : (
              <>
                <Link className="button button-secondary button-small" to="/signin">{t('nav.signIn')}</Link>
                <Link className="button button-small hide-mobile" to="/signup">{t('nav.startTrial')}</Link>
              </>
            )}
            <button type="button" className="button-quiet menu-toggle" aria-label={t('shell.menu')} aria-expanded={open}
              onClick={() => setOpen((value) => !value)}>
              <Icon name="menu" />
            </button>
          </div>
        </div>
      </header>
      <main id="main" className="site-main">{children}</main>
      <SiteFooter />
    </>
  );
}

export function SiteFooter() {
  const { t } = useI18n();
  return (
    <footer className="site-footer">
      <div className="container site-footer-inner">
        <div>
          <strong style={{ color: 'var(--text)' }}>Mixel Remote</strong> · {t('footer.tagline')}
          <div>© {new Date().getFullYear()} {t('footer.company')}</div>
        </div>
        <nav aria-label="Footer">
          <Link to="/pricing">{t('nav.pricing')}</Link>
          <Link to="/download">{t('nav.download')}</Link>
          <Link to="/status">{t('footer.status')}</Link>
          <a href="https://mixel.ch/kontakt" rel="noopener">{t('footer.contact')}</a>
          <a href="https://mixel.ch/impressum" rel="noopener">{t('footer.imprint')}</a>
        </nav>
      </div>
    </footer>
  );
}

/** The browser viewer runs on the ISM origin (the connection service admits that one web origin). */
export const WEB_VIEWER_URL = 'https://ism.mixel.ch/login?next=%2Fremote-next%2F';
