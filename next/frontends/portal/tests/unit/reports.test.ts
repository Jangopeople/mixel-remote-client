import { describe, expect, it } from 'vitest';
import { canEditNote, defaultReportDays, deviceSections, errorKey, isMissingRpc, reportParams } from '../../src/lib/logic';
import {
  buildPdf, fitBytes, pdfTextString, formatDuration, pdfString, reportCsv, reportFileName, reportPdf, reportTotals, rowSeconds, textWidth, winAnsi, wrapText,
  type ReportLabels, type ReportRow,
} from '../../src/lib/reportExport';

const labels: ReportLabels = {
  technician: 'Technician', email: 'Email', device: 'Device', remoteId: 'Remote ID', start: 'Start', end: 'End', duration: 'Duration',
  durationSeconds: 'Duration (seconds)', kind: 'Type', colleague: 'Joined as colleague', note: 'Note', sessionId: 'Session ID',
  attended: 'Attended', unattended: 'Unattended', running: 'Running', yes: 'Yes', no: 'No',
};

function row(over: Partial<ReportRow> = {}): ReportRow {
  return {
    session_id: '11111111-1111-4111-8111-111111111111', technician_id: 't1', technician_name: 'Tom Tech', technician_email: 'tom@acme.test',
    device_id: 'd1', device_name: 'Office PC', connection_id: 196516669, started_at: '2026-11-10T08:00:00Z', ended_at: '2026-11-10T09:02:05Z',
    duration_seconds: 3725, kind: 'attended', colleague: false, note: null, ...over,
  };
}

/** Checks the cross-reference table: every offset points at "<n> 0 obj" and startxref at "xref". */
function checkPdf(pdf: string) {
  expect(pdf.startsWith('%PDF-1.4\n')).toBe(true);
  expect(pdf.endsWith('%%EOF\n')).toBe(true);
  // eslint-disable-next-line no-control-regex
  expect(/^[\x09\x0a\x0d\x20-\x7e]*$/.test(pdf), 'the file is pure ASCII').toBe(true);
  const startxref = Number(/startxref\n(\d+)\n%%EOF\n$/.exec(pdf)![1]);
  expect(pdf.slice(startxref, startxref + 5)).toBe('xref\n');
  const header = /^xref\n0 (\d+)\n/.exec(pdf.slice(startxref))!;
  const size = Number(header[1]);
  const entries = pdf.slice(startxref + header[0].length).split('\n').slice(0, size);
  expect(entries[0]).toBe('0000000000 65535 f ');
  for (let id = 1; id < size; id++) {
    expect(entries[id]).toMatch(/^\d{10} 00000 n $/);
    expect((entries[id] + '\n').length, 'xref entries are 20 bytes').toBe(20);
    const offset = Number(entries[id].slice(0, 10));
    expect(pdf.slice(offset, offset + `${id} 0 obj`.length)).toBe(`${id} 0 obj`);
  }
  expect(new RegExp(`/Size ${size} `).test(pdf)).toBe(true);
  // Every content stream's /Length matches its bytes.
  for (const match of pdf.matchAll(/<< \/Length (\d+) >>\nstream\n/g)) {
    const start = match.index! + match[0].length;
    expect(pdf.slice(start + Number(match[1]), start + Number(match[1]) + 10)).toBe('\nendstream');
  }
  return { pages: Number(/\/Type \/Pages \/Kids \[[^\]]*\] \/Count (\d+)/.exec(pdf)![1]), pageObjects: (pdf.match(/\/Type \/Page /g) ?? []).length };
}

describe('durations and totals', () => {
  it('formats seconds as h:mm:ss', () => {
    expect(formatDuration(0)).toBe('0:00:00');
    expect(formatDuration(59)).toBe('0:00:59');
    expect(formatDuration(3725)).toBe('1:02:05');
    expect(formatDuration(100 * 3600 + 61)).toBe('100:01:01');
    expect(formatDuration(null)).toBe('0:00:00');
    expect(formatDuration(-5)).toBe('0:00:00');
  });
  it('counts a running session until now and sums the rows', () => {
    const now = new Date('2026-11-10T10:00:00Z');
    expect(rowSeconds(row({ ended_at: null, duration_seconds: null, started_at: '2026-11-10T09:30:00Z' }), now)).toBe(1800);
    expect(rowSeconds(row(), now)).toBe(3725);
    expect(reportTotals([row(), row({ duration_seconds: 75 })], now)).toEqual({ count: 2, seconds: 3800 });
  });
});

describe('favorites and recent connections', () => {
  const devices = [
    { device_id: 'a', name: 'Zeta' }, { device_id: 'b', name: 'alpha' }, { device_id: 'c', name: 'Beta' },
    { device_id: 'd', name: 'Delta' }, { device_id: 'e', name: 'Echo' },
  ];
  it('puts favorites (by name) first, then recent devices (newest first), then the rest once each', () => {
    const sections = deviceSections(devices, new Set(['a', 'b']), [
      { device_id: 'c', last_connected_at: '2026-11-01T08:00:00Z' },
      { device_id: 'a', last_connected_at: '2026-11-12T08:00:00Z' },
      { device_id: 'e', last_connected_at: '2026-11-11T08:00:00Z' },
      { device_id: 'gone', last_connected_at: '2026-11-13T08:00:00Z' },
    ]);
    expect(sections.favorites.map((d) => d.name)).toEqual(['alpha', 'Zeta']);
    expect(sections.recent.map((d) => d.name)).toEqual(['Echo', 'Beta']);
    expect(sections.others.map((d) => d.name)).toEqual(['Delta']);
  });
  it('keeps at most `max` recent devices and the original order without favorites', () => {
    const recent = devices.map((d, index) => ({ device_id: d.device_id, last_connected_at: new Date(2026, 0, index + 1).toISOString() }));
    const sections = deviceSections(devices, new Set(), recent, 2);
    expect(sections.recent.map((d) => d.device_id)).toEqual(['e', 'd']);
    expect(sections.others.map((d) => d.device_id)).toEqual(['a', 'b', 'c']);
    expect(deviceSections(devices, new Set(), []).others).toEqual(devices);
  });
});

describe('report filter and permissions', () => {
  it('defaults to the last 30 days', () => {
    expect(defaultReportDays(new Date(2026, 10, 13, 15, 0))).toEqual({ from: '2026-10-14', to: '2026-11-13' });
  });
  it('turns days into a half-open local range and checks it', () => {
    const result = reportParams({ from: '2026-11-01', to: '2026-11-13', technician: '', device: 'd1' });
    expect('params' in result).toBe(true);
    if (!('params' in result)) return;
    expect(new Date(result.params.from_at).getTime()).toBe(new Date(2026, 10, 1).getTime());
    expect(new Date(result.params.to_at).getTime()).toBe(new Date(2026, 10, 14).getTime());
    expect(result.params.technician).toBeNull();
    expect(result.params.device).toBe('d1');
    expect(reportParams({ from: '2026-11-14', to: '2026-11-13', technician: '', device: '' })).toEqual({ problem: 'reports.rangeInvalid' });
    expect(reportParams({ from: '', to: '2026-11-13', technician: '', device: '' })).toEqual({ problem: 'reports.rangeInvalid' });
    expect(reportParams({ from: '2026-02-30', to: '2026-03-13', technician: '', device: '' })).toEqual({ problem: 'reports.rangeInvalid' });
    expect(reportParams({ from: '2025-01-01', to: '2026-11-13', technician: '', device: '' })).toEqual({ problem: 'reports.rangeTooLong' });
    expect('params' in reportParams({ from: '2025-11-13', to: '2026-11-13', technician: '', device: '' })).toBe(true);
  });
  it('lets owners and admins edit any note, the others only their own', () => {
    const mine = { technician_email: 'Tom@Acme.test' };
    expect(canEditNote('owner', { technician_email: 'x@y.z' }, 'tom@acme.test')).toBe(true);
    expect(canEditNote('admin', { technician_email: 'x@y.z' }, null)).toBe(true);
    expect(canEditNote('technician', mine, 'tom@acme.test')).toBe(true);
    expect(canEditNote('technician', { technician_email: 'x@y.z' }, 'tom@acme.test')).toBe(false);
    expect(canEditNote('member', { technician_email: null }, 'tom@acme.test')).toBe(true);
  });
  it('recognises a function that is not on the server yet and the plan error', () => {
    expect(isMissingRpc({ code: 'PGRST202', message: 'Could not find the function public.remote_saas_favorites(tenant) in the schema cache' })).toBe(true);
    expect(isMissingRpc({ code: '42883', message: 'function remote_saas_favorites(uuid) does not exist' })).toBe(true);
    expect(isMissingRpc({ code: '42501', message: 'Session reports need a paid plan' })).toBe(false);
    expect(isMissingRpc(null)).toBe(false);
    expect(errorKey({ code: '42501', message: 'Session reports need a paid plan' })).toBe('error.reports_plan');
  });
});

describe('CSV', () => {
  it('has a BOM, translated headers, CRLF lines, RFC 4180 quoting and neutralised formulas', () => {
    const csv = reportCsv([
      row({ note: 'Printer "HP", fixed\nreboot needed', technician_name: '=HYPERLINK("x")' }),
      row({ session_id: 's2', kind: 'unattended', colleague: true, ended_at: null, duration_seconds: 60, device_name: 'Zürich Büro', note: '' }),
    ], labels, new Date('2026-11-10T10:00:00Z'));
    expect(csv.startsWith('﻿Technician,Email,Device,Remote ID,Start,End,Duration,Duration (seconds),Type,Joined as colleague,Note,Session ID\r\n')).toBe(true);
    const lines = csv.slice(1).split('\r\n');
    expect(lines).toHaveLength(4); // header, two rows, trailing empty
    expect(lines[1]).toContain(`"'=HYPERLINK(""x"")"`);
    expect(lines[1]).toContain('"Printer ""HP"", fixed\nreboot needed"');
    expect(lines[1]).toContain(',1:02:05,3725,Attended,No,');
    expect(lines[1]).toContain(',196516669,');
    expect(lines[2]).toContain('Zürich Büro');
    expect(lines[2]).toContain(',Running,0:01:00,60,Unattended,Yes,,s2');
    expect(lines[1]).toMatch(/,\d{4}-\d{2}-\d{2} \d{2}:\d{2},/);
  });
  it('builds the file names', () => {
    expect(reportFileName('Acme AG!', '2026-10-14', '2026-11-13', 'pdf')).toBe('mixel-remote-acmeag-sessions-2026-10-14-2026-11-13.pdf');
  });
});

describe('PDF', () => {
  it('encodes text as WinAnsi with escapes', () => {
    expect(winAnsi('Grüsse € – “ok” …')).toEqual([0x47, 0x72, 0xfc, 0x73, 0x73, 0x65, 0x20, 0x80, 0x20, 0x96, 0x20, 0x93, 0x6f, 0x6b, 0x94, 0x20, 0x85]);
    expect(winAnsi('Łódź')).toEqual([0x4c, 0xf3, 0x64, 0x7a]); // Ł has no WinAnsi byte → base letter; ó is Latin-1
    expect(winAnsi('日本')).toEqual([0x3f, 0x3f]);
    expect(pdfString(winAnsi('a(b)c\\ é'))).toBe('(a\\(b\\)c\\\\ \\351)');
    expect(pdfTextString('Zü')).toBe('<FEFF005A00FC>');
  });
  it('measures, cuts and wraps text', () => {
    expect(textWidth(winAnsi('AAAA'), 10)).toBeCloseTo(26.68);
    const cut = fitBytes(winAnsi('A long device name that does not fit'), 60, 8.5);
    expect(cut[cut.length - 1]).toBe(0x85);
    expect(textWidth(cut, 8.5)).toBeLessThanOrEqual(60);
    const lines = wrapText('one two three four five six seven eight nine ten', 50, 8.5, 3);
    expect(lines.length).toBe(3);
    expect(lines[2][lines[2].length - 1]).toBe(0x85);
    for (const line of lines) expect(textWidth(line, 8.5)).toBeLessThanOrEqual(50);
    expect(wrapText('', 50, 8.5, 3)).toEqual([[]]);
    expect(wrapText('Supercalifragilisticexpialidocious', 30, 8.5, 9).length).toBeGreaterThan(2);
  });
  it('writes a valid PDF with correct cross-references, one page for a few rows', () => {
    const pdf = reportPdf([row({ note: 'Drucker in Zürich neu gestartet – alles OK' }), row({ session_id: 's2', kind: 'unattended', colleague: true })], {
      company: 'Acme AG', title: 'Session report', range: '14 Oct 2026 – 13 Nov 2026', generated: 'Generated 13 Nov 2026, 15:00',
      totals: '2 sessions · total 2:04:10', page: (page, pages) => `Page ${page} of ${pages}`, labels,
      formatTime: (iso) => iso.slice(0, 16).replace('T', ' '), now: new Date('2026-11-13T14:00:00Z'),
    });
    const { pages, pageObjects } = checkPdf(pdf);
    expect(pages).toBe(1);
    expect(pageObjects).toBe(1);
    expect(pdf).toContain('/BaseFont /Helvetica /Encoding /WinAnsiEncoding');
    expect(pdf).toContain('/MediaBox [0 0 842 595]');
    expect(pdf).toContain('(Acme AG)');
    expect(pdf).toContain('(Office PC)');
    expect(pdf).toContain('(Drucker in Z\\374rich neu gestartet \\226 alles OK)');
    expect(pdf).toContain('(Unattended \\267 Joined)');
    expect(pdf).toContain('(as colleague)');
    expect(pdf).toContain('(2 sessions \\267 total 2:04:10)');
    expect(pdf).toContain('(Page 1 of 1)');
    expect(pdf).toContain('/CreationDate (D:20261113140000Z)');
    expect(pdf).toContain('/Title <FEFF00530065007300730069006F006E0020007200650070006F0072007400202013002000410063006D0065002000410047>');
  });
  it('paginates long reports, repeats the column heads and numbers the pages', () => {
    const rows = Array.from({ length: 120 }, (_, index) => row({ session_id: `s${index}`, device_name: `Device ${index + 1}`, note: index % 7 === 0 ? 'A longer note that wraps onto a second line in the note column of the table, and maybe a third one as well.' : null }));
    const pdf = reportPdf(rows, {
      company: 'Acme AG', title: 'Session report', range: 'r', generated: 'g', totals: '120 sessions', page: (page, pages) => `Page ${page} of ${pages}`,
      labels, formatTime: (iso) => iso,
    });
    const { pages, pageObjects } = checkPdf(pdf);
    expect(pages).toBeGreaterThan(2);
    expect(pageObjects).toBe(pages);
    expect((pdf.match(/\(Technician\)/g) ?? []).length).toBe(pages);
    expect(pdf).toContain(`(Page ${pages} of ${pages})`);
    for (let index = 1; index <= 120; index++) expect(pdf).toContain(`(Device ${index})`);
    expect(pdf.indexOf('(120 sessions)')).toBeGreaterThan(pdf.indexOf('(Device 120)'));
  });
  it('writes an empty table and a generic table', () => {
    checkPdf(reportPdf([], { company: 'A', title: 'T', range: 'r', generated: 'g', totals: '0 sessions', page: () => 'p', labels, formatTime: (iso) => iso }));
    const pdf = buildPdf({ title: 'T', subtitle: [], columns: [{ label: 'A', width: 1 }, { label: 'B', width: 1, align: 'right' }], rows: [['x', 'y']], totals: [], footer: () => '' });
    expect(checkPdf(pdf).pages).toBe(1);
  });
});
