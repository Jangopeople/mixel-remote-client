//! Pictures leave the computer being helped at a pace the connection can carry.
//!
//! A complete picture (keyframe) is tens to hundreds of packets, from a Mac more than a megabyte.
//! Handed to the socket in one burst it overruns the queue of a slow link (a DSL uplink, a mobile
//! connection, a relay): part of it is dropped, the technician asks for a new one, and that one is
//! dropped the same way. Here the packets of a picture are spread over time instead.
//!
//! Nothing changes on the wire, so technician apps that are already published work unchanged:
//! - the packets are the same ones, in the same order;
//! - a picture always arrives within [`PICTURE_LIMIT`]: the technician's side gives up on a
//!   picture that takes more than two seconds to arrive;
//! - the only signal used is the one every technician app already sends, the request for a new
//!   complete picture.
//!
//! The pace: a bucket that fills at `rate` bytes a second and holds [`BURST`] worth of it. Small
//! pictures (nearly all of them) fit into what the bucket holds and go out at once, as before. The
//! rate starts low, at [`START_RATE`]: nothing is known about the connection yet, and a first
//! picture that floods a slow link takes other packets of the session with it. With every
//! complete picture that arrived (no request for another one followed it) the rate is multiplied
//! by four, up to [`MAX_RATE`]; on a fast connection that is reached with the third. When a
//! complete picture did not arrive (a request came well after it was sent) the rate is cut to a
//! third; from then on it only doubles, and the rate at which a picture was lost is remembered as
//! a ceiling that is approached again only slowly.
//!
//! What this does not do: it does not make the pictures smaller. A link slower than the pictures
//! themselves (the encoder aims at 3 to 6 Mbit/s while the screen moves) stays too slow; the
//! pace then holds the capture back, so fewer pictures are sent instead of more being lost.
//! Measured in the lab on a 2 Mbit/s link to the relay: see the release notes of 2.0.4.
use std::time::{Duration, Instant};

/// Bytes a second on a connection nothing is known about yet (4 Mbit/s). The first complete
/// picture of a session takes that long: 40 kB (a Windows or Linux desktop) 60 ms, 300 kB half a
/// second.
pub const START_RATE: f64 = 500_000.0;
/// The fastest pace (96 Mbit/s): a megabyte takes 83 ms, 150 kB go out at once.
pub const MAX_RATE: f64 = 12_000_000.0;
/// The slowest pace (1 Mbit/s). A connection slower than this cannot carry the picture stream.
pub const MIN_RATE: f64 = 125_000.0;
/// What may leave in one go: this much time's worth of the rate (and never less than two packets).
pub const BURST: Duration = Duration::from_millis(20);
const MIN_BURST: f64 = 2_500.0;
/// A picture is on its way no longer than this, whatever the rate (the technician's side drops a
/// picture that takes more than two seconds).
pub const PICTURE_LIMIT: Duration = Duration::from_millis(1200);
/// A request for a complete picture this long after one was sent means that one did not arrive.
/// Requests that come sooner were under way when it was sent.
pub const GRACE: Duration = Duration::from_millis(500);
/// A complete picture arrived when no request followed it for this long: the technician's side
/// asks once a second while it has no picture, and at once when a picture it had is damaged.
pub const CONFIRM: Duration = Duration::from_millis(1500);
/// How long after a loss the pace stays below the rate that lost a picture.
pub const PROBE_AFTER: Duration = Duration::from_secs(20);

pub struct Pacer {
    rate: f64,
    /// What the line delivers (`video_rate`): the pace never exceeds it.
    cap: f64,
    ceiling: f64,
    /// The rate of the picture being sent: `rate`, or more for a picture that would otherwise
    /// take longer than [`PICTURE_LIMIT`].
    picture_rate: f64,
    credit: f64,
    filled: Instant,
    /// When a complete picture had left that is not yet counted as arrived or lost (the oldest
    /// such one), and when the latest one had left.
    complete_sent: Option<Instant>,
    latest_complete: Option<Instant>,
    last_loss: Option<Instant>,
    /// No complete picture was lost yet: the rate grows fourfold instead of twofold.
    untroubled: bool,
    /// The delay on the line is low (`video_rate`: no queue): a lost picture is then loss on the
    /// way (random, or a burst), not a line that is too full, and must not cut the ceiling.
    line_clean: bool,
    /// Lab statistics: complete pictures counted as lost.
    pub losses: u32,
}

impl Pacer {
    pub fn new(now: Instant) -> Self {
        Self {
            rate: START_RATE,
            cap: MAX_RATE,
            ceiling: MAX_RATE,
            picture_rate: START_RATE,
            credit: burst(START_RATE),
            filled: now,
            complete_sent: None,
            latest_complete: None,
            last_loss: None,
            untroubled: true,
            line_clean: false,
            losses: 0,
        }
    }
    /// The line holds no queue at the moment (the delay is low): see `line_clean`.
    pub fn set_line_clean(&mut self, clean: bool) {
        self.line_clean = clean;
    }
    /// Bytes a second at the moment.
    pub fn rate(&self) -> f64 {
        self.rate.min(self.cap)
    }
    /// The pace by the pictures' own fate (without the line limit) and the ceiling, lab statistics.
    pub fn own_rate(&self) -> (f64, f64) {
        (self.rate, self.ceiling)
    }
    /// The most the line delivers, bytes a second (from the technician's receive reports).
    pub fn set_cap(&mut self, cap: f64) {
        self.cap = cap.clamp(MIN_RATE, MAX_RATE);
    }
    /// A picture of `bytes` is about to be sent.
    pub fn begin(&mut self, now: Instant, bytes: usize) {
        if let Some(sent) = self.complete_sent
            && now.saturating_duration_since(sent) >= CONFIRM
        {
            // That complete picture arrived: no request followed it. One sent since then is
            // judged next.
            self.complete_sent = self.latest_complete.filter(|latest| *latest > sent);
            if self.rate < self.ceiling {
                self.rate = (self.rate * if self.untroubled { 4.0 } else { 2.0 }).min(self.ceiling);
            } else if self.ceiling < MAX_RATE
                && self.last_loss.is_none_or(|loss| now.saturating_duration_since(loss) >= PROBE_AFTER)
            {
                self.ceiling = (self.ceiling * 1.25).min(MAX_RATE);
                self.rate = self.ceiling;
            }
        }
        self.refill(now);
        self.picture_rate = self.rate().max(bytes as f64 / PICTURE_LIMIT.as_secs_f64());
        self.credit = self.credit.min(burst(self.picture_rate));
    }
    /// May a packet of `size` bytes leave now? `Err`: not before this much time has passed.
    pub fn take(&mut self, now: Instant, size: usize) -> Result<(), Duration> {
        self.refill(now);
        let size = size as f64;
        if self.credit >= size.min(burst(self.picture_rate)) {
            self.credit -= size;
            return Ok(());
        }
        Err(Duration::from_secs_f64(((size - self.credit) / self.picture_rate).max(0.000_05)))
    }
    /// The last packet of the picture has left.
    pub fn finished(&mut self, now: Instant, complete_picture: bool) {
        if complete_picture {
            self.complete_sent.get_or_insert(now);
            self.latest_complete = Some(now);
        }
    }
    /// The technician's side asked for a complete picture (and will get one).
    pub fn complete_picture_requested(&mut self, now: Instant) {
        let Some(sent) = self.latest_complete else { return };
        if now.saturating_duration_since(sent) <= GRACE {
            return;
        }
        // The one sent did not arrive. It counts once: the next one is judged on its own.
        self.complete_sent = None;
        self.latest_complete = None;
        self.last_loss = Some(now);
        self.untroubled = false;
        self.losses += 1;
        if self.line_clean {
            // No queue: the picture was hit by loss on the way. Step back a little, the ceiling stays.
            self.rate = (self.rate * 0.7).max(MIN_RATE);
            return;
        }
        self.ceiling = (self.rate * 0.75).max(MIN_RATE);
        self.rate = (self.rate / 3.0).max(MIN_RATE);
    }
    fn refill(&mut self, now: Instant) {
        let elapsed = now.saturating_duration_since(self.filled).as_secs_f64();
        self.filled = self.filled.max(now);
        self.credit = (self.credit + elapsed * self.picture_rate).min(burst(self.picture_rate));
    }
}

fn burst(rate: f64) -> f64 {
    (rate * BURST.as_secs_f64()).max(MIN_BURST)
}

/// Sends the packets of one picture in order at the pacer's pace. While the pace holds packets
/// back, `meanwhile` runs (the session keeps answering input and its other channels); `stopped`
/// ends the picture early. `Ok(false)`: stopped before the last packet.
pub async fn spread<P>(
    pacer: &mut Pacer,
    packets: Vec<P>,
    complete_picture: bool,
    size: impl Fn(&P) -> usize,
    mut write: impl AsyncFnMut(P) -> Result<(), String>,
    mut meanwhile: impl AsyncFnMut() -> Result<bool, String>,
) -> Result<bool, String> {
    pacer.begin(Instant::now(), packets.iter().map(&size).sum());
    for packet in packets {
        let bytes = size(&packet);
        while let Err(wait) = pacer.take(Instant::now(), bytes) {
            let before = Instant::now();
            if !meanwhile().await? {
                return Ok(false);
            }
            // The session's own turn usually takes a moment; when it took none, wait here
            // (a short timed wait: the bucket holds enough for a coarse timer).
            if let Some(rest) = wait.checked_sub(before.elapsed()) {
                tokio::time::sleep(rest.min(Duration::from_millis(2))).await;
            }
        }
        write(packet).await?;
    }
    pacer.finished(Instant::now(), complete_picture);
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    const PACKET: usize = 1212;

    /// Sends `bytes` as full packets on a clock that only moves by what the pacer asks for.
    /// Returns when each packet left (seconds after `now`) and moves `now` to the end.
    fn send(pacer: &mut Pacer, now: &mut Instant, bytes: usize, complete: bool) -> Vec<f64> {
        let start = *now;
        let mut left = Vec::new();
        pacer.begin(*now, bytes);
        let mut rest = bytes;
        while rest > 0 {
            let size = rest.min(PACKET);
            while let Err(wait) = pacer.take(*now, size) {
                *now += wait;
            }
            left.push(now.duration_since(start).as_secs_f64());
            rest -= size;
        }
        pacer.finished(*now, complete);
        left
    }
    /// The most bytes that left within any window of `window` seconds.
    fn most_within(left: &[f64], window: f64) -> usize {
        (0..left.len())
            .map(|first| left[first..].iter().take_while(|at| **at - left[first] <= window).count() * PACKET)
            .max()
            .unwrap_or(0)
    }

    #[test]
    fn an_ordinary_picture_leaves_at_once() {
        // Between two complete pictures come small ones (a few kilobytes): never held back,
        // at the starting pace, at the fastest and at the slowest.
        let mut now = Instant::now();
        let mut pacer = Pacer::new(now);
        for rate in [START_RATE, MAX_RATE, MIN_RATE] {
            pacer.rate = rate;
            for _ in 0..90 {
                now += Duration::from_millis(33);
                let left = send(&mut pacer, &mut now, 2_400, false);
                assert!(left.iter().all(|at| *at == 0.0), "{left:?} at {rate}");
            }
        }
    }

    #[test]
    fn a_complete_picture_is_spread_and_not_sent_in_one_burst() {
        let mut now = Instant::now();
        let mut pacer = Pacer::new(now);
        // 300 kB at the starting pace of 500 kB a second: about 0.6 s, not one burst.
        let left = send(&mut pacer, &mut now, 300_000, true);
        let took = *left.last().unwrap();
        assert!((0.55..0.62).contains(&took), "took {took}");
        // In any 20 ms no more than the bucket holds plus what flows in meanwhile.
        let most = most_within(&left, BURST.as_secs_f64());
        assert!(most <= 2 * (START_RATE * BURST.as_secs_f64()) as usize + PACKET, "{most} bytes within 20 ms");
        // The packets leave evenly: no pause longer than a packet's worth, twice over.
        let longest = left.windows(2).map(|pair| pair[1] - pair[0]).fold(0.0, f64::max);
        assert!(longest <= 2.0 * PACKET as f64 / START_RATE + 0.0002, "pause {longest}");
    }

    #[test]
    fn a_fast_connection_is_not_slowed_noticeably() {
        let mut now = Instant::now();
        let mut pacer = Pacer::new(now);
        // The first complete picture, before anything is known: 40 kB (a Windows or Linux
        // desktop) within 70 ms, 150 kB within 0.3 s. Once per session.
        assert!(*send(&mut Pacer::new(now), &mut now.clone(), 40_000, true).last().unwrap() < 0.07);
        let first = *send(&mut pacer, &mut now, 150_000, true).last().unwrap();
        assert!(first < 0.3, "first picture took {first}");
        // Each complete picture that arrives multiplies the pace by four: 2, 8, 12 MB a second.
        for expected in [2_000_000.0, 8_000_000.0, MAX_RATE, MAX_RATE] {
            now += Duration::from_secs(3);
            send(&mut pacer, &mut now, 150_000, true);
            assert_eq!(pacer.rate(), expected);
        }
        // At full pace a 150 kB picture leaves at once and a megabyte (a Mac's) within 70 ms.
        now += Duration::from_secs(3);
        assert_eq!(*send(&mut pacer, &mut now, 150_000, true).last().unwrap(), 0.0);
        now += Duration::from_secs(3);
        let megabyte = *send(&mut pacer, &mut now, 1_000_000, true).last().unwrap();
        assert!(megabyte < 0.07, "a megabyte took {megabyte}");
    }

    #[test]
    fn a_picture_that_did_not_arrive_slows_the_pace_down_to_the_floor() {
        let mut now = Instant::now();
        let mut pacer = Pacer::new(now);
        let mut rates = Vec::new();
        for _ in 0..5 {
            send(&mut pacer, &mut now, 120_000, true);
            // The technician's side asks again a second later: that picture was lost.
            now += Duration::from_secs(1);
            pacer.complete_picture_requested(now);
            rates.push(pacer.rate() as u64);
        }
        assert_eq!(rates, [166_666, 125_000, 125_000, 125_000, 125_000]);
        // At the floor the same picture takes about a second and never floods: 2.5 kB in 20 ms.
        let left = send(&mut pacer, &mut now, 120_000, true);
        let took = *left.last().unwrap();
        assert!((0.9..1.0).contains(&took), "took {took}");
        assert!(most_within(&left, 0.02) <= 3 * PACKET + PACKET, "{} bytes within 20 ms", most_within(&left, 0.02));
    }

    #[test]
    fn requests_that_were_under_way_do_not_slow_the_pace() {
        let mut now = Instant::now();
        let mut pacer = Pacer::new(now);
        // Before any complete picture was sent (the technician asks while the session starts).
        pacer.complete_picture_requested(now);
        assert_eq!(pacer.rate(), START_RATE);
        send(&mut pacer, &mut now, 120_000, true);
        // A request that crossed the picture on the way.
        now += Duration::from_millis(300);
        pacer.complete_picture_requested(now);
        assert_eq!(pacer.rate(), START_RATE);
        // One lost picture counts once, however many requests follow it.
        now += Duration::from_secs(1);
        pacer.complete_picture_requested(now);
        now += Duration::from_secs(1);
        pacer.complete_picture_requested(now);
        assert_eq!(pacer.rate() as u64, 166_666);
    }

    #[test]
    fn the_pace_recovers_to_just_below_the_rate_that_lost_a_picture_and_tries_more_only_later() {
        let mut now = Instant::now();
        let mut pacer = Pacer::new(now);
        send(&mut pacer, &mut now, 120_000, true);
        now += Duration::from_secs(1);
        pacer.complete_picture_requested(now); // lost at 500 kB/s: now 167 kB/s, ceiling 375 kB/s
        let lost_at = now;
        let mut rates = Vec::new();
        for _ in 0..4 {
            send(&mut pacer, &mut now, 120_000, true);
            now += Duration::from_secs(3);
            pacer.begin(now, 1_000);
            rates.push(pacer.rate() as u64);
        }
        // After a loss the pace doubles (no longer fourfold) up to the ceiling.
        assert_eq!(rates, [333_333, 375_000, 375_000, 375_000]);
        assert!(now.duration_since(lost_at) < PROBE_AFTER);
        // Twenty seconds after the loss the pace tries a quarter more.
        now = lost_at + PROBE_AFTER;
        send(&mut pacer, &mut now, 120_000, true);
        now += Duration::from_secs(3);
        pacer.begin(now, 1_000);
        assert_eq!(pacer.rate() as u64, 468_750);
    }

    #[test]
    fn a_technician_that_asks_once_a_second_is_heard_every_time() {
        // The technician's side asks for a complete picture once a second while it has none. The
        // host takes requests half a second apart (a full second here missed every other one),
        // so a complete picture counts as arrived when the next request stays away, and before
        // the next complete picture of a moving screen (two seconds later) is due.
        assert!(crate::remote_control::ControlPipe::keyframe_request_spacing() <= Duration::from_millis(900));
        assert!(CONFIRM > Duration::from_secs(1) + GRACE / 2 && CONFIRM < Duration::from_secs(2));
        assert!(GRACE < Duration::from_secs(1));
    }

    #[test]
    fn complete_pictures_every_two_seconds_still_raise_the_pace() {
        // A moving screen gets a complete picture every 60 pictures (two seconds), sooner than
        // one is counted as arrived: each is still counted, in turn.
        let mut now = Instant::now();
        let mut pacer = Pacer::new(now);
        let mut rates = Vec::new();
        for second in 0..12 {
            for picture in 0..30 {
                let complete = second % 2 == 0 && picture == 0;
                send(&mut pacer, &mut now, if complete { 30_000 } else { 2_000 }, complete);
                now += Duration::from_millis(33);
            }
            rates.push(pacer.rate() as u64 / 1000);
        }
        assert_eq!(rates, [500, 2000, 2000, 8000, 8000, 12000, 12000, 12000, 12000, 12000, 12000, 12000]);
        // And one that is lost among them is noticed: the request comes a second after it.
        send(&mut pacer, &mut now, 30_000, true);
        now += Duration::from_secs(1);
        pacer.complete_picture_requested(now);
        assert_eq!(pacer.rate() as u64, 4_000_000);
    }

    #[test]
    fn every_picture_arrives_within_the_time_the_technician_side_allows() {
        // The published technician apps drop a picture that takes more than two seconds.
        let mut now = Instant::now();
        let mut pacer = Pacer::new(now);
        pacer.rate = MIN_RATE;
        for bytes in [200_000, 1_000_000, 4_000_000, core_protocol::MAX_VIDEO_ACCESS_UNIT] {
            now += Duration::from_secs(3);
            // (Not counted as complete pictures here, so that none of them raises the pace.)
            let took = *send(&mut pacer, &mut now, bytes, false).last().unwrap();
            assert!(took <= PICTURE_LIMIT.as_secs_f64() + 0.001, "{bytes} bytes took {took}");
        }
        assert_eq!(pacer.rate(), MIN_RATE, "a large picture does not change the pace of the next");
    }

    #[test]
    fn time_spent_idle_never_adds_up_to_a_burst() {
        let mut now = Instant::now();
        let mut pacer = Pacer::new(now);
        now += Duration::from_secs(600);
        let left = send(&mut pacer, &mut now, 300_000, true);
        assert!(left.iter().filter(|at| **at == 0.0).count() * PACKET <= (START_RATE * BURST.as_secs_f64()) as usize + PACKET);
    }

    #[tokio::test]
    async fn the_session_keeps_its_turn_while_a_picture_is_spread() {
        let mut pacer = Pacer::new(Instant::now());
        pacer.rate = 500_000.0;
        let packets: Vec<(u32, usize)> = (0..50).map(|number| (number, 1_200)).collect();
        let (mut written, mut turns) = (Vec::new(), 0u32);
        let started = Instant::now();
        let sent = spread(
            &mut pacer,
            packets,
            true,
            |packet| packet.1,
            async |packet: (u32, usize)| {
                written.push(packet.0);
                Ok(())
            },
            async || {
                turns += 1;
                Ok(true)
            },
        )
        .await
        .unwrap();
        assert!(sent);
        assert_eq!(written, (0..50).collect::<Vec<_>>(), "every packet, in order");
        // 60 kB at 500 kB a second, 10 kB of it at once: about 100 ms.
        let took = started.elapsed();
        assert!(took >= Duration::from_millis(80) && took < Duration::from_millis(600), "took {took:?}");
        // (A timed wait is a whole timer tick on Windows, about 15 ms: fewer, longer turns there.)
        assert!(turns >= 4, "the session had {turns} turns");
        assert!(pacer.complete_sent.is_some());
    }

    #[tokio::test]
    async fn a_stopped_session_sends_no_more_of_the_picture() {
        let mut pacer = Pacer::new(Instant::now());
        pacer.rate = MIN_RATE;
        let mut written = 0u32;
        let sent = spread(
            &mut pacer,
            vec![1_200usize; 100],
            true,
            |size| *size,
            async |_| {
                written += 1;
                Ok(())
            },
            async || Ok(false),
        )
        .await
        .unwrap();
        assert!(!sent);
        assert!(written <= 3, "{written} packets after the stop");
        assert!(pacer.complete_sent.is_none() && pacer.latest_complete.is_none(), "a picture cut short is not waited for");
    }

    #[test]
    fn the_line_cap_limits_the_pace_and_lifts_again() {
        let mut pacer = Pacer::new(Instant::now());
        assert_eq!(pacer.rate(), START_RATE);
        pacer.set_cap(400_000.0);
        assert_eq!(pacer.rate(), 400_000.0);
        pacer.set_cap(1.0);
        assert_eq!(pacer.rate(), MIN_RATE);
        pacer.set_cap(MAX_RATE);
        assert_eq!(pacer.rate(), START_RATE, "lifting the cap does not raise the pace by itself");
    }

    #[test]
    fn a_lost_picture_on_a_line_without_queue_keeps_the_ceiling() {
        let mut now = Instant::now();
        let mut pacer = Pacer::new(now);
        pacer.set_line_clean(true);
        for _ in 0..12 {
            send(&mut pacer, &mut now, 120_000, true);
            now += Duration::from_secs(1);
            pacer.complete_picture_requested(now);
            // a sound picture follows and counts as arrived
            send(&mut pacer, &mut now, 30_000, true);
            now += Duration::from_secs(3);
            pacer.begin(now, 1_000);
        }
        let (rate, ceiling) = pacer.own_rate();
        assert_eq!(ceiling, MAX_RATE, "twelve random losses do not lower the ceiling");
        assert!(rate > 3.0 * MIN_RATE, "and the pace is back up: {rate}");
        pacer.set_line_clean(false);
        send(&mut pacer, &mut now, 120_000, true);
        now += Duration::from_secs(1);
        pacer.complete_picture_requested(now);
        assert!(pacer.own_rate().1 < MAX_RATE, "a loss with a queue still cuts the ceiling");
    }

    /// The whole chain on a simulated long line (500 kB/s, 140 ms each way +- 20, 1 % random loss, FIFO
    /// queue of `queue_bytes`): delay controller + pacer + encoder bit-rate steps. A picture is sent when
    /// the one before has left and the controller does not hold things back; the encoder aims at
    /// `encoder_bitrate`; a new encoder (bit-rate step) and a lost complete picture each cost a
    /// complete picture of 40 kB. Any lost packet of a complete picture loses it (no repair: worst case).
    /// Returns (pictures per second, delivered kB/s, encoder restarts, pacer losses, final ceiling),
    /// measured over the last 240 s of 300.
    fn chain(line_clean_rule: bool, queue_bytes: f64, encode_ms: f64) -> (f64, f64, u32, u32, f64) {
        chain_with(line_clean_rule, queue_bytes, encode_ms, 40_000)
    }

    /// Like `chain`, a complete picture of `key_bytes` (1080p60 test pictures: 150-300 kB in the lab).
    fn chain_with(line_clean_rule: bool, queue_bytes: f64, encode_ms: f64, key_bytes: usize) -> (f64, f64, u32, u32, f64) {
        chain_netem(line_clean_rule, queue_bytes, encode_ms, key_bytes, 0)
    }

    /// `netem_limit` > 0: the packet limit of tc netem counts every packet inside it, the ones waiting for
    /// their 140 ms of delay too (the lab's `limit 60`); a packet over it is dropped.
    fn chain_netem(line_clean_rule: bool, queue_bytes: f64, encode_ms: f64, key_bytes: usize, netem_limit: usize) -> (f64, f64, u32, u32, f64) {
        chain_tuned(line_clean_rule, queue_bytes, encode_ms, key_bytes, netem_limit, "").0
    }

    /// The whole chain with lab knobs: returns (the usual tuple, median and p10 pictures a second, cuts, key requests).
    fn chain_tuned(line_clean_rule: bool, queue_bytes: f64, encode_ms: f64, key_bytes: usize, netem_limit: usize, knobs: &str) -> ((f64, f64, u32, u32, f64), f64, f64, u32, usize) {
        use crate::video_rate::{RateControl, Receiver, QUEUE_LOW};
        let t0 = Instant::now();
        let mut rng = 0x9E37_79B9_7F4A_7C15u64;
        let mut rand = move || {
            rng ^= rng << 13;
            rng ^= rng >> 7;
            rng ^= rng << 17;
            (rng >> 11) as f64 / (1u64 << 53) as f64
        };
        let (line_rate, latency, jitter, loss) = (500_000.0f64, 0.140, 0.020, 0.01);
        let size = (1920usize, 1080usize);
        let mut pacer = Pacer::new(t0);
        let mut control = RateControl::tuned(t0, knobs);
        let mut receiver = Receiver::new(t0);
        receiver.clocked(true);
        let mut bitrate = crate::remote_peer::encoder_bitrate(size, pacer.rate());
        let mut bitrate_set = 0.0f64;
        let (mut now, mut free_at, mut seq) = (0.0f64, 0.0f64, 0u16);
        // packets in flight: (arrives, seq, bytes, picture id or none)
        let mut arrivals: Vec<(f64, u16, usize)> = Vec::new();
        let mut seen = 0;
        let mut reports: Vec<(f64, crate::video_rate::Report)> = Vec::new();
        let mut next_report = 0.2f64;
        // the picture being sent: (packets left, bytes, complete, lost packets, picture's last seq)
        let mut current: Option<(Vec<usize>, bool, bool)> = None;
        let mut pending_keyframe = true;
        let mut busy_until = 0.0f64;
        let mut last_arrival = 0.0f64;
        let mut started_n = 0u32;
        let mut pictures_at: Vec<f64> = Vec::new();
        let mut last_of_picture: std::collections::HashMap<u16, (bool, bool)> = std::collections::HashMap::new();
        let (mut delivered, mut restarts) = (0usize, 0u32);
        let mut requests: Vec<f64> = Vec::new();
        let mut asked = 0usize;
        while now < 300.0 {
            let instant = t0 + Duration::from_secs_f64(now);
            control.tick(instant);
            pacer.set_cap(control.limit());
            if line_clean_rule {
                pacer.set_line_clean(control.clocked() && control.queue_delay() < QUEUE_LOW);
            }
            requests.retain(|at| {
                if *at <= now {
                    pacer.complete_picture_requested(instant);
                    pending_keyframe = true;
                    false
                } else {
                    true
                }
            });
            // a new picture: the encoder takes `encode_ms` (CPU) and the picture goes when the last one left
            if current.is_none() && now >= busy_until && !control.backlogged(instant) {
                if now - bitrate_set >= 4.0 {
                    let wanted = crate::remote_peer::next_bitrate(bitrate, crate::remote_peer::encoder_bitrate(size, pacer.rate()));
                    if wanted != bitrate {
                        bitrate = wanted;
                        restarts += 1;
                        pending_keyframe = true;
                    }
                    bitrate_set = now;
                }
                let complete = std::mem::take(&mut pending_keyframe);
                let bytes = if complete { key_bytes } else { (f64::from(bitrate) / 8.0 / 30.0) as usize };
                let mut packets = Vec::new();
                let mut left = bytes;
                while left > 0 {
                    packets.push(left.min(PACKET));
                    left -= left.min(PACKET);
                }
                pacer.begin(instant, bytes);
                busy_until = now + (encode_ms / 1000.0).max(1.0 / 30.0);
                started_n += 1;
                current = Some((packets, complete, false));
            }
            if let Some((packets, complete, lost)) = &mut current {
                while let Some(first) = packets.first().copied() {
                    if pacer.take(instant, first).is_err() {
                        break;
                    }
                    packets.remove(0);
                    control.sent_marked(instant, seq, first, *complete);
                    let dropped = rand() < loss;
                    let start = free_at.max(now);
                    let queued = (start - now) * line_rate;
                    let inside = arrivals.len().saturating_sub(seen);
                    if dropped || queued > queue_bytes || (netem_limit > 0 && inside >= netem_limit) {
                        *lost |= dropped;
                    } else {
                        free_at = start + first as f64 / line_rate;
                        let at = (free_at + latency + jitter * rand()).max(last_arrival);
                        last_arrival = at;
                        arrivals.push((at, seq, first));
                    }
                    if packets.is_empty() {
                        last_of_picture.insert(seq, (*complete, *lost));
                    }
                    seq = seq.wrapping_add(1);
                }
                if packets.is_empty() {
                    let (complete, lost) = (*complete, *lost);
                    pacer.finished(instant, complete);
                    // The technician asks for a complete picture after a round trip and some waiting: when
                    // a complete one lost a packet, or a repair of an ordinary one failed (1 in 20).
                    if (complete && lost) || (lost && rand() < 0.05) {
                        requests.push(now + 0.9);
                        asked += 1;
                    }
                    current = None;
                }
            }
            arrivals.sort_by(|a, b| a.0.total_cmp(&b.0));
            while seen < arrivals.len() && arrivals[seen].0 <= now {
                let (at, s, size) = arrivals[seen];
                receiver.packet(t0 + Duration::from_secs_f64(at), s, size);
                if at > 60.0 {
                    delivered += size;
                }
                if let Some((complete, lost)) = last_of_picture.remove(&s)
                    && (!lost || !complete)
                    && at > 60.0
                {
                    pictures_at.push(at);
                }
                seen += 1;
            }
            if now >= next_report {
                if let Some(report) = receiver.due(t0 + Duration::from_secs_f64(now)) {
                    reports.push((now + latency + jitter * rand(), report));
                }
                next_report += 0.2;
            }
            while reports.first().is_some_and(|(at, _)| *at <= now) {
                let (at, report) = reports.remove(0);
                control.report(t0 + Duration::from_secs_f64(at), report);
            }
            now += 0.01;
        }
        eprintln!("  (started {started_n} pictures) end: limit {:.0} kB/s queue {:?} delivered est {:.0}", control.limit() / 1000.0, control.queue_delay(), control.delivered() / 1000.0);
        let mut per_second = vec![0u32; 240];
        for at in &pictures_at {
            per_second[((*at - 60.0) as usize).min(239)] += 1;
        }
        per_second.sort_unstable();
        let (median, p10) = (f64::from(per_second[120]), f64::from(per_second[24]));
        (
            (pictures_at.len() as f64 / 240.0, delivered as f64 / 240.0 / 1000.0, restarts, pacer.losses, pacer.own_rate().1),
            median,
            p10,
            control.cuts,
            asked,
        )
    }

    #[test]
    fn shallow_queue_with_big_key_pictures() {
        let shallow = 60.0 * 1500.0;
        let deep = 1000.0 * 1500.0;
        for key in [40_000usize, 150_000, 300_000] {
            let o = chain_with(false, shallow, 0.0, key);
            let n = chain_with(true, shallow, 0.0, key);
            let nd = chain_with(true, deep, 0.0, key);
            let on = chain_netem(false, deep, 0.0, key, 60);
            let nn = chain_netem(true, deep, 0.0, key, 60);
            eprintln!("SHALLOW key {key}: old pacer {o:?} | new {n:?} | new deep {nd:?} | NETEM-LIMIT-60 old {on:?} new {nn:?}");
        }
    }

    /// Lab failure 1 (shallow, netem `limit 60`, raw arrival, no delay signal): without a loss rule the limit
    /// runs far above what the line carries. Lab failure 2 (deep, `limit 1000`): a loss rule without
    /// protection fires on lost packets of key-picture bursts. Run with `-- --ignored --nocapture`.
    #[test]
    #[ignore]
    fn rate_tuning_sweep() {
        let deep = 1000.0 * 1500.0;
        let settings = [
            "",
            "loss=0.04",
            "loss=0.04,keyskip=1",
            "loss=0.04,keyskip=1,losswin=2000",
            "loss=0.04,losshold=1500,keyskip=1",
            "loss=0.04,keyskip=1,capmul=1.3,capfloor=0",
            "loss=0.04,keyskip=1,capmul=1.3,capfloor=0,slowgrowth=1.03",
            "loss=0.04,keyskip=1,losswin=2000,capmul=1.5,capfloor=0",
            "loss=0.06,keyskip=1,losswin=2000,capmul=1.3,capfloor=0",
            "capmul=1.3,capfloor=0",
            "capmul=1.3,capfloor=0,slowgrowth=1.03",
        ];
        for key in [40_000usize, 200_000] {
            for knobs in settings {
                let (sh, m1, p1, c1, r1) = chain_tuned(true, deep, 0.0, key, 60, knobs);
                let (dp, m2, p2, c2, r2) = chain_tuned(true, deep, 0.0, key, 1000, knobs);
                eprintln!(
                    "SWEEP key {key:>6} [{knobs}] SHALLOW med {m1} p10 {p1} {:.0} kB/s cuts {c1} req {r1} | DEEP med {m2} p10 {p2} {:.0} kB/s cuts {c2} req {r2}",
                    sh.1, dp.1
                );
            }
        }
    }

    #[test]
    fn the_whole_chain_on_a_long_lossy_line() {
        let deep = 1000.0 * 1500.0;
        let shallow = 60.0 * 1500.0;
        let old_deep = chain(false, deep, 0.0);
        let new_deep = chain(true, deep, 0.0);
        let new_shallow = chain(true, shallow, 0.0);
        let new_slow_cpu = chain(true, deep, 200.0);
        eprintln!("CHAIN old pacer deep:    {old_deep:?}");
        eprintln!("CHAIN new pacer deep:    {new_deep:?}");
        eprintln!("CHAIN new pacer shallow: {new_shallow:?}");
        eprintln!("CHAIN new, 200 ms encode:{new_slow_cpu:?}");
        assert!(new_deep.1 > old_deep.1 * 4.0 && new_deep.1 > 350.0, "delivered: {} vs {}", new_deep.1, old_deep.1);
        assert!(new_shallow.1 > 350.0, "shallow queue: {}", new_shallow.1);
        assert!(new_slow_cpu.0 < 8.0, "a host that needs 200 ms a picture cannot show more than 5 a second");
        assert!(new_deep.0 >= 18.0, "pictures per second {}", new_deep.0);
    }
}
