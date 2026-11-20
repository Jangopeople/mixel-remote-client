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
/// Announced by hosts that also read the technician's own clock in a report ([`Report::clock`]):
/// the delay is then the one-way delay of the picture packets, without the report's way back.
pub const FEATURE_CLOCK: &str = "vrate2";
/// Message prefix on the encrypted control channel, technician to host.
pub const PREFIX: &[u8] = b"mixel-video-report-v1";
/// The same report with the technician's clock appended.
pub const PREFIX_CLOCK: &[u8] = b"mixel-video-report-v2";
/// Reports this far apart (100 ms: halves in-flight lag and detects congestion twice as fast).
pub const REPORT_EVERY: Duration = Duration::from_millis(100);
/// Extra delay above the base that counts as a pile-up (the lowest delay of the last second).
pub const QUEUE_HIGH: Duration = Duration::from_millis(150);
/// Above this the queue is growing: no climbing, a gentle step back.
pub const QUEUE_LOW: Duration = Duration::from_millis(60);
/// The delay that counts is the lowest of this long: jitter and a late report are short, a queue stays.
pub const RECENT: Duration = Duration::from_millis(1000);
/// Without a report for this long the rate decays.
pub const SILENCE: Duration = Duration::from_secs(3);
/// What may be queued in the line beyond one round trip.
pub const QUEUE_ALLOWED: Duration = Duration::from_millis(300);
/// After a queue the limit is held at this many times what was delivered (the encoder takes 60 % of the limit, so it sends 90 % of what the line carried)...
const HOLD_FACTOR: f64 = 1.5;
/// ...and is allowed a quarter more after this long without a queue.
pub const PROBE_EVERY: Duration = Duration::from_secs(8);
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
    /// The technician's own clock (milliseconds since it started reporting) when the report was
    /// made. Only with [`FEATURE_CLOCK`]. Only differences matter (the two clocks are unrelated).
    pub clock: Option<u32>,
}

impl Report {
    pub fn encode(&self) -> Vec<u8> {
        let mut out = if self.clock.is_some() { PREFIX_CLOCK.to_vec() } else { PREFIX.to_vec() };
        out.extend_from_slice(&self.seq.to_be_bytes());
        out.extend_from_slice(&(self.age.as_millis().min(65_535) as u16).to_be_bytes());
        out.extend_from_slice(&(self.interval.as_millis().min(65_535) as u16).to_be_bytes());
        out.extend_from_slice(&self.bytes.to_be_bytes());
        if let Some(clock) = self.clock {
            out.extend_from_slice(&clock.to_be_bytes());
        }
        out
    }
    pub fn decode(plain: &[u8]) -> Option<Self> {
        let (rest, clock) = if let Some(rest) = plain.strip_prefix(PREFIX_CLOCK) {
            let rest: [u8; 14] = rest.try_into().ok()?;
            (rest[..10].to_vec(), Some(u32::from_be_bytes([rest[10], rest[11], rest[12], rest[13]])))
        } else {
            (plain.strip_prefix(PREFIX)?.to_vec(), None)
        };
        let rest: [u8; 10] = rest.as_slice().try_into().ok()?;
        Some(Self {
            clock,
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
    epoch: Instant,
    clocked: bool,
}

impl Receiver {
    pub fn new(now: Instant) -> Self {
        Self { newest: None, bytes: 0, since: now, epoch: now, clocked: false }
    }
    /// Add the own clock to the reports (the host announced [`FEATURE_CLOCK`]).
    pub fn clocked(&mut self, on: bool) {
        self.clocked = on;
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
        let clock = self.clocked.then(|| now.saturating_duration_since(self.epoch).as_millis() as u32);
        let report = Report { seq, age: now.saturating_duration_since(at), interval, bytes: self.bytes, clock };
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
    epoch: Instant,
    /// Round trip of the newest packet of every report (the line's round trip, with the report's way back).
    delays: VecDeque<(Instant, Duration)>,
    /// What the queue signal is made of: one-way delays (with the technician's clock) or round trips,
    /// in milliseconds; only differences count.
    signal: VecDeque<(Instant, i64)>,
    clocked: bool,
    limit: f64,
    delivered: f64,
    slow_start: bool,
    last_report: Option<Instant>,
    last_change: Instant,
    last_cut: Option<Instant>,
    last_soft: Option<Instant>,
    /// The limit the line carried when a queue last built up (grows again by a quarter every
    /// [`PROBE_EVERY`] without one): the climb stops there instead of running into the next queue.
    ceiling: f64,
    last_probe: Instant,
    queue: Duration,
    base: Duration,
    /// Lab statistics: cuts of the limit, worst queue since the last read.
    pub cuts: u32,
    pub softs: u32,
    pub max_queue: Duration,
}

impl RateControl {
    pub fn new(now: Instant) -> Self {
        Self {
            sent: VecDeque::new(),
            sent_total: 0,
            acked_total: 0,
            epoch: now,
            delays: VecDeque::new(),
            signal: VecDeque::new(),
            clocked: false,
            limit: START_LIMIT,
            delivered: 0.0,
            slow_start: true,
            last_report: None,
            last_change: now,
            last_cut: None,
            last_soft: None,
            ceiling: MAX_LIMIT,
            last_probe: now,
            queue: Duration::ZERO,
            base: Duration::ZERO,
            cuts: 0,
            softs: 0,
            max_queue: Duration::ZERO,
        }
    }
    /// What the line carries at the moment, bytes a second.
    /// The reports carry the technician's clock (`vrate2`): the queue signal is a one-way delay.
    pub fn clocked(&self) -> bool {
        self.clocked
    }
    pub fn limit(&self) -> f64 {
        self.limit
    }
    /// What the line delivers, bytes a second (smoothed).
    pub fn delivered(&self) -> f64 {
        self.delivered
    }
    /// The queue the line holds: the lowest delay of the last second above the lowest of 30 seconds.
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
        let mut window = self.limit * 0.6 * (self.base + QUEUE_ALLOWED).as_secs_f64() + 20_000.0;
        if self.queue <= QUEUE_LOW {
            // A clean link (no queue): a single 1080p keyframe or burst between reports must not
            // stall the capture loop (250 kB on LAN, 150 kB on WAN).
            window = window.max(if self.base <= Duration::from_millis(30) {
                250_000.0
            } else {
                150_000.0
            });
        }
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
        let round_trip = now.saturating_duration_since(sent_at).saturating_sub(report.age);
        self.delays.push_back((now, round_trip));
        while self.delays.front().is_some_and(|(at, _)| now.saturating_duration_since(*at) > BASE_WINDOW) {
            self.delays.pop_front();
        }
        self.base = self.delays.iter().map(|(_, d)| *d).min().unwrap_or(round_trip);
        // The signal: with the technician's clock the one-way delay of the newest packet (when it
        // arrived by the technician's clock minus when it left), which carries neither the way back
        // of the report (an ordered, lossy channel: a lost report holds the next ones up) nor how
        // late the host's own loop picked it up. Without: the round trip.
        let sample = match report.clock {
            Some(clock) => {
                (i64::from(clock) - report.age.as_millis() as i64)
                    - sent_at.saturating_duration_since(self.epoch).as_millis() as i64
            }
            None => round_trip.as_millis() as i64,
        };
        if self.clocked != report.clock.is_some() {
            self.signal.clear();
            self.clocked = report.clock.is_some();
        }
        self.signal.push_back((now, sample));
        while self.signal.front().is_some_and(|(at, _)| now.saturating_duration_since(*at) > BASE_WINDOW) {
            self.signal.pop_front();
        }
        let lowest = self.signal.iter().map(|(_, v)| *v).min().unwrap_or(sample);
        let recent = self
            .signal
            .iter()
            .filter(|(at, _)| now.saturating_duration_since(*at) <= RECENT)
            .map(|(_, v)| *v)
            .min()
            .unwrap_or(sample);
        let queue = Duration::from_millis((recent - lowest).max(0) as u64);
        self.queue = queue;
        self.max_queue = self.max_queue.max(queue);
        if !report.interval.is_zero() && report.bytes > 0 {
            let rate = f64::from(report.bytes) / report.interval.as_secs_f64();
            if self.delivered == 0.0 {
                self.delivered = rate;
            } else if rate >= self.delivered
                || queue > QUEUE_LOW
                || self.in_flight() as f64 > self.limit * 0.15
            {
                self.delivered = self.delivered * 0.7 + rate * 0.3;
            }
        }
        let dt = now.saturating_duration_since(self.last_change).as_secs_f64().min(1.0);
        let since = |at: Option<Instant>| at.map_or(Duration::from_secs(3600), |at| now.saturating_duration_since(at));
        if queue > QUEUE_HIGH {
            // A pile-up that lasted a second: back to what was delivered, never by more than 15 % at once.
            if since(self.last_cut) >= Duration::from_millis(1000) {
                let by_delivery = if self.delivered > 0.0 { self.delivered * 1.4 } else { self.limit };
                let cut = by_delivery.min(self.limit * 0.85).max(MIN_LIMIT);
                #[cfg(feature = "qa-session")]
                eprintln!("QA rate cut: queue {} ms (lowest of the last second above the lowest of 30 s, {}), delivered {:.0} kB/s, in flight {} kB, limit {:.0} -> {:.0} kB/s", queue.as_millis(), if self.clocked { "one-way" } else { "round trip" }, self.delivered / 1000.0, self.in_flight() / 1000, self.limit / 1000.0, cut / 1000.0);
                self.cuts += 1;
                self.limit = cut;
                self.ceiling = (self.delivered * HOLD_FACTOR).clamp(MIN_LIMIT, MAX_LIMIT).max(cut);
                self.last_probe = now;
                self.slow_start = false;
                self.last_cut = Some(now);
                self.last_change = now;
            }
        } else if queue > QUEUE_LOW {
            // The queue grows: no climbing, one gentle step back a second.
            self.slow_start = false;
            if since(self.last_soft) >= Duration::from_millis(1000) && since(self.last_cut) >= Duration::from_millis(1000) {
                self.limit = (self.limit * 0.93).max(MIN_LIMIT);
                self.ceiling = self.ceiling.min(self.limit.max(self.delivered * HOLD_FACTOR));
                self.last_probe = now;
                self.softs += 1;
                self.last_soft = Some(now);
                self.last_change = now;
            }
        } else if since(self.last_cut) >= Duration::from_millis(2000) && since(self.last_soft) >= Duration::from_millis(1000) {
            if now.saturating_duration_since(self.last_probe) >= PROBE_EVERY && self.limit >= self.ceiling * 0.97 {
                self.ceiling = (self.ceiling * 1.25).min(MAX_LIMIT);
                self.last_probe = now;
            }
            let cap = (self.delivered * 3.0).clamp(START_LIMIT, MAX_LIMIT).min(self.ceiling.max(self.limit));
            let growth: f64 = if self.slow_start { 2.0 } else { 1.10 };
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

    /// A noisy line like the lab's long one: `rate` bytes a second after a delay of `latency` +- `jitter`
    /// each way (packets are not reordered here: arrival times only grow), random loss `loss`, and
    /// a report channel that is reliable and ordered: a lost report holds up every later one for
    /// `stall`. A keyframe of `key_bytes` leaves every `key_every` seconds, spread at the limit.
    struct Noisy {
        rate: f64,
        latency: f64,
        jitter: f64,
        loss: f64,
        stall: f64,
        key_bytes: usize,
        key_every: f64,
        /// The host picks a report up this much later at most (a loaded machine).
        lag: f64,
        /// The technician adds its clock (`vrate2`).
        clocked: bool,
    }
    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> f64 {
            self.0 ^= self.0 << 13;
            self.0 ^= self.0 >> 7;
            self.0 ^= self.0 << 17;
            (self.0 >> 11) as f64 / (1u64 << 53) as f64
        }
    }
    /// Result of [`noisy`]: per 10 s slice (limit, delivered bytes/s, worst queue s) and the cut count.
    struct Outcome {
        slices: Vec<(f64, f64, f64)>,
        cuts: u32,
        /// When (seconds) each cut happened, and the limit after the run.
        cut_times: Vec<f64>,
    }
    fn noisy(line: &mut Noisy, control: &mut RateControl, seconds: f64, change: Option<(f64, f64)>) -> Outcome {
        let t0 = Instant::now();
        let mut rng = Rng(0x9E37_79B9_7F4A_7C15);
        let mut free_at = 0.0f64;
        let mut arrivals: Vec<(f64, u16, usize)> = Vec::new();
        let mut seen = 0;
        let mut receiver = Receiver::new(t0);
        let (mut seq, mut now, mut next_report, mut next_key) = (0u16, 0.0f64, 0.2f64, 1.0f64);
        let mut reports: Vec<(f64, Report)> = Vec::new();
        let mut last_report_arrival = 0.0f64;
        receiver.clocked(line.clocked);
        let (mut slice_bytes, mut slice_worst) = (0usize, 0.0f64);
        let mut slices = Vec::new();
        let mut cut_times = Vec::new();
        let mut cuts_seen = 0;
        let mut debt = 0.0f64;
        let mut key_left = 0usize;
        let mut credit = 0.0f64;
        while now < seconds {
            if let Some((at, rate)) = change
                && now >= at
            {
                line.rate = rate;
            }
            let instant = t0 + Duration::from_secs_f64(now);
            control.tick(instant);
            let backlogged = control.backlogged(instant);
            if now >= next_key && !backlogged {
                key_left += line.key_bytes;
                next_key += line.key_every;
            }
            // The pacer: pictures leave at the limit; ordinary pictures at the encoder's rate (60 % of it).
            let paced = control.limit();
            let mut budget = 0.0;
            if !backlogged {
                budget += control.limit() * 0.6 * 0.01;
            }
            credit = (credit + paced * 0.01).min(paced * 0.02 + 2500.0);
            let from_key = (key_left as f64).min(credit);
            key_left -= from_key as usize;
            credit -= from_key;
            budget += from_key;
            debt += budget;
            while debt >= 1200.0 {
                debt -= 1200.0;
                control.sent(instant, seq, 1200);
                let start = free_at.max(now);
                if start - now < 2.5 {
                    free_at = start + 1200.0 / line.rate;
                    if rng.next() >= line.loss {
                        let at = (free_at + line.latency + (rng.next() * 2.0 - 1.0) * line.jitter).max(arrivals.last().map_or(0.0, |a| a.0));
                        arrivals.push((at, seq, 1200));
                    }
                }
                seq = seq.wrapping_add(1);
            }
            while seen < arrivals.len() && arrivals[seen].0 <= now {
                let (at, s, size) = arrivals[seen];
                receiver.packet(t0 + Duration::from_secs_f64(at), s, size);
                slice_bytes += size;
                seen += 1;
            }
            if now >= next_report {
                if let Some(report) = receiver.due(instant) {
                    let mut arrive = now + line.latency + (rng.next() * 2.0 - 1.0) * line.jitter;
                    if rng.next() < line.loss {
                        arrive += line.stall;
                    }
                    arrive = arrive.max(last_report_arrival);
                    last_report_arrival = arrive;
                    let picked_up = arrive + rng.next() * line.lag;
                    last_report_arrival = last_report_arrival.max(picked_up);
                    reports.push((picked_up, report));
                }
                next_report += 0.2;
            }
            while reports.first().is_some_and(|(at, _)| *at <= now) {
                let (at, report) = reports.remove(0);
                control.report(t0 + Duration::from_secs_f64(at), report);
            }
            if control.cuts != cuts_seen {
                cuts_seen = control.cuts;
                cut_times.push(now);
            }
            slice_worst = slice_worst.max((free_at - now).max(0.0));
            now += 0.01;
            if ((now * 100.0).round() as u64).is_multiple_of(1000) {
                slices.push((control.limit(), slice_bytes as f64 / 10.0, slice_worst));
                slice_bytes = 0;
                slice_worst = 0.0;
            }
        }
        Outcome { slices, cuts: control.cuts, cut_times }
    }

    fn long_line(clocked: bool, lag: f64) -> (f64, f64, f64, u32) {
        let mut line = Noisy { rate: 500_000.0, latency: 0.14, jitter: 0.02, loss: 0.01, stall: 1.0, key_bytes: 80_000, key_every: 3.0, lag, clocked };
        let mut control = RateControl::new(Instant::now());
        let out = noisy(&mut line, &mut control, 300.0, None);
        let tail: Vec<_> = out.slices.iter().skip(6).collect();
        let n = tail.len() as f64;
        (tail.iter().map(|s| s.0).sum::<f64>() / n, tail.iter().map(|s| s.1).sum::<f64>() / n, tail.iter().map(|s| s.2).fold(0.0, f64::max), out.cuts)
    }

    /// The lab's long line (4 Mbit/s, 140 ms +-20 each way, 1 % loss, reports stalled a second by a
    /// lost one) with a host that picks reports up late (a loaded machine): the limit must settle
    /// near the line rate (the encoder at 60 % of it, 2.4 Mbit/s of 4), not at the floor.
    #[test]
    fn the_long_line_with_a_slow_host_settles_near_the_line_rate() {
        for (clocked, lag) in [(true, 0.0), (true, 0.3), (false, 0.0), (false, 0.3)] {
            let (limit, delivered, worst, cuts) = long_line(clocked, lag);
            eprintln!("LAB long line clocked={clocked} lag={lag}: mean limit {:.0} kB/s, delivered {:.0} kB/s, worst queue {:.2} s, cuts {cuts} in 300 s", limit / 1000.0, delivered / 1000.0, worst);
        }
        let (limit, delivered, worst, cuts) = long_line(true, 0.3);
        assert!(limit > 350_000.0, "limit {limit} stays far from the floor");
        assert!(delivered > 200_000.0, "delivered {delivered}");
        assert!(worst < 1.0, "queue {worst}");
        assert!(cuts <= 12, "{cuts} cuts in 5 minutes");
    }


    #[test]
    fn a_noisy_line_settles_without_swinging() {
        // Jitter, 1 % loss, stalled reports and a slow host: after the first minute the limit stays in a
        // band around the line rate (500 kB/s, the encoder takes 60 % of the limit) in every 10 s.
        let mut line = Noisy { rate: 500_000.0, latency: 0.14, jitter: 0.02, loss: 0.01, stall: 1.0, key_bytes: 80_000, key_every: 3.0, lag: 0.3, clocked: true };
        let mut control = RateControl::new(Instant::now());
        let out = noisy(&mut line, &mut control, 300.0, None);
        for (index, (limit, delivered, _)) in out.slices.iter().enumerate().skip(6) {
            assert!((400_000.0..1_300_000.0).contains(limit), "slice {index}: limit {limit}");
            assert!(*delivered > 250_000.0, "slice {index}: delivered {delivered}");
        }
    }

    #[test]
    fn a_real_drop_of_the_line_is_followed_within_seconds() {
        let mut line = Noisy { rate: 500_000.0, latency: 0.14, jitter: 0.02, loss: 0.01, stall: 1.0, key_bytes: 80_000, key_every: 3.0, lag: 0.1, clocked: true };
        let mut control = RateControl::new(Instant::now());
        // The line falls to a quarter (1 Mbit/s) at 100 s.
        let out = noisy(&mut line, &mut control, 140.0, Some((100.0, 125_000.0)));
        let first = out.cut_times.iter().find(|at| **at >= 100.0).copied();
        let first = first.expect("a cut after the drop");
        assert!(first - 100.0 < 3.0, "first cut {:.1} s after the drop", first - 100.0);
        let (limit, delivered, worst) = out.slices[13];
        assert!(limit < 400_000.0, "limit {limit} follows a line of 125 kB/s");
        assert!(delivered > 60_000.0 && worst < 1.5, "delivered {delivered}, worst queue {worst}");
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
            control.report(at, Report { seq, age: Duration::from_millis(100), interval: Duration::from_millis(200), bytes: 12_000, clock: None });
        }
        let steady = control.limit();
        // One report held up by a retransmission (500 ms late), the next ones normal again.
        seq += 1;
        control.sent(at, seq, 12_000);
        at += Duration::from_millis(700);
        control.report(at, Report { seq, age: Duration::from_millis(100), interval: Duration::from_millis(200), bytes: 12_000, clock: None });
        assert!(control.limit() >= steady, "one late report: {} vs {steady}", control.limit());
        for _ in 0..3 {
            seq += 1;
            control.sent(at, seq, 12_000);
            at += Duration::from_millis(200);
            control.report(at, Report { seq, age: Duration::from_millis(100), interval: Duration::from_millis(200), bytes: 12_000, clock: None });
        }
        assert!(control.limit() >= steady);
        // Now every report is 400 ms late and only 60 kB/s arrive: a queue.
        for _ in 0..6 {
            seq += 1;
            control.sent(at, seq, 12_000);
            at += Duration::from_millis(600);
            control.report(at, Report { seq, age: Duration::from_millis(100), interval: Duration::from_millis(600), bytes: 36_000, clock: None });
        }
        assert!(control.limit() < steady * 0.9, "a lasting queue cuts: {} vs {steady}", control.limit());
    }

    #[test]
    fn a_still_screen_on_a_lan_keeps_the_limit_and_never_stalls_on_one_burst() {
        // Mac to Mac on one LAN: 1 ms round trip. First a busy screen raises the limit, then the screen
        // stays still (2 kB per report: the encoder has nothing to send) for 10 s. The idle rate must
        // not drag the delivery estimate down, so a following burst is not cut to the floor and a
        // 200 kB keyframe between two reports does not stop the pictures.
        let t0 = Instant::now();
        let mut control = RateControl::new(t0);
        let mut at = t0;
        let mut seq = 0u16;
        for _ in 0..50 {
            seq += 1;
            control.sent(at, seq, 400_000);
            at += Duration::from_millis(200);
            control.report(at, Report { seq, age: Duration::from_millis(1), interval: Duration::from_millis(200), bytes: 400_000, clock: None });
        }
        let busy = control.limit();
        assert!(busy >= 1_500_000.0, "a fast LAN reaches a high limit: {busy}");
        for _ in 0..50 {
            seq += 1;
            control.sent(at, seq, 2_000);
            at += Duration::from_millis(200);
            control.report(at, Report { seq, age: Duration::from_millis(1), interval: Duration::from_millis(200), bytes: 2_000, clock: None });
        }
        assert!(control.limit() >= busy * 0.95, "a still screen keeps the limit: {} vs {busy}", control.limit());
        assert!(control.delivered >= 1_000_000.0, "idle reports do not decay delivery: {}", control.delivered);
        seq += 1;
        control.sent(at, seq, 200_000);
        assert!(!control.backlogged(at + Duration::from_millis(5)), "one keyframe between reports does not stall a LAN");
    }

    #[test]
    fn report_round_trips_on_the_wire() {
        let clocked = Report { seq: 7, age: Duration::from_millis(3), interval: Duration::from_millis(200), bytes: 9, clock: Some(4_000_000_000) };
        assert_eq!(Report::decode(&clocked.encode()), Some(clocked));
        assert_eq!(Report::decode(&clocked.encode()[..clocked.encode().len() - 1]), None);
        let report = Report { seq: 65_000, age: Duration::from_millis(40), interval: Duration::from_millis(200), bytes: 123_456, clock: None };
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
        control.report(t0 + Duration::from_millis(100), Report { seq: 0, age: Duration::ZERO, interval: Duration::from_millis(200), bytes: 1200, clock: None });
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
        control.report(t0 + Duration::from_millis(50), Report { seq: 2, age: Duration::ZERO, interval: Duration::from_millis(200), bytes: 2400, clock: None });
        let before = control.limit();
        control.tick(t0 + Duration::from_secs(9));
        assert!(control.limit() < before);
    }

    #[test]
    fn the_receiver_reports_every_100_ms_with_the_newest_packet() {
        let t0 = Instant::now();
        let mut receiver = Receiver::new(t0);
        assert_eq!(receiver.due(t0 + Duration::from_secs(1)), None, "nothing arrived yet");
        receiver.packet(t0 + Duration::from_millis(10), 65_535, 1000);
        receiver.packet(t0 + Duration::from_millis(20), 0, 1000);
        receiver.packet(t0 + Duration::from_millis(30), 65_534, 1000);
        assert_eq!(receiver.due(t0 + Duration::from_millis(50)), None);
        let report = receiver.due(t0 + Duration::from_millis(150)).unwrap();
        assert_eq!((report.seq, report.bytes, report.age), (0, 3000, Duration::from_millis(130)));
    }
}
