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
//! rate starts at [`START_RATE`], doubles with every complete picture that arrived (no request for
//! another one followed it) up to [`MAX_RATE`], and is cut to a third when a complete picture did
//! not arrive (a request came well after it was sent). The rate at which a picture was lost is
//! remembered as a ceiling, approached again only slowly.
//!
//! What this does not do: it does not make the pictures smaller. A link slower than the pictures
//! themselves (the encoder aims at 3 to 6 Mbit/s while the screen moves) stays too slow; the
//! pace then holds the capture back, so fewer pictures are sent instead of more being lost.
//! Measured in the lab on a 2 Mbit/s link to the relay: see the release notes of 2.0.4.
use std::time::{Duration, Instant};

/// Bytes a second on a connection nothing is known about yet (16 Mbit/s).
pub const START_RATE: f64 = 2_000_000.0;
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
/// A complete picture arrived when no request followed it for this long. The technician's side
/// asks at most once a second while it has no picture, so two of its requests would have come.
pub const CONFIRM: Duration = Duration::from_millis(2200);
/// How long after a loss the pace stays below the rate that lost a picture.
pub const PROBE_AFTER: Duration = Duration::from_secs(20);

pub struct Pacer {
    rate: f64,
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
}

impl Pacer {
    pub fn new(now: Instant) -> Self {
        Self {
            rate: START_RATE,
            ceiling: MAX_RATE,
            picture_rate: START_RATE,
            credit: burst(START_RATE),
            filled: now,
            complete_sent: None,
            latest_complete: None,
            last_loss: None,
        }
    }
    /// Bytes a second at the moment.
    pub fn rate(&self) -> f64 {
        self.rate
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
                self.rate = (self.rate * 2.0).min(self.ceiling);
            } else if self.ceiling < MAX_RATE
                && self.last_loss.is_none_or(|loss| now.saturating_duration_since(loss) >= PROBE_AFTER)
            {
                self.ceiling = (self.ceiling * 1.25).min(MAX_RATE);
                self.rate = self.ceiling;
            }
        }
        self.refill(now);
        self.picture_rate = self.rate.max(bytes as f64 / PICTURE_LIMIT.as_secs_f64());
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
        // 300 kB at the starting pace of 2 MB a second: about 130 ms, not one burst.
        let left = send(&mut pacer, &mut now, 300_000, true);
        let took = *left.last().unwrap();
        assert!((0.12..0.16).contains(&took), "took {took}");
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
        // The first complete picture, before anything is known: 150 kB within 70 ms.
        let first = *send(&mut pacer, &mut now, 150_000, true).last().unwrap();
        assert!(first < 0.07, "first picture took {first}");
        // Each complete picture that arrives doubles the pace: 2, 4, 8, 12 MB a second.
        for expected in [4_000_000.0, 8_000_000.0, MAX_RATE, MAX_RATE] {
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
        assert_eq!(rates, [666_666, 222_222, 125_000, 125_000, 125_000]);
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
        assert_eq!(pacer.rate() as u64, 666_666);
    }

    #[test]
    fn the_pace_recovers_to_just_below_the_rate_that_lost_a_picture_and_tries_more_only_later() {
        let mut now = Instant::now();
        let mut pacer = Pacer::new(now);
        send(&mut pacer, &mut now, 120_000, true);
        now += Duration::from_secs(1);
        pacer.complete_picture_requested(now); // lost at 2 MB/s: now 667 kB/s, ceiling 1.5 MB/s
        let lost_at = now;
        let mut rates = Vec::new();
        for _ in 0..4 {
            send(&mut pacer, &mut now, 120_000, true);
            now += Duration::from_secs(3);
            pacer.begin(now, 1_000);
            rates.push(pacer.rate() as u64);
        }
        assert_eq!(rates, [1_333_333, 1_500_000, 1_500_000, 1_500_000]);
        assert!(now.duration_since(lost_at) < PROBE_AFTER);
        // Twenty seconds after the loss the pace tries a quarter more.
        now = lost_at + PROBE_AFTER;
        send(&mut pacer, &mut now, 120_000, true);
        now += Duration::from_secs(3);
        pacer.begin(now, 1_000);
        assert_eq!(pacer.rate() as u64, 1_875_000);
    }

    #[test]
    fn a_technician_that_asks_once_a_second_is_heard_every_time() {
        // The technician's side asks for a complete picture at most once a second. The host
        // takes requests half a second apart (a full second here missed every other one), and
        // a complete picture counts as arrived only after two such requests would have come.
        assert!(crate::remote_control::ControlPipe::keyframe_request_spacing() <= Duration::from_millis(900));
        assert!(CONFIRM > Duration::from_secs(2));
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
        assert_eq!(rates, [2000, 2000, 4000, 4000, 8000, 8000, 12000, 12000, 12000, 12000, 12000, 12000]);
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
}
