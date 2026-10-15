#!/usr/bin/env python3
"""UNQUALIFIED staging: prepare a pinned Standard-foreground QA driver, never run it.

The actual signed QuickSupport outer retains the runner token. Only the existing
ordinary foreground is explicitly selected as the disposable Standard account.
No provider, application, account, ACL or policy operation occurs in this file.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import standard95_guard as G

SOURCE = G.SOURCE
TREE = G.TREE
RUN = G.RUN
WINDOWS_JOB = G.WINDOWS
ATTEMPT = 1
PREFLIGHT = G.PREFLIGHT
COMPARISON = G.COMPARISON
PINS = {
    'test-support-launch-windows.ps1': 'c1f3e99ebda0a146bce9b54f215feeebd0c6d19bec19f2a280259d311983c44b',
    'support-runtime-probe-windows.ps1': '6fda08837d4f8451ede9c02532b8f2bc40a76aec51839ec06fcc91be471fc28e',
    'verify-windows-payload.ps1': '5c562f87f7e67b6dc8d73907f530fa82e7e255aa2837ab20ae4fd4c32dfbf32b',
    'test-hosted-qa-session-directory.cs': 'b7f7886e2e6de9f7d76ef371473a436d8845b1396c12b63517331894d4611d4e',
    'windows-standard-namespace-access.cs': '7f04727ae8161b8e4da1d2e4a74a0324549996da5af52a08c14538ccf53b2589',
    'support-windows-desktop-identity-diagnostic.cs': 'e8e292ee6c5ab2604a2b4d45a6d09b74f86489d48df3dbb194ac552313b8e8d6',
}
DRIVER = 'test-support-launch-windows.ps1'
OBSERVER = 'support-windows-desktop-identity-diagnostic.cs'
SELECTION_SHA = '81d5cb3c9a9a69fbb8316ad18173624f23b3e07796f2599f58981bef384d1176'
FALLBACK_SHA = '38c0bf2ae67c859d298d622d3837450923b26abdb8bcc84c224f8daceec2aa01'
MAIN = 'try {\n  if ($OrdinaryThenQuickSupport) {'
START = '    if (-not [MixelOrdinaryTokenFixture]::Elevated($PID)) {'
END = "    $fixtureStage = 'verify actual ordinary process token'"
FALLBACK_START = "        if ($env:GITHUB_ACTIONS -cne 'true')"
FALLBACK_END = '\n      }\n      $main = Get-Process -Id $ordinaryPid'
CLASS_MARKER = '  public static int StartStandardUser(string executable, string username, string password) {'
FINAL_MARKER = "    $handoffKind = if ($Portable) { 'actual customer QS portable launcher' } else { 'exact signed compiled QS argument (diagnostic)' }"

SCOPE_GUARD = r'''
# Test-only explicit route; this never changes product argv or native API results.
if (-not $Portable -or -not $QuickSupport -or -not $OrdinaryThenQuickSupport -or
    -not $ExpectedPayload -or $CompiledQuickSupportDiagnostic -or $NativeCrashDiagnostic) {
  throw 'Standard foreground95 requires the actual signed portable outer and exact ordinary-to-QS scenario.'
}
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitProcess -or
    $env:GITHUB_ACTIONS -cne 'true' -or $env:GITHUB_REPOSITORY -cne 'Jangopeople/mixel-remote-client' -or
    $env:RUNNER_ENVIRONMENT -cne 'github-hosted' -or $env:GITHUB_JOB -cne 'runtime' -or
    $env:GITHUB_EVENT_NAME -cne 'workflow_dispatch' -or $env:MIXEL_HOSTED_OWNED_QA_NAMESPACE -cne '1' -or
    $env:GITHUB_REF -cnotmatch '\Arefs/(?:heads|tags)/[^\r\n]+\z' -or
    $env:GITHUB_WORKFLOW_REF -cne ('Jangopeople/mixel-remote-client/.github/workflows/build.yml@'+$env:GITHUB_REF)) {
  throw 'Exact existing hosted runtime namespace context required before any application or account action.'
}
foreach ($nativeType in @('MixelOrdinaryTokenFixture','MixelSupportWindowTest','MixelPackagedQaTokenFacts','MixelHostedQaNamespace')) {
  if ($nativeType -as [type]) { throw 'Fresh native QA process required; preloaded fixture types refused.' }
}
Write-Host 'SCOPE: actual signed outer retains runner token; this proves disposable Standard foreground ownership only.'
'''

ASSERT_FUNCTION = r'''function Assert-MixelOwnedStandardForeground95([string]$Phase) {
  if (-not $ownedSid -or $ownedUser -cnotmatch '\Amixelqs[0-9a-f]{10}\z' -or -not $main) {
    throw 'Explicit disposable Standard foreground was not selected.'
  }
  $account = Get-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid))
  if ($account.Name -cne $ownedUser -or -not $account.Enabled) { throw 'Actual disposable account identity changed.' }
  $admins = @(Get-LocalGroupMember -SID ([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')) |
    Where-Object { $_.SID.Value -ceq $ownedSid })
  $users = @(Get-LocalGroupMember -SID ([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545')) |
    Where-Object { $_.SID.Value -ceq $ownedSid })
  if ($admins.Count -ne 0 -or $users.Count -ne 1) { throw 'Actual owned local account lacks the required Standard membership facts.' }
  $facts = [MixelOrdinaryTokenFixture]::ObserveOwnedDesktopPrimary($main.Id,$ownedSid)
  $callerSession = (Get-Process -Id $PID).SessionId
  if ([MixelOrdinaryTokenFixture]::StartedStatus($main.Id) -cne 'still-running' -or
      $facts.pid -ne $main.Id -or -not $facts.retainedPidMatched -or $facts.status -cne 'available' -or
      $facts.operation -cne 'complete' -or $null -ne $facts.failureType -or $null -ne $facts.nativeError -or
      $null -ne $facts.tokenCloseError -or $facts.ownedSidMatched -ne $true -or $facts.elevated -ne $false -or
      $callerSession -eq 0 -or $facts.tokenSessionId -ne $callerSession -or $main.SessionId -ne $callerSession -or
      -not $facts.tokenOpened -or -not $facts.tokenCloseAttempted -or -not $facts.tokenClosed) {
    throw 'Actual retained foreground primary token is not the owned non-elevated Standard identity in the runner session.'
  }
  Write-Host ('OBSERVED: genuine Standard foreground95; '+([ordered]@{
    schema=1;phase=$Phase;foregroundPid=$main.Id;ownedSidMatched=$true;retainedPidMatched=$true;
    standardAccount=$true;localAdministratorsMembership=$false;localUsersMembership=$true;
    elevated=$false;tokenSessionMatched=$true;tokenClosed=$true;
    outerToken='runner-original-unchanged';standardOuterQualified=$false
  } | ConvertTo-Json -Depth 3 -Compress))
}

'''
INITIAL_ASSERT = "    Assert-MixelOwnedStandardForeground95 'before-ordinary-baseline'\n"
FINAL_ASSERT = "    Assert-MixelOwnedStandardForeground95 'after-real95-before-cleanup'\n"


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(raw):
    text = raw.decode('utf-8', errors='strict')
    require(not text.startswith('\ufeff'), 'Unexpected source BOM')
    if '\r' in text:
        remaining = text.replace('\r\n', '')
        require('\r' not in remaining and '\n' not in remaining, 'Mixed or bare-CR source refused')
        text = text.replace('\r\n', '\n')
    return text


def read_sources(repository):
    result = {}
    for name, expected in PINS.items():
        path = repository / 'scripts' / name
        require(path.is_file() and not path.is_symlink(), 'Pinned source must be a regular nonlink file: '+name)
        text = canonical(G.owned_read(path, 1_000_000))
        require(sha(text.encode()) == expected, 'Pinned source drift before output/native work: '+name)
        result[name] = text
    return result


def replace_one(text, before, after):
    require(text.count(before) == 1, 'Exact single projection boundary changed')
    return text.replace(before, after, 1)


def projected(source, observer):
    require(sha(source.encode()) == PINS[DRIVER], 'Original whole launch source changed')
    require(sha(observer.encode()) == PINS[OBSERVER], 'Existing actual-token observer changed')
    main = source.index(MAIN)
    start = source.index(START, main)
    end = source.index(END, start)
    selection = source[start:end]
    fallback_start = selection.index(FALLBACK_START)
    fallback_end = selection.index(FALLBACK_END, fallback_start)
    fallback = selection[fallback_start:fallback_end]
    require(sha(selection.encode()) == SELECTION_SHA and sha(fallback.encode()) == FALLBACK_SHA,
            'Exact existing current/linked/owned selection body changed')
    # The old selection stays intact in an explicit alternate branch. No native
    # query result or failure is manufactured; the new route directly selects
    # the exact already-existing account/ACL/namespace/CreateProcessWithLogonW.
    selected = ('    if ($true) { # explicit test-only disposable Standard selection\n' +
                fallback + '\n      $main = Get-Process -Id $ordinaryPid\n' +
                '    } else { # conserved original default selection\n' + selection + '    }\n')
    edits = [
        ('$ErrorActionPreference = \'Stop\'\n', '$ErrorActionPreference = \'Stop\'\n'+SCOPE_GUARD),
        (CLASS_MARKER, observer+'\n'+CLASS_MARKER),
        (selection, selected),
        (END, INITIAL_ASSERT+END),
        (FINAL_MARKER, FINAL_ASSERT+FINAL_MARKER),
        (MAIN, ASSERT_FUNCTION+MAIN),
    ]
    candidate = source
    for old, new in edits:
        candidate = replace_one(candidate, old, new)
    inverse = candidate
    for old, new in reversed(edits):
        inverse = replace_one(inverse, new, old)
    require(inverse == source and sha(inverse.encode()) == PINS[DRIVER], 'Whole-source inverse conservation failed')
    return candidate, edits


def unique_json(raw):
    return G.unique_json(raw)


def validate_gate(gate):
    require(gate.get('schema') == 1 and type(gate.get('schema')) is int and
            gate.get('scope') == G.SCOPE, 'Exact Root gate scope required')
    require(gate.get('root_whole_green_verified') is True, 'Root whole-green gate is not verified')
    exact = {'source': SOURCE, 'tree': TREE, 'producer_run': RUN, 'producer_attempt': ATTEMPT,
             'producer_windows_job':WINDOWS_JOB,'preflight_run': PREFLIGHT, 'comparison_run': COMPARISON}
    for key, expected in exact.items():
        require(type(gate.get(key)) is type(expected) and gate[key] == expected, 'Root gate identity mismatch: '+key)
    require(gate.get('checks') == {name: 'success' for name in
            ('all_required_producer_jobs','mandatory_preflight','actual2022_source_comparison','actual2025_source_comparison')},
            'Complete same-HEAD Root whole-green checks required')
    evidence = gate.get('original_evidence_sha256')
    require(isinstance(evidence, dict) and set(evidence) == set(G.ROLES) and
            all(isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) for value in evidence.values()),
            'Original Root evidence hashes required')
    blobs = gate.get('reviewed_harness_blobs')
    require(isinstance(blobs,dict) and {'scripts/prepare-standard-foreground95.py',
            'scripts/test-standard-foreground95-adapter.py','scripts/standard95_guard.py',
            'scripts/capture-standard95-state.py','scripts/test-standard95-normal-guards.py',
            'scripts/standard_foreground95_workflow_projection.py','.github/workflows/build.yml'} <= set(blobs) and
            all(isinstance(name,str) and re.fullmatch(r'(?:scripts|\.github/workflows)/[A-Za-z0-9_./-]+',name) and
                '..' not in name.split('/') and isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value)
                for name,value in blobs.items()), 'Reviewed adapter/caller Git blob hashes required')
    require(type(gate.get('producer_windows_job')) is int and gate['producer_windows_job'] > 0,
            'Root actual positive Windows job identity required')
    return gate


def validate_collection(provenance, gate):
    require(provenance.get('evidence_kind') == 'debugger-free-windows-customer-release-gate-inputs' and
            provenance.get('repository') == 'Jangopeople/mixel-remote-client' and provenance.get('publication') is False,
            'Exact existing normal Windows collector provenance required')
    for key, expected in {'build_run_id':str(RUN), 'source_commit':SOURCE, 'application_source_baseline':SOURCE,
                          'producer_windows_job_id':gate['producer_windows_job'], 'producer_windows_job_conclusion':'success',
                          'build_status':'completed','build_conclusion':'success'}.items():
        actual = provenance.get(key)
        if key == 'build_run_id':
            require(type(actual) in (int,str) and str(actual) == expected, 'Collector producer run mismatch')
        else:
            require(type(actual) is type(expected) and actual == expected, 'Collector source/status mismatch: '+key)
    artifacts = provenance.get('collected_artifacts')
    require(isinstance(artifacts,dict) and set(artifacts) == {'windows','windows-msix-payload'}, 'Exact normal artifact roles required')
    require(isinstance(gate.get('artifacts'),dict) and set(gate['artifacts']) == set(artifacts), 'Actual Root artifact identities required')
    for name, item in artifacts.items():
        require(type(item.get('artifact_id')) is int and item['artifact_id'] > 0 and
                re.fullmatch('sha256:[0-9a-f]{64}',item.get('artifact_digest','')) is not None and
                item.get('actual_archive_digest') == item['artifact_digest'], 'Exact actual artifact digest required')
        sealed = gate['artifacts'][name]
        require(isinstance(sealed,dict) and type(sealed.get('id')) is int and sealed['id'] > 0,
                'Root artifact ID must be an actual positive integer')
        require(gate['artifacts'][name] == {'id':item['artifact_id'],'digest':item['artifact_digest']}, 'Root artifact binding mismatch')
        files = item.get('files_sha256')
        require(isinstance(files,dict) and files and all(isinstance(k,str) and k.startswith('artifacts/'+name+'/') and
                isinstance(v,str) and re.fullmatch('[0-9a-f]{64}',v) for k,v in files.items()), 'Actual collector file hashes required')
    return provenance


def verify_harness(repository, source, tree, reviewed_blobs, gate_path, gate_digest):
    require(re.fullmatch('[0-9a-f]{40}',source) and re.fullmatch('[0-9a-f]{40}',tree), 'Exact future harness source/tree required')
    def git(*args):
        return subprocess.check_output(['git','-C',str(repository),*args], stderr=subprocess.PIPE, timeout=30)
    require(git('rev-parse','HEAD').decode().strip() == source and git('rev-parse','HEAD^{tree}').decode().strip() == tree,
            'Actual harness checkout differs from reviewed caller source/tree')
    require(not git('diff','--name-only','HEAD','--'), 'Tracked future harness source is dirty')
    relative = Path(__file__).resolve().relative_to(repository.resolve()).as_posix()
    require(relative.startswith('scripts/') and relative in reviewed_blobs,
            'Adapter must be reviewed tracked harness source; ignored artifact staging cannot qualify')
    for name, expected in reviewed_blobs.items():
        path = repository/name
        require(path.is_file() and not path.is_symlink() and sha(G.owned_read(path,1_000_000)) == expected and
                git('show',source+':'+name) == G.owned_read(path,1_000_000), 'Reviewed caller/adapter Git blob differs: '+name)
    gate_relative = gate_path.resolve().relative_to(repository.resolve()).as_posix()
    require(gate_relative.startswith('scripts/') and gate_relative not in reviewed_blobs and
            sha(G.owned_read(gate_path,1_000_000)) == gate_digest and git('show',source+':'+gate_relative) == G.owned_read(gate_path,1_000_000),
            'Sealed Root gate must equal its separately tracked future harness blob')
    require(not git('status','--porcelain','--',*reviewed_blobs,gate_relative), 'Reviewed harness has uncommitted source drift')
    return {'source':source,'tree':tree,'adapter_path':relative,'adapter_sha256':sha(Path(__file__).read_bytes()),
            'reviewed_harness_blobs':reviewed_blobs,'root_gate_path':gate_relative,'root_gate_sha256':gate_digest}


def prepare(repository, output, gate_path, gate_digest, collection_path, harness_source, harness_tree, live_state):
    require(re.fullmatch('[0-9a-f]{64}',gate_digest), 'Explicit reviewed Root gate file SHA256 required')
    require(gate_path.is_file() and not gate_path.is_symlink(), 'Root gate must be a regular file')
    gate_raw = G.owned_read(gate_path,1_000_000)
    require(sha(gate_raw) == gate_digest, 'Actual Root gate file differs from reviewed caller binding')
    gate = validate_gate(unique_json(gate_raw))
    require(collection_path.is_file() and not collection_path.is_symlink(), 'Actual collector provenance file required')
    expected_jobs = G.originals(repository, gate)
    require(isinstance(live_state,Path), 'Actual fresh normal state required')
    live_hashes = G.live_state(live_state, gate, expected_jobs)
    collection_raw = G.owned_read(collection_path,8*1024*1024)
    collection = validate_collection(unique_json(collection_raw), gate)
    sources = read_sources(repository)
    candidate, _ = projected(sources[DRIVER],sources[OBSERVER])
    harness = verify_harness(repository,harness_source,harness_tree,gate['reviewed_harness_blobs'],gate_path,gate_digest)
    parent = Path(os.environ.get('RUNNER_TEMP',''))
    require(str(parent) not in ('','.','/') and parent.is_dir() and not parent.is_symlink(), 'Actual owned runner temporary parent required')
    require(output.parent.resolve() == parent.resolve() and re.fullmatch('mixel-standard-foreground95-[0-9a-f]{32}',output.name),
            'Fresh direct owned runner temporary child required')
    require(not output.exists() and not output.is_symlink(), 'Existing output refused without mutation')
    # Every source, gate, artifact role and harness identity is validated before
    # the first write. No app action is performed by preparation.
    output.mkdir(mode=0o700)
    for name,text in sources.items():
        if name != DRIVER:
            (output/name).write_bytes(text.encode())
    generated = output/'test-standard-foreground95.generated.ps1'
    generated.write_bytes(candidate.encode())
    record = {'schema':1,'scope':'prepared-standard-foreground95-not-runtime-qualified','artifact_source':SOURCE,
              'artifact_tree':TREE,'producer_run':RUN,'producer_attempt':ATTEMPT,'producer_windows_job':gate['producer_windows_job'],
              'harness':harness,'root_gate_sha256':gate_digest,'collector_provenance_sha256':sha(collection_raw),'fresh_original_sha256':live_hashes,
              'actual_collected_artifacts':collection['collected_artifacts'],'source_pins':PINS,
              'generated_driver_sha256':sha(candidate.encode()),'original_driver_sha256':PINS[DRIVER],
              'native_runtime_qualified':False,'outer_token':'runner-original-unchanged','standard_outer_qualified':False,
              'policy_mutation':False,'original_cleanup_and_95_assertions_conserved':True}
    (output/'preparation-manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    print('PREPARED ONLY: '+str(generated))
    print('Actual native Windows foregroundStandard95 and all original cleanup remain mandatory; signed outer retains runner token.')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--root-gate',type=Path,required=True)
    parser.add_argument('--root-gate-sha256',required=True)
    parser.add_argument('--collection',type=Path,required=True)
    parser.add_argument('--harness-source',required=True)
    parser.add_argument('--harness-tree',required=True)
    parser.add_argument('--live-state',type=Path,required=True)
    args = parser.parse_args()
    prepare(args.repository,args.output,args.root_gate,args.root_gate_sha256,args.collection,args.harness_source,args.harness_tree,args.live_state)


if __name__ == '__main__':
    main()
