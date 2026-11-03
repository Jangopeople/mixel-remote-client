# Local unsigned Windows x64 build

`build-windows.py` consumes preinstalled native Windows tools, pinned source and
already-built static codecs. It performs no installer, account, ACL, policy,
service, VM, signing, client launch or publication operation. GitHub remains
source backup; this driver starts no Actions job and supplies no hosted identity.
Default invocation is an inert plan. `--run` explicitly starts the local build.

Required existing Windows inputs:

- Native AMD64 Windows and native Python3.11; ARM emulation,32-bit Python, WSL and
  the Windows Store alias cannot qualify. Python must already import Brotli,
  matching `rustdesk/libs/portable/requirements.txt`.
- Git for Windows/Git Bash, PowerShell7, CMake, Ninja and ImageMagick on PATH.
- An activated existing MSVC x64 developer environment with the native Microsoft
  LINK/cl pair, x64 host/target, INCLUDE/LIB and a Windows SDK containing its
  Windows.h, x64 kernel32.lib and x64 UCRT libraries. Flutter's Windows desktop
  prerequisites include VC.Tools.x86.x64 and VC.CMake.Project. Driver activation
  does not create/import a shell or install a workload; start it from the already
  activated native developer shell.
- A preinstalled native x64 LLVM dependency copied into a separate owned ordinary
  directory inside this repository, with `bin/libclang.dll`, its adjacent runtime
  dependencies and `lib/clang/<version>/include/stddef.h`. Pass the absolute DLL
  path with `--libclang`; the driver queries its actual Clang version and required
  exports, checks its AMD64 PE, and holds every file in that LLVM tree before and
  after the build. Rust `libs/scrap/build.rs` uses bindgen, so the DLL and resource
  headers are required independently of MSVC. The driver supplies only the admitted
  DLL directory as `LIBCLANG_PATH`; inherited LLVM/libclang overrides are discarded.
- A separately authorized, already active `RemoteSigned` **Process** execution
  context. The driver preserves only an existing
  `PSExecutionPolicyPreference=RemoteSigned`; it never sets execution policy,
  unblocks files or accepts `Bypass`/`Unrestricted`. It queries actual effective
  policy and all five scopes through the qualified PowerShell executable, saves
  the metadata, and refuses restrictive Group Policy or a different effective
  policy. Machine/User Policy remains authoritative. An approval to prepare
  these sources does not authorize changing the caller's execution context.
- Rust/Cargo1.75.0, host and installed target x86_64-pc-windows-msvc.
- Official complete Flutter3.27.4 SDK, revision
  d8a9f9a52e5af486f80d932e838ee93861ffd863, including native Dart and the precached
  windows-x64-release Flutter DLL/import library. No SDK download/precache runs.
- Clean ordinary upstream1abc897c451c8b5bbff3792509a7fef9d12f2ce3 with hbb_common
  48c37de3e6c4e399af6f51ca20e8e3e1fd037976. Preserve source LF bytes when obtaining
  it (`core.autocrlf=false`); homogeneous native LF/CRLF source output is checked.
- Pinned vcpkg120deac3062162151622ca4860575a33844ba10b and preinstalled
  x64-windows-static libvpx/libyuv/opus/aom/libjpeg-turbo. The six expected archives
  vpx/yuv/opus/aom/jpeg/turbojpeg.lib must contain actual AMD64 COFF objects.
- An existing owned repository/run root on fixed local NTFS with at least80GiB
  free. Repository/source/capsule/dependencies and new output must pass the exact
  existing current-token-SID/private-DACL/file-ID checks. Trusted DACL principals
  are current owner, SYSTEM and Administrators. Driver never changes permissions.
  Run root/output must actually be Git ignored. Output must not exist.

Prepare the bridge capsule on the Mac, from this exact checkout after all source
changes intended for the Windows build are stable:

```sh
python3.11 scripts/local/build-windows.py \
  --mac-build "$PWD/_scratch/local-build/arm64-build-r4" \
  --export-bridge "$PWD/_scratch/windows-bridge-r4-final"
```

Export is source-only. It binds the qualified Mac result, actual FRB1.80.1
version/generation receipts, all six physical output hashes, complete Rust/native/
local-dependency trees and every Cargo/build input. It retains physical hashes
and sizes plus strict whole-file homogeneous LF/CRLF views. Mixed/bare CR rejects;
binary files remain exact. No other normalization or missing input is permitted.
Transfer the capsule intact as binary files/archive into the private Windows
checkout. Six generated files must remain byte-exact. Pipeline source hashes must
match the export; after source changes, make a fresh capsule.

Windows branding omits a Darwin-only rdev overlay. The driver invokes that
existing strict source owner on its private copy, making the Mac-only callback
files/dependency and exact lock edge match the shared bridge input profile.
An actual private source inverse/reapply proof is recorded under
`_scratch/windows-bridge-profile-proof/RESULT.json`; all574 input files match
except the one reviewed Windows epoch successor. The sole projected source delta
is complete src/server/mixel_receiver_custodian_epoch_windows.rs:
b662ed35b2bea75c7f02214424186b983096565ab79bfbcb5625b5a165e6aeb7 →
f362530835fd3a45cb98d8616dc7517cc077189d700a1790e7700a434085c9a2.
Unknown bytes, mixed profiles and any other Rust/native/Cargo change refuse.

Example from the exact repository root in the activated Windows developer shell:

```powershell
python scripts/local/build-windows.py --source "$PWD/rustdesk" `
  --flutter "$PWD/_scratch/flutter" --vcpkg "$PWD/_scratch/vcpkg" `
  --bridge "$PWD/_scratch/windows-bridge-r4-final" `
  --libclang "$PWD/_scratch/llvm/bin/libclang.dll" `
  --run-root "$PWD/_scratch/windows-builds" `
  --output "$PWD/_scratch/windows-builds/x64-build" --jobs 1 --run
```

The wrapper independently copies source, uses fresh owned Cargo/Pub/temporary
caches, applies existing branding and only the six bound bridge outputs, requires
shipping extended_text14 and reruns pub get. Normal locked build-dependency fetches
are allowed; no prewarmed cache is required. Private TEMP/TMP avoids global build
output. Jobs1 is the default (bounded1–8) and overrides Cargo/CMake/vcpkg concurrency.

It runs existing local `check-app-rust.py --run --local-run-root`, actual
`build.py --flutter --skip-portable-pack`, full Flutter analyze with errors/warnings
fatal, compiled support guard, compiled asset branding, existing unsigned payload
preparation/PE metadata checks, then unsigned portable packaging and asInvoker
manifest verification. Every produced EXE/DLL must be native AMD64. Windows
build.py requires native target/release paths, so an inherited CARGO_BUILD_TARGET
or CARGO_TARGET_DIR override refuses; Rust host/target selection is proved first.

Cargo.lock is held after fetch, whole-app check, build, guard, packaging and every
exit. Supplied source/installed vcpkg/capsule/pipeline/toolchain hashes are rechecked.
Raw argv/stdout/stderr/actual exits, codec members, tool identities, bridge inputs,
payload and outer hashes are saved only under the fresh output. Failed or partial
commands cannot qualify; output is never reused.
`libclang-admission.json` retains the exact DLL version/hash and complete LLVM
dependency inventory, including directories that would be application caches in
a source tree. `execution-policy.json` retains the actual policy query even when
it refuses. A changed LLVM runtime, header or DLL cannot qualify the build.

A pass qualifies only an **unsigned** local payload and
`dist/Mixel-Remote-UNSIGNED-local-QA.exe`. This outer embeds unsigned input; the
customer signed outer must later be rebuilt from its verified signed inner
payload. Signing, full Windows source/native component suites, debugger-free cold
execution, customer Accept/control/clipboard/files/reconnect and held cleanup
remain separate mandatory acceptance. No Windows run is claimed by Mac inert
controls or by transferring Mac bridge source.

Current PC01 readiness observations from Root (2026-11-03): builtin csc exists,
existing account is signed in, but Git/Rust/Flutter are absent on PATH and Python
is a Store alias. MSVC/SDK/PowerShell7/Brotli and static codecs are not qualified;
C: has9.44GiB free of49.44GiB. The80GiB resource decision remains outside this
driver. No VM/disk changes are performed. A real Windows invocation must establish
all prerequisites above before any full application compilation can qualify.

Source controls on the Mac (all native APIs and processes mocked):

```sh
python3.11 scripts/test-local-windows-build.py
```
