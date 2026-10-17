Entire exact SAS Rust leaf / Rust 1.75.0 / hosted Windows MSVC

Preparation (source-only, default; no subprocess or native API):
  python scripts/fixtures/receiver-sas-native/prepare.py --output <fresh-existing-parent/output>

Root-admitted hosted Windows execution only:
  python scripts/fixtures/receiver-sas-native/prepare.py --output <fresh-runner-temp/output> --run

Root must provision Rust1.75.0 x86_64-pc-windows-msvc and actual MSVC/Windows SDK
(including sas.lib). The default command does not install tools or execute cargo.
The explicit --run uses fresh output/target and frozen lock v3 with --locked;
resolution or compilation failure is retained, with no permissive fallback.
Set a finite hosted job timeout above the driver's240s total budget. A command
timeout is failure; the ephemeral runner is the outer descendant cleanup bound.

The full production leaf is copied byte-for-byte as a prefix; fourteen tests are
appended. Real winreg0.11.0 and its production checksum-pinned dependency closure,
real anyhow/Tokio time, and actual kernel32/advapi32/sas FFI compile unchanged.
Only parent IPC types and receiver-context admission are rejecting stubs. Their
transport is never executed. Minimal hbb_common definitions are exact copies of
three production public exports/functions. The lock graph is prepared from the
retained production lock and local crate manifests; cargo resolution has NOT run.

Seven pure decision cases plus seven complete-leaf/kernel cases are prepared.
Native negatives use owned unnamed events/mutexes and two bounded component
child processes only. They cover duplicate rights and lifetime, missing process
DUP rights, real signaled revoke, actual retained child death, finite cross-thread
mutex wait and actual named-pipe foreign PID rejection. Pipe/event names belong
only to this fresh component. No service is installed/started, policy written,
application launched, UAC changed, or positive SYSTEM authority fabricated.
SendSAS/dispatch/request are type-referenced but never invoked by the tests.

A passing component run would prove compilation and these negatives only.
Actual installed-service positive, correct secure desktop/session delivery,
revocation races at the real effect, and physical Windows acceptance remain
separate actual-package tests. SendSAS is VOID, never a delivery acknowledgment.
