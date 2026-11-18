//! Lab builds only (`qa-session`): when each step of opening a session happened, on one clock per
//! process, so a lab run can say where the time between "Connect" and the first picture goes
//! (`QA connect +<ms> <step> (clock <time of day>)`). Release builds compile these calls to nothing.

#[cfg(feature = "qa-session")]
mod lab {
    use std::{sync::Mutex, time::Instant};

    static START: Mutex<Option<Instant>> = Mutex::new(None);

    /// MIXEL_QA_RTC_LOG=1: the transport stack's own debug lines (ICE, DTLS, SCTP) with this clock, so a lab run
    /// shows which flight or timer holds the connect up (speed pass, 18 Nov 2026). Bounded to 4000 lines.
    struct RtcLog;
    static LINES: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    impl log::Log for RtcLog {
        fn enabled(&self, metadata: &log::Metadata) -> bool {
            let target = metadata.target();
            target.starts_with("rtc") || target.starts_with("sctp") || target.starts_with("dtls") || target.starts_with("ice") || target.starts_with("webrtc")
        }
        fn log(&self, record: &log::Record) {
            if self.enabled(record.metadata()) && LINES.fetch_add(1, std::sync::atomic::Ordering::Relaxed) < 4000 {
                let since = START.lock().ok().and_then(|start| start.map(|at| at.elapsed().as_millis())).unwrap_or(0);
                eprintln!("QA rtc +{since} [{}] {} (clock {})", record.target(), record.args(), crate::remote_control::qa_wall_clock());
            }
        }
        fn flush(&self) {}
    }
    static RTC_LOG: RtcLog = RtcLog;
    static RTC_LOG_ONCE: std::sync::Once = std::sync::Once::new();

    pub fn begin(step: &str) {
        LINES.store(0, std::sync::atomic::Ordering::Relaxed);
        if std::env::var_os("MIXEL_QA_RTC_LOG").is_some() {
            RTC_LOG_ONCE.call_once(|| {
                if log::set_logger(&RTC_LOG).is_ok() {
                    log::set_max_level(log::LevelFilter::Debug);
                }
            });
        }
        if let Ok(mut start) = START.lock() {
            *start = Some(Instant::now());
        }
        mark(step);
    }

    pub fn mark(step: &str) {
        let since = START
            .lock()
            .ok()
            .map(|mut start| start.get_or_insert_with(Instant::now).elapsed())
            .map_or(0, |elapsed| elapsed.as_millis());
        eprintln!("QA connect +{since} {step} (clock {})", crate::remote_control::qa_wall_clock());
    }
}

/// The user asked to connect (technician) or a request came in (host): the clock starts here.
#[inline]
pub fn begin(step: &str) {
    #[cfg(feature = "qa-session")]
    lab::begin(step);
    #[cfg(not(feature = "qa-session"))]
    let _ = step;
}

/// One step of opening the session.
#[inline]
pub fn mark(step: &str) {
    #[cfg(feature = "qa-session")]
    lab::mark(step);
    #[cfg(not(feature = "qa-session"))]
    let _ = step;
}

/// The candidate types in a session description, e.g. `host 2, srflx 1, relay 3 (udp 2, tcp 1)`.
pub fn candidate_summary(sdp: &str) -> String {
    let (mut host, mut srflx, mut relay, mut prflx, mut udp, mut tcp) = (0, 0, 0, 0, 0, 0);
    for line in sdp.lines().filter(|line| line.starts_with("a=candidate:")) {
        let fields: Vec<&str> = line.split_ascii_whitespace().collect();
        match fields.windows(2).find(|pair| pair[0] == "typ").map(|pair| pair[1]) {
            Some("host") => host += 1,
            Some("srflx") => srflx += 1,
            Some("relay") => relay += 1,
            Some("prflx") => prflx += 1,
            _ => {}
        }
        match fields.get(2).map(|proto| proto.to_ascii_lowercase()) {
            Some(proto) if proto == "udp" => udp += 1,
            Some(proto) if proto == "tcp" => tcp += 1,
            _ => {}
        }
    }
    format!("host {host}, srflx {srflx}, relay {relay}, prflx {prflx} (udp {udp}, tcp {tcp})")
}

#[cfg(test)]
mod tests {
    #[test]
    fn candidate_types_are_counted() {
        let sdp = "v=0\r\na=candidate:1 1 udp 2130706431 10.0.0.2 5000 typ host\r\n\
                   a=candidate:2 1 udp 1694498815 1.2.3.4 5000 typ srflx raddr 10.0.0.2 rport 5000\r\n\
                   a=candidate:3 1 udp 16777215 5.6.7.8 6000 typ relay raddr 1.2.3.4 rport 5000\r\n";
        assert_eq!(super::candidate_summary(sdp), "host 1, srflx 1, relay 1, prflx 0 (udp 3, tcp 0)");
    }
}
