"""Exact inverse of the QA-only Standard foreground95 runtime caller; no native work."""
import re

MATRIX_ORIGINAL = '        os: [windows-latest, windows-2022]\n'
MATRIX_CURRENT = '        os: [windows-2025, windows-2022]\n'
CALLER_ORIGINAL = '          ./scripts/test-support-launch-windows.ps1 -Executable release-client/Mixel-Remote-QS.exe -Portable -QuickSupport -ExpectedPayload runtime-client -OrdinaryThenQuickSupport\n'
CALLER_CURRENT = "          $ErrorActionPreference = 'Stop'\n          python -B scripts/test-standard-foreground95-adapter.py\n          if ($LASTEXITCODE -ne 0) { throw 'Standard foreground95 adapter source controls failed.' }\n          $gate = (Resolve-Path scripts/qa/current77-standard-foreground95-root-gate.json).Path\n          $gateHash = (Get-FileHash -LiteralPath $gate -Algorithm SHA256).Hash.ToLowerInvariant()\n          $tree = (& git rev-parse 'HEAD^{tree}').Trim()\n          if ($LASTEXITCODE -ne 0) { throw 'Actual future QA harness tree unavailable.' }\n          $owned = Join-Path $env:RUNNER_TEMP ('mixel-standard-foreground95-'+[Guid]::NewGuid().ToString('N'))\n          python -B scripts/prepare-standard-foreground95.py --repository $env:GITHUB_WORKSPACE --output $owned --root-gate $gate --root-gate-sha256 $gateHash --collection release-runtime-collection/artifact-provenance.json --harness-source $env:GITHUB_SHA --harness-tree $tree\n          if ($LASTEXITCODE -ne 0) { throw 'Source77 Standard foreground95 preparation refused.' }\n          if (Test-Path -LiteralPath standard-foreground95-preparation.json) { throw 'Existing Standard foreground95 preparation evidence refused.' }\n          Copy-Item -LiteralPath (Join-Path $owned 'preparation-manifest.json') -Destination standard-foreground95-preparation.json\n          $generated = Join-Path $owned 'test-standard-foreground95.generated.ps1'\n          & pwsh -NoLogo -NoProfile -NonInteractive -File $generated -Executable (Resolve-Path release-client/Mixel-Remote-QS.exe).Path -Portable -QuickSupport -ExpectedPayload (Resolve-Path runtime-client).Path -OrdinaryThenQuickSupport\n          if ($LASTEXITCODE -ne 0) { throw 'Actual signed Standard foreground95 failed.' }\n"
UPLOAD_ORIGINAL = "          $ownedLogs = Join-Path $env:RUNNER_TEMP 'mixel-owned-ordinary-qs-logs'\n"
UPLOAD_CURRENT = "          if (Test-Path -LiteralPath standard-foreground95-preparation.json) {\n            Copy-Item -LiteralPath standard-foreground95-preparation.json -Destination runtime-diagnostics/standard-foreground95-preparation.json\n          }\n          $ownedLogs = Join-Path $env:RUNNER_TEMP 'mixel-owned-ordinary-qs-logs'\n"

HUNKS = ((MATRIX_ORIGINAL, MATRIX_CURRENT), (CALLER_ORIGINAL, CALLER_CURRENT), (UPLOAD_ORIGINAL, UPLOAD_CURRENT))
APP77_BUILD_WORKFLOW_SHA256 = 'cfabeb31aef95f1c8a20df5946caf9ea83f9b95c37ca851a0f5c709a0645cdf8'


def inverse_standard_foreground95_runtime_checks(text, workflow):
    from private_standard95_workflow_projection import inverse_private_standard95
    text = inverse_private_standard95(text, workflow)
    if workflow not in ('build.yml','preflight.yml'):
        raise AssertionError('Unknown Standard foreground95 runtime workflow')
    if workflow == 'preflight.yml':
        if any(current in text for _,current in HUNKS) or 'scripts/prepare-standard-foreground95.py' in text:
            raise AssertionError('Standard foreground95 runtime caller moved into preflight')
        return text
    marker = '\n  runtime:\n'
    if text.count(marker) != 1:
        raise AssertionError('Exact existing runtime job required')
    start = text.index(marker)
    match = re.search(r'\n  [A-Za-z][A-Za-z0-9_-]*:\n',text[start+len(marker):])
    end = len(text) if match is None else start+len(marker)+match.start()
    segment = text[start:end]
    before = (
        '      matrix:\n',
        '        run: |\n',
        "            Copy-Item release-runtime-collection/run-metadata.json runtime-diagnostics/customer-release-producer-run.json\n          }\n",
    )
    after = (
        '    runs-on: ${{ matrix.os }}\n',
        '      - name: Save synthetic runtime diagnostics\n',
        '          if (Test-Path $ownedLogs) {\n',
    )
    # Validate every whole hunk and its original adjacent gates before removing
    # anything. A similarly named addition in another job cannot qualify.
    for (original,current),first,last in zip(HUNKS,before,after):
        exact = first+current+last
        if text.count(current) != 1 or segment.count(current) != 1 or segment.count(exact) != 1:
            raise AssertionError('Changed, missing, duplicate or moved Standard foreground95 runtime hunk')
    for original,current in HUNKS:
        text = text.replace(current,original,1)
    return text
