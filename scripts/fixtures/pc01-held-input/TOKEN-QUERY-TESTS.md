The observer uses `TokenRestrictedSids` to reject restricted tokens. It also
requires non-elevated Medium integrity, rejects app containers and impersonation,
and checks existing desktop DACL access with deny-only groups preserved.
An ordinary UAC limited token is not treated as a genuine Standard account.

PC01 returned a one-byte sizing result for `TokenHasRestrictions`. That class
means the token has ever been filtered, so it cannot distinguish the restriction
being checked here. PC01 also returned `ERROR_BAD_LENGTH` for a zero-length
`TokenElevation` sizing probe. Fixed DWORD queries now allocate four bytes and
require an actual four-byte result; variable buffers retain their bounded query.

Run the regression on native x64 Windows in a fresh, ignored QA directory:

```powershell
$fixture = 'scripts/fixtures/pc01-held-input'
$output = '_scratch/qa-token-query'
New-Item -ItemType Directory $output -ErrorAction Stop
& "$env:WINDIR/Microsoft.NET/Framework64/v4.0.30319/csc.exe" /nologo /warn:4 /warnaserror+ /langversion:5 /platform:x64 /target:exe "/out:$output/TokenQueryTests.exe" "$fixture/KeyState.cs" "$fixture/TokenQueryTests.cs"
if ($LASTEXITCODE -ne 0) { throw 'Native compilation failed' }
& "$output/TokenQueryTests.exe" --self-test
if ($LASTEXITCODE -ne 0) { throw 'Inert controls failed' }
& "$output/TokenQueryTests.exe" --native-own-token
if ($LASTEXITCODE -ne 0) { throw 'Native own-token regression failed' }
```

The default executable mode performs no native calls. `--self-test` exercises
nine inert bounds and restricted-group cases. `--native-own-token` invokes the
exact observer methods against only its own query-only token handle, verifies
invalid queries are refused, and reports actual elevation and integrity. It
does not query a receiver, desktop or key state, alter a token, or qualify a
customer connection. A High builder result is not Medium receiver evidence.

The surrounding controller checks run with
`python3 scripts/test-pc01-held-input.py`.
