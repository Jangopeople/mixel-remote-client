#!/usr/bin/env python3
"""Writes frontends/desktop/i18n-strings.ts from i18n/strings.py (S: texts, P: message patterns)."""
import importlib.util, json, pathlib, re
here = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('strings', here / 'strings.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
out = ["// Generated: German, French and Italian texts of the Mixel Remote app, keyed by the English text.",
       "// eslint-disable-next-line @typescript-eslint/no-unused-vars",
       "const MIXEL_I18N: Record<string, {texts: Record<string, string>; patterns: [string, string][]}> = {"]
for li, lang in enumerate(['de', 'fr', 'it']):
    out += [f'"{lang}": {{', '"texts": {']
    out.append(",\n".join(f'{json.dumps(e, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}'
                          for e, v in sorted({t[0]: t[li + 1] for t in m.S}.items())))
    out += ['},', '"patterns": [']
    out.append(",\n".join('[\n' + json.dumps(p[0], ensure_ascii=False) + ',\n'
                          + json.dumps(re.sub(r'\\(\d)', r'$\1', p[li + 1]), ensure_ascii=False) + '\n]' for p in m.P))
    out += [']', '}' + (',' if li < 2 else '')]
out += ['};', '(window as unknown as {MIXEL_I18N: typeof MIXEL_I18N}).MIXEL_I18N = MIXEL_I18N;']
(here.parent / 'frontends/desktop/i18n-strings.ts').write_text("\n".join(out) + "\n")
print("i18n-strings.ts:", len(m.S), "texts,", len(m.P), "patterns")
