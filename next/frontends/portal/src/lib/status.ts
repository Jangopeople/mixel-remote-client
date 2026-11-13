// Logic of the public status page (/status); data from /api/status (worker -> mixel-remote-health).
export type Level = 'operational' | 'degraded' | 'outage';
export interface StatusDay { date: string; uptime: number | null; worst: Level }
export interface StatusComponent { id: string; name: string; status: Level; since: string | null; history: StatusDay[] }
export interface StatusReport { status: Level | 'unknown'; updated_at?: string; reason?: string; components?: StatusComponent[] }

/** The monitor writes every 2 minutes; older than 10 minutes means it is not running. */
export const STALE_AFTER_MS = 10 * 60 * 1000;
export const DAYS = 90;

/** What the page shows overall: the reported level, or "unknown" when the report is missing or stale. */
export function overall(report: StatusReport | null, now: number): Level | 'unknown' {
  if (!report || report.status === 'unknown' || !report.updated_at) return 'unknown';
  const updated = Date.parse(report.updated_at);
  if (!Number.isFinite(updated) || now - updated > STALE_AFTER_MS) return 'unknown';
  return report.status;
}

/** The last 90 days, oldest first, with empty days where the monitor has no data yet. */
export function lastDays(history: StatusDay[], today: Date): (StatusDay | null)[] {
  const byDate = new Map(history.map((day) => [day.date, day]));
  const days: (StatusDay | null)[] = [];
  for (let offset = DAYS - 1; offset >= 0; offset -= 1) {
    const date = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - offset)).toISOString().slice(0, 10);
    days.push(byDate.get(date) ?? null);
  }
  return days;
}

/** Average uptime over the days with data, or null. */
export function uptime(history: StatusDay[]): number | null {
  const values = history.map((day) => day.uptime).filter((value): value is number => typeof value === 'number');
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

