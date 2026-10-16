"""Exact inverse of the reviewed file-dialog CI additions for historical proofs."""
import re


def early_windows_lifecycle():
    return ("      - name: Prove file-dialog lifecycle before complete Windows source checks\n"
            "        shell: pwsh\n"
            "        run: |\n"
            "          if (-not $env:DART_BIN) { throw 'Canonical Windows Dart SDK is required for the early lifecycle gate.' }\n"
            "          $early = Join-Path $env:RUNNER_TEMP 'mixel-file-dialog-lifecycle-early'\n"
            "          python scripts/test-support-file-dialog-lifecycle.py --source rustdesk --dart $env:DART_BIN --output $early\n"
            "          if ($LASTEXITCODE -ne 0) { throw 'Early actual file-dialog lifecycle controls failed.' }\n"
            "      - name: Preserve early Windows file-dialog lifecycle evidence\n"
            "        if: always()\n"
            "        uses: actions/upload-artifact@v6\n"
            "        with:\n"
            "          name: windows-file-dialog-lifecycle-early\n"
            "          path: ${{ runner.temp }}/mixel-file-dialog-lifecycle-early/\n"
            "          if-no-files-found: ignore\n"
            "          retention-days: 14\n\n")


def lifecycle_upload(platform, name):
    paths = ("          path: |\n"
             "            ${{ runner.temp }}/mixel-file-dialog-lifecycle/*.log\n"
             "            ${{ runner.temp }}/mixel-file-dialog-lifecycle/*.json\n"
             "            ${{ runner.temp }}/mixel-file-dialog-lifecycle/actual-*.dart\n"
             if platform == "Windows" else
             "          path: ${{ runner.temp }}/mixel-file-dialog-lifecycle/\n")
    return ("      - name: Preserve " + platform + " file-dialog lifecycle evidence\n"
            "        if: always()\n"
            "        uses: actions/upload-artifact@v6\n"
            "        with:\n"
            "          name: " + name + "\n" + paths +
            "          if-no-files-found: ignore\n"
            "          retention-days: 14\n\n")


def lifecycle_workflow_projections(workflow):
    if workflow not in ("build.yml", "preflight.yml"):
        raise AssertionError("Unknown lifecycle workflow projection")
    build = workflow == "build.yml"
    source = "rustdesk" if build else '"$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"'
    command = ('          python3 scripts/test-support-file-dialog-lifecycle.py --source ' + source +
               ' --dart "$(command -v dart)" --output "$RUNNER_TEMP/mixel-file-dialog-lifecycle"\n')
    sdk = ('          python3 scripts/test-support-flutter-sdk-compatibility.py --app ' + source +
           (' --flutter "$(command -v flutter)"' if build else '') + ' --output "$RUNNER_TEMP/mixel-sdk-compatibility"\n')
    native = ('          python3 scripts/test-support-native-warnings.py --source ' + source +
              ' --source-only --output "$RUNNER_TEMP/mixel-native-warning-source"\n')
    jobs = ("linux", "macos") if build else ("upstream-source", "macos-runtime-harness")
    result = [(job, sdk, command, native) for job in jobs]
    if build:
        result.append(("windows",
                       '            https://github.com/rustdesk/rustdesk.git rustdesk\n\n',
                       early_windows_lifecycle(), '      - name: Apply Mixel-Remote branding\n'))
        for job, platform, name in (("linux", "Linux", "linux-file-dialog-lifecycle"),
                                    ("macos", "Mac", "macos-file-dialog-lifecycle-${{ matrix.job.target }}")):
            result.append((job, '          dart scripts/test-support-invite.dart\n\n' + sdk_compatibility_upload("linux-sdk-compatibility" if job == "linux" else "macos-sdk-compatibility-${{ matrix.job.target }}"),
                           lifecycle_upload(platform, name),
                           '      - name: Build (cargo + flutter via build.py)\n'))
        result.append(("windows", '          name: windows\n          path: dist/\n          if-no-files-found: error\n\n',
                       lifecycle_upload("Windows", "windows-file-dialog-lifecycle"), '  # '))
    else:
        result.append(("windows-packaging",
                       '        run: git clone --depth 1 --branch "$UPSTREAM_VERSION" --recurse-submodules https://github.com/rustdesk/rustdesk.git rustdesk\n',
                       early_windows_lifecycle(), '      - name: Fetch exact pinned native AV1 API headers without codec compilation\n'))
        result.extend((
            ("windows-packaging", '          name: windows-clipboard-source-native\n          path: windows-clipboard-proof/*.log\n          if-no-files-found: ignore\n',
             lifecycle_upload("Windows", "windows-file-dialog-lifecycle"), '  portable-standard-lease-qa:\n'),
            ("upstream-source", '          dart scripts/test-support-invite.dart\n' + sdk_compatibility_upload("upstream-source-sdk-compatibility"),
             lifecycle_upload("Linux", "linux-file-dialog-lifecycle"), '  macos-runtime-harness:\n'),
            ("macos-runtime-harness", '          if-no-files-found: warn\n\n',
             lifecycle_upload("Mac", "macos-file-dialog-lifecycle"), '  session-directory-access-observation:\n'),
        ))
    return result


def inverse_lifecycle_workflow_checks(text, workflow):
    """Validate all exact additions and their jobs/adjacency before projecting."""
    projections = lifecycle_workflow_projections(workflow)
    for job, before, addition, after in projections:
        marker = '\n  ' + job + ':\n'
        if text.count(marker) != 1:
            raise AssertionError("Changed lifecycle workflow job: " + job)
        start = text.index(marker)
        match = re.search(r'\n  [A-Za-z][A-Za-z0-9_-]*:\n', text[start + len(marker):])
        end = len(text) if match is None else start + len(marker) + match.start()
        expected = sum(item[2] == addition for item in projections)
        exact = before + addition + after
        if (text.count(addition) != expected or text[start:end].count(addition.rstrip('\n')) != 1
                or text.count(exact) != expected):
            raise AssertionError("Changed, missing, duplicate or moved lifecycle workflow addition: " + job)
    for _, before, addition, after in projections:
        text = text.replace(before + addition + after, before + after, 1)
    return inverse_sdk_compatibility_retention(text, workflow)


def sdk_compatibility_upload(name):
    """Retain only command receipts and streams, never app/dependency trees."""
    return ("      - name: Preserve SDK compatibility diagnostics\n"
            "        if: always()\n"
            "        uses: actions/upload-artifact@v6\n"
            "        with:\n"
            "          name: " + name + "\n"
            "          path: |\n"
            "            ${{ runner.temp }}/mixel-sdk-compatibility/*.log\n"
            "            ${{ runner.temp }}/mixel-sdk-compatibility/*.command.json\n"
            "          if-no-files-found: ignore\n"
            "          retention-days: 14\n\n")


def sdk_compatibility_workflow_projections(workflow):
    if workflow not in ("build.yml", "preflight.yml"):
        raise AssertionError("Unknown SDK diagnostic workflow projection")
    build = workflow == "build.yml"
    source = "rustdesk" if build else '"$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"'
    command = ('          python3 scripts/test-support-flutter-sdk-compatibility.py --app ' + source +
               (' --flutter "$(command -v flutter)"' if build else ''))
    jobs = (("linux", "linux-sdk-compatibility"),
            ("macos", "macos-sdk-compatibility-${{ matrix.job.target }}")) if build else (
            ("upstream-source", "upstream-source-sdk-compatibility"),
            ("macos-runtime-harness", "macos-runtime-harness-sdk-compatibility"))
    result = [(job, command, ' --output "$RUNNER_TEMP/mixel-sdk-compatibility"', '\n') for job, _ in jobs]
    for job, name in jobs:
        before = ('          RUSTC_BIN="$(rustup which rustc)" python3 scripts/test-macos-update.py --source ' + source + '\n'
                  if job == 'macos-runtime-harness' else '          dart scripts/test-support-invite.dart\n' + ('\n' if build else ''))
        result.append((job, before, sdk_compatibility_upload(name), ''))
    result.append(('windows' if build else 'windows-packaging',
                   '            ${{ runner.temp }}/mixel-sdk-source/*.timing.json\n',
                   '            ${{ runner.temp }}/mixel-sdk-compatibility/*.log\n'
                   '            ${{ runner.temp }}/mixel-sdk-compatibility/*.command.json\n',
                   '          if-no-files-found: ignore\n'))
    return result


def inverse_sdk_compatibility_retention(text, workflow):
    """Require every exact role, command, immediate upload and log-only path."""
    projections = sdk_compatibility_workflow_projections(workflow)
    for job, before, addition, after in projections:
        marker = '\n  ' + job + ':\n'
        if text.count(marker) != 1:
            raise AssertionError('Changed SDK diagnostic workflow job: ' + job)
        start = text.index(marker)
        match = re.search(r'\n  [A-Za-z][A-Za-z0-9_-]*:\n', text[start + len(marker):])
        end = len(text) if match is None else start + len(marker) + match.start()
        expected = sum(item[2].count(addition) for item in projections)
        exact = before + addition + after
        if (text.count(addition) != expected or text[start:end].count(addition.rstrip('\n')) != 1
                or text.count(exact) != sum((item[1] + item[2] + item[3]).count(exact) for item in projections)
                or text[start:end].count(exact.rstrip('\n')) != 1):
            raise AssertionError('Changed, missing, duplicate or moved SDK diagnostic addition: ' + job)
    for _, before, addition, after in projections:
        text = text.replace(before + addition + after, before + after, 1)
    return text


def compose_sdk_compatibility_retention(text, workflow):
    """Apply the same exact diagnostic hunks only to their declared old contexts."""
    for job, before, addition, after in sdk_compatibility_workflow_projections(workflow):
        marker = '\n  ' + job + ':\n'
        if text.count(marker) != 1:
            raise AssertionError('Changed SDK diagnostic insertion job: ' + job)
        start = text.index(marker)
        match = re.search(r'\n  [A-Za-z][A-Za-z0-9_-]*:\n', text[start + len(marker):])
        end = len(text) if match is None else start + len(marker) + match.start()
        segment = text[start:end]
        if addition in segment or segment.count((before + after).rstrip('\n')) != 1:
            raise AssertionError('Changed or duplicate SDK diagnostic insertion context: ' + job)
        segment = segment.replace(before + after, before + addition + after, 1)
        text = text[:start] + segment + text[end:]
    inverse_sdk_compatibility_retention(text, workflow)
    return text
