// Minimal type dependency, not an application or IPC implementation.
// These three definitions are byte-identical to the current hbb_common source.
pub use anyhow::{self, bail};
pub fn timeout<T: std::future::Future>(ms: u64, future: T) -> tokio::time::Timeout<T> {
    tokio::time::timeout(std::time::Duration::from_millis(ms), future)
}
pub type ResultType<F, E = anyhow::Error> = anyhow::Result<F, E>;
