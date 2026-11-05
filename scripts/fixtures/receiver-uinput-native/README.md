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

Each generation and device kind selects one strong service-generated channel
instance through a once-published slot in the revoke page. Both native effects
and cleanup ACKs require that instance. Reconnecting a backend requires retiring
the old generation and fresh customer authorization; an empty replacement ledger
cannot acknowledge an old device's held keys. Cleanup slices cap process probes
and stable receipt scans, and reset their deadline after work.

Add `--tokio` to a Linux `--run` command to exercise the exact production send
observer against real Tokio timers and slow counted cleanup callbacks. The
fixture also restores the original priority, missing reset and Burst behavior,
using a real three-second missed-tick backlog. It requires the original
starvation control to fail with its exact bounded panic marker. This
requires Cargo and the pinned Tokio 1.44.2 dependency. The format-3 Cargo.lock
retains the product's versions and checksums, including macro dependencies;
both Cargo commands use --locked. The prepared closure still requires actual
Cargo qualification and must fail rather than resolve a replacement lock.
These scenarios still do not compile or qualify the complete application leaf
or actual desktop input.

The complete app also requires the separate terminal
`patch-support-receiver-uinput-deps.py` source owner after own-uinput. The pinned
evdev VirtualDevice and parity Unix Connection otherwise expose no raw file
descriptor. Its two adapters implement AsRawFd over their existing private
handles; they do not change emit, SYN, setup, transport, or dependency edges.
The owner embeds genuine pinned commit/tree/blob snapshots and creates ordinary
shallow Git checkouts offline, retaining exact upstream origins/revisions. It
pins complete Cargo before/after sources and every tracked dependency byte and
mode. `--check` creates nothing. Private `previous()` removes only the two fully
authenticated new snapshots in its copy, preserving the supplied source and Git
metadata. Inverse order is deps, own-uinput, receiver, native. These source/Git
controls still do not replace a complete Linux application build.
