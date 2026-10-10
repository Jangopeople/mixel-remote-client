#!/usr/bin/env bash
set -euo pipefail
mixel_windows_source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$mixel_windows_source_root"
source scripts/use-windows-msvc-bash.sh
for mixel_source_test in \
  test-support-invite \
  test-generated-support-paths \
  test-support-macos-uri-routing \
  test-support-lease \
  test-windows-lease-error \
  test-support-network \
  test-secure-support \
  test-support-linux \
  test-support-signals \
  test-support-input \
  test-support-clipboard \
  test-support-video \
  test-support-macos \
  test-support-wakelock \
  test-branding; do
  python "scripts/$mixel_source_test.py"
done
dart analyze scripts/support-invite-reporter.dart scripts/test-support-invite.dart
dart scripts/test-support-invite.dart
