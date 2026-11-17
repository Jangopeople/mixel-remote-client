//! The host's picture rate follows what the line delivers (not only what it loses).
//!
//! `video_pacing` only slows down when a complete picture was lost. A line that queues instead of
//! dropping (a deep buffer, a relay) never loses one: the delay grows, the encoder aims higher
//! and higher, and in the lab on a 4 Mbit/s line the technician saw 6 pictures a second and in the
//! end the connectivity checks starved behind the pictures. The technician therefore reports,
//! five times a second, the highest picture packet it received, how long ago, and how many bytes
//! arrived since the last report ([`Report`], announced by the host as the feature
//! [`FEATURE`]). From that this controller derives
//! - the delay the line adds (round trip of the newest packet minus the smallest one seen lately),
//! - the delivered rate, and
//! - the bytes still on their way.
//!
//! The limit is the pace in the units of `video_pacing` (the encoder aims at 60 % of it). Rules: when
//! the queue delay stays above [`QUEUE_HIGH`] for two reports in a row (one late report is a
//! retransmission on the lossy control channel, not a queue) the limit is cut to 1.4 times what was
//! delivered (the encoder then sends 85 % of it) and never by more than 15 % at once; when the queue is
//! low the limit climbs (fast until the first cut, then 8 % a second) but never above 2.5 times what
//! was delivered. [`RateControl::backlogged`] says when more than the line
//! can hold is on its way: the host then encodes nothing new (the screen is captured again when
//! it goes on, so the newest picture wins and the delay cannot grow). Without reports (an older
//! technician) nothing changes. When they stop for [`SILENCE`] the limit decays.
use std::collections::VecDeque;
use std::time::{Duration, Instant};

/// Announced by hosts that use the reports (`a=x-mixel-features:`).
pub const FEATURE: &str = "vrate";
/// Message prefix on the encrypted control channel, technician to host.
pub const PREFIX: &[u8] = b"mixel-video-report-v1";
/// Reports this far apart.
pub const REPORT_EVERY: Duration = Duration::from_millis(200);
/// Extra delay above the base that counts as a queue building up.
pub const QUEUE_HIGH: Duration = Duration::from_millis(200);
/// Below this the line is not queueing and the limit may climb.
pub const QUEUE_LOW: Duration = Duration::from_millis(80);
/// Without a report for this long the rate decays.
pub const SILENCE: Duration = Duration::from_secs(3);
/// What may be queued in the line beyond one round trip.
pub const QUEUE_ALLOWED: Duration = Duration::from_millis(300);
pub const MIN_LIMIT: f64 = 125_000.0;
pub const START_LIMIT: f64 = 500_000.0;
pub const MAX_LIMIT: f64 = 12_000_000.0;
const HISTORY: usize = 8192;
const BASE_WINDOW: Duration = Duration::from_secs(30);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Report {
    /// Highest picture packet sequence number received.
    pub seq: u16,
    /// How long ago that packet arrived.
    pub age: Duration,
    /// Time covered by `bytes`.
    pub interval: Duration,
    /// Picture packet bytes received in that time.
    pub bytes: u32,
}

impl Report {
    pub fn encode(&self) -> Vec<u8> {
        let mut out = PREFIX.to_vec();
        out.extend_from_slice(&self.seq.to_be_bytes());
        out.extend_from_slice(&(self.age.as_millis().min(65_535) as u16).to_be_bytes());
        out.extend_from_slice(&(self.interval.as_millis().min(65_535) as u16).to_be_bytes());
        out.extend_from_slice(&self.bytes.to_be_bytes());
        out
    }
    pub fn decode(plain: &[u8]) -> Option<Self> {
        let rest = plain.strip_prefix(PREFIX)?;
        let rest: [u8; 10] = rest.try_into().ok()?;
        Some(Self {
            seq: u16::from_be_bytes([rest[0], rest[1]]),
            age: Duration::from_millis(u64::from(u16::from_be_bytes([rest[2], rest[3]]))),
            interval: Duration::from_millis(u64::from(u16::from_be_bytes([rest[4], rest[5]]))),
            bytes: u32::from_be_bytes([rest[6], rest[7], rest[8], rest[9]]),
        })
    }
}

/// Technician side: what arrived, turned into [`Report`]s.
pub struct Receiver {
    newest: Option<(u16, Instant)>,
    bytes: u32,
    since: Instant,
}

impl Receiver {
    pub fn new(now: Instant) -> Self {
        Self { newest: None, bytes: 0, since: now }
    }
    /// A picture packet of `size` bytes (header and payload) arrived.
    pub fn packet(&mut self, now: Instant, seq: u16, size: usize) {
        self.bytes = self.bytes.saturating_add(size as u32);
        if self.newest.is_none_or(|(newest, _)| seq.wrapping_sub(newest) < 0x8000) {
            self.newest = Some((seq, now));
        }
    }
    /// The next report, when one is due (not before the first packet).
    pub fn due(&mut self, now: Instant) -> Option<Report> {
        let (seq, at) = self.newest?;
        let interval = now.saturating_duration_since(self.since);
        if interval < REPORT_EVERY {
            return None;
        }
        let report = Report { seq, age: now.saturating_duration_since(at), interval, bytes: self.bytes };
        self.bytes = 0;
        self.since = now;
        Some(report)
    }
}

/// Host side.
pub struct RateControl {
    /// (sequence number, when sent, bytes sent up to and including it).
    sent: VecDeque<(u16, Instant, u64)>,
    sent_total: u64,
    acked_total: u64,
    delays: VecDeque<(Instant, Duration)>,
    limit: f64,
    delivered: f64,
    slow_start: bool,
    last_report: Option<Instant>,
    last_change: Instant,
    last_cut: Option<Instant>,
    /// The first report of the current run above [`QUEUE_HIGH`], and how many there were.
    high: Option<(Instant, u32)>,
    queue: Duration,
    base: Duration,
}

impl RateControl {
    pub fn new(now: Instant) -> Self {
        Self {
            sent: VecDeque::new(),
            sent_total: 0,
            acked_total: 0,
            delays: VecDeque::new(),
            limit: START_LIMIT,
            delivered: 0.0,
            slow_start: true,
            last_report: None,
            last_change: now,
            last_cut: None,
            high: None,
            queue: Duration::ZERO,
            base: Duration::ZERO,
        }
    }
    /// What the line carries at the moment, bytes a second.
    pub fn limit(&self) -> f64 {
        self.limit
    }
    /// The queue delay of the last report.
    pub fn queue_delay(&self) -> Duration {
        self.queue
    }
    /// The smallest round trip of the last 30 seconds.
    pub fn base_delay(&self) -> Duration {
        self.base
    }
    /// A picture packet left.
    pub fn sent(&mut self, now: Instant, seq: u16, size: usize) {
        self.sent_total += size as u64;
        self.sent.push_back((seq, now, self.sent_total));
        if self.sent.len() > HISTORY {
            self.sent.pop_front();
        }
    }
    /// Bytes sent that no report has covered yet.
    pub fn in_flight(&self) -> u64 {
        self.sent_total - self.acked_total
    }
    /// More is on its way than the line holds: send nothing new until reports catch up.
    pub fn backlogged(&self, now: Instant) -> bool {
        let Some(last) = self.last_report else { return false };
        if now.saturating_duration_since(last) >= SILENCE {
            return false;
        }
        let window = self.limit * 0.6 * (self.base + QUEUE_ALLOWED).as_secs_f64() + 20_000.0;
        self.in_flight() as f64 > window
    }
    /// Call regularly: decays the limit when the reports stopped.
    pub fn tick(&mut self, now: Instant) {
        let Some(last) = self.last_report else { return };
        if now.saturating_duration_since(last) >= SILENCE
            && now.saturating_duration_since(self.last_change) >= Duration::from_secs(2)
        {
            self.limit = (self.limit / 2.0).max(MIN_LIMIT);
            self.last_change = now;
        }
    }
    pub fn report(&mut self, now: Instant, report: Report) {
        let Some(&(_, sent_at, total)) = self.sent.iter().rev().find(|(seq, _, _)| *seq == report.seq) else {
            return;
        };
        self.last_report = Some(now);
        self.acked_total = self.acked_total.max(total);
        let delay = now.saturating_duration_since(sent_at).saturating_sub(report.age);
        self.delays.push_back((now, delay));
        while self.delays.front().is_some_and(|(at, _)| now.saturating_duration_since(*at) > BASE_WINDOW) {
            self.delays.pop_front();
        }
        self.base = self.delays.iter().map(|(_, d)| *d).min().unwrap_or(delay);
        let queue = delay.saturating_sub(self.base);
        self.queue = queue;
        if !report.interval.is_zero() && report.bytes > 0 {
            let rate = f64::from(report.bytes) / report.interval.as_secs_f64();
            self.delivered = if self.delivered == 0.0 { rate } else { self.delivered * 0.7 + rate * 0.3 };
        }
        let dt = now.saturating_duration_since(self.last_change).as_secs_f64().min(1.0);
        self.high = if queue > QUEUE_HIGH {
            Some(self.high.map_or((now, 1), |(since, count)| (since, count + 1)))
        } else {
            None
        };
        let persistent = self
            .high
            .is_some_and(|(since, count)| count >= 2 && now.saturating_duration_since(since) >= Duration::from_millis(300));
        if persistent {
            if self.last_cut.is_none_or(|cut| now.saturating_duration_since(cut) >= Duration::from_millis(500)) {
                let by_delivery = if self.delivered > 0.0 { self.delivered * 1.4 } else { self.limit };
                self.limit = by_delivery.min(self.limit * 0.85).max(MIN_LIMIT);
                self.slow_start = false;
                self.last_cut = Some(now);
                self.last_change = now;
            }
        } else if queue < QUEUE_LOW
            && self.last_cut.is_none_or(|cut| now.saturating_duration_since(cut) >= Duration::from_secs(1))
        {
            let cap = (self.delivered * 2.5).clamp(START_LIMIT, MAX_LIMIT);
            let growth: f64 = if self.slow_start { 2.0 } else { 1.08 };
            self.limit = self.limit.max((self.limit * growth.powf(dt)).min(cap));
            self.last_change = now;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A line: `rate` bytes a second, `latency` one way, FIFO queue. Packets are 1200 bytes.
    struct Line {
        rate: f64,
        latency: Duration,
        free_at: f64,
        /// (seq, arrives at (s), bytes)
        arrivals: Vec<(u16, f64, usize)>,
    }
    impl Line {
        fn send(&mut self, at: f64, seq: u16, size: usize) {
            let start = self.free_at.max(at);
            self.free_at = start + size as f64 / self.rate;
            self.arrivals.push((seq, self.free_at + self.latency.as_secs_f64(), size));
        }
    }

    /// Runs `seconds` of a source that sends at `limit * 0.6` (the encoder follows the limit as
    /// in the host), reports every 200 ms. Returns (final limit, worst queue delay seen in s,
    /// bytes delivered in the last 10 s / 10).
    fn simulate(line: &mut Line, control: &mut RateControl, t0: Instant, from: f64, seconds: f64, seq: &mut u16) -> (f64, f64, f64) {
        let mut now = from;
        let mut receiver = Receiver::new(t0 + Duration::from_secs_f64(from));
        let mut next_report = from + 0.2;
        let mut worst: f64 = 0.0;
        let mut delivered = 0usize;
        let mut seen = 0;
        let mut travelling: Vec<(f64, Report)> = Vec::new();
        while now < from + seconds {
            // 10 ms steps: the source sends what the encoder allows unless backlogged.
            let instant = t0 + Duration::from_secs_f64(now);
            control.tick(instant);
            if !control.backlogged(instant) {
                let bytes = (control.limit() * 0.6 * 0.01) as usize;
                let mut left = bytes;
                while left > 0 {
                    let size = left.min(1200);
                    control.sent(instant, *seq, size);
                    line.send(now, *seq, size);
                    *seq = seq.wrapping_add(1);
                    left -= size;
                }
            }
            while seen < line.arrivals.len() && line.arrivals[seen].1 <= now {
                let (s, at, size) = line.arrivals[seen];
                receiver.packet(t0 + Duration::from_secs_f64(at), s, size);
                if now > from + seconds - 10.0 {
                    delivered += size;
                }
                seen += 1;
            }
            if now >= next_report {
                // The report travels back over the same latency.
                if let Some(report) = receiver.due(t0 + Duration::from_secs_f64(now)) {
                    travelling.push((now + line.latency.as_secs_f64(), report));
                }
                next_report += 0.2;
            }
            while travelling.first().is_some_and(|(at, _)| *at <= now) {
                let (at, report) = travelling.remove(0);
                control.report(t0 + Duration::from_secs_f64(at), report);
            }
            worst = worst.max((line.free_at - now).max(0.0));
            now += 0.01;
        }
        (control.limit(), worst, delivered as f64 / 10.0)
    }

    #[test]
    fn one_late_report_does_not_cut_the_limit_but_a_lasting_queue_does() {
        let t0 = Instant::now();
        let mut control = RateControl::new(t0);
        let mut at = t0;
        let mut seq = 0u16;
        // A steady line: round trip 100 ms, 20 reports.
        for _ in 0..20 {
            seq += 1;
            control.sent(at, seq, 12_000);
            at += Duration::from_millis(200);
            control.report(at, Report { seq, age: Duration::from_millis(100), interval: Duration::from_millis(200), bytes: 12_000 });
        }
        let steady = control.limit();
        // One report held up by a retransmission (500 ms late), the next ones normal again.
        seq += 1;
        control.sent(at, seq, 12_000);
        at += Duration::from_millis(700);
        control.report(at, Report { seq, age: Duration::from_millis(100), interval: Duration::from_millis(200), bytes: 12_000 });
        assert!(control.limit() >= steady, "one late report: {} vs {steady}", control.limit());
        for _ in 0..3 {
            seq += 1;
            control.sent(at, seq, 12_000);
            at += Duration::from_millis(200);
            control.report(at, Report { seq, age: Duration::from_millis(100), interval: Duration::from_millis(200), bytes: 12_000 });
        }
        assert!(control.limit() >= steady);
        // Now every report is 400 ms late and only 60 kB/s arrive: a queue.
        for _ in 0..6 {
            seq += 1;
            control.sent(at, seq, 12_000);
            at += Duration::from_millis(600);
            control.report(at, Report { seq, age: Duration::from_millis(100), interval: Duration::from_millis(600), bytes: 36_000 });
        }
        assert!(control.limit() < steady * 0.9, "a lasting queue cuts: {} vs {steady}", control.limit());
    }

    #[test]
    fn report_round_trips_on_the_wire() {
        let report = Report { seq: 65_000, age: Duration::from_millis(40), interval: Duration::from_millis(200), bytes: 123_456 };
        assert_eq!(Report::decode(&report.encode()), Some(report));
        assert_eq!(Report::decode(b"mixel-video-report-v1\x00"), None);
        assert_eq!(Report::decode(b"other"), None);
    }

    #[test]
    fn it_converges_below_the_line_rate_and_keeps_the_queue_short() {
        let t0 = Instant::now();
        let mut line = Line { rate: 500_000.0, latency: Duration::from_millis(140), free_at: 0.0, arrivals: Vec::new() };
        let mut control = RateControl::new(t0);
        let mut seq = 0u16;
        let (limit, worst, delivered) = simulate(&mut line, &mut control, t0, 0.0, 120.0, &mut seq);
        assert!(limit * 0.6 < 625_000.0, "the encoder rate {} follows the line (500 kB/s), not the ceiling", limit * 0.6);
        assert!(worst < 1.2, "the queue never held more than {worst} s");
        assert!(delivered > 150_000.0, "pictures keep flowing: {delivered} B/s");
    }

    #[test]
    fn it_recovers_when_the_line_gets_faster() {
        let t0 = Instant::now();
        let mut line = Line { rate: 250_000.0, latency: Duration::from_millis(100), free_at: 0.0, arrivals: Vec::new() };
        let mut control = RateControl::new(t0);
        let mut seq = 0u16;
        let (slow, _, _) = simulate(&mut line, &mut control, t0, 0.0, 60.0, &mut seq);
        line.rate = 4_000_000.0;
        let (fast, _, _) = simulate(&mut line, &mut control, t0, 60.0, 120.0, &mut seq);
        assert!(slow < 600_000.0, "slow line: {slow}");
        assert!(fast > slow * 2.5, "after the line improved: {fast} (was {slow})");
    }

    #[test]
    fn a_fast_line_reaches_a_high_limit_quickly() {
        let t0 = Instant::now();
        let mut line = Line { rate: 12_000_000.0, latency: Duration::from_millis(5), free_at: 0.0, arrivals: Vec::new() };
        let mut control = RateControl::new(t0);
        let mut seq = 0u16;
        let (limit, _, _) = simulate(&mut line, &mut control, t0, 0.0, 15.0, &mut seq);
        assert!(limit >= 3_000_000.0, "limit {limit}");
    }

    #[test]
    fn stale_pictures_are_not_sent_when_too_much_is_on_the_way() {
        let t0 = Instant::now();
        let mut control = RateControl::new(t0);
        let mut seq = 0u16;
        // One report so the controller knows reports arrive.
        control.sent(t0, 0, 1200);
        control.report(t0 + Duration::from_millis(100), Report { seq: 0, age: Duration::ZERO, interval: Duration::from_millis(200), bytes: 1200 });
        assert!(!control.backlogged(t0 + Duration::from_millis(100)));
        for _ in 0..400 {
            seq += 1;
            control.sent(t0 + Duration::from_millis(100), seq, 1200);
        }
        assert!(control.backlogged(t0 + Duration::from_millis(110)), "480 kB unreported is more than the line holds");
        // Without reports for a while it never blocks (a technician that stopped reporting).
        assert!(!control.backlogged(t0 + Duration::from_secs(5)));
    }

    #[test]
    fn without_reports_nothing_changes_and_silence_decays_the_limit() {
        let t0 = Instant::now();
        let mut control = RateControl::new(t0);
        control.sent(t0, 1, 1200);
        assert!(!control.backlogged(t0 + Duration::from_secs(10)));
        control.tick(t0 + Duration::from_secs(10));
        assert_eq!(control.limit(), START_LIMIT, "no report was ever seen");
        control.sent(t0, 2, 1200);
        control.report(t0 + Duration::from_millis(50), Report { seq: 2, age: Duration::ZERO, interval: Duration::from_millis(200), bytes: 2400 });
        let before = control.limit();
        control.tick(t0 + Duration::from_secs(9));
        assert!(control.limit() < before);
    }

    #[test]
    fn the_receiver_reports_every_200_ms_with_the_newest_packet() {
        let t0 = Instant::now();
        let mut receiver = Receiver::new(t0);
        assert_eq!(receiver.due(t0 + Duration::from_secs(1)), None, "nothing arrived yet");
        receiver.packet(t0 + Duration::from_millis(10), 65_535, 1000);
        receiver.packet(t0 + Duration::from_millis(20), 0, 1000);
        receiver.packet(t0 + Duration::from_millis(30), 65_534, 1000);
        assert_eq!(receiver.due(t0 + Duration::from_millis(100)), None);
        let report = receiver.due(t0 + Duration::from_millis(250)).unwrap();
        assert_eq!((report.seq, report.bytes, report.age), (0, 3000, Duration::from_millis(230)));
    }
}
