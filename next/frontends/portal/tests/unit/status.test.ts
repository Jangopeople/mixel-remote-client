import { describe, expect, it } from 'vitest';
import { STALE_AFTER_MS, lastDays, overall, uptime, type StatusDay } from '../../src/lib/status';

describe('status page logic', () => {
  const now = Date.parse('2026-11-13T12:00:00Z');
  it('shows the reported level only while the report is fresh', () => {
    expect(overall({ status: 'operational', updated_at: '2026-11-13T11:58:00Z', components: [] }, now)).toBe('operational');
    expect(overall({ status: 'outage', updated_at: '2026-11-13T11:59:00Z', components: [] }, now)).toBe('outage');
    expect(overall({ status: 'operational', updated_at: new Date(now - STALE_AFTER_MS - 1000).toISOString(), components: [] }, now)).toBe('unknown');
    expect(overall({ status: 'unknown', reason: 'monitor_unreachable' }, now)).toBe('unknown');
    expect(overall(null, now)).toBe('unknown');
  });
  it('lays out 90 days ending today with gaps', () => {
    const history: StatusDay[] = [{ date: '2026-11-13', uptime: 100, worst: 'operational' }, { date: '2026-11-11', uptime: 98, worst: 'outage' }];
    const days = lastDays(history, new Date(now));
    expect(days).toHaveLength(90);
    expect(days[89]?.date).toBe('2026-11-13');
    expect(days[88]).toBeNull();
    expect(days[87]?.worst).toBe('outage');
    expect(uptime(history)).toBe(99);
    expect(uptime([])).toBeNull();
  });
});
