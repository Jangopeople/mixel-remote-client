// Shows Mixel Remote in the computer's language (German, French or Italian),
// or the language chosen in Settings. English is the source text. Device names,
// chat messages and typed values are data and are never translated.
(() => {
  const KEY = 'mixel.remote.language.v1';
  const SUPPORTED = ['de', 'fr', 'it'];
  type Table = {texts: Record<string, string>; patterns: [string, string][]};
  const all = (window as unknown as {MIXEL_I18N?: Record<string, Table>}).MIXEL_I18N ?? {};
  let chosen = 'auto';
  try { chosen = localStorage.getItem(KEY) ?? 'auto'; } catch { /* default */ }
  const system = (navigator.languages?.[0] ?? navigator.language ?? 'en').slice(0, 2).toLowerCase();
  const language = chosen === 'auto' ? (SUPPORTED.includes(system) ? system : 'en') : chosen;
  document.documentElement.lang = language;
  (window as unknown as {mixelSetLanguage: (value: string) => void}).mixelSetLanguage = (value: string) => {
    try { if (value === 'auto') localStorage.removeItem(KEY); else localStorage.setItem(KEY, value); } catch { /* not saved */ }
    location.reload();
  };
  (window as unknown as {mixelLanguageChoice: string}).mixelLanguageChoice = chosen;
  const table = all[language];
  if (!table) return;
  const patterns = table.patterns.map(([source, target]) => [new RegExp(source), target] as const);
  const skip = (node: Node | null): boolean => {
    for (let at = node instanceof Element ? node : node?.parentElement; at; at = at.parentElement) {
      if (at.matches('script, style, textarea, input, select[data-no-translate], #remote-chat-log, #devices-list strong, #devices-list code, [data-no-translate]')) return true;
    }
    return false;
  };
  function translate(text: string): string | null {
    const core = text.trim();
    if (!core || core.length > 600) return null;
    const hit = table!.texts[core];
    if (hit !== undefined) return hit === core ? null : text.replace(core, hit);
    for (const [pattern, target] of patterns) {
      if (pattern.test(core)) return text.replace(core, core.replace(pattern, target));
    }
    return null;
  }
  function translateNode(node: Node): void {
    if (node.nodeType === Node.TEXT_NODE) {
      if (skip(node)) return;
      const next = translate(node.nodeValue ?? '');
      if (next !== null && next !== node.nodeValue) node.nodeValue = next;
      return;
    }
    if (!(node instanceof Element)) return;
    const field = node.matches('input, textarea, select');
    // Fields keep their values; only their hints are translated.
    if (!field && skip(node)) return;
    if (field && node.parentElement && skip(node.parentElement)) return;
    for (const name of ['placeholder', 'aria-label', 'title']) {
      const value = node.getAttribute(name);
      const next = value ? translate(value) : null;
      if (next !== null && next !== value) node.setAttribute(name, next);
    }
    if (field) return;
    for (const child of Array.from(node.childNodes)) translateNode(child);
  }
  const start = (): void => {
    translateNode(document.body);
    new MutationObserver(records => {
      for (const record of records) {
        if (record.type === 'characterData') translateNode(record.target);
        else if (record.type === 'attributes') translateNode(record.target);
        else record.addedNodes.forEach(translateNode);
      }
    }).observe(document.body, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['placeholder', 'aria-label', 'title']});
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
