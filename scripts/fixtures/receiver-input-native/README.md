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

The Windows component also has a hosted-CI-only positive different-SID case. It
creates one fresh standard local account with a cryptographically random credential
kept only in memory, executes the exact production logon/identity/lease code from
a private GUID fixture copy, and checks intended read-only revoke, mutex and cleanup
rights. Only that account receives Read/Execute on the private fixture files. No
desktop or session-directory ACL bootstrap is used. Exact process exit, original
private DACL restoration, account/profile deletion and private-directory deletion
are mandatory; an unknown cleanup state fails and retains owned recovery resources.
This High-caller-to-Standard-account component case does not qualify elevation,
secure-desktop capture, application startup, native input delivery or PC01.
