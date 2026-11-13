// Company data export (SQL 027 remote_saas_export): the JSON as returned, and one CSV per table.
// CSV: comma-separated, RFC 4180 quoting, UTF-8 with BOM (Excel opens it with the right characters),
// and cells that a spreadsheet would run as a formula are prefixed with an apostrophe.

export interface CompanyExport {
  format: string;
  format_version: number;
  exported_at: string;
  company: Record<string, unknown> & { name: string; slug: string };
  members: Record<string, unknown>[];
  invitations: Record<string, unknown>[];
  groups: Record<string, unknown>[];
  devices: Record<string, unknown>[];
  deploy_links: Record<string, unknown>[];
  terms_acceptances: Record<string, unknown>[];
  audit_log: Record<string, unknown>[];
  [key: string]: unknown;
}

export type CsvTable = 'members' | 'devices' | 'groups' | 'invitations' | 'audit_log';

export const CSV_COLUMNS: Record<CsvTable, string[]> = {
  members: ['name', 'email', 'role', 'joined_at', 'user_id'],
  devices: ['name', 'os', 'group', 'remote_id', 'status', 'last_seen', 'added_via', 'added_by', 'created_at', 'enrolled_at', 'revoked', 'device_id'],
  groups: ['name', 'technicians', 'created_at', 'group_id'],
  invitations: ['email', 'role', 'created_at', 'expires_at', 'accepted_at', 'revoked_at', 'mail_state'],
  audit_log: ['occurred_at', 'actor', 'action', 'target', 'detail', 'id'],
};

function cell(value: unknown): string {
  if (value === null || value === undefined) return '';
  let text = Array.isArray(value) ? value.join('; ') : typeof value === 'object' ? JSON.stringify(value) : String(value);
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(rows: Record<string, unknown>[], columns: string[]): string {
  const lines = [columns.join(','), ...rows.map((row) => columns.map((column) => cell(row[column])).join(','))];
  return '﻿' + lines.join('\r\n') + '\r\n';
}

export function exportFileName(data: CompanyExport, part: string, extension: 'json' | 'csv'): string {
  const day = (data.exported_at ?? new Date().toISOString()).slice(0, 10);
  const slug = String(data.company.slug ?? 'company').replace(/[^a-z0-9-]/gi, '') || 'company';
  return `mixel-remote-${slug}-${part}-${day}.${extension}`;
}

/** Starts a download of `content` in the browser. */
export function download(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
