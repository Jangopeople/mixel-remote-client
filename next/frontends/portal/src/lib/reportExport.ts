// Session report export: CSV (Excel-ready) and a small hand-written PDF 1.4 writer.
// No dependency: the PDF uses the built-in Helvetica fonts with WinAnsi encoding (umlauts, accents,
// €, quotes and dashes print correctly; other characters fall back to their base letter or "?"),
// landscape A4, a header on every page, a paginated table, totals and page numbers. The file is
// pure ASCII (bytes above 126 are written as octal escapes), so its length equals its byte count.
// The desktop app has the same logic in frontends/desktop/report-export.ts (plain script, no bundler).
import { toCsv } from './exportData';

export interface ReportRow {
  session_id: string;
  technician_id: string | null;
  technician_name: string | null;
  technician_email: string | null;
  device_id: string | null;
  device_name: string | null;
  connection_id: number | null;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  kind: 'attended' | 'unattended' | string;
  colleague: boolean | null;
  note: string | null;
}

// ---------------------------------------------------------------------------------------------
// Durations and times

/** Seconds of a row; a running session counts until `now`. */
export function rowSeconds(row: Pick<ReportRow, 'started_at' | 'ended_at' | 'duration_seconds'>, now: Date = new Date()): number {
  if (typeof row.duration_seconds === 'number' && Number.isFinite(row.duration_seconds) && row.duration_seconds >= 0) return Math.round(row.duration_seconds);
  const start = Date.parse(row.started_at);
  const end = row.ended_at ? Date.parse(row.ended_at) : now.getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) return 0;
  return Math.max(0, Math.round((end - start) / 1000));
}

/** 3725 -> "1:02:05"; language-neutral, sorts and sums well in a spreadsheet. */
export function formatDuration(seconds: number | null | undefined): string {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

/** Local "YYYY-MM-DD HH:MM" (CSV: readable and sortable). */
export function localStamp(iso: string | null | undefined): string {
  if (!iso) return '';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '';
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

export interface ReportTotals { count: number; seconds: number }

export function reportTotals(rows: ReportRow[], now: Date = new Date()): ReportTotals {
  return { count: rows.length, seconds: rows.reduce((sum, row) => sum + rowSeconds(row, now), 0) };
}

// ---------------------------------------------------------------------------------------------
// Labels (the caller passes the texts in the person's language)

export interface ReportLabels {
  technician: string;
  email: string;
  device: string;
  remoteId: string;
  start: string;
  end: string;
  duration: string;
  durationSeconds: string;
  kind: string;
  colleague: string;
  note: string;
  sessionId: string;
  attended: string;
  unattended: string;
  running: string;
  yes: string;
  no: string;
}

export function kindLabel(row: Pick<ReportRow, 'kind' | 'colleague'>, labels: ReportLabels): string {
  const kind = row.kind === 'unattended' ? labels.unattended : labels.attended;
  return row.colleague ? `${kind} · ${labels.colleague}` : kind;
}

// ---------------------------------------------------------------------------------------------
// CSV

/** UTF-8 with BOM, RFC 4180 quoting, formula-looking cells neutralised (shared with the data export). */
export function reportCsv(rows: ReportRow[], labels: ReportLabels, now: Date = new Date()): string {
  const columns = ['technician', 'email', 'device', 'remote_id', 'start', 'end', 'duration', 'duration_seconds', 'kind', 'colleague', 'note', 'session_id'];
  const header: Record<string, string> = {
    technician: labels.technician, email: labels.email, device: labels.device, remote_id: labels.remoteId, start: labels.start,
    end: labels.end, duration: labels.duration, duration_seconds: labels.durationSeconds, kind: labels.kind, colleague: labels.colleague,
    note: labels.note, session_id: labels.sessionId,
  };
  const data = rows.map((row) => {
    const seconds = rowSeconds(row, now);
    return {
      technician: row.technician_name ?? '',
      email: row.technician_email ?? '',
      device: row.device_name ?? '',
      remote_id: row.connection_id ?? '',
      start: localStamp(row.started_at),
      end: row.ended_at ? localStamp(row.ended_at) : labels.running,
      duration: formatDuration(seconds),
      duration_seconds: seconds,
      kind: row.kind === 'unattended' ? labels.unattended : labels.attended,
      colleague: row.colleague ? labels.yes : labels.no,
      note: row.note ?? '',
      session_id: row.session_id,
    };
  });
  // toCsv writes the column keys as the first line; replace it with the translated header.
  const body = toCsv(data, columns);
  const firstBreak = body.indexOf('\r\n');
  const headerLine = toCsv([header], columns).split('\r\n')[1];
  return '﻿' + headerLine + body.slice(firstBreak);
}

// ---------------------------------------------------------------------------------------------
// PDF 1.4 writer

// Unicode code points of WinAnsiEncoding bytes 0x80-0x9F (0 = unused).
const WIN_ANSI_HIGH = [
  0x20ac, 0, 0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021, 0x02c6, 0x2030, 0x0160, 0x2039, 0x0152, 0, 0x017d, 0,
  0, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014, 0x02dc, 0x2122, 0x0161, 0x203a, 0x0153, 0, 0x017e, 0x0178,
];
// Letters without a decomposition and their closest WinAnsi letter.
const SIMILAR: Record<string, string> = { 'Ł': 'L', 'ł': 'l', 'Đ': 'D', 'đ': 'd', 'Ħ': 'H', 'ħ': 'h', 'ı': 'i', 'Ŀ': 'L', 'ŀ': 'l', 'ŉ': 'n', 'Ŧ': 'T', 'ŧ': 't' };
const FROM_UNICODE = new Map<number, number>(WIN_ANSI_HIGH.map((code, index) => [code, 0x80 + index] as [number, number]).filter(([code]) => code !== 0));

/** One WinAnsi byte per character; unknown characters become their base letter, else "?". */
export function winAnsi(text: string): number[] {
  const bytes: number[] = [];
  for (const char of text.replace(/\r\n?/g, '\n')) {
    const code = char.codePointAt(0)!;
    if (code === 0x09 || code === 0x0a) bytes.push(0x20);
    else if (code >= 0x20 && code <= 0x7e) bytes.push(code);
    else if (code >= 0xa0 && code <= 0xff) bytes.push(code);
    else if (FROM_UNICODE.has(code)) bytes.push(FROM_UNICODE.get(code)!);
    else if (code < 0x20 || code === 0x7f || (code >= 0x80 && code < 0xa0)) continue;
    else if (code === 0x2212 || code === 0x2010 || code === 0x2011) bytes.push(0x2d);
    else if (code === 0x202f || code === 0x2009 || code === 0x2007) bytes.push(0x20);
    else if (SIMILAR[char]) bytes.push(SIMILAR[char].charCodeAt(0));
    else {
      const base = char.normalize('NFD').replace(/\p{M}/gu, '');
      const first = base.codePointAt(0) ?? 0;
      bytes.push(base.length === 1 && first >= 0x20 && first <= 0xff && first !== 0x7f && !(first >= 0x80 && first < 0xa0) ? first : 0x3f);
    }
  }
  return bytes;
}

// Helvetica and Helvetica-Bold advance widths (1/1000 em) for ASCII 32-126 (Adobe AFM).
const HELVETICA = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556,
  556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667,
  611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
  556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584];
const HELVETICA_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556,
  556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722,
  667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611,
  611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584];

function byteWidth(byte: number, bold: boolean): number {
  const table = bold ? HELVETICA_BOLD : HELVETICA;
  if (byte >= 32 && byte <= 126) return table[byte - 32];
  if (byte === 0x85) return 1000; // …
  if (byte === 0x96) return 556; // –
  if (byte === 0x97) return 1000; // —
  if (byte >= 0x91 && byte <= 0x94) return bold ? 278 : 222; // quotes
  if (byte >= 0xc0) {
    // Accented letters have the width of their base letter.
    const base = String.fromCharCode(byte).normalize('NFD').charCodeAt(0);
    if (base >= 32 && base <= 126) return table[base - 32];
    if (byte === 0xdf) return 611; // ß
    if (byte === 0xc6 || byte === 0xe6) return byte === 0xc6 ? 1000 : 889;
  }
  return 556;
}

export function textWidth(bytes: number[], size: number, bold = false): number {
  return (bytes.reduce((sum, byte) => sum + byteWidth(byte, bold), 0) * size) / 1000;
}

const ELLIPSIS = 0x85;

/** Cuts the text to `width` points, ending with "…" when it was too long. */
export function fitBytes(bytes: number[], width: number, size: number, bold = false): number[] {
  if (textWidth(bytes, size, bold) <= width) return bytes;
  const room = width - textWidth([ELLIPSIS], size, bold);
  const out: number[] = [];
  let used = 0;
  for (const byte of bytes) {
    const next = (byteWidth(byte, bold) * size) / 1000;
    if (used + next > room) break;
    out.push(byte);
    used += next;
  }
  while (out.length && out[out.length - 1] === 0x20) out.pop();
  return [...out, ELLIPSIS];
}

/** Greedy word wrap to lines of at most `width` points; at most `maxLines` (the last one cut with "…"). */
export function wrapText(text: string, width: number, size: number, maxLines: number, bold = false): number[][] {
  const lines: number[][] = [];
  const paragraphs = text.replace(/\r\n?/g, '\n').split('\n');
  let truncated = false;
  outer: for (const paragraph of paragraphs) {
    const words = winAnsi(paragraph).reduce<number[][]>((acc, byte) => {
      if (byte === 0x20) acc.push([]);
      else acc[acc.length - 1].push(byte);
      return acc;
    }, [[]]).filter((word) => word.length);
    let line: number[] = [];
    for (let word of words) {
      const candidate = line.length ? [...line, 0x20, ...word] : word;
      if (textWidth(candidate, size, bold) <= width) {
        line = candidate;
        continue;
      }
      if (line.length) {
        lines.push(line);
        if (lines.length >= maxLines) { truncated = true; break outer; }
        line = [];
      }
      // A word longer than the column is broken.
      while (textWidth(word, size, bold) > width) {
        let cut = word.length;
        while (cut > 1 && textWidth(word.slice(0, cut), size, bold) > width) cut--;
        lines.push(word.slice(0, cut));
        if (lines.length >= maxLines) { truncated = true; break outer; }
        word = word.slice(cut);
      }
      line = word;
    }
    if (line.length || !words.length) {
      if (lines.length >= maxLines) { truncated = true; break; }
      if (line.length) lines.push(line);
    }
  }
  if (truncated && lines.length) {
    const last = [...lines[lines.length - 1]];
    while (last.length && textWidth([...last, ELLIPSIS], size, bold) > width) last.pop();
    while (last.length && last[last.length - 1] === 0x20) last.pop();
    lines[lines.length - 1] = [...last, ELLIPSIS];
  }
  return lines.length ? lines : [[]];
}

/** A PDF literal string: ( ) \ escaped, everything outside printable ASCII as an octal escape. */
export function pdfString(bytes: number[]): string {
  let out = '(';
  for (const byte of bytes) {
    if (byte === 0x28 || byte === 0x29 || byte === 0x5c) out += '\\' + String.fromCharCode(byte);
    else if (byte < 0x20 || byte > 0x7e) out += '\\' + byte.toString(8).padStart(3, '0');
    else out += String.fromCharCode(byte);
  }
  return out + ')';
}

/** A text string outside content streams (document information): UTF-16BE with a byte order mark, as hex. */
export function pdfTextString(text: string): string {
  let hex = '<FEFF';
  for (let index = 0; index < text.length; index++) hex += text.charCodeAt(index).toString(16).padStart(4, '0').toUpperCase();
  return hex + '>';
}

const num = (value: number) => (Math.round(value * 100) / 100).toString();

export interface PdfColumn { label: string; width: number; align?: 'left' | 'right'; wrap?: boolean }

export interface PdfTable {
  title: string;
  subtitle: string[];
  columns: PdfColumn[];
  rows: string[][];
  totals: string[];
  footer: (page: number, pages: number) => string;
  /** Document title for the PDF metadata. */
  documentTitle?: string;
  createdAt?: Date;
}

const PAGE_W = 842; // A4 landscape, points
const PAGE_H = 595;
const MARGIN = 36;
const FONT = 8.5;
const LINE = 11;
const PAD = 4;
const MAX_NOTE_LINES = 6;

interface Block { lines: number[][][]; height: number }

/** Builds the whole PDF as a string of ASCII characters (one per byte). */
export function buildPdf(table: PdfTable): string {
  const tableWidth = PAGE_W - 2 * MARGIN;
  const declared = table.columns.reduce((sum, column) => sum + column.width, 0);
  const scale = declared > 0 ? tableWidth / declared : 1;
  const columns = table.columns.map((column) => ({ ...column, width: column.width * scale }));

  const headerTop = PAGE_H - MARGIN;
  const headerHeight = 22 + table.subtitle.length * 12 + 10;
  const tableTop = headerTop - headerHeight;
  const bottom = MARGIN + 18;
  const headRow: Block = {
    lines: columns.map((column) => wrapText(column.label, column.width - 2 * PAD, FONT, 2, true)),
    height: 0,
  };
  headRow.height = Math.max(...headRow.lines.map((cell) => cell.length)) * LINE + 2 * PAD;
  const blocks: Block[] = table.rows.map((row) => {
    const lines = columns.map((column, index) => {
      const text = row[index] ?? '';
      return column.wrap ? wrapText(text, column.width - 2 * PAD, FONT, MAX_NOTE_LINES) : [fitBytes(winAnsi(text), column.width - 2 * PAD, FONT)];
    });
    return { lines, height: Math.max(...lines.map((cell) => cell.length)) * LINE + 2 * PAD };
  });

  // Paginate: each page holds the header, the column heads and as many rows as fit.
  const pages: Block[][] = [[]];
  let y = tableTop - headRow.height;
  for (const block of blocks) {
    if (y - block.height < bottom && pages[pages.length - 1].length) {
      pages.push([]);
      y = tableTop - headRow.height;
    }
    pages[pages.length - 1].push(block);
    y -= block.height;
  }
  const totalsHeight = 10 + table.totals.length * 13;
  if (y - totalsHeight < bottom) {
    // The totals alone on a last page: no column heads there.
    pages.push([]);
    y = tableTop;
  }
  const lastPageY = y;

  const text = (bytes: number[], x: number, baseline: number, size: number, bold = false) =>
    `BT /${bold ? 'F2' : 'F1'} ${num(size)} Tf ${num(x)} ${num(baseline)} Td ${pdfString(bytes)} Tj ET\n`;

  function cellText(lines: number[][], column: (typeof columns)[number], x: number, top: number, bold: boolean): string {
    let out = '';
    lines.forEach((line, index) => {
      const baseline = top - PAD - FONT + 1 - index * LINE;
      const left = column.align === 'right' ? x + column.width - PAD - textWidth(line, FONT, bold) : x + PAD;
      if (line.length) out += text(line, left, baseline, FONT, bold);
    });
    return out;
  }

  const streams = pages.map((pageBlocks, pageIndex) => {
    let out = '0 g\n';
    out += text(fitBytes(winAnsi(table.title), tableWidth, 15, true), MARGIN, headerTop - 15, 15, true);
    table.subtitle.forEach((line, index) => {
      out += '0.3 g\n' + text(fitBytes(winAnsi(line), tableWidth, 9), MARGIN, headerTop - 32 - index * 12, 9) + '0 g\n';
    });
    // Column heads on a light band (not on a last page that holds only the totals).
    let top = tableTop;
    const row = (lines: number[][][], bold: boolean) => {
      let x = MARGIN;
      columns.forEach((column, index) => { out += cellText(lines[index], column, x, top, bold); x += column.width; });
    };
    if (pageBlocks.length || pageIndex === 0) {
      out += `0.93 g ${num(MARGIN)} ${num(top - headRow.height)} ${num(tableWidth)} ${num(headRow.height)} re f 0 g\n`;
      row(headRow.lines, true);
      top -= headRow.height;
    }
    for (const block of pageBlocks) {
      row(block.lines, false);
      top -= block.height;
      out += `0.8 G 0.4 w ${num(MARGIN)} ${num(top)} m ${num(MARGIN + tableWidth)} ${num(top)} l S 0 G\n`;
    }
    if (pageIndex === pages.length - 1) {
      let baseline = lastPageY - 18;
      for (const line of table.totals) {
        out += text(fitBytes(winAnsi(line), tableWidth, 9.5, true), MARGIN, baseline, 9.5, true);
        baseline -= 13;
      }
    }
    const footer = winAnsi(table.footer(pageIndex + 1, pages.length));
    out += '0.4 g\n' + text(footer, PAGE_W - MARGIN - textWidth(footer, 8), MARGIN - 4, 8) + '0 g\n';
    return out;
  });

  // Objects: 1 catalog, 2 page tree, 3 Helvetica, 4 Helvetica-Bold, 5 info, then page + content per page.
  const objects: string[] = [];
  const pageIds = pages.map((_, index) => 6 + index * 2);
  objects[1] = '<< /Type /Catalog /Pages 2 0 R >>';
  objects[2] = `<< /Type /Pages /Kids [${pageIds.map((id) => `${id} 0 R`).join(' ')}] /Count ${pages.length} >>`;
  objects[3] = '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>';
  objects[4] = '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>';
  const created = table.createdAt ?? new Date();
  const stamp = created.toISOString().replace(/[-:T]/g, '').slice(0, 14);
  objects[5] = `<< /Producer (Mixel Remote) /Title ${pdfTextString(table.documentTitle ?? table.title)} /CreationDate (D:${stamp}Z) >>`;
  streams.forEach((stream, index) => {
    const pageId = pageIds[index];
    objects[pageId] = `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_W} ${PAGE_H}] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents ${pageId + 1} 0 R >>`;
    objects[pageId + 1] = `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`;
  });

  let pdf = '%PDF-1.4\n';
  const offsets: number[] = [];
  for (let id = 1; id < objects.length; id++) {
    offsets[id] = pdf.length;
    pdf += `${id} 0 obj\n${objects[id]}\nendobj\n`;
  }
  const xref = pdf.length;
  pdf += `xref\n0 ${objects.length}\n0000000000 65535 f \n`;
  for (let id = 1; id < objects.length; id++) pdf += `${String(offsets[id]).padStart(10, '0')} 00000 n \n`;
  pdf += `trailer\n<< /Size ${objects.length} /Root 1 0 R /Info 5 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return pdf;
}

// ---------------------------------------------------------------------------------------------
// The session report as a PDF

export interface ReportPdfOptions {
  company: string;
  title: string;
  range: string;
  generated: string;
  totals: string;
  page: (page: number, pages: number) => string;
  labels: ReportLabels;
  formatTime: (iso: string) => string;
  now?: Date;
}

export function reportPdf(rows: ReportRow[], options: ReportPdfOptions): string {
  const { labels } = options;
  const now = options.now ?? new Date();
  return buildPdf({
    title: options.company,
    documentTitle: `${options.title} – ${options.company}`,
    subtitle: [`${options.title} · ${options.range}`, options.generated],
    columns: [
      { label: labels.technician, width: 125 },
      { label: labels.device, width: 120 },
      { label: labels.start, width: 88 },
      { label: labels.end, width: 88 },
      { label: labels.duration, width: 58, align: 'right' },
      { label: labels.kind, width: 92, wrap: true },
      { label: labels.note, width: 199, wrap: true },
    ],
    rows: rows.map((row) => [
      row.technician_name || row.technician_email || '',
      row.device_name ?? '',
      options.formatTime(row.started_at),
      row.ended_at ? options.formatTime(row.ended_at) : labels.running,
      formatDuration(rowSeconds(row, now)),
      kindLabel(row, labels),
      row.note ?? '',
    ]),
    totals: [options.totals],
    footer: options.page,
    createdAt: now,
  });
}

/** "mixel-remote-<company>-sessions-<from>-<to>.<ext>" */
export function reportFileName(slug: string, from: string, to: string, extension: 'csv' | 'pdf'): string {
  const clean = slug.replace(/[^a-z0-9-]/gi, '').toLowerCase() || 'company';
  return `mixel-remote-${clean}-sessions-${from}-${to}.${extension}`;
}
