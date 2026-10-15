"""Exact conservative inverse for the isolated fast Windows source/API step."""
import hashlib

ORIGINAL_SHA256 = '701f80060ad827e970d11b1d8c37ae4d30c5fd2b9c15d1da21e58adc76b3a9b0'
ADDITION = "      - name: Probe PRIVATE95 source and read-only owner APIs on its isolated QA ref\n        if: github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/codex/current77-private95-source-probe'\n        shell: pwsh\n        run: |\n          $ErrorActionPreference = 'Stop'\n          python -B scripts/probe-private95-windows-source-api.py 2>&1 | Tee-Object -FilePath (Join-Path $env:GITHUB_WORKSPACE 'shell-boundary-proof/private95-source-api.log')\n          if ($LASTEXITCODE -ne 0) { throw 'Isolated PRIVATE95 source/API controls failed; application qualification remains separate.' }\n"

def inverse(text):
    marker = '      - uses: actions/upload-artifact@v6\n'
    assert text.count(ADDITION) == 1 and text.count(marker) == 1
    assert ADDITION + marker in text
    original = text.replace(ADDITION, '', 1)
    assert hashlib.sha256(original.encode()).hexdigest() == ORIGINAL_SHA256
    return original
