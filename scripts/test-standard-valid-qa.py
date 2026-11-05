"""Source/mock qualification only. Never runs Windows native setup or children."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'scripts'
_lifecycle_spec = importlib.util.spec_from_file_location('mixel_lifecycle_workflow_projection', SCRIPTS / 'file_dialog_workflow_projection.py')
_lifecycle_projection = importlib.util.module_from_spec(_lifecycle_spec)
_lifecycle_spec.loader.exec_module(_lifecycle_projection)
inverse_lifecycle_workflow_checks = _lifecycle_projection.inverse_lifecycle_workflow_checks
DRIVER = SCRIPTS / 'observe-standard-valid-qa-windows.ps1'
KNOWN = SCRIPTS / 'test-standard-qa-session-directory.cs'
ORIGINAL = SCRIPTS / 'test-support-lease-standard-windows.ps1'
SPEC = importlib.util.spec_from_file_location('previous_controls', SCRIPTS / 'test-session-directory-access.py')
PREVIOUS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREVIOUS)


COMPOSED_WORKFLOW_PROJECTION = [('          python3 scripts/test-support-launch-macos.py --self-test\n', '          python3 scripts/test-support-launch-macos-concurrency.py --original-desktop --self-test\n', 1), ('', '          python3 scripts/test-support-launch-macos-concurrency-controls.py --evidence-dir "$RUNNER_TEMP/mixel-macos-concurrency-source"\n', 1), ('  release-pipeline:\n    runs-on: ubuntu-22.04\n    steps:\n      - uses: actions/checkout@v5\n      - name: Run release staging and configuration regression tests\n        run: |\n          python3 scripts/test-release-pipeline.py\n          python3 scripts/test-flutter-sdk-pipeline.py\n', "  release-pipeline:\n    runs-on: ubuntu-22.04\n    steps:\n      - uses: actions/checkout@v5\n      - uses: dtolnay/rust-toolchain@v1\n        with:\n          toolchain: '1.75'\n      - name: Run release staging and configuration regression tests\n        run: |\n          python3 scripts/test-release-pipeline.py\n          python3 scripts/test-flutter-sdk-pipeline.py\n          python3 scripts/test-support-url-log-privacy.py\n", 1), ('          python3 scripts/test-notarize-macos.py\n          python3 scripts/test-macos-native-minimum.py\n          python3 scripts/test-macos-bundle-minimum.py\n          python3 scripts/test-sdk-discovery.py\n          python3 scripts/test-pinned-source-archives.py\n          python3 scripts/test-support-session-fixture.py\n          python3 scripts/test-flutter-branding.py\n          python3 scripts/test-support-video-sampling.py\n', '          python3 scripts/test-notarize-macos.py\n          python3 scripts/test-macos-native-minimum.py\n          python3 scripts/test-macos-bundle-minimum.py\n          python3 scripts/test-sdk-discovery.py\n          python3 scripts/test-rust-toolchain.py\n          python3 scripts/test-pinned-source-archives.py\n          python3 scripts/test-support-session-fixture.py\n          python3 scripts/test-flutter-branding.py\n          python3 scripts/test-support-video-sampling.py\n', 1), ("        run: bash -n scripts/apply-branding.sh scripts/local/sign-and-publish-macos.sh\n  windows-packaging:\n    runs-on: ${{ github.event_name == 'workflow_dispatch' && inputs.windows_source_runner == 'windows-2025' && 'windows-2025' || 'windows-2022' }}\n    timeout-minutes: 20\n    steps:\n      - uses: actions/checkout@v5\n      - name: Exercise Windows payload preparation and IPC response validation\n        shell: pwsh\n        run: ./scripts/test-windows-pipeline.ps1\n      - name: Exercise exact runtime health IPC with stock Windows PowerShell 5\n", '        run: bash -n scripts/apply-branding.sh scripts/local/sign-and-publish-macos.sh\n  windows-packaging:\n    runs-on: ${{ github.event_name == \'workflow_dispatch\' && inputs.windows_source_runner == \'windows-2025\' && \'windows-2025\' || \'windows-2022\' }}\n    timeout-minutes: 20\n    env:\n      PYTHONUTF8: \'1\'\n    steps:\n      - uses: actions/checkout@v5\n      - name: Verify UTF-8 repository source decoding\n        shell: pwsh\n        run: |\n          python -c "import sys; from pathlib import Path; assert sys.flags.utf8_mode == 1; p = Path(\'.github/workflows/build.yml\'); assert p.read_text() == p.read_text(encoding=\'utf-8\'); print(\'PASS: Windows Python reads repository source as strict UTF-8\')"\n          if ($LASTEXITCODE -ne 0) { throw \'Windows repository source encoding verification failed.\' }\n      - name: Exercise Windows payload preparation and IPC response validation\n        shell: pwsh\n        run: ./scripts/test-windows-pipeline.ps1\n      - name: Exercise exact runtime health IPC with stock Windows PowerShell 5\n', 1), ("      - uses: subosito/flutter-action@v2\n        with:\n          channel: stable\n          flutter-version: '3.27.4'\n      - name: Fetch pinned complete Windows source\n        shell: bash\n        env:\n          UPSTREAM_VERSION: ${{ steps.config.outputs.upstream_version }}\n", '      - uses: subosito/flutter-action@v2\n        with:\n          channel: stable\n          flutter-version: \'3.27.4\'\n      - name: Resolve canonical Windows Dart executable\n        shell: pwsh\n        run: |\n          python -c "import os, sys; from pathlib import Path; sys.path.insert(0, \'scripts\'); from sdk_discovery import find_dart; binary = find_dart(); assert binary and Path(binary).is_file(), \'Installed Dart SDK is missing\'; assert not binary.lower().endswith(\'.exe\') or binary.endswith(\'.exe\'), \'Windows Dart executable suffix is not canonical\'; stream = open(os.environ[\'GITHUB_ENV\'], \'a\', encoding=\'utf-8\', newline=\'\\n\'); stream.write(\'DART_BIN=\' + binary + \'\\n\'); stream.close(); print(\'PASS: canonical installed Windows Dart executable exported to unchanged source tests\')"\n          if ($LASTEXITCODE -ne 0) { throw \'Windows Dart SDK resolution failed.\' }\n      - name: Fetch pinned complete Windows source\n        shell: bash\n        env:\n          UPSTREAM_VERSION: ${{ steps.config.outputs.upstream_version }}\n', 1), ("        shell: pwsh\n        run: |\n          python scripts/test-support-windows-clipboard.py --evidence-dir windows-clipboard-proof\n          if ($LASTEXITCODE -ne 0) { throw 'Native Windows clipboard C/Rust ownership qualification failed.' }\n      - name: Qualify actual native Rust linker after Git Bash startup\n        shell: bash\n        run: |\n          source scripts/use-windows-msvc-bash.sh\n", "        shell: pwsh\n        run: |\n          python scripts/test-support-windows-clipboard.py --evidence-dir windows-clipboard-proof\n          if ($LASTEXITCODE -ne 0) { throw 'Native Windows clipboard C/Rust ownership qualification failed.' }\n      - name: Prove explicit MSVC selection under actual Git Bash coreutils shadow\n        shell: bash\n        run: python scripts/test-rust-toolchain.py --native-windows\n      - name: Qualify actual native Rust linker after Git Bash startup\n        shell: bash\n        run: |\n          source scripts/use-windows-msvc-bash.sh\n", 1), ("        shell: powershell\n        run: |\n          if ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1 -or $PSVersionTable.PSEdition -cne 'Desktop' -or -not [Environment]::Is64BitProcess) { throw 'Requires actual stock x64 Windows PowerShell 5.1 Desktop.' }\n          Write-Host ('PASS: actual stock Windows PowerShell ' + $PSVersionTable.PSVersion.ToString() + '; Desktop; x64; source controls only.')\n          ./scripts/test-support-lease-interactive-source.ps1\n      - name: Verify portable source and malformed-input refusals\n        shell: pwsh\n        run: |\n          python scripts/test-standard-lease-qa-bundle.py\n", "        shell: powershell\n        run: |\n          if ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1 -or $PSVersionTable.PSEdition -cne 'Desktop' -or -not [Environment]::Is64BitProcess) { throw 'Requires actual stock x64 Windows PowerShell 5.1 Desktop.' }\n          Write-Host ('PASS: actual stock Windows PowerShell ' + $PSVersionTable.PSVersion.ToString() + '; Desktop; x64; source controls only.')\n          ./scripts/test-support-lease-interactive-source.ps1 -NativePipeControls\n      - name: Verify portable source and malformed-input refusals\n        shell: pwsh\n        run: |\n          python scripts/test-standard-lease-qa-bundle.py\n", 1), ('        run: git clone --depth 1 --branch "$UPSTREAM_VERSION" --recurse-submodules https://github.com/rustdesk/rustdesk.git rustdesk\n      - name: Exercise actual source patches and support reporter\n        run: |\n          python3 scripts/test-support-flutter-analysis.py --dart "$(command -v dart)" --evidence-dir "$RUNNER_TEMP/mixel-sdk-source"\n          python3 scripts/test-support-flutter-sdk-compatibility.py --app "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"\n          python3 scripts/test-support-native-warnings.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --source-only --output "$RUNNER_TEMP/mixel-native-warning-source"\n          python3 scripts/test-macos-update.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --source-only\n          python3 scripts/test-support-file-transfer-window.py --app "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --output "$RUNNER_TEMP/mixel-file-transfer-source" --flutter "$(command -v flutter)"\n          python3 scripts/test-support-macos-termination.py --flutter "$(command -v flutter)"\n          python3 scripts/test-support-invite.py\n          python3 scripts/test-generated-support-paths.py\n          python3 scripts/test-support-lease.py\n          python3 scripts/test-support-network.py\n          python3 scripts/test-secure-support.py\n          python3 scripts/test-support-linux.py\n          python3 scripts/test-support-signals.py\n', '        run: git clone --depth 1 --branch "$UPSTREAM_VERSION" --recurse-submodules https://github.com/rustdesk/rustdesk.git rustdesk\n      - name: Exercise actual source patches and support reporter\n        run: |\n          python3 scripts/test-support-flutter-analysis.py --dart "$(command -v dart)" --evidence-dir "$RUNNER_TEMP/mixel-sdk-source"\n          python3 scripts/test-macos-startup.py\n          python3 scripts/test-support-macos-tray-url.py\n          python3 scripts/test-support-launch-macos-tray-controls.py\n          python3 scripts/test-support-macos-termination-support.py --app "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"\n          python3 scripts/test-support-flutter-sdk-compatibility.py --app "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"\n          python3 scripts/test-support-native-warnings.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --source-only --output "$RUNNER_TEMP/mixel-native-warning-source"\n          python3 scripts/test-macos-update.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --source-only\n          python3 scripts/test-support-file-transfer-window.py --app "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --output "$RUNNER_TEMP/mixel-file-transfer-source" --flutter "$(command -v flutter)"\n          python3 scripts/test-support-macos-termination.py --flutter "$(command -v flutter)"\n          python3 scripts/test-support-invite.py\n          python3 scripts/test-generated-support-paths.py\n          python3 scripts/test-support-macos-uri-routing.py\n          python3 scripts/test-support-macos-url-ack.py\n          python3 scripts/test-support-macos-url-async-peer.py\n          python3 scripts/test-macos-update.py\n          python3 scripts/test-support-lease.py\n          python3 scripts/test-windows-lease-error.py\n          python3 scripts/test-support-network.py\n          python3 scripts/test-secure-support.py\n          python3 scripts/test-support-linux.py\n          python3 scripts/test-support-signals.py\n', 1), ('          dart analyze scripts/support-invite-reporter.dart scripts/test-support-invite.dart\n          dart scripts/test-support-invite.dart\n  macos-runtime-harness:\n    runs-on: macos-14\n    timeout-minutes: 10\n    steps:\n      - uses: actions/checkout@v5\n      - name: Resolve the pinned native Mac source\n        id: config\n        run: python3 scripts/release-artifacts.py config\n      - uses: dtolnay/rust-toolchain@v1\n        with:\n', '          dart analyze scripts/support-invite-reporter.dart scripts/test-support-invite.dart\n          dart scripts/test-support-invite.dart\n  macos-runtime-harness:\n    runs-on: macos-14\n    timeout-minutes: 30\n    steps:\n      - uses: actions/checkout@v5\n      - name: Resolve the pinned upstream version\n        id: config\n        run: python3 scripts/release-artifacts.py config\n      - uses: dtolnay/rust-toolchain@v1\n        with:\n', 1), ('        run: git clone --depth 1 --branch "$UPSTREAM_VERSION" --recurse-submodules https://github.com/rustdesk/rustdesk.git rustdesk\n      - name: Prove exact SDK source compatibility and composed branding\n        run: |\n          python3 scripts/test-support-flutter-analysis.py --dart "$(command -v dart)" --evidence-dir "$RUNNER_TEMP/mixel-sdk-source"\n          python3 scripts/test-support-flutter-sdk-compatibility.py --app "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"\n          python3 scripts/test-support-native-warnings.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --source-only --output "$RUNNER_TEMP/mixel-native-warning-source"\n          RUSTC_BIN="$(rustup which rustc)" python3 scripts/test-macos-update.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"\n      - name: Prove native fatal-crash handling with isolated owned children\n', '        run: git clone --depth 1 --branch "$UPSTREAM_VERSION" --recurse-submodules https://github.com/rustdesk/rustdesk.git rustdesk\n      - name: Prove exact SDK source compatibility and composed branding\n        run: |\n          python3 scripts/test-support-flutter-analysis.py --dart "$(command -v dart)" --evidence-dir "$RUNNER_TEMP/mixel-sdk-source"\n          python3 scripts/test-macos-startup.py\n          python3 scripts/test-support-macos-tray-url.py\n          python3 scripts/test-support-launch-macos-tray-controls.py\n          python3 scripts/test-support-macos-termination-support.py --app "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"\n          python3 scripts/test-support-flutter-sdk-compatibility.py --app "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"\n          python3 scripts/test-support-native-warnings.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --source-only --output "$RUNNER_TEMP/mixel-native-warning-source"\n          RUSTC_BIN="$(rustup which rustc)" python3 scripts/test-macos-update.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"\n      - name: Prove native fatal-crash handling with isolated owned children\n', 1), ('      - name: Exercise native macOS URL activation and window probe self-tests\n        run: |\n          python3 scripts/test-support-launch-macos-diagnostics.py\n          python3 scripts/test-support-launch-macos.py --self-test\n          python3 scripts/test-macos-native-minimum.py\n          python3 scripts/test-macos-bundle-minimum.py\n      - uses: actions/upload-artifact@v6\n        if: always()\n', '      - name: Exercise native macOS URL activation and window probe self-tests\n        run: |\n          python3 scripts/test-support-launch-macos-diagnostics.py\n          python3 scripts/test-support-launch-macos.py --self-test\n          python3 scripts/test-support-macos-uri-routing.py\n          python3 scripts/test-macos-native-minimum.py\n          python3 scripts/test-macos-bundle-minimum.py\n      - uses: actions/upload-artifact@v6\n        if: always()\n', 1)]
COMPOSED_SESSION_TEST_PROJECTION = [("HISTORICAL_WRAPPER_SHA = '5d81c7737eb2e9040fbb7f2c0046ec6649f774ca46098f3f61faceacb8a97b46'\n\nclass Controls(unittest.TestCase):\n    def ps(self, body, *, include_mock=False):\n", "HISTORICAL_WRAPPER_SHA = '5d81c7737eb2e9040fbb7f2c0046ec6649f774ca46098f3f61faceacb8a97b46'\n\nACK_VIEW = load('ack_unit_view', SCRIPTS / 'test-support-directory-security.py')\ninverse_current_ack_source = ACK_VIEW.inverse_current_ack_source\nACK_SOURCE_INVERSES = ACK_VIEW.ACK_SOURCE_INVERSES\n\n\nclass Controls(unittest.TestCase):\n    def ps(self, body, *, include_mock=False):\n", 1), ("            self.assertFalse(destination.exists())\n\n    def test_actual_composition_preserves_original_source_and_gate(self):\n        original=SCRIPTS/'test-support-lease-standard-windows.ps1'\n", "            self.assertFalse(destination.exists())\n\n    def test_ack_unit_view_closed_inverse_and_drift_refusal(self):\n        for name, binding in ACK_SOURCE_INVERSES.items():\n            data=(SCRIPTS/name).read_bytes().replace(b'\\r\\n',b'\\n')\n            for raw in (data, data.replace(b'\\n',b'\\r\\n')):\n                self.assertEqual(hashlib.sha256(inverse_current_ack_source(name,raw)).hexdigest(),binding['inputSHA256'])\n            for raw in (data+b'# changed\\n', data.replace(b'\\n',b'\\r\\n',1), data[:-1]):\n                with self.assertRaises(AssertionError):\n                    inverse_current_ack_source(name,raw)\n        print('PASS: exact three current ack fixture byte inverses recover the frozen optional family in LF/CRLF; drift, mixed and truncated sources refuse. Frozen runtime helpers remain unchanged.')\n\n    def test_actual_composition_preserves_original_source_and_gate(self):\n        original=SCRIPTS/'test-support-lease-standard-windows.ps1'\n", 1), ('        pins=json.loads(re.search(r"\\$pins=ConvertFrom-Json @\'\\n(.*?)\\n\'@",wrapper,re.S).group(1))\n        for name,digest in pins.items():\n            self.assertEqual(hashlib.sha256((SCRIPTS/name).read_bytes()).hexdigest(),digest,name)\n\n    def test_literal_powershell_orchestration_restoration_and_error_precedence(self):\n', '        pins=json.loads(re.search(r"\\$pins=ConvertFrom-Json @\'\\n(.*?)\\n\'@",wrapper,re.S).group(1))\n        for name,digest in pins.items():\n            self.assertEqual(hashlib.sha256(inverse_current_ack_source(name, (SCRIPTS/name).read_bytes())).hexdigest(),digest,name)\n\n    def test_literal_powershell_orchestration_restoration_and_error_precedence(self):\n', 1), ("    spec.loader.exec_module(module)\n    return module\n\n\nclass Controls(unittest.TestCase):\n    def ps(self, body, *, include_mock=False):\n        with tempfile.TemporaryDirectory(prefix='mixel-aba-source-') as folder:\n            root = Path(folder)\n", '    spec.loader.exec_module(module)\n    return module\n\n\nCOMPOSED_WRAPPER_PROJECTION = [(\'  $scope=$null; $world=[IntPtr]::Zero; $bodyFailure=$null; $restoreFailure=$null; $reportFailure=$null\\n  $cleanupFailures=[Collections.Generic.List[Exception]]::new()\\n  $observations=[Collections.Generic.List[object]]::new()\\n  $sourceHash=(Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()\\n  try {\\n    $world=[MixelIsolatedLeaseControl]::WorldEvent($eventName)\\n    $scope=[MixelSessionDirectoryAba+Scope]::new([MixelSessionDirectoryAba+NativeApi]::new(),$sidBytes)\\n    $scope.Open()\\n\', \'  $scope=$null; $world=[IntPtr]::Zero; $bodyFailure=$null; $restoreFailure=$null; $reportFailure=$null\\n  $cleanupFailures=[Collections.Generic.List[Exception]]::new()\\n  $observations=[Collections.Generic.List[object]]::new()\\n  $sourceHash=(Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()\\n  # Preserve quarantine in the original outer finally even if this independent\\n  # diagnostic fails before the later mask12 registration/lifecycle starts.\\n  $script:namespaceRestorationKnown=$false\\n  try {\\n    $world=[MixelIsolatedLeaseControl]::WorldEvent($eventName)\\n    $scope=[MixelSessionDirectoryAba+Scope]::new([MixelSessionDirectoryAba+NativeApi]::new(),$sidBytes)\\n    $scope.Open()\\n\', 1), ("  if ($restoreFailure -or $cleanupFailures.Count -gt 0 -or\\n      ($scope -and $scope.facts.writeAttempted -and -not $scope.facts.restorationVerified)) {\\n    throw \'Incomplete temporary directory ACL/process ownership; original Standard gate must not run in an unknown modified environment.\'\\n  }\\n  if ($bodyFailure -or $reportFailure) {\\n    $script:abaDeferredFailure=$(if($bodyFailure){$bodyFailure}else{$reportFailure})\\n    Write-Host \'FAIL: restored CI-only directory experiment failed; deferred behind unchanged original Standard gate.\'\\n  }\\n", "  if ($restoreFailure -or $cleanupFailures.Count -gt 0 -or\\n      ($scope -and $scope.facts.writeAttempted -and -not $scope.facts.restorationVerified)) {\\n    throw \'Incomplete temporary directory ACL/process ownership; original Standard gate must not run in an unknown modified environment.\'\\n  }\\n  $script:namespaceRestorationKnown=$true\\n  if ($bodyFailure -or $reportFailure) {\\n    $script:abaDeferredFailure=$(if($bodyFailure){$bodyFailure}else{$reportFailure})\\n    Write-Host \'FAIL: restored CI-only directory experiment failed; deferred behind unchanged original Standard gate.\'\\n  }\\n", 1), (\'{\\n  "support-invite-guard.rs": "4b48b7995b36cc9662958ef8753be70ef0b6cf35626b7264f0151bc553293f29",\\n  "test-session-directory-access.cs": "c562cf11de6dc395d4a6aae6d887dbe7fb970603bde74c8e4d111ac26956eb76",\\n  "test-support-directory-security.cs": "3f70bcef70508b264a9559dbb9f7fc9bdbb0c3e28ea7929008dc084dec6d973e",\\n  "test-support-launch-windows.ps1": "012ad7b7a2e52ddc7bd1c2c7aae63b7726187dab2d6aa2291bbb39de82928dec",\\n  "test-support-lease-standard-windows.ps1": "a9b0bf8ee68209f78a82ca4260c9d35faf10449a25f5976775731e98042a057f",\\n  "test-support-lease.py": "d11b25c95232544eb64f5de78f0e31cbc54630dd832185c55edfb4a3157cd604",\\n  "test-support-nt-event-binding.cs": "4f166e8e0fbdce7a5fb6786862e031c5bb175704e7490a0e4a9feaac9fac62ca",\\n  "test-support-nt-event-child.rs": "80cf1b1969b66568910d3a84ba46d4749db1c5744aac21b69777cd795dc0b034",\\n  "test-windows-lease-observation.py": "bc64f1bd16d32d201de7654b04f26074428a742f896d18977a6012408329a75e"\\n}\\n\\\'@\\nforeach ($pin in $pins.PSObject.Properties) {\\n  $path=Join-Path $PSScriptRoot $pin.Name\\n\', \'{\\n  "support-invite-guard.rs": "4b48b7995b36cc9662958ef8753be70ef0b6cf35626b7264f0151bc553293f29",\\n  "test-session-directory-access.cs": "c562cf11de6dc395d4a6aae6d887dbe7fb970603bde74c8e4d111ac26956eb76",\\n  "test-support-directory-security.cs": "3f70bcef70508b264a9559dbb9f7fc9bdbb0c3e28ea7929008dc084dec6d973e",\\n  "test-support-launch-windows.ps1": "c1f3e99ebda0a146bce9b54f215feeebd0c6d19bec19f2a280259d311983c44b",\\n  "test-support-lease-standard-windows.ps1": "c2897356a81f5410789fcebab4e845639a329da892b41cff4675aa08bc17f15e",\\n  "test-support-lease.py": "bdba6cc24c0b247de85a3bcda79744dd932f00bb6e97d038eba14a7e43d1db64",\\n  "test-support-nt-event-binding.cs": "4f166e8e0fbdce7a5fb6786862e031c5bb175704e7490a0e4a9feaac9fac62ca",\\n  "test-support-nt-event-child.rs": "80cf1b1969b66568910d3a84ba46d4749db1c5744aac21b69777cd795dc0b034",\\n  "test-windows-lease-observation.py": "2de9b36dc99e76523572f8db19fdeba0b2747769a87b476a10819470690f23f9",\\n  "rust_toolchain.py": "2156c1452df874462a5e2a569f25a800a339cc2cb92f5732340aa12a633afa3c",\\n  "windows-standard-namespace-readonly.rs": "23e5fee444459cb68ed7c2bb2ac6c8696583697ce8874019101b16659334b705",\\n  "windows-standard-namespace-access.cs": "7f04727ae8161b8e4da1d2e4a74a0324549996da5af52a08c14538ccf53b2589",\\n  "windows-standard-namespace-diagnostics.cs": "980178cba4736f260d1365d3f19b3371158efcd0856d75fb12c036b0a093aad8"\\n}\\n\\\'@\\nforeach ($pin in $pins.PSObject.Properties) {\\n  $path=Join-Path $PSScriptRoot $pin.Name\\n\', 1)]\nHISTORICAL_WRAPPER_SHA = \'5d81c7737eb2e9040fbb7f2c0046ec6649f774ca46098f3f61faceacb8a97b46\'\n\nclass Controls(unittest.TestCase):\n    def ps(self, body, *, include_mock=False):\n        with tempfile.TemporaryDirectory(prefix=\'mixel-aba-source-\') as folder:\n            root = Path(folder)\n', 1), ("            restored=restored.replace(current,previous)\n        self.assertEqual(hashlib.sha256(restored.encode('utf-8')).hexdigest(),\n                         'bf291d27fcdb4aec3cae1b19b29f70491c74159dafba5e49642746cbf6233379')\n        wrapper=WRAPPER.read_text(encoding='utf-8')\n        # Reverse only the exact final-carrier source-family pin rebindings.\n        for current, original in [('012ad7b7a2e52ddc7bd1c2c7aae63b7726187dab2d6aa2291bbb39de82928dec', '765b98c6324161018321eadb3528a14a844dc4dd1bee1df158c9f68db6c8af8e'), ('d11b25c95232544eb64f5de78f0e31cbc54630dd832185c55edfb4a3157cd604', 'f1051afc3860751cfea233216fa65cd1f7e29b639200f648c203dafc294530a9'), ('bc64f1bd16d32d201de7654b04f26074428a742f896d18977a6012408329a75e', '6f1ea99ec821be91fe88c13d8b62743346265e145b694dc4f05a60e94bcb121e')]:\n            self.assertEqual(wrapper.count(current), 1)\n            wrapper = wrapper.replace(current, original, 1)\n", "            restored=restored.replace(current,previous)\n        self.assertEqual(hashlib.sha256(restored.encode('utf-8')).hexdigest(),\n                         'bf291d27fcdb4aec3cae1b19b29f70491c74159dafba5e49642746cbf6233379')\n        wrapper=WRAPPER.read_text(encoding='utf-8')\n        for original, added, count in COMPOSED_WRAPPER_PROJECTION:\n            self.assertEqual(wrapper.count(added), count)\n            wrapper = wrapper.replace(added, original)\n        self.assertEqual(hashlib.sha256(wrapper.encode()).hexdigest(), HISTORICAL_WRAPPER_SHA)\n        # Reverse only the exact final-carrier source-family pin rebindings.\n        for current, original in [('012ad7b7a2e52ddc7bd1c2c7aae63b7726187dab2d6aa2291bbb39de82928dec', '765b98c6324161018321eadb3528a14a844dc4dd1bee1df158c9f68db6c8af8e'), ('d11b25c95232544eb64f5de78f0e31cbc54630dd832185c55edfb4a3157cd604', 'f1051afc3860751cfea233216fa65cd1f7e29b639200f648c203dafc294530a9'), ('bc64f1bd16d32d201de7654b04f26074428a742f896d18977a6012408329a75e', '6f1ea99ec821be91fe88c13d8b62743346265e145b694dc4f05a60e94bcb121e')]:\n            self.assertEqual(wrapper.count(current), 1)\n            wrapper = wrapper.replace(current, original, 1)\n", 1), ("$resultPath=Join-Path $root 'fixture-result.json'\nforeach($mode in @('normal','set-false','set-throw','restore-false','restore-readback-drift','close-false','unload-throw','open-denied','query-fails','foreign-drift','body-before','body-temporary','stop-fails','close-fails','exit-fails','identity-fails')){\n $script:abaEvidence=Join-Path $root $mode;[void][IO.Directory]::CreateDirectory($script:abaEvidence)\n $script:abaDeferredFailure=$null;$script:probeCalls.Clear();$script:probeFault='none'\n [AbaMock+Platform]::Mode=$mode;[MixelOrdinaryTokenFixture]::mode=$mode\n if($mode.StartsWith('body-')){[AbaMock+Platform]::Mode='normal';$script:probeFault=$mode.Substring(5)}\n if($mode -eq 'identity-fails'){$freshFacts.integrityRid=12288}\n $failed=$null;try{Invoke-SessionDirectoryAba}catch{$failed=$_}\n $fatal=$mode -in @('restore-false','restore-readback-drift','close-false','unload-throw','stop-fails','close-fails','exit-fails','identity-fails')\n if(($null -ne $failed) -ne $fatal){throw ('Wrong fatal-vs-deferred: '+$mode+' '+$failed)}\n if($mode -eq 'identity-fails'){$freshFacts.integrityRid=8192;continue}\n $report=[IO.File]::ReadAllText((Join-Path $script:abaEvidence 'session-directory-aba.json'))|ConvertFrom-Json\n if($report.actualWin32RouteMeasured -or $report.causeEstablished -or $report.productionWorkaroundQualified -or $report.standardEightQualified){throw 'Qualification escaped mock scope'}\n if($mode -eq 'normal'){\n  if($report.observations.Count -ne 6 -or $script:probeCalls.Count -ne 6){throw 'Both source launchers/three phases missing'}\n", "$resultPath=Join-Path $root 'fixture-result.json'\nforeach($mode in @('normal','set-false','set-throw','restore-false','restore-readback-drift','close-false','unload-throw','open-denied','query-fails','foreign-drift','body-before','body-temporary','stop-fails','close-fails','exit-fails','identity-fails')){\n $script:abaEvidence=Join-Path $root $mode;[void][IO.Directory]::CreateDirectory($script:abaEvidence)\n $script:abaDeferredFailure=$null;$script:probeCalls.Clear();$script:probeFault='none'\n $script:namespaceRestorationKnown=$true\n [AbaMock+Platform]::Mode=$mode;[MixelOrdinaryTokenFixture]::mode=$mode\n if($mode.StartsWith('body-')){[AbaMock+Platform]::Mode='normal';$script:probeFault=$mode.Substring(5)}\n if($mode -eq 'identity-fails'){$freshFacts.integrityRid=12288}\n $failed=$null;try{Invoke-SessionDirectoryAba}catch{$failed=$_}\n $fatal=$mode -in @('restore-false','restore-readback-drift','close-false','unload-throw','stop-fails','close-fails','exit-fails','identity-fails')\n if(($null -ne $failed) -ne $fatal){throw ('Wrong fatal-vs-deferred: '+$mode+' '+$failed)}\n if($mode -eq 'identity-fails'){$freshFacts.integrityRid=8192;continue}\n if([bool]$script:namespaceRestorationKnown -eq $fatal){throw ('Actual pre-registration diagnostic lost restoration/quarantine flag: '+$mode)}\n $report=[IO.File]::ReadAllText((Join-Path $script:abaEvidence 'session-directory-aba.json'))|ConvertFrom-Json\n if($report.actualWin32RouteMeasured -or $report.causeEstablished -or $report.productionWorkaroundQualified -or $report.standardEightQualified){throw 'Qualification escaped mock scope'}\n if($mode -eq 'normal'){\n  if($report.observations.Count -ne 6 -or $script:probeCalls.Count -ne 6){throw 'Both source launchers/three phases missing'}\n", 1)]
HISTORICAL_SESSION_TEST_SHA = 'b2f9d2019fed31e7bcf44fe15cfae5a74444a8d97964e6cccaf28f2fcfc12a3a'
COMPOSED_WRAPPER_PROJECTION = [('  $scope=$null; $world=[IntPtr]::Zero; $bodyFailure=$null; $restoreFailure=$null; $reportFailure=$null\n  $cleanupFailures=[Collections.Generic.List[Exception]]::new()\n  $observations=[Collections.Generic.List[object]]::new()\n  $sourceHash=(Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()\n  try {\n    $world=[MixelIsolatedLeaseControl]::WorldEvent($eventName)\n    $scope=[MixelSessionDirectoryAba+Scope]::new([MixelSessionDirectoryAba+NativeApi]::new(),$sidBytes)\n    $scope.Open()\n', '  $scope=$null; $world=[IntPtr]::Zero; $bodyFailure=$null; $restoreFailure=$null; $reportFailure=$null\n  $cleanupFailures=[Collections.Generic.List[Exception]]::new()\n  $observations=[Collections.Generic.List[object]]::new()\n  $sourceHash=(Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()\n  # Preserve quarantine in the original outer finally even if this independent\n  # diagnostic fails before the later mask12 registration/lifecycle starts.\n  $script:namespaceRestorationKnown=$false\n  try {\n    $world=[MixelIsolatedLeaseControl]::WorldEvent($eventName)\n    $scope=[MixelSessionDirectoryAba+Scope]::new([MixelSessionDirectoryAba+NativeApi]::new(),$sidBytes)\n    $scope.Open()\n', 1), ("  if ($restoreFailure -or $cleanupFailures.Count -gt 0 -or\n      ($scope -and $scope.facts.writeAttempted -and -not $scope.facts.restorationVerified)) {\n    throw 'Incomplete temporary directory ACL/process ownership; original Standard gate must not run in an unknown modified environment.'\n  }\n  if ($bodyFailure -or $reportFailure) {\n    $script:abaDeferredFailure=$(if($bodyFailure){$bodyFailure}else{$reportFailure})\n    Write-Host 'FAIL: restored CI-only directory experiment failed; deferred behind unchanged original Standard gate.'\n  }\n", "  if ($restoreFailure -or $cleanupFailures.Count -gt 0 -or\n      ($scope -and $scope.facts.writeAttempted -and -not $scope.facts.restorationVerified)) {\n    throw 'Incomplete temporary directory ACL/process ownership; original Standard gate must not run in an unknown modified environment.'\n  }\n  $script:namespaceRestorationKnown=$true\n  if ($bodyFailure -or $reportFailure) {\n    $script:abaDeferredFailure=$(if($bodyFailure){$bodyFailure}else{$reportFailure})\n    Write-Host 'FAIL: restored CI-only directory experiment failed; deferred behind unchanged original Standard gate.'\n  }\n", 1), ('    "`nif (`$script:abaDeferredFailure) { throw `$script:abaDeferredFailure }`n"\n}\n\nfunction Start-StandardValidQaNamespace {\n  if ($script:qaNamespaceScope -or $ownedUser -cnotmatch \'\\Amixellease[0-9a-f]{10}\\z\' -or\n      (Get-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid))).Name -cne $ownedUser -or\n      $runnerFacts.unavailableKind -or $freshFacts.unavailableKind -or\n      $runnerFacts.integrityRid -ne 12288 -or $freshFacts.integrityRid -ne 8192 -or\n      $runnerFacts.hasRestrictions -or $runnerFacts.isAppContainer -or\n', '    "`nif (`$script:abaDeferredFailure) { throw `$script:abaDeferredFailure }`n"\n}\n\nfunction Start-StandardValidQaNamespace {\n  if ($script:qaNamespaceRegistered -or $ownedUser -cnotmatch \'\\Amixellease[0-9a-f]{10}\\z\' -or\n      (Get-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid))).Name -cne $ownedUser -or\n      $runnerFacts.unavailableKind -or $freshFacts.unavailableKind -or\n      $runnerFacts.integrityRid -ne 12288 -or $freshFacts.integrityRid -ne 8192 -or\n      $runnerFacts.hasRestrictions -or $runnerFacts.isAppContainer -or\n', 1), ("      $runnerFacts.sessionId -ne $freshFacts.sessionId -or\n      $runnerFacts.sessionId -ne (Get-Process -Id $PID).SessionId) {\n    throw 'Exact disposable owned QA identity and High/Medium same-session source facts required.'\n  }\n  $identity=[Security.Principal.SecurityIdentifier]::new($ownedSid)\n  $sidBytes=[byte[]]::new($identity.BinaryLength);$identity.GetBinaryForm($sidBytes,0)\n  $script:qaNamespaceScope=[MixelSessionDirectoryFixture+Scope]::new([MixelSessionDirectoryFixture+NativeApi]::new(),$sidBytes)\n  $script:qaNamespaceCleanupOkay=$false\n  # Publish ownership before Open/Apply; failed queries or partially writing setters\n  # are independently drained by the original outer finally.\n  $script:qaNamespaceScope.Open()\n  $script:qaNamespaceScope.Apply()\n  Write-Host ('FIXTURE: isolated owned QA namespace bootstrap before exact original8; mask15 is measured sufficient, minimum unqualified; ' +\n      ($script:qaNamespaceScope.facts | ConvertTo-Json -Depth 6 -Compress))\n}\n\nfunction Restore-StandardValidQaNamespace {\n  if (-not $script:qaNamespaceScope) { $script:qaNamespaceCleanupOkay=$true;return }\n  $restoreFailure=$null;$disposeFailure=$null\n  try { $script:qaNamespaceScope.Restore() } catch { $restoreFailure=$_ }\n  finally {\n    try { $script:qaNamespaceScope.Dispose() } catch { $disposeFailure=$_ }\n  }\n  $facts=$script:qaNamespaceScope.facts\n  $script:qaNamespaceCleanupOkay=($null -eq $restoreFailure -and $null -eq $disposeFailure -and\n      $facts.cleanupOkay -and (-not $facts.writeAttempted -or $facts.restorationVerified))\n  Write-Host ('OBSERVED: isolated owned QA namespace bootstrap cleanup; ' +\n      ([ordered]@{directory=$facts;restoreFailed=($null -ne $restoreFailure);disposeFailed=($null -ne $disposeFailure);\n        accountCleanupPermitted=$script:qaNamespaceCleanupOkay;standardEightQualified=$false;\n        minimumMaskQualified=$false;actualWin32RouteMeasured=$false;productPermissionChanged=$false} |\n        ConvertTo-Json -Depth 8 -Compress))\n  if (-not $script:qaNamespaceCleanupOkay) {\n", "      $runnerFacts.sessionId -ne $freshFacts.sessionId -or\n      $runnerFacts.sessionId -ne (Get-Process -Id $PID).SessionId) {\n    throw 'Exact disposable owned QA identity and High/Medium same-session source facts required.'\n  }\n  # Register only. The original script observes A before its sole mask12 grant.\n  $script:qaNamespaceRegistered=$true\n  $script:qaNamespaceCleanupOkay=$false\n  Write-Host 'FIXTURE: optional identity validated before original A; original script owns the single mask12 grant, exact8 and restoration probe.'\n}\n\nfunction Restore-StandardValidQaNamespace {\n  if (-not $script:qaNamespaceRegistered) { $script:qaNamespaceCleanupOkay=$true;return }\n  # The original finally restores independently of child termination. Observe\n  # its managed result here; never create a second directory grant or handle.\n  $facts=$script:namespaceFacts\n  $factsKnown=($null -eq $facts -or ($facts.cleanupOkay -and (-not $facts.writeAttempted -or $facts.restorationVerified)))\n  $script:qaNamespaceCleanupOkay=([bool]$script:namespaceRestorationKnown -and $factsKnown)\n  Write-Host ('OBSERVED: isolated owned QA namespace bootstrap cleanup; ' +\n      ([ordered]@{directory=$script:namespaceFacts;namespaceRestorationKnown=$script:namespaceRestorationKnown;\n        accountCleanupPermitted=$script:qaNamespaceCleanupOkay;standardEightQualified=$false;\n        minimumMaskQualified=$false;actualWin32RouteMeasured=$false;productPermissionChanged=$false} |\n        ConvertTo-Json -Depth 8 -Compress))\n  if (-not $script:qaNamespaceCleanupOkay) {\n", 1), ('}\n\nfunction New-StandardValidQaScript([string]$Original,[string]$Prefix) {\n  $setupAnchor="`n  Invoke-OptionalCallerLinkedLeaseSubsystemObservations`n"\n  $drainAnchor="`n  Complete-LeaseNtPendingProtocols`n"\n  $deleteAnchor="`n  if (`$profileStarted -and `$ownedSid) {`n"\n  foreach ($anchor in @($setupAnchor,$drainAnchor,$deleteAnchor)) {\n    if ($Original.IndexOf($anchor) -lt 0 -or $Original.IndexOf($anchor) -ne $Original.LastIndexOf($anchor)) {\n      throw \'Exact original fixture setup/outer cleanup boundary changed.\'\n    }\n  }\n  $setup=$setupAnchor+"  Start-StandardValidQaNamespace`n"\n  $drain="`n"+@\'\n  try { Restore-StandardValidQaNamespace }\n  catch { $cleanup.Add(\'Owned QA namespace restoration/cleanup failed; no false success.\') }\n  try { Complete-LeaseNtPendingProtocols }\n  catch { $cleanup.Add(\'Exact pending NT protocol drain failed; other owned restoration remains independent.\') }\n\'@ + "`n"\n  $deletion="`n"+@\'\n  if (-not $script:qaNamespaceCleanupOkay) {\n    Write-Host \'FAIL: owned QA namespace remains unknown; actual account/profile/root retained. Original Standard failure remains fatal.\'\n    if ($failure) { throw $failure }\n    throw \'Owned QA namespace did not restore/close before account cleanup.\'\n  }\n\'@ + "`n" + $deleteAnchor\n  $modified=$Original.Replace($setupAnchor,$setup).Replace($drainAnchor,$drain).Replace($deleteAnchor,$deletion)\n  $state="`$script:qaNamespaceScope=`$null;`$script:qaNamespaceCleanupOkay=`$true`n"\n  return New-SessionDirectoryAbaScript $modified ($Prefix+"`n"+$state)\n}\n\nif ($env:GITHUB_ACTIONS -cne \'true\' -or $env:GITHUB_EVENT_NAME -cne \'workflow_dispatch\' -or\n', '}\n\nfunction New-StandardValidQaScript([string]$Original,[string]$Prefix) {\n  $setupAnchor="`n  Invoke-OptionalCallerLinkedLeaseSubsystemObservations`n"\n  $drainAnchor="`n  try { Complete-LeaseNtPendingProtocols } catch { `$cleanup.Add(\'Exact pending NT protocol drain failed.\') }`n"\n  $deleteAnchor="`n  if (`$ownedCleanupPermitted -and `$profileStarted -and `$ownedSid) {`n"\n  foreach ($anchor in @($setupAnchor,$drainAnchor,$deleteAnchor)) {\n    if ($Original.IndexOf($anchor) -lt 0 -or $Original.IndexOf($anchor) -ne $Original.LastIndexOf($anchor)) {\n      throw \'Exact original fixture setup/outer cleanup boundary changed.\'\n    }\n  }\n  $stopAnchor="  try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses(); `$ownedProcessesExited=`$true } catch { `$cleanup.Add(\'Exact owned standard lease process cleanup failed.\') }"\n  if ($Original.IndexOf($stopAnchor) -lt 0 -or $Original.IndexOf($stopAnchor) -ne $Original.LastIndexOf($stopAnchor)) { throw \'Exact original owned process cleanup boundary changed.\' }\n  $setup=$setupAnchor+"  Start-StandardValidQaNamespace`n"\n  $drain="`n"+@\'\n  try { Complete-LeaseNtPendingProtocols }\n  catch { $cleanup.Add(\'Exact pending NT protocol drain failed; other owned restoration remains independent.\') }\n\'@ + "`n"\n  $deletion="`n"+@\'\n  $script:qaProcessesKnownStopped=[bool]$ownedProcessesExited\n  try { Restore-StandardValidQaNamespace }\n  catch { $cleanup.Add(\'Owned QA namespace restoration/cleanup failed; no false success.\') }\n  if (-not $script:qaNamespaceCleanupOkay -or -not $script:qaProcessesKnownStopped -or\n      $script:ntPendingProtocols.Count -gt 0 -or $script:ntPreservedRoots.Count -gt 0) {\n    $ownedCleanupPermitted=$false\n    $cleanup.Add(\'Owned QA namespace did not restore/close before account cleanup.\')\n    try { Write-Host \'FAIL: owned QA namespace remains unknown; actual account/profile/root retained. Original Standard failure remains fatal.\' } catch {}\n  }\n\'@ + "`n" + $deleteAnchor\n  $modified=$Original.Replace($setupAnchor,$setup).Replace($drainAnchor,$drain).Replace($deleteAnchor,$deletion)\n  $state="`$script:qaNamespaceRegistered=`$false;`$script:qaNamespaceCleanupOkay=`$true;`$script:qaProcessesKnownStopped=`$false`n"\n  return New-SessionDirectoryAbaScript $modified ($Prefix+"`n"+$state)\n}\n\nif ($env:GITHUB_ACTIONS -cne \'true\' -or $env:GITHUB_EVENT_NAME -cne \'workflow_dispatch\' -or\n', 1), ('  "support-invite-guard.rs": "4b48b7995b36cc9662958ef8753be70ef0b6cf35626b7264f0151bc553293f29",\n  "test-session-directory-access.cs": "c562cf11de6dc395d4a6aae6d887dbe7fb970603bde74c8e4d111ac26956eb76",\n  "test-standard-qa-session-directory.cs": "310b613adc779fed71c2d2bc689041b6cfd07750dd7b9dff5f786c9c446c51a8",\n  "test-support-directory-security.cs": "3f70bcef70508b264a9559dbb9f7fc9bdbb0c3e28ea7929008dc084dec6d973e",\n  "test-support-launch-windows.ps1": "012ad7b7a2e52ddc7bd1c2c7aae63b7726187dab2d6aa2291bbb39de82928dec",\n  "test-support-lease-standard-windows.ps1": "a9b0bf8ee68209f78a82ca4260c9d35faf10449a25f5976775731e98042a057f",\n  "test-support-lease.py": "d11b25c95232544eb64f5de78f0e31cbc54630dd832185c55edfb4a3157cd604",\n  "test-support-nt-event-binding.cs": "4f166e8e0fbdce7a5fb6786862e031c5bb175704e7490a0e4a9feaac9fac62ca",\n  "test-support-nt-event-child.rs": "80cf1b1969b66568910d3a84ba46d4749db1c5744aac21b69777cd795dc0b034",\n  "test-windows-lease-observation.py": "bc64f1bd16d32d201de7654b04f26074428a742f896d18977a6012408329a75e"\n}\n\'@\nforeach ($pin in $pins.PSObject.Properties) {\n  $path=Join-Path $PSScriptRoot $pin.Name\n', '  "support-invite-guard.rs": "4b48b7995b36cc9662958ef8753be70ef0b6cf35626b7264f0151bc553293f29",\n  "test-session-directory-access.cs": "c562cf11de6dc395d4a6aae6d887dbe7fb970603bde74c8e4d111ac26956eb76",\n  "test-standard-qa-session-directory.cs": "310b613adc779fed71c2d2bc689041b6cfd07750dd7b9dff5f786c9c446c51a8",\n  "test-support-directory-security.cs": "3f70bcef70508b264a9559dbb9f7fc9bdbb0c3e28ea7929008dc084dec6d973e",\n  "test-support-launch-windows.ps1": "c1f3e99ebda0a146bce9b54f215feeebd0c6d19bec19f2a280259d311983c44b",\n  "test-support-lease-standard-windows.ps1": "c2897356a81f5410789fcebab4e845639a329da892b41cff4675aa08bc17f15e",\n  "test-support-lease.py": "bdba6cc24c0b247de85a3bcda79744dd932f00bb6e97d038eba14a7e43d1db64",\n  "test-support-nt-event-binding.cs": "4f166e8e0fbdce7a5fb6786862e031c5bb175704e7490a0e4a9feaac9fac62ca",\n  "test-support-nt-event-child.rs": "80cf1b1969b66568910d3a84ba46d4749db1c5744aac21b69777cd795dc0b034",\n  "test-windows-lease-observation.py": "2de9b36dc99e76523572f8db19fdeba0b2747769a87b476a10819470690f23f9",\n  "rust_toolchain.py": "2156c1452df874462a5e2a569f25a800a339cc2cb92f5732340aa12a633afa3c",\n  "windows-standard-namespace-readonly.rs": "23e5fee444459cb68ed7c2bb2ac6c8696583697ce8874019101b16659334b705",\n  "windows-standard-namespace-access.cs": "7f04727ae8161b8e4da1d2e4a74a0324549996da5af52a08c14538ccf53b2589",\n  "windows-standard-namespace-diagnostics.cs": "980178cba4736f260d1365d3f19b3371158efcd0856d75fb12c036b0a093aad8"\n}\n\'@\nforeach ($pin in $pins.PSObject.Properties) {\n  $path=Join-Path $PSScriptRoot $pin.Name\n', 1)]
HISTORICAL_WRAPPER_SHA = 'c346e831055a505344b62245009869ad8539d95871af0c2b8842005c311d899a'

def linux_close_preflight_projections():
    dart = 'python3 scripts/test-support-linux-window-close.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --dart "$(command -v dart)"\n'
    native = 'python3 scripts/test-support-linux-child-window-close.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --output "$RUNNER_TEMP/mixel-linux-child-close"\n'
    return (
        (
            '          python3 scripts/test-support-macos-cleanup.py --dart "$(command -v dart)" --flutter "$(command -v flutter)"\n',
            '          ' + dart + '          ' + native,
            '          python3 scripts/test-support-wakelock.py\n',
        ),
        (
            '      - name: Prove awaited Mac page and terminal cleanup with real Flutter widgets\n'
            '        run: python3 scripts/test-support-macos-cleanup.py --dart "$(command -v dart)" --flutter "$(command -v flutter)"\n',
            '      - name: Prove Linux terminal-close futures and conserved nonfinal window paths\n'
            '        run: ' + dart +
            '      - name: Prove Linux child close ownership and mouse-hook cleanup\n'
            '        run: ' + native,
            '      - name: Prove bounded main-engine Quit and owned resource cleanup\n'
            '        run: python3 scripts/test-support-macos-termination.py --flutter "$(command -v flutter)" --framework "$MIXEL_FLUTTER_FRAMEWORK"\n',
        ),
    )


def inverse_linux_close_preflight_checks(text):
    """Project only the approved, exactly adjacent Linux close regression pairs."""
    for before, addition, after in linux_close_preflight_projections():
        current = before + addition + after
        if text.count(addition) != 1 or text.count(current) != 1:
            raise AssertionError('Exact ordered Linux close preflight checks changed')
        text = text.replace(current, before + after, 1)
    return text


def uri_workflow_projections(workflow):
    """Only the reviewed URI checks beside their unchanged Linux regression gates."""
    if workflow not in ('build.yml', 'preflight.yml'):
        raise AssertionError('Unknown URI workflow projection')
    source = 'rustdesk' if workflow == 'build.yml' else '"$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"'
    uri = 'python3 scripts/test-support-uri-routing.py --source ' + source + ' --dart "$(command -v dart)" --output "$RUNNER_TEMP/mixel-uri-routing"\n'
    linux = 'python3 scripts/test-support-linux-window-close.py --source ' + source + ' --dart "$(command -v dart)"\n'
    projections = [(
        '          python3 scripts/test-support-macos-cleanup.py --dart "$(command -v dart)" --flutter "$(command -v flutter)"\n',
        '          ' + uri,
        '          ' + linux,
        2 if workflow == 'build.yml' else 1,
    )]
    if workflow == 'preflight.yml':
        projections.append((
            '      - name: Prove awaited Mac page and terminal cleanup with real Flutter widgets\n'
            '        run: python3 scripts/test-support-macos-cleanup.py --dart "$(command -v dart)" --flutter "$(command -v flutter)"\n',
            '      - name: Prove terminal support URI routing without ID fallthrough\n'
            '        run: ' + uri,
            '      - name: Prove Linux terminal-close futures and conserved nonfinal window paths\n'
            '        run: ' + linux,
            1,
        ))
    return projections


WINDOWS_SDK_SOURCE_DIAGNOSTICS_UPLOAD = '      - name: Preserve Windows SDK source diagnostics\n        if: always()\n        uses: actions/upload-artifact@v6\n        with:\n          name: windows-sdk-source-diagnostics\n          path: |\n            ${{ runner.temp }}/mixel-sdk-source/*.log\n            ${{ runner.temp }}/mixel-sdk-source/*.timing.json\n          if-no-files-found: ignore\n          retention-days: 14\n'
WINDOWS_SDK_SOURCE_DIAGNOSTICS_CONTEXTS = {'build.yml': ('windows', '      - name: Preflight generated support source and reporter\n        id: preflight\n        continue-on-error: ${{ needs.config.outputs.collect_windows_preflight_failure == \'true\' }}\n        shell: bash\n        env:\n          MIXEL_HOSTED_OWNED_QA_NAMESPACE: "1"\n          VCPKG_ROOT: ${{ github.workspace }}/vcpkg\n          RDREPO: ./rustdesk\n          MIXEL_SDK_FULL_APP_PREFLIGHT: "true"\n        run: bash scripts/test-windows-generated-source.sh\n', '\n      - name: Build (cargo + flutter via build.py)\n'), 'preflight.yml': ('windows-packaging', '      - name: Exercise complete Windows generated source, keep-awake and native branding\n        shell: bash\n        env:\n          MIXEL_HOSTED_OWNED_QA_NAMESPACE: "1"\n          RDREPO: ./rustdesk\n          AOM_INCLUDE_DIR: ${{ github.workspace }}/native-aom-headers\n        run: bash scripts/test-windows-generated-source.sh\n', '      - uses: actions/upload-artifact@v6\n')}

def inverse_windows_sdk_source_diagnostics(text, workflow):
    if workflow not in WINDOWS_SDK_SOURCE_DIAGNOSTICS_CONTEXTS:
        raise AssertionError('Unknown Windows SDK diagnostics workflow')
    job, before, after = WINDOWS_SDK_SOURCE_DIAGNOSTICS_CONTEXTS[workflow]
    marker = '\n  ' + job + ':\n'
    if text.count(marker) != 1 or text.count(WINDOWS_SDK_SOURCE_DIAGNOSTICS_UPLOAD) != 1:
        raise AssertionError('Changed, missing or duplicate Windows SDK diagnostics upload')
    start = text.index(marker)
    tail = text[start + len(marker):]
    match = re.search(r'\n  [A-Za-z][A-Za-z0-9_-]*:\n', tail)
    segment = text[start:] if match is None else text[start:start + len(marker) + match.start()]
    exact = before + WINDOWS_SDK_SOURCE_DIAGNOSTICS_UPLOAD + after
    if segment.count(exact) != 1:
        raise AssertionError('Changed or moved Windows SDK diagnostics upload context')
    return text.replace(exact, before + after, 1)

def inverse_uri_workflow_checks(text, workflow):
    if workflow == 'preflight.yml':
        text = inverse_uri_preflight_budget(text)
    for before, addition, after, count in uri_workflow_projections(workflow):
        current = before + addition + after
        if text.count(addition) != count or text.count(current) != count:
            raise AssertionError('Exact URI workflow check count/order/placement changed: ' + workflow)
        text = text.replace(current, before + after, count)
    return text


def inverse_early_windows_lease_gate(text):
    """Unit-only inverse of the exact early private fixture gate and upload."""
    before = "      - uses: dtolnay/rust-toolchain@v1\n        with:\n          toolchain: '1.75'\n"
    addition = "      - name: Prove early Windows acknowledgment publication\n        timeout-minutes: 3\n        shell: pwsh\n        run: |\n          ./scripts/activate-windows-msvc.ps1 -ExportToGitHubEnvironment\n          python scripts/test-support-lease-ack.py\n          if ($LASTEXITCODE -ne 0) { throw 'Actual Windows acknowledgment controls failed.' }\n      - name: Prove early actual standard-token lease lifecycle\n        timeout-minutes: 5\n        shell: pwsh\n        run: |\n          $env:MIXEL_HOSTED_OWNED_QA_NAMESPACE='1'\n          ./scripts/invoke-hosted-standard-qa-windows.ps1 -EvidenceDirectory (Join-Path $env:RUNNER_TEMP 'mixel-early-standard-qa')\n      - name: Preserve early Windows standard-token lifecycle evidence\n        if: always()\n        uses: actions/upload-artifact@v6\n        with:\n          name: windows-lease-ack-early\n          path: ${{ runner.temp }}/mixel-early-standard-qa/\n          if-no-files-found: ignore\n          retention-days: 14\n"
    after = '      - uses: subosito/flutter-action@v2\n'
    exact = before + addition + after
    if text.count(addition) != 1 or text.count(exact) != 1:
        raise AssertionError('Exact early Windows lease gate changed, moved or duplicated')
    return text.replace(exact, before + after, 1)


def windows_preflight_budget_projection():
    before = "  windows-packaging:\n    runs-on: ${{ github.event_name == 'workflow_dispatch' && inputs.windows_source_runner == 'windows-2025' && 'windows-2025' || 'windows-2022' }}\n"
    current = '    timeout-minutes: 240\n'
    after = "    env:\n      PYTHONUTF8: '1'\n    steps:\n      - uses: actions/checkout@v5\n"
    return before, current, after


def inverse_windows_preflight_budget(text):
    before, current, after = windows_preflight_budget_projection()
    approved = before + current + after
    if text.count(approved) != 1 or text.count(before) != 1 or text.count(current) != 1:
        raise AssertionError('Exact Windows preflight 240-minute source-job budget context changed')
    return text.replace(approved, before + '    timeout-minutes: 120\n' + after, 1)


def uri_preflight_budget_projection():
    before = '  upstream-source:\n    runs-on: ubuntu-22.04\n'
    current = '    timeout-minutes: 45\n'
    after = '    steps:\n      - uses: actions/checkout@v5\n'
    return before, current, after


def uri_mac_preflight_budget_projection():
    before = '  macos-runtime-harness:\n    runs-on: macos-14\n'
    current = '    timeout-minutes: 90\n'
    after = '    steps:\n      - uses: actions/checkout@v5\n'
    return before, current, after


def inverse_uri_preflight_budget(text):
    for projection, historical in ((uri_preflight_budget_projection(), '20'),
                                   (uri_mac_preflight_budget_projection(), '60')):
        before, current, after = projection
        approved = before + current + after
        if text.count(approved) != 1 or text.count(before) != 1:
            raise AssertionError('Exact upstream/Mac URI source-job budget context changed')
        text = text.replace(approved, before + '    timeout-minutes: ' + historical + '\n' + after, 1)
    return text


def invoke(body, csharp=None):
    with tempfile.TemporaryDirectory(prefix='mixel-valid-qa-source-') as folder:
        root = Path(folder)
        prefix = "$ErrorActionPreference='Stop'\n"
        if '__HISTORICAL_DRIVER__' in body or '__HISTORICAL_STANDARD__' in body:
            driver = DRIVER.read_text()
            for original, added, count in COMPOSED_WRAPPER_PROJECTION:
                if driver.count(added) != count:
                    raise AssertionError('Exact optional wrapper inverse changed')
                driver = driver.replace(added, original)
            if hashlib.sha256(driver.encode()).hexdigest() != HISTORICAL_WRAPPER_SHA:
                raise AssertionError('Historical optional wrapper was not exactly reconstructed')
            historical_driver = root / 'historical-driver.ps1'
            historical_driver.write_text(driver, encoding='utf-8')
            module = importlib.util.spec_from_file_location('directory_composition', SCRIPTS / 'test-support-directory-security.py')
            composition = importlib.util.module_from_spec(module)
            module.loader.exec_module(composition)
            standard = ORIGINAL.read_text(encoding='utf-8')
            if hashlib.sha256(standard.encode()).hexdigest() != composition.COMPOSED_STANDARD_SHA:
                raise AssertionError('Exact current mask12 source changed')
            for original, added, count in composition.COMPOSED_STANDARD_PROJECTION:
                if standard.count(added) != count:
                    raise AssertionError('Exact standard composition inverse changed')
                standard = standard.replace(added, original)
            if hashlib.sha256(standard.encode()).hexdigest() != composition.OWNER_STANDARD_SHA:
                raise AssertionError('Historical original8 standard was not reconstructed')
            historical_standard = root / 'historical-standard.ps1'
            historical_standard.write_text(standard, encoding='utf-8')
            body = body.replace('__HISTORICAL_DRIVER__', str(historical_driver).replace("'", "''")).replace('__HISTORICAL_STANDARD__', str(historical_standard).replace("'", "''"))
        if csharp:
            (root / 'helper.cs').write_text(csharp, encoding='utf-8')
            prefix += "Add-Type -Path (Join-Path $PSScriptRoot 'helper.cs') -CompilerOptions /warnaserror+ -ErrorAction Stop\n"
        (root / 'run.ps1').write_text(prefix + body, encoding='utf-8-sig')
        result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-NonInteractive', '-File', str(root / 'run.ps1')],
                                capture_output=True, text=True, encoding='utf-8', timeout=60)
        if result.returncode:
            raise AssertionError(f'Actual source control exit{result.returncode}\n{result.stdout}\n{result.stderr}')
        return result.stdout


FUNCTIONS = r'''
$t=$null;$e=$null;$tree=[Management.Automation.Language.Parser]::ParseFile('__DRIVER__',[ref]$t,[ref]$e)
if($e.Count){throw 'Actual driver parse error'}
$functions=@($tree.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst]})
if($functions.Count -ne 7){throw 'Exact seven function family changed'}
foreach($fn in $functions){
 $text=$fn.Extent.Text.Replace('[Security.Principal.SecurityIdentifier]::new($ownedSid)','[AbaMock+Sid]::new($ownedSid)').Replace('[MixelSessionDirectoryFixture+NativeApi]::new()','[AbaMock+Platform]::new()')
 . ([scriptblock]::Create($text))
}
'''.replace('__DRIVER__', str(DRIVER).replace("'", "''"))


HISTORICAL_FUNCTIONS = FUNCTIONS.replace(str(DRIVER).replace("'", "''"), '__HISTORICAL_DRIVER__')

class IgnoredFixtureControls(unittest.TestCase):
    def test_known_fifteen_only_class_rename_and_native_fence(self):
        raw = KNOWN.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), '310b613adc779fed71c2d2bc689041b6cfd07750dd7b9dff5f786c9c446c51a8')
        old = raw.replace(b'MixelSessionDirectoryFixture', b'MixelSessionDirectoryAba')
        self.assertEqual(hashlib.sha256(old).hexdigest(), 'bf291d27fcdb4aec3cae1b19b29f70491c74159dafba5e49642746cbf6233379')
        text = raw.decode()
        for value in ('addedMask=15', 'Put(value,end+4,15);', 'AddAccessAllowedAceEx(target,revision,0,15,sid)',
                      '"workflow_dispatch"', '"session-directory-access-observation"', '"MIXEL_SESSION_DIRECTORY_ABA"'):
            self.assertIn(value, text)
        # Compile both distinct real classes, complete native declarations included.
        body = "Add-Type -Path '" + str(SCRIPTS / 'test-session-directory-access.cs') + "' -CompilerOptions /warnaserror+ -ErrorAction Stop\n"
        body += "Add-Type -Path '" + str(KNOWN) + "' -CompilerOptions /warnaserror+ -ErrorAction Stop\n"
        body += "'PASS: complete real mask8 and known15 classes compile warning-fatal; no native constructor called.'\n"
        print(invoke(body).strip())

    def test_default_refusal_precedes_files_and_native(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'absent'
            env = dict(os.environ, GITHUB_ACTIONS='false')
            result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-File', str(DRIVER), '-EvidenceDirectory', str(destination)],
                                    capture_output=True, text=True, encoding='utf-8', env=env, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Explicit optional disposable hosted Windows CI scope required', result.stderr)
            self.assertFalse(destination.exists())

    def test_source_composition_original_eight_functions_and_cleanup_order(self):
        body = FUNCTIONS + r'''
$original=[IO.File]::ReadAllText('__ORIGINAL__')
$generated=New-StandardValidQaScript $original 'PREFIX'
$a=$original.IndexOf("  `$absent = 'ownerFailed=false;", $original.IndexOf("`n  Invoke-OptionalCallerLinkedLeaseSubsystemObservations`n"))
$b=$original.IndexOf('  if ($currentCallerFailure)', $a)
$eight=$original.Substring($a,$b-$a)
if($generated.IndexOf($eight) -lt 0 -or $generated.IndexOf($eight) -ne $generated.LastIndexOf($eight)){throw 'Original whole eight block changed'}
if($generated.IndexOf('  Invoke-SessionDirectoryAba') -gt $generated.IndexOf('  Start-StandardValidQaNamespace')){throw 'Unmodified A/B/A must precede later full8 setup'}
if($generated.IndexOf('  Start-StandardValidQaNamespace') -lt $generated.IndexOf('  Invoke-OptionalCallerLinkedLeaseSubsystemObservations')){throw 'Full8 bootstrap started before original supplemental controls'}
if($generated.IndexOf('  try { Restore-StandardValidQaNamespace }') -lt $generated.IndexOf('  try { Complete-LeaseNtPendingProtocols }')){throw 'Original independent protocol drain must precede final managed restoration observation'}
if($generated.IndexOf('  if (-not $script:qaNamespaceCleanupOkay)') -gt $generated.IndexOf('  if ($ownedCleanupPermitted -and $profileStarted -and $ownedSid)')){throw 'Unknown environment reached account cleanup'}
foreach($bad in @($original.Replace("`n  try { Complete-LeaseNtPendingProtocols } catch { `$cleanup.Add('Exact pending NT protocol drain failed.') }`n",''),($original+$original))){
 $refused=$false;try{New-StandardValidQaScript $bad 'PREFIX'|Out-Null}catch{$refused=$true}
 if(-not $refused){throw 'Missing/duplicate strict original anchor accepted'}
}
'PASS: literal entire original8/restored-A conserved; separate diagnostic A/B/A before registration; original mask12 restoration before managed cleanup/account deletion; strict anchors refuse.'
'''.replace('__ORIGINAL__', str(ORIGINAL).replace("'", "''"))
        print(invoke(body).strip())
        old = (SCRIPTS / 'observe-session-directory-aba-windows.ps1').read_text()
        new = DRIVER.read_text()
        # Four original orchestration functions are byte-for-byte present.
        for first, last in [('function Invoke-SessionDirectoryAba {', '\nfunction Get-SessionDirectoryAbaFailure'),
                            ('function Get-SessionDirectoryAbaFailure(', '\nfunction Invoke-SessionDirectoryAbaPhase'),
                            ('function Invoke-SessionDirectoryAbaPhase(', '\nfunction New-SessionDirectoryAbaScript'),
                            ('function New-SessionDirectoryAbaScript(', "\nif ($env:GITHUB_ACTIONS")]:
            fragment = old[old.index(first):old.index(last, old.index(first))]
            self.assertIn(fragment, new)
        pins = json.loads(re.search(r"\$pins=ConvertFrom-Json @'\n(.*?)\n'@", new, re.S).group(1))
        for name, digest in pins.items():
            self.assertEqual(hashlib.sha256(PREVIOUS.inverse_current_ack_source(name, (SCRIPTS / name).read_bytes())).hexdigest(), digest)

    def test_complete_generated_script_parses_before_any_execution(self):
        body = FUNCTIONS + r'''
$original=[IO.File]::ReadAllText('__ORIGINAL__')
$generated=New-StandardValidQaScript $original (($functions | ForEach-Object {$_.Extent.Text}) -join "`n")
$path=Join-Path $PSScriptRoot 'generated.ps1'
[IO.File]::WriteAllText($path,$generated,[Text.UTF8Encoding]::new($true))
$t=$null;$e=$null;$tree=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$t,[ref]$e)
if($e.Count -or -not $tree){throw 'Complete composed Standard script parser refused'}
if($generated.IndexOf('Start-StandardValidQaNamespace') -lt 0 -or $generated.IndexOf('Restore-StandardValidQaNamespace') -lt 0){throw 'Actual setup/restore omitted'}
'PASS: complete generated script plus all original functions parse before execution; no native or child launch.'
'''.replace('__ORIGINAL__', str(ORIGINAL).replace("'", "''"))
        print(invoke(body).strip())

    def test_current_eight_projection_and_exact_optional_workflow(self):
        # Reconstruct the complete historical wrapper through exact composition
        # inverses before applying its separately pinned original controls.
        driver = DRIVER.read_bytes()
        for original, added, count in COMPOSED_WRAPPER_PROJECTION:
            self.assertEqual(driver.count(added.encode()), count)
            driver = driver.replace(added.encode(), original.encode())
        self.assertEqual(hashlib.sha256(driver).hexdigest(), HISTORICAL_WRAPPER_SHA)
        # Restore only exact final-carrier pins before the historical whole-file proof.
        for actual, original in [(b'012ad7b7a2e52ddc7bd1c2c7aae63b7726187dab2d6aa2291bbb39de82928dec', b'765b98c6324161018321eadb3528a14a844dc4dd1bee1df158c9f68db6c8af8e'), (b'd11b25c95232544eb64f5de78f0e31cbc54630dd832185c55edfb4a3157cd604', b'f1051afc3860751cfea233216fa65cd1f7e29b639200f648c203dafc294530a9'), (b'bc64f1bd16d32d201de7654b04f26074428a742f896d18977a6012408329a75e', b'6f1ea99ec821be91fe88c13d8b62743346265e145b694dc4f05a60e94bcb121e')]:
            self.assertEqual(driver.count(actual), 1)
            driver = driver.replace(actual, original, 1)
        current = b'c562cf11de6dc395d4a6aae6d887dbe7fb970603bde74c8e4d111ac26956eb76'
        previous = b'65fc503abd02da1fc50f0e2abe73a747fd21468512298376cea73062422b0a64'
        self.assertEqual(driver.count(current), 1)
        self.assertEqual(hashlib.sha256(driver.replace(current, previous)).hexdigest(),
                         'e0b93f5d3b519877650e5cca61c37102c5f8897510d76fecfe8fd29867692126')
        self.assertEqual(hashlib.sha256((SCRIPTS / 'test-session-directory-access.cs').read_bytes()).hexdigest(), current.decode())
        workflow = (ROOT / '.github/workflows/preflight.yml').read_text()
        workflow = inverse_lifecycle_workflow_checks(workflow, 'preflight.yml')
        workflow = inverse_early_windows_lease_gate(workflow)
        workflow = inverse_windows_preflight_budget(workflow)
        workflow = inverse_windows_sdk_source_diagnostics(workflow, 'preflight.yml')
        workflow = inverse_uri_workflow_checks(workflow, 'preflight.yml')
        workflow = inverse_linux_close_preflight_checks(workflow)
        # Remove only the exact new mandatory compiler source controls before
        # reconstructing the unchanged whole historical workflow below.
        compiler_control = '          python3 scripts/test-support-compiler-cleanup.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --source-only --output "$RUNNER_TEMP/mixel-compiler-cleanup-source"\n'
        self.assertEqual(workflow.count(compiler_control), 2)
        workflow = workflow.replace(compiler_control, '', 2)
        # Reverse only the exact hosted Windows source-job budget before its historical pin.
        current_budget = "  windows-packaging:\n    runs-on: ${{ github.event_name == 'workflow_dispatch' && inputs.windows_source_runner == 'windows-2025' && 'windows-2025' || 'windows-2022' }}\n    timeout-minutes: 120\n"
        historical_budget = current_budget.replace('    timeout-minutes: 120\n', '    timeout-minutes: 20\n', 1)
        self.assertEqual(workflow.count(current_budget), 1)
        workflow = workflow.replace(current_budget, historical_budget, 1)
        # Reverse only the exact Mac source-job budget before its historical pin.
        mac_budget = "  macos-runtime-harness:\n    runs-on: macos-14\n    timeout-minutes: 60\n"
        previous_mac_budget = mac_budget.replace('    timeout-minutes: 60\n', '    timeout-minutes: 30\n', 1)
        self.assertEqual(workflow.count(mac_budget), 1)
        workflow = workflow.replace(mac_budget, previous_mac_budget, 1)
        for original, added, count in COMPOSED_WORKFLOW_PROJECTION:
            self.assertEqual(workflow.count(added), count)
            workflow = workflow.replace(added, original)
        flag = '          MIXEL_HOSTED_OWNED_QA_NAMESPACE: "1"\n'
        self.assertEqual(workflow.count(flag), 1)
        workflow = workflow.replace(flag, '', 1)
        suffix = "          python scripts/test-standard-valid-qa.py\n          if ($LASTEXITCODE -ne 0) { throw 'Valid owned QA original eight source and cleanup controls failed.' }\n"
        self.assertEqual(workflow.count(suffix), 1)
        restored = workflow.replace(suffix, '').replace(
            '- name: Observe original World children then run exact eight in an independently restored owned QA fixture',
            '- name: Observe six original World children around an exactly restored temporary directory DACL').replace(
            'run: ./scripts/observe-standard-valid-qa-windows.ps1 -EvidenceDirectory session-directory-aba-proof',
            'run: ./scripts/observe-session-directory-aba-windows.ps1 -EvidenceDirectory session-directory-aba-proof')
        # Reverse only the exact approved control additions after the historical
        # optional-wrapper baseline; the original whole-workflow pin stays intact.
        later_controls = (
            '          python3 scripts/test-pinned-source-archives.py\n',
            '          targets: x86_64-unknown-linux-gnu,x86_64-pc-windows-msvc,aarch64-apple-darwin\n',
            '          python3 scripts/test-support-native-warnings.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --source-only --output "$RUNNER_TEMP/mixel-native-warning-source"\n          python3 scripts/test-macos-update.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --source-only\n          python3 scripts/test-support-file-transfer-window.py --app "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --output "$RUNNER_TEMP/mixel-file-transfer-source" --flutter "$(command -v flutter)"\n',
            '          python3 scripts/test-support-keyboard-default.py --source rustdesk --source-root "$PWD" --evidence-dir "$RUNNER_TEMP/mixel-keyboard-default-source" --linux-sysroot "$(rustc --print sysroot)"\n          python3 scripts/test-support-uinput-scroll.py --source rustdesk --evidence-dir "$RUNNER_TEMP/mixel-uinput-wheel-source" --linux-sysroot "$(rustc --print sysroot)"\n          python3 scripts/test-support-uinput-device.py --source rustdesk --wheel-helper scripts/patch-support-uinput-scroll.py --output "$RUNNER_TEMP/mixel-uinput-device-source" --rustc "$(command -v rustc)" --linux-sysroot "$(rustc --print sysroot)"\n          RUSTC_BIN="$(rustup which rustc)" python3 scripts/test-support-fingerprint.py --source rustdesk --evidence-dir "$RUNNER_TEMP/mixel-fingerprint-source"\n          RUSTC_BIN="$(rustup which rustc)" python3 scripts/test-support-fatal-crash.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --upstream-repo rustdesk --evidence-dir "$RUNNER_TEMP/mixel-fatal-crash-source" --linux-sysroot "$(rustc --print sysroot)"\n',
            '          python3 scripts/test-support-native-warnings.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --source-only --output "$RUNNER_TEMP/mixel-native-warning-source"\n          RUSTC_BIN="$(rustup which rustc)" python3 scripts/test-macos-update.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"\n      - name: Prove native fatal-crash handling with isolated owned children\n        run: RUSTC_BIN="$(rustup which rustc)" python3 scripts/test-support-fatal-crash.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --upstream-repo rustdesk --evidence-dir "$RUNNER_TEMP/mixel-fatal-crash-source"\n',
        )
        for control in later_controls:
            self.assertEqual(restored.count(control), 1)
            restored = restored.replace(control, '', 1)
        self.assertEqual(hashlib.sha256(restored.encode()).hexdigest(),
                         'e8c4e5fd3f7ee7126867122dca7e5e86b6e4e12eff80aec9f1307b3abd4654e4')
        job = workflow.split('\n  session-directory-access-observation:', 1)[1]
        self.assertLess(job.index('test-session-directory-access.py'), job.index('test-standard-valid-qa.py'))
        self.assertLess(job.index('test-standard-valid-qa.py'), job.index('observe-standard-valid-qa-windows.ps1'))
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.session_directory_access_observation", job)
        self.assertNotIn('continue-on-error', job)
        original_test = (SCRIPTS / 'test-session-directory-access.py').read_bytes()
        for original, added, count in COMPOSED_SESSION_TEST_PROJECTION:
            self.assertEqual(original_test.count(added.encode()), count)
            original_test = original_test.replace(added.encode(), original.encode())
        self.assertEqual(hashlib.sha256(original_test).hexdigest(), HISTORICAL_SESSION_TEST_SHA)
        original_test = original_test.replace(
            b"job.index('observe-standard-valid-qa-windows.ps1')", b"job.index('observe-session-directory-aba-windows.ps1')")
        projection = "        # Reverse only the exact final-carrier source-family pin rebindings.\n        for current, original in [('012ad7b7a2e52ddc7bd1c2c7aae63b7726187dab2d6aa2291bbb39de82928dec', '765b98c6324161018321eadb3528a14a844dc4dd1bee1df158c9f68db6c8af8e'), ('d11b25c95232544eb64f5de78f0e31cbc54630dd832185c55edfb4a3157cd604', 'f1051afc3860751cfea233216fa65cd1f7e29b639200f648c203dafc294530a9'), ('bc64f1bd16d32d201de7654b04f26074428a742f896d18977a6012408329a75e', '6f1ea99ec821be91fe88c13d8b62743346265e145b694dc4f05a60e94bcb121e')]:\n            self.assertEqual(wrapper.count(current), 1)\n            wrapper = wrapper.replace(current, original, 1)\n"
        self.assertEqual(original_test.count(projection.encode()), 1)
        original_test = original_test.replace(projection.encode(), b'', 1)
        self.assertEqual(hashlib.sha256(original_test).hexdigest(),
                         '9b185f245cd60ae1d053712e36b56a3399cbbfc3092edb172a22ab98da9ca1a5')

    def test_linux_close_preflight_inverse_refuses_changed_missing_duplicate_reordered_or_moved_checks(self):
        text = (ROOT / '.github/workflows/preflight.yml').read_text()
        text = inverse_uri_workflow_checks(text, 'preflight.yml')
        projected = inverse_linux_close_preflight_checks(text)
        self.assertNotIn('test-support-linux-window-close.py', projected)
        self.assertNotIn('test-support-linux-child-window-close.py', projected)
        for before, addition, after in linux_close_preflight_projections():
            lines = addition.splitlines(keepends=True)
            first = ''.join(lines[:len(lines) // 2])
            second = ''.join(lines[len(lines) // 2:])
            mutations = {
                'missing': '', 'missing-first': second, 'missing-second': first,
                'duplicate': addition + addition, 'duplicate-first': first + addition,
                'changed': addition.replace('--source ', '--source changed ', 1),
                'reordered': second + first,
            }
            for kind, changed in mutations.items():
                with self.subTest(addition=addition, mutation=kind), self.assertRaises(AssertionError):
                    inverse_linux_close_preflight_checks(text.replace(addition, changed, 1))
            for kind, changed in (
                    ('moved', text.replace(addition, '', 1) + addition),
                    ('preceding-gate', text.replace(before + addition + after, before.rstrip('\n') + ' # changed\n' + addition + after, 1)),
                    ('following-gate', text.replace(before + addition + after, before + addition + after.rstrip('\n') + ' # changed\n', 1))):
                with self.subTest(addition=addition, mutation=kind), self.assertRaises(AssertionError):
                    inverse_linux_close_preflight_checks(changed)

    def test_windows_preflight_budget_exact_inverse_refuses_drift_before_commands(self):
        path = ROOT / '.github/workflows/preflight.yml'
        raw = path.read_bytes()
        text = raw.decode()
        before, current, after = windows_preflight_budget_projection()
        approved = before + current + after
        forbidden = AssertionError('Budget source projection attempted a command')
        with mock.patch.object(subprocess, 'run', side_effect=forbidden), mock.patch.object(subprocess, 'Popen', side_effect=forbidden):
            projected = inverse_windows_preflight_budget(text)
            self.assertEqual(projected, text.replace(current, '    timeout-minutes: 120\n', 1))
            for changed in (
                    text.replace(approved, before + after, 1),
                    text.replace(current, current + current, 1),
                    text.replace(approved, approved + approved, 1),
                    text.replace(current, current.replace('240', '241'), 1),
                    text.replace(current, current.replace('240', '180'), 1),
                    text.replace(current, current.replace('240', '120'), 1),
                    text.replace(approved, before + after, 1) + current,
                    text.replace(approved, before + '# disjoint\n' + current + after, 1),
                    text.replace(before, before.replace('windows-packaging:', 'foreign-job:'), 1)):
                with self.assertRaises(AssertionError):
                    inverse_windows_preflight_budget(changed)
        self.assertEqual(path.read_bytes(), raw)

    def test_uri_workflow_projection_refuses_drift_missing_duplicate_or_disjoint_checks_before_commands(self):
        for workflow in ('preflight.yml',):
            path = ROOT / '.github/workflows' / workflow
            raw = path.read_bytes()
            text = raw.decode()
            with mock.patch.object(subprocess, 'run', side_effect=AssertionError('Source-only URI projection attempted a command')), mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('Source-only URI projection attempted a command')):
                projected = inverse_uri_workflow_checks(text, workflow)
                self.assertNotIn('scripts/test-support-uri-routing.py', projected)
                for before, addition, after, count in uri_workflow_projections(workflow):
                    for kind, changed in (
                            ('missing', text.replace(addition, '', 1)),
                            ('duplicate', text.replace(addition, addition + addition, 1)),
                            ('argument-drift', text.replace(addition, addition.replace('--source ', '--source foreign ', 1), 1)),
                            ('disjoint', text.replace(before + addition + after, before + '# disjoint\n' + addition + after, 1)),
                            ('reordered', text.replace(addition + after, after + addition, 1)),
                            ('moved', text.replace(addition, '', 1) + addition)):
                        with self.subTest(workflow=workflow, mutation=kind), self.assertRaises(AssertionError):
                            inverse_uri_workflow_checks(changed, workflow)
            if workflow == 'preflight.yml':
                for before, current, after in (uri_preflight_budget_projection(), uri_mac_preflight_budget_projection()):
                    for changed in (text.replace(before + current + after, before + after, 1),
                                    text.replace(before + current + after, before + current + current + after, 1),
                                    text.replace(before + current + after, before + current.replace(': ', ': 1', 1) + after, 1),
                                    text.replace(before + current + after, before + after, 1) + current,
                                    text.replace(before + current + after, before + '# disjoint\n' + current + after, 1)):
                        with self.assertRaises(AssertionError):
                            inverse_uri_workflow_checks(changed, workflow)
            self.assertEqual(path.read_bytes(), raw)
        with self.assertRaises(AssertionError):
            inverse_uri_workflow_checks('', 'foreign.yml')

    def test_actual_scope_partial_failures_and_unknown_cleanup(self):
        mock = PREVIOUS.MOCK.split('public static class MixelOrdinaryTokenFixture', 1)[0]
        mock = mock.replace('MixelSessionDirectoryAba', 'MixelSessionDirectoryFixture').replace('mask==8', 'mask==15').replace('ToUInt32(a.descriptor,96)==8', 'ToUInt32(a.descriptor,96)==15')
        body = HISTORICAL_FUNCTIONS + r'''
function Get-LocalUser {return [pscustomobject]@{Name=$ownedUser}}
function Get-Process {return [pscustomobject]@{SessionId=2}}
$ownedUser='mixellease0123456789';$ownedSid='owned-local-qa-sid'
$runnerFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=12288;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
$freshFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=8192;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
foreach($mode in @('normal','set-false','set-zero-false','set-throw','query-fails','open-denied','restore-false','restore-readback-drift','close-false','unload-throw')){
 [AbaMock+Platform]::Mode=$mode;$script:qaNamespaceScope=$null;$script:qaNamespaceCleanupOkay=$true
 $startFailure=$null;try{Start-StandardValidQaNamespace}catch{$startFailure=$_}
 $restoreFailure=$null;try{Restore-StandardValidQaNamespace}catch{$restoreFailure=$_}
 $unsafe=$mode -in @('restore-false','restore-readback-drift','close-false','unload-throw')
 if(($null -ne $restoreFailure) -ne $unsafe -or $script:qaNamespaceCleanupOkay -eq $unsafe){throw ('Cleanup attribution changed: '+$mode)}
 $platform=[AbaMock+Platform]::Last
 if($platform.unloads -ne 1){throw 'Independent module dispose skipped'}
 if($mode -in @('set-false','set-zero-false','set-throw') -and ($platform.sets -ne 2 -or -not $script:qaNamespaceScope.facts.restorationVerified)){throw 'Partial setter did not restore exact original'}
 if($unsafe -and $script:qaNamespaceScope.facts.cleanupOkay){throw 'Failed native cleanup falsely qualified'}
}
$script:qaNamespaceScope=$null;$freshFacts.integrityRid=12288
$refused=$false;try{Start-StandardValidQaNamespace}catch{$refused=$true}
if(-not $refused -or $script:qaNamespaceScope){throw 'Foreign/high QA identity mutated namespace'}
'PASS: literal setup/restore10 scenarios; failed partial writes restore, close/module independently attempted, unknown cleanup cannot delete account; source identity refusal before mutation.'
'''
        print(invoke(body, KNOWN.read_text() + mock).splitlines()[-1])

    def test_literal_original_eight_and_outer_finally_fault_order(self):
        mock = PREVIOUS.MOCK.split('public static class MixelOrdinaryTokenFixture', 1)[0]
        mock = mock.replace('MixelSessionDirectoryAba', 'MixelSessionDirectoryFixture').replace('mask==8', 'mask==15').replace('ToUInt32(a.descriptor,96)==8', 'ToUInt32(a.descriptor,96)==15')
        fixture = r'''
public static class MixelOrdinaryTokenFixture {
 public static bool stopFailure; public static int stops,profiles,handleCloses,desktopRestores;
 public static void StopOwnedStartedProcesses(){stops++;if(stopFailure)throw new Exception("Injected stop failure");}
 public static void CloseStartedObservations(){handleCloses++;}
 public static string StartedStatus(int pid){if(pid!=512)throw new Exception("Foreign owner PID");return "exited:0x00000001";}
 public static string DesktopAclHashes(){return "original-desktop";}
 public static void RemoveProfile(string sid){if(sid!="owned-local-qa-sid")throw new Exception("Foreign profile");profiles++;}
 public sealed class DesktopAccess {public void Dispose(){desktopRestores++;}}
}
public static class MixelIsolatedLeaseControl {
 public static int closed;
 public static IntPtr DeniedEvent(string name){return new IntPtr(77);}
 public static IntPtr WrongObject(string name){return new IntPtr(88);}
 public static void Close(IntPtr handle){if(handle.ToInt64()!=77&&handle.ToInt64()!=88)throw new Exception("Foreign event close");closed++;}
 public static void ClosePending(){}
}
'''
        body = HISTORICAL_FUNCTIONS + r'''
function Get-LocalUser {param($SID,$Name,$ErrorAction);if($Name -and $script:userRemoved){return $null};return [pscustomobject]@{Name=$ownedUser}}
function Get-Process {return [pscustomobject]@{SessionId=2}}
function Remove-LocalUser {param($SID);$script:userRemoved=$true}
function Complete-LeaseNtPendingProtocols {$script:drains++;if($script:drainFailure){throw 'Injected protocol drain failure'}}
function Start-Sleep {param($Seconds)}
function Invoke-StandardLeaseControl {
 param($Mode,$ExpectedStatus,[int]$ExpectedNativeError,$ExpectedAttestation,[bool]$ExpectedRequiresClick,[bool]$ExpectedOwnerFailure,[switch]$KeepAlive)
 $script:eightCalls.Add([pscustomobject]@{mode=$Mode;status=$ExpectedStatus;error=$ExpectedNativeError;attestation=$ExpectedAttestation;click=$ExpectedRequiresClick;failedOwner=$ExpectedOwnerFailure;kept=[bool]$KeepAlive})
 if($script:originalFailure -and $script:eightCalls.Count -eq 1){throw 'ORIGINAL_NATIVE5'}
 return 512
}
$original=[IO.File]::ReadAllText('__ORIGINAL__');$generated=New-StandardValidQaScript $original 'PREFIX'
$a=$original.IndexOf("  `$absent = 'ownerFailed=false;",$original.IndexOf("`n  Invoke-OptionalCallerLinkedLeaseSubsystemObservations`n"))
$b=$original.IndexOf('  if ($currentCallerFailure)',$a);$eight=$original.Substring($a,$b-$a)
$tailAnchor="} catch {`n  `$failure = `$_`n  throw`n} finally {`n  `$cleanup = [System.Collections.Generic.List[string]]::new()"
$tailAt=$generated.LastIndexOf($tailAnchor);if($tailAt -lt 0){throw 'Exact original outer finally absent'}
$tail=$generated.Substring($tailAt).Replace('[Security.Principal.SecurityIdentifier]::new($ownedSid)','[AbaMock+Sid]::new($ownedSid)')
$runnerFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=12288;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
$freshFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=8192;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
foreach($mode in @('normal','original-native5','stop-failure','protocol-failure','restore-false','original5-and-restore-false','close-false','unload-throw')){
 $ownedUser='mixellease0123456789';$ownedSid='owned-local-qa-sid';$profileStarted=$true;$eventHandle=[IntPtr]::Zero;$failure=$null
 $desktopAccess=[MixelOrdinaryTokenFixture+DesktopAccess]::new();$originalDesktop='original-desktop';$eventName='Global\Mixel-Remote-Standard-Lease-'+('d'*32)
 $script:ntPendingProtocols=[Collections.Generic.List[object]]::new();$script:ntPreservedRoots=[Collections.Generic.HashSet[string]]::new()
 $fixtureRoot=Join-Path $PSScriptRoot $mode;[void][IO.Directory]::CreateDirectory($fixtureRoot)
 $script:qaNamespaceScope=$null;$script:qaNamespaceCleanupOkay=$true;$script:abaDeferredFailure=$null;$script:userRemoved=$false;$script:drains=0
 $script:eightCalls=[Collections.Generic.List[object]]::new();$script:originalFailure=$mode -in @('original-native5','original5-and-restore-false')
 $script:drainFailure=$mode -eq 'protocol-failure';[MixelOrdinaryTokenFixture]::stopFailure=$mode -eq 'stop-failure'
 [MixelOrdinaryTokenFixture]::stops=0;[MixelOrdinaryTokenFixture]::profiles=0;[MixelOrdinaryTokenFixture]::desktopRestores=0
 [AbaMock+Platform]::Mode=$(if($mode -eq 'original5-and-restore-false'){'restore-false'}elseif($mode -in @('restore-false','close-false','unload-throw')){$mode}else{'normal'})
 $errorText=$null;try{& ([scriptblock]::Create("try {`n  Start-StandardValidQaNamespace`n"+$eight+$tail))}catch{$errorText=$_.Exception.Message}
 $unsafe=$mode -in @('restore-false','original5-and-restore-false','close-false','unload-throw')
 if($script:drains -ne 1 -or [MixelOrdinaryTokenFixture]::desktopRestores -ne 1){throw ('Independent pending drain/desktop restore skipped: '+$mode)}
 if($unsafe){
  if($script:userRemoved -or [MixelOrdinaryTokenFixture]::profiles -ne 0 -or -not [IO.Directory]::Exists($fixtureRoot)){throw 'Unknown namespace deleted owned account/profile/root'}
  if(-not $errorText){throw 'Unknown namespace silently passed'}
 }elseif(-not $script:userRemoved -or [MixelOrdinaryTokenFixture]::profiles -ne 1 -or [IO.Directory]::Exists($fixtureRoot)){throw ('Verified restoration did not preserve original cleanup: '+$mode)}
 if($script:originalFailure -and $errorText -cne 'ORIGINAL_NATIVE5'){throw 'Original native5 was replaced by cleanup failure'}
 if($mode -eq 'normal'){
  if($errorText -or $script:eightCalls.Count -ne 8 -or ($script:eightCalls.error -join ',') -cne '2,0,0,2,5,6,6,2'){throw 'Exact original8 assertion modes/codes changed'}
  if($script:eightCalls[4].attestation -cne 'guard-unavailable' -or -not $script:eightCalls[6].failedOwner -or -not $script:eightCalls[1].kept){throw 'Original negative/ownership semantics changed'}
 }
 if($mode -eq 'protocol-failure' -and -not $errorText){throw 'Deferred protocol failure lost fatal semantics'}
}
'PASS: literal original8 and full outer finally8 fault scenarios; codes2,0,0,2,5,6,6,2; restore/dispose before protocol drain; desktop independent; unknown account/profile/root preserved; original5 firstfatal.'
'''.replace('__ORIGINAL__', '__HISTORICAL_STANDARD__')
        print(invoke(body, KNOWN.read_text() + mock + fixture).splitlines()[-1])

    def test_current_optional_registration_never_applies_a_second_grant(self):
        driver = DRIVER.read_text()
        start = driver.index('function Start-StandardValidQaNamespace')
        end = driver.index('function New-StandardValidQaScript', start)
        source = driver[start:end]
        for forbidden in ('+Scope]::new', '+NativeApi]::new', '.Open()', '.Apply()', '.Restore()', '.Dispose()'):
            self.assertNotIn(forbidden, source)
        mock = PREVIOUS.MOCK.split('public static class MixelOrdinaryTokenFixture', 1)[0]
        mock = mock.replace('MixelSessionDirectoryAba', 'MixelSessionDirectoryFixture').replace('mask==8', 'mask==15').replace('ToUInt32(a.descriptor,96)==8', 'ToUInt32(a.descriptor,96)==15')
        fence = 'public static class HostedGuardMock { public static bool permitted=true; public static bool Allowed(bool windows,bool x64,string actions,string repository,string runner,string job,string eventName,string reference,string workflow,string toggle,string context){return permitted;} }'
        body = FUNCTIONS.replace(" . ([scriptblock]::Create($text))", " $text=$text.Replace('[MixelSessionDirectoryFixture]::HostedScopeAllowed(', '[HostedGuardMock]::Allowed(')\n . ([scriptblock]::Create($text))") + r'''
function Get-LocalUser {return [pscustomobject]@{Name=$(if($case -eq 'foreign-account'){'foreign'}else{$ownedUser})}}
function Get-Process {return [pscustomobject]@{SessionId=2}}
$ownedUser='mixellease0123456789';$ownedSid='owned-local-qa-sid'
foreach($case in @('normal','unknown-restore','stale-success-flag','high-child','foreign-session','restricted-child','foreign-account','duplicate-registration')){
 $runnerFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=12288;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
 $freshFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=8192;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
 $script:qaNamespaceRegistered=$case -eq 'duplicate-registration';$script:qaNamespaceCleanupOkay=$true
 $script:namespaceRestorationKnown=$case -ne 'unknown-restore'
 $expectedKnown=$script:namespaceRestorationKnown -and $case -ne 'stale-success-flag'
 $script:namespaceFacts=[pscustomobject]@{grantMask=12;writeAttempted=$true;restorationVerified=$expectedKnown;cleanupOkay=$expectedKnown}
 [HostedGuardMock]::permitted=$case -ne 'foreign-account';[AbaMock+Platform]::Last=$null
 if($case -eq 'high-child'){$freshFacts.integrityRid=12288}
 if($case -eq 'foreign-session'){$freshFacts.sessionId=3}
 if($case -eq 'restricted-child'){$freshFacts.hasRestrictions=$true}
 $startFailure=$null;try{Start-StandardValidQaNamespace}catch{$startFailure=$_}
 $valid=$case -in @('normal','unknown-restore','stale-success-flag')
 if(($null -eq $startFailure) -ne $valid){throw ('Registration identity/fence refusal changed: '+$case+'; '+$startFailure.Exception.Message)}
 if($valid){
  if(-not $script:qaNamespaceRegistered -or $script:qaNamespaceCleanupOkay){throw 'Registration lost uncertainty before actual mask12 lifecycle'}
  $restoreFailure=$null;try{Restore-StandardValidQaNamespace}catch{$restoreFailure=$_}
  if(($null -eq $restoreFailure) -ne $expectedKnown -or $script:qaNamespaceCleanupOkay -ne $expectedKnown){throw 'Managed restoration uncertainty or a stale success flag was concealed'}
 }
 if($null -ne [AbaMock+Platform]::Last){throw 'Registration opened a second native directory scope'}
}
'PASS: actual optional registration eight identity/fence/restoration cases; zero second grants and unknown restoration or stale success stays fatal.'
'''
        print(invoke(body, KNOWN.read_text() + mock + fence).splitlines()[-1])

    def test_current_optional_mask12_original_eight_and_restored_a_faults(self):
        mock = PREVIOUS.MOCK.split('public static class MixelOrdinaryTokenFixture', 1)[0]
        mock = mock.replace('MixelSessionDirectoryAba', 'MixelSessionDirectoryFixture').replace('mask==8', 'mask==15').replace('ToUInt32(a.descriptor,96)==8', 'ToUInt32(a.descriptor,96)==15')
        fixture = r'''
public static class HostedGuardMock {
 public static bool Allowed(bool windows,bool x64,string actions,string repository,string runner,string job,string eventName,string reference,string workflow,string toggle,string context){return true;}
}
public static class MixelOrdinaryTokenFixture {
 public static bool stopFailure,desktopFailure,hashFailure,hashDrift; public static int stops,profiles,handleCloses,desktopRestores,hashQueries;
 public static void StopOwnedStartedProcesses(){stops++;if(stopFailure)throw new Exception("Injected stop failure");}
 public static void CloseStartedObservations(){handleCloses++;}
 public static string StartedStatus(int pid){if(pid!=512)throw new Exception("Foreign owner PID");return "exited:0x00000001";}
 public static string DesktopAclHashes(){hashQueries++;if(hashFailure)throw new Exception("Injected desktop hash failure");return hashDrift?"foreign-desktop":"original-desktop";}
 public static void RemoveProfile(string sid){if(sid!="owned-local-qa-sid")throw new Exception("Foreign profile");profiles++;}
 public sealed class DesktopAccess {public void Dispose(){desktopRestores++;if(desktopFailure)throw new Exception("Injected desktop restore failure");}}
}
public static class MixelIsolatedLeaseControl {
 public static int closed;
 public static IntPtr DeniedEvent(string name){return new IntPtr(77);}
 public static IntPtr WrongObject(string name){return new IntPtr(88);}
 public static void Close(IntPtr handle){if(handle.ToInt64()!=77&&handle.ToInt64()!=88)throw new Exception("Foreign event close");closed++;}
 public static void ClosePending(){}
}
namespace MixelCI {
 public sealed class StandardNamespaceAccess : IDisposable {
  public sealed class Facts {public bool writeAttempted=true,restorationVerified,cleanupOkay;public uint grantMask=12;}
  public static string Mode="normal";public static int scopes,disposals;
  public readonly Facts facts=new Facts();readonly string mode;
  public StandardNamespaceAccess(string sid,string user,uint session){
   if(sid!="owned-local-qa-sid"||user!="mixellease0123456789"||session!=2)throw new Exception("Foreign mask12 namespace fixture");
   scopes++;mode=Mode;if(mode=="ctor-partial-write")throw new Exception("Injected partial-write fatal mask12 constructor");
  }
  public void Dispose(){
   disposals++;if(mode=="restore-false"||mode=="foreign-drift")throw new Exception("Injected mask12 restoration failure");
   facts.restorationVerified=true;if(mode=="close-false")throw new Exception("Injected mask12 handle close failure");
   facts.cleanupOkay=true;
  }
 }
}
'''
        functions = FUNCTIONS.replace(" . ([scriptblock]::Create($text))", " $text=$text.Replace('[MixelSessionDirectoryFixture]::HostedScopeAllowed(', '[HostedGuardMock]::Allowed(')\n . ([scriptblock]::Create($text))")
        body = functions + r'''
function Get-LocalUser {param($SID,$Name,$ErrorAction);if($Name -and $script:userRemoved){return $null};return [pscustomobject]@{Name=$ownedUser}}
function Get-Process {return [pscustomobject]@{SessionId=2}}
function Remove-LocalUser {param($SID);$script:userRemoved=$true}
function Complete-LeaseNtPendingProtocols {$script:drains++;if($script:drainFailure){throw 'Injected protocol drain failure'}}
function Start-Sleep {param($Seconds)}
function Write-Host {param($Object);if($script:outputFailure){throw 'INJECTED_OUTPUT_FAILURE'}}
function Invoke-StandardLeaseControl {
 param($Mode,$ExpectedStatus,[int]$ExpectedNativeError,$ExpectedAttestation,[bool]$ExpectedRequiresClick,[bool]$ExpectedOwnerFailure,[switch]$KeepAlive,[switch]$ObserveOnly,$ObservationLabel)
 if($ObserveOnly){
  $script:initialObservations++
  if([MixelCI.StandardNamespaceAccess]::scopes -ne 0){throw 'Initial A was observed after a namespace grant'}
  [IO.File]::WriteAllText($resultPath,($script:beforeRecord|ConvertTo-Json -Compress))
  return 512
 }
 $script:eightCalls.Add([pscustomobject]@{mode=$Mode;status=$ExpectedStatus;error=$ExpectedNativeError;attestation=$ExpectedAttestation;click=$ExpectedRequiresClick;failedOwner=$ExpectedOwnerFailure;kept=[bool]$KeepAlive})
 if($script:originalFailure -and $script:eightCalls.Count -eq 1){$script:outputFailure=$mode -in @('original5-and-output-failure','original5-and-quarantine-output-failure');throw 'ORIGINAL_NATIVE5'}
 if($script:eightCalls.Count -eq 9 -and -not $script:namespaceRestorationKnown){throw 'Restored A was asserted before exact restoration'}
 return 512
}
$original=[IO.File]::ReadAllText('__ORIGINAL__');$generated=New-StandardValidQaScript $original 'PREFIX'
$anchor="  [void](Invoke-StandardLeaseControl -Mode probe -ObserveOnly -ObservationLabel 'global-original-before-owned-grant')"
$a=$original.IndexOf($anchor);$b=$original.IndexOf('  if ($currentCallerFailure)',$a);if($a -lt 0 -or $b -le $a){throw 'Exact A/mask12/original8/restored-A terminal absent'}
$core=$original.Substring($a,$b-$a)
$tailAnchor="} catch {`n  `$failure = `$_`n  throw`n} finally {`n  `$cleanup = [System.Collections.Generic.List[string]]::new()"
$tailAt=$generated.LastIndexOf($tailAnchor);if($tailAt -lt 0){throw 'Exact original outer finally absent'}
$tail=$generated.Substring($tailAt).Replace('[Security.Principal.SecurityIdentifier]::new($ownedSid)','[AbaMock+Sid]::new($ownedSid)')
$runnerFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=12288;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
$freshFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=8192;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
foreach($mode in @('normal','original-native5','stop-failure','protocol-failure','restore-false','original5-and-restore-false','close-false','foreign-drift','nt-live','ctor-partial-write','desktop-restore-failure','desktop-hash-failure','desktop-hash-drift','original5-and-desktop-failure','original5-and-output-failure','original5-and-quarantine-output-failure')){
 $ownedUser='mixellease0123456789';$ownedSid='owned-local-qa-sid';$profileStarted=$true;$eventHandle=[IntPtr]::Zero;$failure=$null
 $desktopAccess=[MixelOrdinaryTokenFixture+DesktopAccess]::new();$originalDesktop='original-desktop';$eventName='Global\Mixel-Remote-Standard-Lease-'+('d'*32)
 $script:ntPendingProtocols=[Collections.Generic.List[object]]::new();$script:ntPreservedRoots=[Collections.Generic.HashSet[string]]::new()
 if($mode -eq 'nt-live'){$script:ntPendingProtocols.Add([pscustomobject]@{childExited=$false})}
 $fixtureRoot=Join-Path $PSScriptRoot $mode;[void][IO.Directory]::CreateDirectory($fixtureRoot);$resultPath=Join-Path $fixtureRoot 'fixture-result.json'
 $namespaceAccess=$null;$script:namespaceRestorationKnown=$true;$script:namespaceFacts=$null
 $script:qaNamespaceRegistered=$false;$script:qaNamespaceCleanupOkay=$true;$script:userRemoved=$false;$script:drains=0;$script:initialObservations=0
 $script:beforeRecord=[pscustomobject]@{pid=512;sourceStatus='ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false';rawOpenError=2;attestation='';requiresClick=$false;ownerFailed=$false}
 $script:eightCalls=[Collections.Generic.List[object]]::new();$script:originalFailure=$mode -in @('original-native5','original5-and-restore-false','original5-and-desktop-failure','original5-and-output-failure','original5-and-quarantine-output-failure')
 $script:drainFailure=$mode -eq 'protocol-failure';[MixelOrdinaryTokenFixture]::stopFailure=$mode -eq 'stop-failure'
 [MixelOrdinaryTokenFixture]::desktopFailure=$mode -in @('desktop-restore-failure','original5-and-desktop-failure','original5-and-output-failure','original5-and-quarantine-output-failure');[MixelOrdinaryTokenFixture]::hashFailure=$mode -eq 'desktop-hash-failure';[MixelOrdinaryTokenFixture]::hashDrift=$mode -eq 'desktop-hash-drift'
 [MixelOrdinaryTokenFixture]::stops=0;[MixelOrdinaryTokenFixture]::profiles=0;[MixelOrdinaryTokenFixture]::desktopRestores=0;[MixelOrdinaryTokenFixture]::hashQueries=0
 [MixelCI.StandardNamespaceAccess]::scopes=0;[MixelCI.StandardNamespaceAccess]::disposals=0
 [MixelCI.StandardNamespaceAccess]::Mode=$(if($mode -eq 'original5-and-restore-false'){'restore-false'}elseif($mode -in @('restore-false','close-false','foreign-drift','ctor-partial-write')){$mode}else{'normal'})
 $script:credentialsCleared=$false;$script:outputFailure=$false
 $credentialFence="`$ownedPassword='mock-secret';`$random='mock-secret';try {`ntry {`n  Start-StandardValidQaNamespace`n"+$core+$tail+"`n} finally { `$script:credentialsCleared=(`$null -eq `$ownedPassword -and `$null -eq `$random) }"
 $errorText=$null;try{& ([scriptblock]::Create($credentialFence))}catch{$errorText=$_.Exception.Message}
 $unsafe=$mode -eq 'original5-and-quarantine-output-failure' -or $mode -in @('stop-failure','restore-false','original5-and-restore-false','close-false','foreign-drift','nt-live','ctor-partial-write','desktop-restore-failure','desktop-hash-failure','desktop-hash-drift','original5-and-desktop-failure','original5-and-output-failure','original5-and-quarantine-output-failure')
 if($script:initialObservations -ne 1 -or [MixelCI.StandardNamespaceAccess]::scopes -ne 1){throw 'Initial A or the single mask12 scope was lost'}
 if($script:drains -ne 1 -or [MixelOrdinaryTokenFixture]::desktopRestores -ne 1 -or [MixelOrdinaryTokenFixture]::hashQueries -ne 1){throw ('Independent pending drain/desktop restore skipped: '+$mode)}
 if(-not $script:credentialsCleared){throw ('Original finally did not clear ephemeral credential variables: '+$mode)}
 if($unsafe){
  if($script:userRemoved -or [MixelOrdinaryTokenFixture]::profiles -ne 0 -or -not [IO.Directory]::Exists($fixtureRoot)){throw 'Unknown namespace/child ownership deleted owned account/profile/root'}
  if(-not $errorText){throw 'Unknown namespace/child ownership silently passed'}
 }elseif(-not $script:userRemoved -or [MixelOrdinaryTokenFixture]::profiles -ne 1 -or [IO.Directory]::Exists($fixtureRoot)){throw ('Verified restoration did not preserve original cleanup: '+$mode)}
 if($script:originalFailure -and $errorText -cne 'ORIGINAL_NATIVE5'){throw 'Original native5 was replaced by cleanup failure'}
 if($mode -eq 'normal'){
  if($errorText -or $script:eightCalls.Count -ne 9 -or ($script:eightCalls.error -join ',') -cne '2,0,0,2,5,6,6,2,2'){throw 'Exact original8 plus restored-A assertion modes/codes changed'}
  if($script:eightCalls[4].attestation -cne 'guard-unavailable' -or -not $script:eightCalls[6].failedOwner -or -not $script:eightCalls[1].kept){throw 'Original negative/ownership semantics changed'}
 }
 if($mode -eq 'protocol-failure' -and -not $errorText){throw 'Deferred protocol failure lost fatal semantics'}
}
$script:namespaceRestorationKnown=$false;$script:namespaceFacts=$null;$namespaceAccess=$null
$script:qaNamespaceRegistered=$false;$script:qaNamespaceCleanupOkay=$true;$script:userRemoved=$false
$script:outputFailure=$false;$script:drainFailure=$false;$script:drains=0;$script:initialObservations=0
[MixelOrdinaryTokenFixture]::stopFailure=$false;[MixelOrdinaryTokenFixture]::desktopFailure=$false;[MixelOrdinaryTokenFixture]::hashFailure=$false;[MixelOrdinaryTokenFixture]::hashDrift=$false
[MixelOrdinaryTokenFixture]::profiles=0;[MixelCI.StandardNamespaceAccess]::scopes=0
$fixtureRoot=Join-Path $PSScriptRoot 'aba-unverified';[void][IO.Directory]::CreateDirectory($fixtureRoot)
$script:ntPendingProtocols.Clear();$script:ntPreservedRoots.Clear()
$first=$null;try{& ([scriptblock]::Create("try {`nthrow 'ABA_UNVERIFIED'`n"+$tail))}catch{$first=$_.Exception.Message}
if($first -cne 'ABA_UNVERIFIED' -or $script:userRemoved -or [MixelOrdinaryTokenFixture]::profiles -ne 0 -or -not [IO.Directory]::Exists($fixtureRoot) -or [MixelCI.StandardNamespaceAccess]::scopes -ne 0 -or $script:drains -ne 1){throw 'Unverified diagnostic before registration bypassed original finally quarantine/first error'}
'PASS: literal A/single-mask12/original8/restored-A and full outer finally sixteen fault scenarios; initialA precedes grant; original8 codes retained; restoration and desktop independent; ctor/desktop/hash/child/namespace uncertainty quarantines account/profile/root; pre-registration diagnostic quarantine and output error precedence held; temporary credentials cleared; original5 remains firstfatal.'
'''.replace('__ORIGINAL__', str(ORIGINAL).replace("'", "''"))
        print(invoke(body, KNOWN.read_text() + mock + fixture).splitlines()[-1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
