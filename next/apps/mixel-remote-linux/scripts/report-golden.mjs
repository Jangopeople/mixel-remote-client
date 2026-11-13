// report-golden.mjs: the fixtures of src/report_export.rs. Runs the desktop app's report writer
// (frontends/desktop/report-export.ts, types stripped by Node) on fixed rows and texts and writes
// tests/fixtures/report-golden.{json,pdf,csv}; the Rust tests must produce the same bytes.
//   TZ=UTC node scripts/report-golden.mjs        (Node 22.13 or newer)
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {stripTypeScriptTypes} from 'node:module';
import {fileURLToPath} from 'node:url';

if (process.env.TZ !== 'UTC') { console.error('run with TZ=UTC'); process.exit(1); }
const here = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const source = fs.readFileSync(path.join(here, '../../frontends/desktop/report-export.ts'), 'utf8');
const window = {};
vm.runInNewContext(stripTypeScriptTypes(source), {window});
const r = window.mixelReports;

const labels = {technician: 'Technician', email: 'E-mail', device: 'Device', remoteId: 'Remote ID', start: 'Start', end: 'End', duration: 'Duration',
  durationSeconds: 'Duration (seconds)', kind: 'Type', colleague: 'Joined as colleague', note: 'Note', sessionId: 'Session ID',
  attended: 'Attended', unattended: 'Unattended', running: 'Running', yes: 'Yes', no: 'No'};
const names = ['Olivia Owner', 'Lukas Brunner', 'Zoë Müller-Lüdenscheidt', 'Łukasz Żółć', 'François (Support) \\ Team', null];
const notes = [null, '', 'Printer driver updated', '=SUM(A1)', 'Line one\nLine two\r\nLine three',
  'A long note that keeps going and going so that it has to be wrapped over several lines in the note column of the report, ' +
  'and then some more words until the six line limit cuts it off with an ellipsis at the very end of the last line shown.',
  'Averyveryverylongwordwithoutanyspacesthatmustbebrokeninsidethecolumnbecauseitdoesnotfit', '€ “quotes” – dash — 中文 😀 é ǅ'];
const rows = [];
const start = Date.UTC(2026, 10, 1, 7, 30, 0);
for (let index = 0; index < 64; index++) {
  const at = new Date(start + index * 9 * 3600 * 1000 + index * 61 * 1000);
  const seconds = (index * 437) % 9000 + 15;
  const running = index === 63;
  rows.push({
    session_id: `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`,
    technician_id: index % 6 === 5 ? null : `aaaaaaaa-0000-4000-8000-00000000000${index % 6}`,
    technician_name: names[index % names.length],
    technician_email: index % 6 === 5 ? null : `tech${index % 6}@acme.test`,
    device_id: `d${index}`,
    device_name: index % 9 === 4 ? 'A very long device name that will not fit in its column at all' : `PC-${index} (Büro)`,
    connection_id: 100000000 + index * 7919,
    started_at: at.toISOString(),
    ended_at: running ? null : new Date(at.getTime() + seconds * 1000).toISOString(),
    duration_seconds: seconds,
    kind: index % 3 === 0 ? 'unattended' : 'attended',
    colleague: index % 5 === 2,
    note: notes[index % notes.length],
  });
}
const nowDate = new Date(Date.UTC(2026, 10, 13, 18, 4, 54, 613));
const now = Math.floor(nowDate.getTime() / 1000);
const totals = r.reportTotals(rows, nowDate);
const texts = {now, company: 'Acme Informatik AG', title: 'Session report', range: '1 Nov 2026 – 30 Nov 2026', generated: 'Generated 13 Nov 2026, 19:04',
  totals: `${totals.count} sessions · ${r.formatDuration(totals.seconds)} in total`, labels, rows};
const when = (iso) => r.localStamp(iso);
const pdf = r.reportPdf(rows, {company: texts.company, title: texts.title, range: texts.range, generated: texts.generated, totals: texts.totals,
  page: (page, pages) => `Page ${page} of ${pages}`, labels, formatTime: when, now: nowDate});
const csv = r.reportCsv(rows, labels, nowDate);
const out = path.join(here, 'tests/fixtures');
fs.mkdirSync(out, {recursive: true});
fs.writeFileSync(path.join(out, 'report-golden.json'), JSON.stringify(texts, null, 1) + '\n');
fs.writeFileSync(path.join(out, 'report-golden.pdf'), pdf, 'latin1');
fs.writeFileSync(path.join(out, 'report-golden.csv'), csv, 'utf8');
console.log(`pdf ${pdf.length} bytes, ${/\/Count (\d+)/.exec(pdf)[1]} pages; csv ${csv.length} chars; now ${now}`);
