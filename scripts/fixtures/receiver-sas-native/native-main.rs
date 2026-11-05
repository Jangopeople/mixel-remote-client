//! Entire production SAS leaf compilation plus real Windows kernel negatives.
//! IPC is a rejecting type stub. No service/effect positive is simulated.
#[cfg(not(windows))]
compile_error!("This component harness requires actual Windows MSVC");

pub const POSTFIX_SERVICE: &str = "component-transport-disabled";
pub mod ipc {
    use hbb_common::{bail, ResultType};
    pub enum Data {
        SASScoped { owner: String, sequence: u64, revoked: u64, effect: u64 },
        SASReady { sequence: u64, error: Option<String> },
        SASCommit(u64),
    }
    pub struct Connection;
    impl Connection {
        pub fn sas_pipe_handle(&self) -> *mut std::ffi::c_void { std::ptr::null_mut() }
        pub async fn send(&mut self, _: &Data) -> ResultType<()> {
            bail!("IPC type stub: transport execution forbidden in component harness")
        }
        pub async fn next_timeout(&mut self, _: u64) -> ResultType<Option<Data>> {
            bail!("IPC type stub: transport execution forbidden in component harness")
        }
    }
    pub async fn connect(_: u64, _: &str) -> ResultType<Connection> {
        bail!("IPC type stub: transport execution forbidden in component harness")
    }
}
pub mod server {
    pub mod input_service {
        pub fn sas_effect_admitted() -> bool { false }
    }
}
#[path = "sas_leaf_under_test.rs"]
mod sas;
