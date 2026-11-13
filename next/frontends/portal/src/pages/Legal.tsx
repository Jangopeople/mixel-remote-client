// Legal documents (DRAFT pending review): /legal, /legal/terms, /legal/privacy, /legal/dpa,
// /legal/subprocessors. Every page carries the draft banner and "noindex" (also sent as the
// X-Robots-Tag header by worker/index.ts).
import { useEffect } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { LANGS, useI18n } from '../i18n';
import { LEGAL, LEGAL_DATE, LEGAL_DOCS, LEGAL_VERSION, PROVIDER, SUBPROCESSORS, type Block, type LegalDocKey, type LegalTexts } from '../legal';
import { PublicLayout } from '../components/PublicLayout';
import { Icon } from '../components/Icon';
import { formatDate } from '../components/ui';

function useNoIndex(title: string) {
  useEffect(() => {
    const previousTitle = document.title;
    document.title = `${title} — Mixel Remote`;
    const meta = document.createElement('meta');
    meta.name = 'robots';
    meta.content = 'noindex, nofollow';
    document.head.appendChild(meta);
    return () => {
      document.title = previousTitle;
      meta.remove();
    };
  }, [title]);
}

function DraftBanner({ texts }: { texts: LegalTexts }) {
  return (
    <div className="notice notice-warning legal-draft" role="note" data-testid="legal-draft">
      <Icon name="alert" />
      <p><strong>{texts.ui.draftTitle}.</strong> {texts.ui.draftText}</p>
    </div>
  );
}

function SubprocessorTable({ texts }: { texts: LegalTexts }) {
  const c = texts.ui.columns;
  return (
    <div className="table-wrap legal-table">
      <table className="table" data-testid="subprocessor-table">
        <thead><tr><th>{c.name}</th><th>{c.purpose}</th><th>{c.data}</th><th>{c.location}</th><th>{c.safeguard}</th></tr></thead>
        <tbody>
          {SUBPROCESSORS.map((row, index) => {
            const text = texts.subprocessorRows[index];
            return (
              <tr key={row.name}>
                <td className="cell-main"><strong>{row.name}</strong><span>{row.address}</span></td>
                <td>{text.purpose}</td><td>{text.data}</td><td>{text.location}</td><td>{text.safeguard}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function BlockView({ block, texts }: { block: Block; texts: LegalTexts }) {
  if (typeof block === 'string') return <p>{block}</p>;
  if ('list' in block) return <ul>{block.list.map((item) => <li key={item}>{item}</li>)}</ul>;
  return <SubprocessorTable texts={texts} />;
}

export function LegalIndex() {
  const { lang, locale } = useI18n();
  const texts = LEGAL[lang];
  useNoIndex(texts.ui.centre);
  return (
    <PublicLayout>
      <div className="container legal-page">
        <DraftBanner texts={texts} />
        <h1>{texts.ui.centre}</h1>
        <p className="lead">{texts.ui.centreLead}</p>
        <div className="legal-cards">
          {LEGAL_DOCS.map((key) => (
            <Link key={key} to={`/legal/${key}`} className="feature legal-card" data-doc={key}>
              <strong>{texts[key].title}</strong>
              <p>{texts[key].summary}</p>
            </Link>
          ))}
        </div>
        <p className="field-hint">{texts.ui.version} {LEGAL_VERSION} · {texts.ui.updated} {formatDate(LEGAL_DATE, locale)}</p>
        <address className="legal-provider">{PROVIDER.name}<br />{PROVIDER.street}, {PROVIDER.city}, {PROVIDER.country}<br />UID {PROVIDER.uid} · {PROVIDER.email} · {PROVIDER.phone}</address>
      </div>
    </PublicLayout>
  );
}

export function LegalDocument() {
  const { doc } = useParams();
  const { lang, setLang, locale } = useI18n();
  const texts = LEGAL[lang];
  const key = (LEGAL_DOCS as string[]).includes(doc ?? '') ? (doc as LegalDocKey) : null;
  useNoIndex(key ? texts[key].title : texts.ui.centre);
  if (!key) return <Navigate to="/legal" replace />;
  const content = texts[key];
  return (
    <PublicLayout>
      <article className="container legal-page" data-testid={`legal-${key}`} lang={lang}>
        <DraftBanner texts={texts} />
        <p className="eyebrow"><Link to="/legal">{texts.ui.centre}</Link></p>
        <h1>{content.title}</h1>
        <p className="lead">{content.summary}</p>
        <p className="field-hint">
          {texts.ui.version} {LEGAL_VERSION} · {texts.ui.updated} {formatDate(LEGAL_DATE, locale)} ·{' '}
          {texts.ui.otherLanguages}{' '}
          {LANGS.filter((item) => item.code !== lang).map((item, index) => (
            <span key={item.code}>{index ? ', ' : ''}<button type="button" className="link-button" onClick={() => setLang(item.code)}>{item.label}</button></span>
          ))}
          {' · '}<button type="button" className="link-button" onClick={() => window.print()}>{texts.ui.print}</button>
        </p>
        {content.sections.length > 1 && (
          <nav className="legal-toc" aria-label={texts.ui.contents}>
            <strong>{texts.ui.contents}</strong>
            <ol>{content.sections.map((section, index) => <li key={section.heading}><a href={`#s${index + 1}`}>{section.heading}</a></li>)}</ol>
          </nav>
        )}
        {content.sections.map((section, index) => (
          <section key={section.heading} id={`s${index + 1}`} className="legal-section">
            <h2>{section.heading}</h2>
            {section.body.map((block, at) => <BlockView key={at} block={block} texts={texts} />)}
          </section>
        ))}
        <address className="legal-provider">{PROVIDER.name}<br />{PROVIDER.street}, {PROVIDER.city}, {PROVIDER.country}<br />UID {PROVIDER.uid} · {PROVIDER.email} · {PROVIDER.phone}</address>
      </article>
    </PublicLayout>
  );
}
