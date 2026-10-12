#!/usr/bin/env bash
set -euo pipefail
mixel_windows_source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$mixel_windows_source_root"
source scripts/use-windows-msvc-bash.sh
mixel_analyzer_bash="$BASH"
if [[ "${OS:-}" == Windows_NT ]]; then
  mixel_analyzer_bash="$(cygpath -w "$BASH")"
fi
python scripts/test-support-flutter-analysis.py --dart "$(command -v dart)" --bash "$mixel_analyzer_bash" --evidence-dir "$RUNNER_TEMP/mixel-sdk-source"
case "${MIXEL_SDK_FULL_APP_PREFLIGHT:-false}" in
  true|false) ;;
  *) echo "Invalid complete SDK application preflight request" >&2; exit 1 ;;
esac
if [[ "${MIXEL_SDK_FULL_APP_PREFLIGHT:-false}" == true ]]; then
  python scripts/test-support-flutter-sdk-compatibility.py --app "$RDREPO" --flutter "$(command -v flutter)" --bash "$mixel_analyzer_bash"
else
  python scripts/test-support-flutter-sdk-compatibility.py --app "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --bash "$mixel_analyzer_bash"
fi
python scripts/test-support-macos-termination.py --flutter "$(command -v flutter)" --bash "$mixel_analyzer_bash"
unset mixel_analyzer_bash
for mixel_source_test in \
  test-support-invite \
  test-generated-support-paths \
  test-windows-lease-observation \
  test-support-lease \
  test-support-network \
  test-secure-support \
  test-support-linux \
  test-support-signals \
  test-support-input \
  test-support-clipboard \
  test-support-video \
  test-support-macos \
  test-support-macos-cleanup \
  test-support-wakelock \
  test-branding; do
  python "scripts/$mixel_source_test.py"
done
dart analyze scripts/support-invite-reporter.dart scripts/test-support-invite.dart
dart scripts/test-support-invite.dart
