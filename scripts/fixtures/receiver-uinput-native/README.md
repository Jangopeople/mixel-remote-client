This fixture prepares and executes the exact production Linux capability and
receipt-core modules. It creates owned Unix sockets and memfds, uses real
SO_PEERCRED and pidfds, and starts only its own bounded child fixtures. Its evdev
callbacks are counted component sinks. It does not open /dev/uinput, inject
input, obtain privileges, launch Mixel, or qualify a desktop session.

Prepare without a compiler:

```
python3 scripts/fixtures/receiver-uinput-native/prepare.py --output /new/fixture/path
```

On Linux in an authorized compiler window, add `--run`. The helper records each
actual compiler/run result, stdout, stderr, and input hashes in RESULT.json.
Preparation alone always records nativeComponentPassed=false. This component
fixture uses a thin Linux ABI declaration shim; the complete application build
must separately compile the real libc, evdev, Tokio and Enigo integration.

The receipt ledger is authoritative per actual VirtualDevice and IPC channel.
Upstream creates a new virtual device per connection. Scoped frontend key and
mouse suppression are bypassed for this own-service backend, so every owner
claim reaches that device's ledger. Other backends keep their native receipt
ownership. A transport failure retires the affected channel's actual receipts;
it never resets other devices or OS-global keys.

The prepared scenarios cover successful/failed actual callback receipts,
shared physical key claims, pulse ownership, final-Up retries, scoped modifier
queries, bounded binding caches, active-claim retention, real capability rights,
size/write seals, permanent revoke, queued-work barriers, 4,100 completed
generations and old Bind refusal, actual process exit, changed executable,
same-executable re-exec and a saturated Unix socket send deadline. Complete
application build and native Wayland customer consent/control remain separate
required proofs.
