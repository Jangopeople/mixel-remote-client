# Local unsigned Apple Silicon build

`build-macos.py` implements the existing macOS build recipe on the Mac Mini.
GitHub remains source backup; this driver does not dispatch Actions.

Use Python 3.11, a complete selected Xcode installation and these tools:

```sh
brew install autoconf automake libtool pkg-config nasm cmake ninja imagemagick cocoapods
rustup toolchain install 1.81 --component rustfmt --target aarch64-apple-darwin,x86_64-apple-darwin
```

Supply a clean ordinary RustDesk checkout at exact 1.4.6 including its exact
`hbb_common` submodule, the complete official Flutter 3.27.4 SDK, and a source-only
vcpkg checkout at `120deac3062162151622ca4860575a33844ba10b`. Leave vcpkg
unbootstrapped; the driver creates private source copies, builds codecs at macOS
12.3 and verifies every archive member. Its Cargo, codegen, codec and pub caches
belong to the fresh output directory. Flutter may populate its own SDK engine
cache with `precache --macos`; its exact Git revision and tracked cleanliness are
checked and the used Dart/framework bytes are hashed.

From the exact repository root, inspect the command without starting any tools:

```sh
repo="$PWD"
python3.11 scripts/local/build-macos.py \
  --source "$repo/rustdesk" \
  --flutter "$repo/_scratch/local-build/flutter-sdk/flutter" \
  --vcpkg "$repo/_scratch/local-build/vcpkg" \
  --run-root "$repo/_scratch/local-build" \
  --output "$repo/_scratch/local-build/arm64-build" \
  --jobs 1
```

The run root must already exist, be Git-ignored and belong to the current user.
The output must not exist. After obtaining the current local native compiler
reservation, add `--run` to execute the same command. It fetches locked native/Dart
dependencies, generates all six bridge files in the exact reviewed order, checks
the full Rust app, builds the full Flutter app, analyzes it with fatal errors and
warnings, runs the compiled support guard, and verifies branding and every bundled
Mach-O architecture/minimum. It stops at the first failure without retrying.
`--jobs` accepts integers from 1 through 8 and defaults to 1. The driver owns
`CARGO_BUILD_JOBS`, `CMAKE_BUILD_PARALLEL_LEVEL` and `VCPKG_MAX_CONCURRENCY`, replacing
inherited values. The plan and final result record the selected concurrency.

`build.result.json` is successful only after all gates pass and both supplied
source trees remain unchanged. `commands/` contains every exact argv, working
directory, exit status and raw stdout/stderr. `app-manifest.json` binds the complete
unsigned `.app` tree by SHA256. The driver does not package, sign, notarize, publish,
install, launch an app, change OS privacy grants, or establish native remote-support
acceptance. Run the other mandatory source/native checks separately, followed by
the authorized signing and physical acceptance workflows.

Source-only driver controls (all child processes mocked):

```sh
python3.11 scripts/test-local-macos-build.py
```
