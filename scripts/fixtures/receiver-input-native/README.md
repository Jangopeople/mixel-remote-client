This fixture compiles the exact product receiver capability templates with a
small controlled native-effect adapter. The compiled prefix and test fixture
hashes are retained in source-binding.json.

It exercises actual generation queues, revoke/close/regrant ordering, cleanup
acknowledgment, aggregate bounds, nonblocking deferred admission and backend
transition refusal. On macOS it uses the real main dispatch queue; on Windows it
uses actual named events/mutexes, pipe peer process identities, process death,
read-only revocation handles, abandoned mutex refusal and a shared native thread.
The Windows positive peer tests require a nonzero interactive logon session;
Session 0 is intentionally denied by the product code.

Native keyboard/mouse effects and BlockInput are mocks. The fixture never blocks
the CI desktop or injects real input. It does not establish native receipt
correctness, complete application compilation, service SAS safety or live remote
support acceptance. Those remain separate required gates.

Use scripts/test-support-receiver-input-native.py --output <new-directory>.
Use --prepare-only for source binding without invoking Cargo. The Windows
negative compile gate proves an acquired mutex guard cannot migrate threads.
