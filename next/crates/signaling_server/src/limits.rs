//! How many WebSocket connections the broker keeps open at once (`MIXEL_MAX_CONNECTIONS`).
//!
//! Every connection holds one file descriptor, so the number is bounded by the process's open-file
//! limit (`ulimit -n`, `docker run --ulimit nofile=…`) minus a reserve for the listener, the
//! outgoing HTTPS clients (authorisation, support policy, presence) and the runtime. It is also
//! bounded by the presence report, which carries at most `MAX_CONNECTIONS` connection IDs.
//! Per-company limits (the plan's concurrent sessions, checked by the support policy) and the
//! per-connection limits (sessions per peer, messages per second) are separate and unchanged.

/// Without `MIXEL_MAX_CONNECTIONS`: this many, or fewer if the open-file limit is lower.
pub const DEFAULT_MAX_CONNECTIONS: usize = 10_000;
/// Highest accepted setting (the presence report carries at most this many IDs).
pub const MAX_CONNECTIONS: usize = 20_000;
/// File descriptors kept free for everything that is not a client connection.
pub const FD_RESERVE: u64 = 256;
/// The broker raises its soft open-file limit up to the hard limit, but not beyond this.
#[cfg(unix)]
const OPEN_FILES_TARGET: u64 = 65_536;
// `rlim_t` is u64 on Linux (glibc, musl) and macOS, the broker's platforms.
#[cfg(unix)]
const _: () = assert!(std::mem::size_of::<libc::rlim_t>() == 8);

/// The connection limit from the `MIXEL_MAX_CONNECTIONS` setting (if any) and the open-file limit
/// (if known). A setting the open-file limit cannot carry is an error, not silently lowered, so a
/// container started without `--ulimit nofile` is noticed at start and not under load.
pub fn max_connections(setting: Option<&str>, open_files: Option<u64>) -> Result<usize, String> {
    let fits = open_files
        .map(|limit| usize::try_from(limit.saturating_sub(FD_RESERVE)).unwrap_or(usize::MAX));
    match setting.map(str::trim).filter(|value| !value.is_empty()) {
        Some(value) => {
            let wanted: usize = value
                .parse()
                .ok()
                .filter(|wanted| (1..=MAX_CONNECTIONS).contains(wanted))
                .ok_or_else(|| {
                    format!(
                        "MIXEL_MAX_CONNECTIONS must be a whole number from 1 to {MAX_CONNECTIONS}"
                    )
                })?;
            match (fits, open_files) {
                (Some(fits), Some(limit)) if wanted > fits => Err(format!(
                    "MIXEL_MAX_CONNECTIONS={wanted} needs at least {} open files, the limit is {limit}: raise it (docker run --ulimit nofile=65536:65536) or lower the setting",
                    wanted as u64 + FD_RESERVE
                )),
                _ => Ok(wanted),
            }
        }
        None => {
            let limit = fits.map_or(DEFAULT_MAX_CONNECTIONS, |fits| {
                fits.min(DEFAULT_MAX_CONNECTIONS)
            });
            if limit < 16 {
                return Err(format!(
                    "the open-file limit ({}) leaves no room for connections: raise it (docker run --ulimit nofile=65536:65536)",
                    open_files.unwrap_or_default()
                ));
            }
            Ok(limit)
        }
    }
}

/// Raises the soft open-file limit towards the hard limit (at most 65536) and returns the soft
/// limit now in force; `None` where the platform does not say.
#[cfg(unix)]
pub fn raise_open_files() -> Option<u64> {
    let mut limit = libc::rlimit {
        rlim_cur: 0,
        rlim_max: 0,
    };
    // SAFETY: getrlimit/setrlimit only read and write the struct passed by pointer.
    #[allow(unsafe_code)]
    unsafe {
        if libc::getrlimit(libc::RLIMIT_NOFILE, &mut limit) != 0 {
            return None;
        }
        let target = limit.rlim_max.min(OPEN_FILES_TARGET);
        if limit.rlim_cur < target {
            let raised = libc::rlimit {
                rlim_cur: target,
                rlim_max: limit.rlim_max,
            };
            if libc::setrlimit(libc::RLIMIT_NOFILE, &raised) == 0 {
                limit.rlim_cur = raised.rlim_cur;
            }
        }
    }
    Some(limit.rlim_cur)
}
#[cfg(not(unix))]
pub fn raise_open_files() -> Option<u64> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_default_is_ten_thousand_or_what_the_open_file_limit_carries() {
        assert_eq!(
            max_connections(None, Some(65_536)),
            Ok(DEFAULT_MAX_CONNECTIONS)
        );
        assert_eq!(max_connections(None, None), Ok(DEFAULT_MAX_CONNECTIONS));
        assert_eq!(
            max_connections(Some(" "), Some(1_048_576)),
            Ok(DEFAULT_MAX_CONNECTIONS)
        );
        // Docker's default soft limit (1024) gives the old 1000-ish, minus the reserve.
        assert_eq!(max_connections(None, Some(1024)), Ok(768));
        assert!(max_connections(None, Some(200)).is_err());
    }

    #[test]
    fn a_setting_is_used_within_its_range_and_the_open_file_limit() {
        assert_eq!(max_connections(Some("20000"), Some(65_536)), Ok(20_000));
        assert_eq!(max_connections(Some("500"), Some(1024)), Ok(500));
        assert_eq!(max_connections(Some("1"), None), Ok(1));
        for bad in ["0", "20001", "-5", "1e4", "ten"] {
            assert!(max_connections(Some(bad), Some(65_536)).is_err(), "{bad}");
        }
        let error = max_connections(Some("5000"), Some(1024)).unwrap_err();
        assert!(
            error.contains("needs at least 5256 open files, the limit is 1024"),
            "{error}"
        );
        assert!(error.contains("--ulimit nofile=65536:65536"));
    }

    #[cfg(unix)]
    #[test]
    fn the_soft_open_file_limit_is_raised_never_lowered() {
        let read = || {
            let mut limit = libc::rlimit {
                rlim_cur: 0,
                rlim_max: 0,
            };
            // SAFETY: getrlimit only writes the struct passed by pointer.
            #[allow(unsafe_code)]
            let status = unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, &mut limit) };
            assert_eq!(status, 0);
            (limit.rlim_cur, limit.rlim_max)
        };
        let (before, hard) = read();
        let soft = raise_open_files().unwrap();
        assert_eq!(soft, read().0);
        assert!(soft >= before && soft <= hard);
        // Linux allows any soft limit up to the hard one (macOS caps it lower by sysctl).
        #[cfg(target_os = "linux")]
        assert!(soft >= hard.min(OPEN_FILES_TARGET));
    }
}
