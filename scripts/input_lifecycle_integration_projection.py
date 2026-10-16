"""Pure exact insertion/inverse recipe for Root's bound combined wrapper."""
import hashlib

WRAPPER = 'patch-support-input-lifecycle.py'
ENGINE = 'support-input-lifecycle-engine.py'
PLAN = 'support-input-lifecycle-plan.json'
BASE_SHA = {
    'scripts/apply-branding.sh': '94c37cb53118fe97b2538df8421737a096bef0382d4678442d58ef61d5133051',
    '.github/workflows/build.yml': '1e8994da5da419d7b7ab321ea0109bc4c5cc40d133e22dc104fd600678b7a8ab',
    '.github/workflows/preflight.yml': '7c3d83f2d8c8ca33c116c4c3688a758a11f2db0ac2a3343ae9576c517e927005',
}
BRIDGE_HEADER = '      - name: Generate bridge\n        working-directory: rustdesk\n        run: |\n'
BRIDGE_OLD = (
    '          python3 ../scripts/patch-support-uri-routing.py --root . --restore\n'
    '          python3 ../scripts/patch-support-linux-child-window-close.py --root . --restore\n'
    '          RDREPO=. python3 ../scripts/patch-support-macos-texture.py\n'
    '          RDREPO=. python3 ../scripts/patch-support-macos-window.py\n'
    '          python3 ../scripts/patch-support-linux-child-window-close.py --root . --after-branding\n'
    '          python3 ../scripts/patch-support-uri-routing.py --root . --after-branding\n'
)
BRIDGE_AFTER = "          pushd flutter && sed -i -e 's/extended_text: 14.0.0/extended_text: 13.0.0/g' pubspec.yaml && flutter pub get && popd\n"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(raw):
    logical = raw.replace(b'\r\n', b'\n')
    newline = b'\r\n' if b'\r\n' in raw else b'\n'
    if b'\r' in logical or logical.replace(b'\n', newline) != raw:
        raise ValueError('Mixed integration source newlines')
    return logical.decode('utf-8', errors='strict'), newline


def hunks(name):
    if name == 'scripts/apply-branding.sh':
        command = 'python3 "$(dirname "${BASH_SOURCE[0]}")/' + WRAPPER + '" --root "$RDREPO"'
        early = ('# Validate and invert the complete desktop input layer before older owners write.\n' +
            command + ' --restore --check\n' + command + ' --restore\n\n')
        late = ('# Apply desktop input ownership only after all previous functional layers.\n' +
            command + '\n\n')
        return [(
            'python3 "$(dirname "${BASH_SOURCE[0]}")/patch-support-file-dialog-lifecycle.py" --root "$RDREPO" --check\n\n',
            early,
            '# Restore exact terminal URI routing before the native and earlier owner checks.\n',
        ), (
            'python3 "$(dirname "${BASH_SOURCE[0]}")/patch-support-file-dialog-lifecycle.py" --root "$RDREPO"\n\n',
            late,
            'echo "✓ Branding applied — product identity is $APP_NAME only."\n',
        )]
    if name == '.github/workflows/build.yml':
        command = '          python3 "$GITHUB_WORKSPACE/scripts/' + WRAPPER + '" --root .'
        early = command + ' --restore --check\n' + command + ' --restore\n'
        late = command + '\n'
        native_before = '        run: python3 scripts/release-artifacts.py config\n\n'
        native_after = ('  # ===========================================================================\n'
                        '  # BRIDGE — generate flutter_rust_bridge codegen once for all platforms.\n'
                        '  # ===========================================================================\n'
                        '  bridge:\n')
        return [(native_before, NATIVE_REGRESSIONS, native_after),
                (BRIDGE_HEADER, early, BRIDGE_OLD + BRIDGE_AFTER),
                (BRIDGE_OLD, late, BRIDGE_AFTER)]
    if name == '.github/workflows/preflight.yml':
        return []
    raise ValueError('Unknown terminal integration source')


NATIVE_REGRESSIONS = r"""  # ===========================================================================
  # INPUT REGRESSIONS — actual production components on all desktop hosts.
  # ===========================================================================
  input-regressions:
    needs: config
    strategy:
      fail-fast: false
      matrix:
        job:
          - { os: ubuntu-22.04, rust: '1.75', name: linux }
          - { os: windows-2022, rust: '1.75', name: windows }
          - { os: macos-14, rust: '1.81', name: macos }
    runs-on: ${{ matrix.job.os }}
    timeout-minutes: 25
    defaults:
      run:
        shell: bash
    env:
      UPSTREAM_VERSION: ${{ needs.config.outputs.upstream_version }}
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: '3.11'
      - name: Install pinned production Rust compiler
        uses: dtolnay/rust-toolchain@v1
        with:
          toolchain: ${{ matrix.job.rust }}
      - name: Fetch pinned upstream with submodules
        run: |
          git -c core.autocrlf=false clone --depth 1 --branch "${UPSTREAM_VERSION}" --recurse-submodules \
            https://github.com/rustdesk/rustdesk.git rustdesk
      - name: Apply complete reviewed branding and input ownership
        run: ./scripts/apply-branding.sh
        env:
          RDREPO: ./rustdesk
          BRANDING: ./branding
      - name: Prove actual input components and required original failures
        run: |
          python3 scripts/fixtures/input-lifecycle/native-input/native_input_gate.py \
            --source rustdesk --output "$RUNNER_TEMP/mixel-native-input" --online
      - name: Preserve input regression evidence
        if: always()
        uses: actions/upload-artifact@v6
        with:
          name: native-input-regressions-${{ matrix.job.name }}
          path: |
            ${{ runner.temp }}/mixel-native-input/
            !${{ runner.temp }}/mixel-native-input/target/**
          if-no-files-found: ignore
          retention-days: 14

"""


MAC_REGRESSIONS = r"""      - name: Prove desktop input lifetime regressions
        run: |
          python3 scripts/test-support-input-lifecycle-flutter.py \
            --source rustdesk --flutter "$(command -v flutter)" \
            --fixture scripts/fixtures/input-lifecycle/source2_pointer_modifiers_test.dart \
            --expected-input a15f40adcf4887b146b78caa9c183938d1023fcb5dc31e71c64f17093d389b02 \
            --expected-fixture 65d294d32c706c8d03e53428d1cae15fb69921ed3d53a9cf03cf6bdbb7a2a7d7 \
            --relative-fixture scripts/fixtures/input-lifecycle/relative_mouse_late_enable_test.dart \
            --expected-relative-fixture 7e4148cf60fa04cb9c39fc70bb00e1f346e55006089f15242f7ec8fcd05a1ab0 \
            --expected-relative-model 99c3f74d8ca12bf27fa9269f74d9490fb7f859106cbc35a554938011d1edf9f6 \
            --expected-count 180 \
            --context-lock scripts/fixtures/input-lifecycle/flutter-test-context.lock \
            --output "$RUNNER_TEMP/mixel-input-lifecycle-flutter"
          python3 scripts/fixtures/input-lifecycle/native_lease_gate.py \
            --source rustdesk --output "$RUNNER_TEMP/mixel-input-lifecycle-swift"

      - name: Preserve desktop input lifetime regression evidence
        if: always()
        uses: actions/upload-artifact@v6
        with:
          name: macos-input-lifecycle-${{ matrix.job.target }}
          path: |
            ${{ runner.temp }}/mixel-input-lifecycle-flutter/
            ${{ runner.temp }}/mixel-input-lifecycle-swift/
          if-no-files-found: ignore
          retention-days: 14

"""


def workflow_hunks():
    """New workflow owners compose here by exact job and adjacent old gates."""
    before = ('      - name: Require the complete generated Flutter app to analyze without errors or warnings\n'
              '        working-directory: rustdesk/flutter\n'
              '        run: flutter analyze --no-pub --no-fatal-infos\n\n')
    after = '      - name: Test compiled attended support guard\n'
    return {'macos': [(before, MAC_REGRESSIONS, after)]}


def project_job_hunks(text, restore):
    """An exact block in another job cannot qualify the Mac invocation."""
    import re
    for job, changes in workflow_hunks().items():
        matches = list(re.finditer(r'^  [A-Za-z0-9_-]+:\n', text, re.MULTILINE))
        matches_for_job = [match for match in matches if match.group() == '  ' + job + ':\n']
        if len(matches_for_job) != 1:
            raise ValueError('Changed integration workflow job')
        start = matches_for_job[0].start()
        following = [match.start() for match in matches if match.start() > start]
        end = min(following) if following else len(text)
        segment = text[start:end]
        for before, addition, after in reversed(changes) if restore else changes:
            if restore:
                if text.count(addition) != 1 or segment.count(before + addition + after) != 1:
                    raise ValueError('Changed/missing/moved job-bound regression block')
                segment = segment.replace(before + addition + after, before + after, 1)
            else:
                if addition in text or segment.count(before + after) != 1:
                    raise ValueError('Changed job-bound regression insertion')
                segment = segment.replace(before + after, before + addition + after, 1)
        text = text[:start] + segment + text[end:]
    return text


def project_shipping_dependencies(text, restore):
    """Only each original shipping job may gain the required native gate."""
    original = '    needs: [config, bridge]\n'
    current = '    needs: [config, bridge, input-regressions]\n'
    for job in ('linux', 'macos', 'windows'):
        before = '  ' + job + ':\n'
        after = "    if: ${{ needs.config.outputs." + job + " == 'true' }}\n"
        old, new = (current, original) if restore else (original, current)
        if text.count(before) != 1 or text.count(before + old + after) != 1:
            raise ValueError('Changed/missing/moved shipping input dependency')
        text = text.replace(before + old + after, before + new + after, 1)
    return text

def compose(raw, name):
    text, newline = canonical(raw)
    if name not in BASE_SHA or digest(text.encode()) != BASE_SHA[name]:
        raise ValueError('Changed canonical integration baseline')
    for before, addition, after in hunks(name):
        if text.count(before + after) != 1 or addition in text:
            raise ValueError('Changed exact terminal insertion context')
        text = text.replace(before + after, before + addition + after, 1)
    if name == '.github/workflows/build.yml':
        text = project_shipping_dependencies(text, restore=False)
        text = project_job_hunks(text, restore=False)
    return sdk_retention_projection(text.encode().replace(b'\n', newline), name, restore=False)


def inverse_blocks(raw, name):
    """Strict new hunks only; historical caller's existing final hash stays fatal.

    Other independently reviewed workflow additions may compose outside these
    exact hunks, then be inversed by their own unchanged guard owners.
    """
    text, newline = canonical(raw)
    if name == '.github/workflows/build.yml':
        text = project_job_hunks(text, restore=True)
        text = project_shipping_dependencies(text, restore=True)
    changes = hunks(name)
    for before, addition, after in reversed(changes):
        if text.count(addition) != 1 or text.count(before + addition + after) != 1:
            raise ValueError('Changed/missing/moved terminal integration block')
        text = text.replace(before + addition + after, before + after, 1)
    if changes and any(WRAPPER in line for line in text.splitlines()):
        raise ValueError('Unreviewed additional terminal engine invocation')
    return text.encode().replace(b'\n', newline)


def project_to_canonical(raw, name):
    result = inverse_blocks(sdk_retention_projection(raw, name, restore=True), name)
    logical, _ = canonical(result)
    if digest(logical.encode()) != BASE_SHA[name]:
        raise ValueError('Terminal integration inverse changed other canonical bytes')
    return result


def current_source(root):
    """Strict complete-tree entry only; internal historical fixtures never call this.

    Every target must be one whole fresh/original/final profile, including absence
    of the newly owned Rust module. Only final needs a private exact inverse.
    This function must not be used as an optional presence/marker selector.
    """
    import contextlib
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        'mixel_complete_input_lifecycle', Path(__file__).with_name(WRAPPER))
    terminal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(terminal)
    final = terminal.has_layer(root)  # Strict inspect, never an error fallback.
    if final:
        return terminal.previous(root)
    return contextlib.nullcontext(Path(root))


def committed_workflow(raw, name):
    """Read HEAD at either exact pre-integration baseline or strict new carrier.

    This is only for the historical fixture's original working-file == HEAD
    assertion. The actual working-file entry always requires new exact hunks.
    """
    text, _ = canonical(raw)
    if digest(text.encode()) == BASE_SHA[name]:
        return raw
    return inverse_blocks(raw, name)


def sdk_retention_projection(raw, name, restore):
    """Compose only the exact separately reviewed SDK evidence workflow layer."""
    if name not in ('.github/workflows/build.yml', '.github/workflows/preflight.yml'):
        return raw
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        'mixel_sdk_retention_projection', Path(__file__).with_name('file_dialog_workflow_projection.py'))
    projection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(projection)
    text, newline = canonical(raw)
    operation = (projection.inverse_sdk_compatibility_retention if restore else
                 projection.compose_sdk_compatibility_retention)
    return operation(text, Path(name).name).encode().replace(b'\n', newline)
