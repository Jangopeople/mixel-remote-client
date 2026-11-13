//! Session report export: CSV and PDF. A port of the desktop app's `frontends/desktop/report-export.ts`
//! (itself a port of the portal's `lib/reportExport.ts`): the same bytes for the same rows and texts,
//! checked against the TypeScript output by the `golden` tests (`scripts/report-golden.mjs` makes the
//! fixtures). CSV: UTF-8 with BOM, RFC 4180 quoting, formula-looking cells neutralised. PDF: a
//! hand-written PDF 1.4, Helvetica in WinAnsi, landscape A4, paginated, ASCII only.
//! Pure functions: local times come in through the `stamp` / `when` callbacks.
use crate::models::SessionRow;
use unicode_normalization::{UnicodeNormalization, char::is_combining_mark};

/// Column titles and words of the report, in the person's language.
#[derive(Clone, Debug)]
pub struct Labels {
    pub technician: String,
    pub email: String,
    pub device: String,
    pub remote_id: String,
    pub start: String,
    pub end: String,
    pub duration: String,
    pub duration_seconds: String,
    pub kind: String,
    pub colleague: String,
    pub note: String,
    pub session_id: String,
    pub attended: String,
    pub unattended: String,
    pub running: String,
    pub yes: String,
    pub no: String,
}

/// Seconds of a row; a running session counts until `now` (Unix seconds) when the service gave none.
pub fn row_seconds(row: &SessionRow, now: i64) -> i64 {
    if row.duration_seconds >= 0 {
        return row.duration_seconds;
    }
    let start = crate::format::parse_time(&row.started_at);
    let end = match &row.ended_at {
        Some(end) => crate::format::parse_time(end),
        None => Some(now),
    };
    match (start, end) {
        (Some(start), Some(end)) => (end - start).max(0),
        _ => 0,
    }
}

/// 3725 -> "1:02:05": language-neutral, sorts and sums well in a spreadsheet.
pub fn format_duration(seconds: i64) -> String {
    let total = seconds.max(0);
    format!("{}:{:02}:{:02}", total / 3600, (total % 3600) / 60, total % 60)
}

pub fn totals(rows: &[SessionRow], now: i64) -> (usize, i64) {
    (rows.len(), rows.iter().map(|row| row_seconds(row, now)).sum())
}

pub fn kind_label(row: &SessionRow, labels: &Labels) -> String {
    let kind = if row.kind == "unattended" { &labels.unattended } else { &labels.attended };
    if row.colleague { format!("{kind} · {}", labels.colleague) } else { kind.clone() }
}

// -------------------------------------------------------------------------------------------------
// CSV

fn csv_cell(text: &str) -> String {
    let text = if text.starts_with(['=', '+', '-', '@', '\t', '\r']) { format!("'{text}") } else { text.to_owned() };
    if text.contains([',', '"', '\r', '\n']) { format!("\"{}\"", text.replace('"', "\"\"")) } else { text }
}

fn csv_line(cells: &[String]) -> String {
    cells.iter().map(|cell| csv_cell(cell)).collect::<Vec<_>>().join(",")
}

/// The report as CSV; `stamp` turns a time into the local "YYYY-MM-DD HH:MM".
pub fn report_csv(rows: &[SessionRow], labels: &Labels, now: i64, stamp: &dyn Fn(&str) -> String) -> String {
    let header = [
        &labels.technician, &labels.email, &labels.device, &labels.remote_id, &labels.start, &labels.end, &labels.duration,
        &labels.duration_seconds, &labels.kind, &labels.colleague, &labels.note, &labels.session_id,
    ]
    .map(|text| text.to_string());
    let mut lines = vec![csv_line(&header)];
    for row in rows {
        let seconds = row_seconds(row, now);
        lines.push(csv_line(&[
            row.technician_name.clone().unwrap_or_default(),
            row.technician_email.clone().unwrap_or_default(),
            row.device_name.clone(),
            row.connection_id.to_string(),
            stamp(&row.started_at),
            row.ended_at.as_deref().map(stamp).unwrap_or_else(|| labels.running.clone()),
            format_duration(seconds),
            seconds.to_string(),
            if row.kind == "unattended" { labels.unattended.clone() } else { labels.attended.clone() },
            if row.colleague { labels.yes.clone() } else { labels.no.clone() },
            row.note.clone().unwrap_or_default(),
            row.session_id.clone(),
        ]));
    }
    format!("\u{feff}{}\r\n", lines.join("\r\n"))
}

// -------------------------------------------------------------------------------------------------
// PDF 1.4 writer

/// Unicode code points of WinAnsiEncoding bytes 0x80-0x9F (0 = unused).
const WIN_ANSI_HIGH: [u32; 32] = [
    0x20ac, 0, 0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021, 0x02c6, 0x2030, 0x0160, 0x2039, 0x0152, 0, 0x017d, 0, 0, 0x2018, 0x2019,
    0x201c, 0x201d, 0x2022, 0x2013, 0x2014, 0x02dc, 0x2122, 0x0161, 0x203a, 0x0153, 0, 0x017e, 0x0178,
];

/// Letters without a decomposition and their closest WinAnsi letter.
fn similar(letter: char) -> Option<u8> {
    Some(match letter {
        'Ł' | 'Ŀ' => b'L',
        'ł' | 'ŀ' => b'l',
        'Đ' => b'D',
        'đ' => b'd',
        'Ħ' => b'H',
        'ħ' => b'h',
        'ı' => b'i',
        'ŉ' => b'n',
        'Ŧ' => b'T',
        'ŧ' => b't',
        _ => return None,
    })
}

fn latin1_printable(code: u32) -> bool {
    (0x20..=0xff).contains(&code) && code != 0x7f && !(0x80..0xa0).contains(&code)
}

/// One WinAnsi byte per character; unknown characters become their base letter, else "?".
pub fn win_ansi(text: &str) -> Vec<u8> {
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    let mut bytes = Vec::with_capacity(text.len());
    for letter in text.chars() {
        let code = u32::from(letter);
        if code == 0x09 || code == 0x0a {
            bytes.push(0x20);
        } else if (0x20..=0x7e).contains(&code) || (0xa0..=0xff).contains(&code) {
            bytes.push(code as u8);
        } else if let Some(index) = WIN_ANSI_HIGH.iter().position(|high| *high != 0 && *high == code) {
            bytes.push(0x80 + index as u8);
        } else if code < 0x20 || code == 0x7f || (0x80..0xa0).contains(&code) {
            continue;
        } else if matches!(code, 0x2212 | 0x2010 | 0x2011) {
            bytes.push(b'-');
        } else if matches!(code, 0x202f | 0x2009 | 0x2007) {
            bytes.push(b' ');
        } else if let Some(byte) = similar(letter) {
            bytes.push(byte);
        } else {
            let base: String = letter.nfd().filter(|part| !is_combining_mark(*part)).collect();
            let units: Vec<u16> = base.encode_utf16().collect();
            bytes.push(if units.len() == 1 && latin1_printable(u32::from(units[0])) { units[0] as u8 } else { b'?' });
        }
    }
    bytes
}

/// Helvetica and Helvetica-Bold advance widths (1/1000 em) for ASCII 32-126 (Adobe AFM).
const HELVETICA: [u16; 95] = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278,
    278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722,
    667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556,
    556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
];
const HELVETICA_BOLD: [u16; 95] = [
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333,
    333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722,
    667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611,
    611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584,
];

fn byte_width(byte: u8, bold: bool) -> f64 {
    let table = if bold { &HELVETICA_BOLD } else { &HELVETICA };
    let width = |base: u32| (32..=126).contains(&base).then(|| f64::from(table[(base - 32) as usize]));
    if let Some(width) = width(u32::from(byte)) {
        return width;
    }
    match byte {
        0x85 | 0x97 => return 1000.0,
        0x96 => return 556.0,
        0x91..=0x94 => return if bold { 278.0 } else { 222.0 },
        _ => {}
    }
    if byte >= 0xc0 {
        // Accented letters have the width of their base letter.
        let base = char::from(byte).nfd().next().map(u32::from).unwrap_or(0);
        if let Some(width) = width(base) {
            return width;
        }
        if byte == 0xdf {
            return 611.0;
        }
        if byte == 0xc6 {
            return 1000.0;
        }
        if byte == 0xe6 {
            return 889.0;
        }
    }
    556.0
}

pub fn text_width(bytes: &[u8], size: f64, bold: bool) -> f64 {
    bytes.iter().map(|byte| byte_width(*byte, bold)).sum::<f64>() * size / 1000.0
}

const ELLIPSIS: u8 = 0x85;

/// Cuts the text to `width` points, ending with "…" when it was too long.
pub fn fit_bytes(bytes: Vec<u8>, width: f64, size: f64, bold: bool) -> Vec<u8> {
    if text_width(&bytes, size, bold) <= width {
        return bytes;
    }
    let room = width - text_width(&[ELLIPSIS], size, bold);
    let mut out = Vec::new();
    let mut used = 0.0;
    for byte in bytes {
        let next = byte_width(byte, bold) * size / 1000.0;
        if used + next > room {
            break;
        }
        out.push(byte);
        used += next;
    }
    while out.last() == Some(&b' ') {
        out.pop();
    }
    out.push(ELLIPSIS);
    out
}

/// Greedy word wrap to lines of at most `width` points; at most `max_lines` (the last one cut with "…").
pub fn wrap_text(text: &str, width: f64, size: f64, max_lines: usize, bold: bool) -> Vec<Vec<u8>> {
    let mut lines: Vec<Vec<u8>> = Vec::new();
    let normalized = text.replace("\r\n", "\n").replace('\r', "\n");
    let mut truncated = false;
    'outer: for paragraph in normalized.split('\n') {
        let words: Vec<Vec<u8>> = win_ansi(paragraph).split(|byte| *byte == b' ').filter(|word| !word.is_empty()).map(<[u8]>::to_vec).collect();
        let mut line: Vec<u8> = Vec::new();
        for word in &words {
            let mut word = word.clone();
            let candidate = if line.is_empty() { word.clone() } else { [line.as_slice(), b" ", word.as_slice()].concat() };
            if text_width(&candidate, size, bold) <= width {
                line = candidate;
                continue;
            }
            if !line.is_empty() {
                lines.push(std::mem::take(&mut line));
                if lines.len() >= max_lines {
                    truncated = true;
                    break 'outer;
                }
            }
            // A word longer than the column is broken.
            while text_width(&word, size, bold) > width {
                let mut cut = word.len();
                while cut > 1 && text_width(&word[..cut], size, bold) > width {
                    cut -= 1;
                }
                lines.push(word[..cut].to_vec());
                if lines.len() >= max_lines {
                    truncated = true;
                    break 'outer;
                }
                word = word[cut..].to_vec();
            }
            line = word;
        }
        if !line.is_empty() || words.is_empty() {
            if lines.len() >= max_lines {
                truncated = true;
                break;
            }
            if !line.is_empty() {
                lines.push(line);
            }
        }
    }
    if truncated && let Some(last) = lines.last_mut() {
        let mut cut = last.clone();
        while !cut.is_empty() && text_width(&[cut.as_slice(), &[ELLIPSIS]].concat(), size, bold) > width {
            cut.pop();
        }
        while cut.last() == Some(&b' ') {
            cut.pop();
        }
        cut.push(ELLIPSIS);
        *last = cut;
    }
    if lines.is_empty() { vec![Vec::new()] } else { lines }
}

/// A PDF literal string: ( ) \ escaped, everything outside printable ASCII as an octal escape.
pub fn pdf_string(bytes: &[u8]) -> String {
    let mut out = String::from("(");
    for &byte in bytes {
        match byte {
            b'(' | b')' | b'\\' => {
                out.push('\\');
                out.push(char::from(byte));
            }
            0x20..=0x7e => out.push(char::from(byte)),
            _ => out.push_str(&format!("\\{byte:03o}")),
        }
    }
    out.push(')');
    out
}

/// A text string outside content streams (document information): UTF-16BE with a byte order mark, as hex.
pub fn pdf_text_string(text: &str) -> String {
    let mut hex = String::from("<FEFF");
    for unit in text.encode_utf16() {
        hex.push_str(&format!("{unit:04X}"));
    }
    hex.push('>');
    hex
}

/// A number as JavaScript prints `Math.round(value * 100) / 100`.
fn num(value: f64) -> String {
    let scaled = value * 100.0;
    let floor = scaled.floor();
    let rounded = if scaled - floor >= 0.5 { floor + 1.0 } else { floor };
    format!("{}", rounded / 100.0 + 0.0)
}

#[derive(Clone, Copy, PartialEq)]
enum Align {
    Left,
    Right,
}

struct Column {
    label: String,
    width: f64,
    align: Align,
    wrap: bool,
}

struct Table<'a> {
    title: String,
    document_title: String,
    subtitle: Vec<String>,
    columns: Vec<Column>,
    rows: Vec<Vec<String>>,
    totals: Vec<String>,
    footer: &'a dyn Fn(usize, usize) -> String,
    /// Unix seconds (the document's creation date, UTC).
    created: i64,
}

const PAGE_W: f64 = 842.0; // A4 landscape, points
const PAGE_H: f64 = 595.0;
const MARGIN: f64 = 36.0;
const FONT: f64 = 8.5;
const LINE: f64 = 11.0;
const PAD: f64 = 4.0;
const MAX_NOTE_LINES: usize = 6;

struct Block {
    lines: Vec<Vec<Vec<u8>>>,
    height: f64,
}

fn block(lines: Vec<Vec<Vec<u8>>>) -> Block {
    let tallest = lines.iter().map(Vec::len).max().unwrap_or(0);
    Block { height: tallest as f64 * LINE + 2.0 * PAD, lines }
}

/// "YYYYMMDDHHMMSS" in UTC.
fn utc_stamp(at: i64) -> String {
    let (days, seconds) = (at.div_euclid(86_400), at.rem_euclid(86_400));
    // Civil from days (Howard Hinnant's algorithm).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!("{year:04}{month:02}{day:02}{:02}{:02}{:02}", seconds / 3600, seconds % 3600 / 60, seconds % 60)
}

fn text_op(bytes: &[u8], x: f64, baseline: f64, size: f64, bold: bool) -> String {
    format!("BT /{} {} Tf {} {} Td {} Tj ET\n", if bold { "F2" } else { "F1" }, num(size), num(x), num(baseline), pdf_string(bytes))
}

/// Builds the whole PDF (ASCII only).
fn build_pdf(table: &Table) -> String {
    let table_width = PAGE_W - 2.0 * MARGIN;
    let declared: f64 = table.columns.iter().map(|column| column.width).sum();
    let scale = if declared > 0.0 { table_width / declared } else { 1.0 };
    let columns: Vec<Column> =
        table.columns.iter().map(|column| Column { label: column.label.clone(), width: column.width * scale, align: column.align, wrap: column.wrap }).collect();

    let header_top = PAGE_H - MARGIN;
    let header_height = 22.0 + table.subtitle.len() as f64 * 12.0 + 10.0;
    let table_top = header_top - header_height;
    let bottom = MARGIN + 18.0;
    let head_row = block(columns.iter().map(|column| wrap_text(&column.label, column.width - 2.0 * PAD, FONT, 2, true)).collect());
    let blocks: Vec<Block> = table
        .rows
        .iter()
        .map(|row| {
            block(
                columns
                    .iter()
                    .enumerate()
                    .map(|(index, column)| {
                        let text = row.get(index).map(String::as_str).unwrap_or("");
                        if column.wrap {
                            wrap_text(text, column.width - 2.0 * PAD, FONT, MAX_NOTE_LINES, false)
                        } else {
                            vec![fit_bytes(win_ansi(text), column.width - 2.0 * PAD, FONT, false)]
                        }
                    })
                    .collect(),
            )
        })
        .collect();

    // Paginate: each page holds the header, the column heads and as many rows as fit.
    let mut pages: Vec<Vec<&Block>> = vec![Vec::new()];
    let mut y = table_top - head_row.height;
    for item in &blocks {
        if y - item.height < bottom && !pages.last().is_some_and(Vec::is_empty) {
            pages.push(Vec::new());
            y = table_top - head_row.height;
        }
        pages.last_mut().expect("a page").push(item);
        y -= item.height;
    }
    let totals_height = 10.0 + table.totals.len() as f64 * 13.0;
    if y - totals_height < bottom {
        // The totals alone on a last page: no column heads there.
        pages.push(Vec::new());
        y = table_top;
    }
    let last_page_y = y;

    let cell_text = |lines: &[Vec<u8>], column: &Column, x: f64, top: f64, bold: bool| -> String {
        let mut out = String::new();
        for (index, line) in lines.iter().enumerate() {
            let baseline = top - PAD - FONT + 1.0 - index as f64 * LINE;
            let left = if column.align == Align::Right { x + column.width - PAD - text_width(line, FONT, bold) } else { x + PAD };
            if !line.is_empty() {
                out.push_str(&text_op(line, left, baseline, FONT, bold));
            }
        }
        out
    };
    let row_text = |lines: &[Vec<Vec<u8>>], top: f64, bold: bool| -> String {
        let mut out = String::new();
        let mut x = MARGIN;
        for (index, column) in columns.iter().enumerate() {
            out.push_str(&cell_text(&lines[index], column, x, top, bold));
            x += column.width;
        }
        out
    };

    let page_count = pages.len();
    let streams: Vec<String> = pages
        .iter()
        .enumerate()
        .map(|(page_index, page_blocks)| {
            let mut out = String::from("0 g\n");
            out.push_str(&text_op(&fit_bytes(win_ansi(&table.title), table_width, 15.0, true), MARGIN, header_top - 15.0, 15.0, true));
            for (index, line) in table.subtitle.iter().enumerate() {
                out.push_str("0.3 g\n");
                out.push_str(&text_op(&fit_bytes(win_ansi(line), table_width, 9.0, false), MARGIN, header_top - 32.0 - index as f64 * 12.0, 9.0, false));
                out.push_str("0 g\n");
            }
            // Column heads on a light band (not on a last page that holds only the totals).
            let mut top = table_top;
            if !page_blocks.is_empty() || page_index == 0 {
                out.push_str(&format!("0.93 g {} {} {} {} re f 0 g\n", num(MARGIN), num(top - head_row.height), num(table_width), num(head_row.height)));
                out.push_str(&row_text(&head_row.lines, top, true));
                top -= head_row.height;
            }
            for item in page_blocks {
                out.push_str(&row_text(&item.lines, top, false));
                top -= item.height;
                out.push_str(&format!("0.8 G 0.4 w {} {} m {} {} l S 0 G\n", num(MARGIN), num(top), num(MARGIN + table_width), num(top)));
            }
            if page_index == page_count - 1 {
                let mut baseline = last_page_y - 18.0;
                for line in &table.totals {
                    out.push_str(&text_op(&fit_bytes(win_ansi(line), table_width, 9.5, true), MARGIN, baseline, 9.5, true));
                    baseline -= 13.0;
                }
            }
            let footer = win_ansi(&(table.footer)(page_index + 1, page_count));
            out.push_str("0.4 g\n");
            out.push_str(&text_op(&footer, PAGE_W - MARGIN - text_width(&footer, 8.0, false), MARGIN - 4.0, 8.0, false));
            out.push_str("0 g\n");
            out
        })
        .collect();

    // Objects: 1 catalog, 2 page tree, 3 Helvetica, 4 Helvetica-Bold, 5 info, then page + content per page.
    let page_ids: Vec<usize> = (0..page_count).map(|index| 6 + index * 2).collect();
    let mut objects: Vec<String> = vec![String::new(); 6 + page_count * 2];
    objects[1] = "<< /Type /Catalog /Pages 2 0 R >>".into();
    objects[2] = format!("<< /Type /Pages /Kids [{}] /Count {page_count} >>", page_ids.iter().map(|id| format!("{id} 0 R")).collect::<Vec<_>>().join(" "));
    objects[3] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".into();
    objects[4] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>".into();
    objects[5] = format!("<< /Producer (Mixel Remote) /Title {} /CreationDate (D:{}Z) >>", pdf_text_string(&table.document_title), utc_stamp(table.created));
    for (index, stream) in streams.iter().enumerate() {
        let page_id = page_ids[index];
        objects[page_id] = format!(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {} 0 R >>",
            page_id + 1
        );
        objects[page_id + 1] = format!("<< /Length {} >>\nstream\n{stream}\nendstream", stream.len());
    }

    let mut pdf = String::from("%PDF-1.4\n");
    let mut offsets = vec![0usize; objects.len()];
    for id in 1..objects.len() {
        offsets[id] = pdf.len();
        pdf.push_str(&format!("{id} 0 obj\n{}\nendobj\n", objects[id]));
    }
    let xref = pdf.len();
    pdf.push_str(&format!("xref\n0 {}\n0000000000 65535 f \n", objects.len()));
    for offset in offsets.iter().skip(1) {
        pdf.push_str(&format!("{offset:010} 00000 n \n"));
    }
    pdf.push_str(&format!("trailer\n<< /Size {} /Root 1 0 R /Info 5 0 R >>\nstartxref\n{xref}\n%%EOF\n", objects.len()));
    pdf
}

/// Texts around the PDF table, in the person's language.
pub struct PdfTexts<'a> {
    pub company: String,
    pub title: String,
    pub range: String,
    pub generated: String,
    pub totals: String,
    pub page: &'a dyn Fn(usize, usize) -> String,
    pub labels: Labels,
    /// A time as the person reads it (local).
    pub when: &'a dyn Fn(&str) -> String,
    /// Unix seconds.
    pub now: i64,
}

/// The session report as a PDF (the desktop app's and the portal's layout).
pub fn report_pdf(rows: &[SessionRow], texts: &PdfTexts) -> String {
    let labels = &texts.labels;
    let column = |label: &str, width: f64, align: Align, wrap: bool| Column { label: label.to_owned(), width, align, wrap };
    build_pdf(&Table {
        title: texts.company.clone(),
        document_title: format!("{} – {}", texts.title, texts.company),
        subtitle: vec![format!("{} · {}", texts.title, texts.range), texts.generated.clone()],
        columns: vec![
            column(&labels.technician, 125.0, Align::Left, false),
            column(&labels.device, 120.0, Align::Left, false),
            column(&labels.start, 88.0, Align::Left, false),
            column(&labels.end, 88.0, Align::Left, false),
            column(&labels.duration, 58.0, Align::Right, false),
            column(&labels.kind, 92.0, Align::Left, true),
            column(&labels.note, 199.0, Align::Left, true),
        ],
        rows: rows
            .iter()
            .map(|row| {
                vec![
                    row.technician_name.clone().filter(|name| !name.is_empty()).or_else(|| row.technician_email.clone().filter(|email| !email.is_empty())).unwrap_or_default(),
                    row.device_name.clone(),
                    (texts.when)(&row.started_at),
                    row.ended_at.as_deref().map(texts.when).unwrap_or_else(|| labels.running.clone()),
                    format_duration(row_seconds(row, texts.now)),
                    kind_label(row, labels),
                    row.note.clone().unwrap_or_default(),
                ]
            })
            .collect(),
        totals: vec![texts.totals.clone()],
        footer: texts.page,
        created: texts.now,
    })
}

/// "mixel-remote-<company>-sessions-<from>-<to>.<ext>"
pub fn report_file_name(slug: &str, from: &str, to: &str, extension: &str) -> String {
    let clean: String = slug.chars().filter(|letter| letter.is_ascii_alphanumeric() || *letter == '-').collect::<String>().to_lowercase();
    let clean = if clean.is_empty() { "company".to_owned() } else { clean };
    format!("mixel-remote-{clean}-sessions-{from}-{to}.{extension}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Deserialize;

    #[derive(Deserialize)]
    struct Golden {
        now: i64,
        company: String,
        title: String,
        range: String,
        generated: String,
        totals: String,
        labels: std::collections::HashMap<String, String>,
        rows: Vec<serde_json::Value>,
    }

    fn golden() -> (Golden, Vec<SessionRow>, Labels) {
        let golden: Golden = serde_json::from_str(include_str!("../tests/fixtures/report-golden.json")).expect("fixture");
        let rows = golden.rows.iter().map(|row| serde_json::from_value::<SessionRow>(row.clone()).expect("row")).collect();
        let label = |key: &str| golden.labels[key].clone();
        let labels = Labels {
            technician: label("technician"),
            email: label("email"),
            device: label("device"),
            remote_id: label("remoteId"),
            start: label("start"),
            end: label("end"),
            duration: label("duration"),
            duration_seconds: label("durationSeconds"),
            kind: label("kind"),
            colleague: label("colleague"),
            note: label("note"),
            session_id: label("sessionId"),
            attended: label("attended"),
            unattended: label("unattended"),
            running: label("running"),
            yes: label("yes"),
            no: label("no"),
        };
        (golden, rows, labels)
    }

    /// The fixtures' time format: UTC "YYYY-MM-DD HH:MM" (the script runs with TZ=UTC).
    fn utc(iso: &str) -> String {
        let stamp = crate::format::parse_time(iso).map(utc_stamp).unwrap_or_default();
        if stamp.len() < 12 { String::new() } else { format!("{}-{}-{} {}:{}", &stamp[0..4], &stamp[4..6], &stamp[6..8], &stamp[8..10], &stamp[10..12]) }
    }

    #[test]
    fn golden_pdf_matches_the_desktop_writer_byte_for_byte() {
        let (golden, rows, labels) = golden();
        let page = |page: usize, pages: usize| format!("Page {page} of {pages}");
        let texts = PdfTexts {
            company: golden.company.clone(),
            title: golden.title.clone(),
            range: golden.range.clone(),
            generated: golden.generated.clone(),
            totals: golden.totals.clone(),
            page: &page,
            labels,
            when: &utc,
            now: golden.now,
        };
        let pdf = report_pdf(&rows, &texts);
        let expected = include_str!("../tests/fixtures/report-golden.pdf");
        if pdf != expected {
            let at = pdf.bytes().zip(expected.bytes()).position(|(a, b)| a != b).unwrap_or(pdf.len().min(expected.len()));
            panic!("differs at byte {at}: ours {:?} / desktop {:?}", &pdf[at.saturating_sub(80)..(at + 80).min(pdf.len())], &expected[at.saturating_sub(80)..(at + 80).min(expected.len())]);
        }
        // The shape the desktop app's save check expects, and several pages.
        assert!(pdf.starts_with("%PDF-1.4\n") && pdf.is_ascii() && pdf.ends_with("%%EOF\n"));
        assert!(pdf.contains("/Count 5 >>"), "paginated");
    }

    #[test]
    fn golden_csv_matches_the_desktop_writer() {
        let (golden, rows, labels) = golden();
        let csv = report_csv(&rows, &labels, golden.now, &utc);
        assert_eq!(csv, include_str!("../tests/fixtures/report-golden.csv"));
    }

    #[test]
    fn pdf_parts() {
        assert_eq!(win_ansi("Zürich €5 – “ok” Łódź ǅ 中 e\u{301}"), b"Z\xfcrich \x805 \x96 \x93ok\x94 L\xf3dz ? ? e?".to_vec());
        assert_eq!(pdf_string(b"(a\\b)\x85"), "(\\(a\\\\b\\)\\205)");
        assert_eq!(pdf_text_string("Ä😀"), "<FEFF00C4D83DDE00>");
        assert_eq!(num(36.0), "36");
        assert_eq!(num(523.455), "523.46");
        assert_eq!(num(-0.001), "0");
        assert_eq!(format_duration(3725), "1:02:05");
        assert_eq!(utc_stamp(1_794_593_094), "20261113180454");
        let cut = fit_bytes(win_ansi("A very long device name for a narrow column"), 60.0, FONT, false);
        assert_eq!(cut.last(), Some(&ELLIPSIS));
        assert!(text_width(&cut, FONT, false) <= 60.0);
        let lines = wrap_text(&"word ".repeat(200), 100.0, FONT, 6, false);
        assert_eq!(lines.len(), 6);
        assert_eq!(lines[5].last(), Some(&ELLIPSIS));
        assert_eq!(report_file_name("Acme AG!", "2026-11-01", "2026-11-30", "pdf"), "mixel-remote-acmeag-sessions-2026-11-01-2026-11-30.pdf");
        assert_eq!(report_file_name("", "a", "b", "csv"), "mixel-remote-company-sessions-a-b.csv");
    }
}
