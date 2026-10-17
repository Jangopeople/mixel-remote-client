# Early complete application Rust typecheck

`scripts/check-app-rust.py` prepares a private copy of the complete, already
branded upstream application and records the exact shipping Rust command. It
runs the real command only when `--run` is supplied on the matching GitHub-hosted
runner. Source preparation is the default; every invocation needs a new output
directory. Preparing an output does not authorize or perform compilation.

Run the source controls with:

```sh
python3 scripts/test-app-rust-check.py
```

The controls test this driver's copying, path guards, evidence and command
selection. They do not compile application source or demonstrate a working app.

## Workflow placement

Run after the shipping toolchain, target, native compiler and pinned vcpkg
triplet are installed, branding has completed, and bridge artifacts have been
restored. On Mac, first complete the existing owned Cargo home / pinned Tao
privacy patch and codec-minimum checks. On Windows, first activate the existing
native x64 MSVC environment. Place the check before the long complete-source
preflight. Keep the normal source preflight, full app builds, Flutter analysis,
signing, native/session/runtime tests and all publication gates mandatory.

Suggested Linux step body, using the existing shipping dependencies:

```sh
python3 scripts/test-app-rust-check.py
VCPKG_ROOT="$GITHUB_WORKSPACE/vcpkg" python3 scripts/check-app-rust.py \
  --source rustdesk --output "$RUNNER_TEMP/mixel-app-rust-linux" \
  --target x86_64-unknown-linux-gnu --run
```

Suggested Mac matrix step body (`matrix.job.target` is the existing actual
`aarch64-apple-darwin` or `x86_64-apple-darwin`):

```sh
python3 scripts/test-app-rust-check.py
VCPKG_ROOT="$GITHUB_WORKSPACE/vcpkg" python3 scripts/check-app-rust.py \
  --source rustdesk --output "$RUNNER_TEMP/mixel-app-rust-${{ matrix.job.target }}" \
  --target "${{ matrix.job.target }}" --run
```

The Mac check inherits the existing `CARGO_HOME`,
`VCPKG_OVERLAY_TRIPLETS`, `MACOSX_DEPLOYMENT_TARGET`, SDK and native tool setup.
It refuses a missing or unowned Cargo home, a different deployment minimum, or
an absent overlay. It does not fetch, repatch or change these prerequisites.

Suggested Windows Bash step body:

```sh
source scripts/use-windows-msvc-bash.sh
python scripts/test-app-rust-check.py
VCPKG_ROOT="$GITHUB_WORKSPACE/vcpkg" python scripts/check-app-rust.py \
  --source rustdesk --output "$RUNNER_TEMP/mixel-app-rust-windows" \
  --target x86_64-pc-windows-msvc --run
```

The driver uses the existing strict `rust_toolchain.find_msvc_linker` check
against the installed Microsoft x64 LINK binary, preserving the shipping SDK
and developer environment.

The exact command is:

```text
rustup run 1.75 cargo check --locked --release --lib --features flutter --target x86_64-unknown-linux-gnu
rustup run 1.75 cargo check --locked --release --lib --features flutter --target x86_64-pc-windows-msvc
rustup run 1.81 cargo check --locked --release --lib --features flutter,unix-file-copy-paste --target aarch64-apple-darwin
rustup run 1.81 cargo check --locked --release --lib --features flutter,unix-file-copy-paste --target x86_64-apple-darwin
```

Each invocation executes only its selected matrix command. It preserves default
features and native dependencies, with no mock backend or retry. Existing job
and step timeout policies should bound the hosted operation. On failure the
step must fail, retaining diagnostics before any longer preflight proceeds.

## Source isolation and evidence

The output directory contains `source/` (an ordinary private source copy) and a
private `target/`. This is necessary because application/hbb_common build scripts
write generated source such as `src/version.rs`. Original source files are
copied rather than hard-linked. Relative internal source links and dependency
Git pointers remain relocatable. Root/dependency Git metadata is retained.
External/absolute Git pointers, external object alternates, absolute/escaping
source links, linked-worktree roots and indirect output ancestors are refused.
Use canonical paths; parent traversal in source/output arguments is refused.

Every subtree named `worktrees` (case insensitive) is excluded before inspecting
or traversing it. Other exclusions are limited to the established anchored
application build/Dart/Flutter ephemeral caches. Source directories with the
same names at other locations remain included. The plan lists this policy.

The complete included input inventory records bytes, paths, modes and links,
including generated files that differ from the upstream Git tree. The driver
records upstream HEAD/tree plus an inventory SHA-256 and verifies the copy
before compilation. It re-inventories the original after every check attempt,
including a compiler failure. A source mismatch fails the gate. The original
application target directory and any inherited `CARGO_TARGET_DIR` are never
used. This driver does not share a Cargo target cache or link to another job's
artifacts; the installed shipping Cargo registry and vcpkg remain inherited.

Retain only these small evidence files with an `if: always()` artifact step:

```text
<output>/*.json
<output>/*.log
```

Do not upload the private source or target directories. Evidence includes:

- `prepare.attempt.json` and `prepare.result.json`, even on source-validation or
  copy failure after safe output reservation;
- `source.input.json`, containing the full included source inventory and Git
  binding, and `check.plan.json`, containing the exact target/toolchain/features,
  source binding, driver hash and command;
- `check.environment.json`, containing a small explicit prerequisite summary;
  inherited signing credentials and the complete environment are not dumped;
- real `rustc -Vv`, `cargo -V` and `cargo check` command/result JSON plus separate
  stdout/stderr logs, streamed live to CI; command evidence includes its source
  binding and resolved launcher hash;
- `check.result.json`, including the original source's final inventory binding
  and whether it remained unchanged, even when the check fails.

A passing result qualifies the real application Rust typecheck for that exact
source and target. It does not qualify a complete installer or release. Full
application builds, analysis, signing, native behavior, attended authorization,
reconnect/input and live support acceptance still require their existing gates.
