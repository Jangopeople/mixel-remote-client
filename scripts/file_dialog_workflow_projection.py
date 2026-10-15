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
           (' --flutter "$(command -v flutter)"' if build else '') + '\n')
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
            result.append((job, '          dart scripts/test-support-invite.dart\n\n',
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
            ("upstream-source", '          dart scripts/test-support-invite.dart\n',
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
    return text
