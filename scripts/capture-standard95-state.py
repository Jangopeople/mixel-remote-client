#!/usr/bin/env python3
"""Normal1979 Root/source admission and fresh raw facts; never starts an app."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import standard95_guard as G

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('normal_standard95_adapter', HERE / 'prepare-standard-foreground95.py')
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)


_REFUSAL_MESSAGES = {
    'Actual normal hosted runtime context required before mutation': 'HOSTED_CONTEXT',
    'Exact reviewed normal workflow/ref required': 'WORKFLOW_REF',
    'Actual owned normal workspace/temp required': 'WORKSPACE_TEMP',
    'Normal restoration requires a clean exact checkout': 'CLEAN_CHECKOUT',
    'Input must be absolute canonical': 'INPUT_CANONICAL',
    'Input parent must be a directory': 'INPUT_PARENT',
    'Input must be owned bounded single-link regular file': 'INPUT_FILE',
    'Input owner differs from the actual current account': 'INPUT_OWNER',
    'Input changed during read': 'INPUT_CHANGED',
    'Windows owner/token SID missing': 'OWNER_SID_MISSING',
    'Current Windows token SID size refused': 'OWNER_TOKEN_SIZE',
    'Current Windows token owner inspection refused': 'OWNER_TOKEN_OPEN',
    'Current Windows token SID inspection refused': 'OWNER_TOKEN_INFO',
    'Windows file-handle owner inspection refused': 'OWNER_FILE_SECURITY',
    'Windows owner inspection handle cleanup failed': 'OWNER_HANDLE_CLOSE',
    'Owned runner temporary directory handle unavailable': 'OWNER_DIRECTORY_HANDLE',
    'Owned runner directory handle cleanup failed': 'OWNER_DIRECTORY_CLOSE',
    'Exact separately tracked normal Root gate required': 'ROOT_GATE_PATH',
    'Normal Root gate absent; no collection or application work permitted': 'ROOT_GATE_MISSING',
    'Actual normal Root gate digest differs': 'ROOT_GATE_DIGEST',
    'Exact Root gate scope required': 'ROOT_GATE_SCOPE',
    'Root whole-green gate is not verified': 'ROOT_GATE_NOT_GREEN',
    'Complete same-HEAD Root whole-green checks required': 'ROOT_GATE_CHECKS',
    'Original Root evidence hashes required': 'ROOT_GATE_EVIDENCE',
    'Reviewed adapter/caller Git blob hashes required': 'ROOT_GATE_BLOBS',
    'Root actual positive Windows job identity required': 'ROOT_GATE_WINDOWS_JOB',
    'Exact future harness source/tree required': 'HARNESS_ID_FORMAT',
    'Actual harness checkout differs from reviewed caller source/tree': 'HARNESS_CHECKOUT',
    'Tracked future harness source is dirty': 'HARNESS_DIRTY',
    'Adapter must be reviewed tracked harness source; ignored artifact staging cannot qualify': 'HARNESS_TRACKED_ADAPTER',
    'Sealed Root gate must equal its separately tracked future harness blob': 'HARNESS_GATE_BLOB',
    'Reviewed harness has uncommitted source drift': 'HARNESS_UNCOMMITTED',
    'Six sealed original normal run/job inventories required': 'ORIGINAL_REFERENCES',
    'Exact typed normal original reference required': 'ORIGINAL_REFERENCE_FORMAT',
    'Original normal byte binding differs': 'ORIGINAL_BYTES',
    'Original normal evidence hashes differ': 'ORIGINAL_HASHES',
    'Actual same-HEAD attempt1 whole-run success required': 'SOURCE_RUN_IDENTITY',
    'Complete exact normal job inventory required': 'SOURCE_JOB_INVENTORY',
    'Every required normal job must actually pass on the same source': 'SOURCE_JOB_STATE',
    'Actual complete normal step inventory required': 'SOURCE_STEP_INVENTORY',
    'Exact unique normal job identities required': 'SOURCE_JOB_IDENTITIES',
    'Actual Windows job differs from Root seal': 'SOURCE_WINDOWS_JOB',
    'Mandatory Windows customer/source gate must pass': 'SOURCE_WINDOWS_STEPS',
    'Fresh direct owned normal state folder required': 'FRESH_OUTPUT',
    'Canonical fresh normal state directory required': 'FRESH_STATE_DIRECTORY',
    'Fresh normal job identities changed': 'FRESH_JOB_IDENTITIES',
    'Fresh read-only normal metadata request failed': 'METADATA_REQUEST',
    'Bounded normal source inventory required': 'RESTORE_INVENTORY',
    'Exact scoped normal source paths required': 'RESTORE_PATHS',
    'Regular scoped normal source required': 'RESTORE_FILE',
    'Duplicate JSON key': 'JSON_DUPLICATE',
}
_REFUSAL_PREFIXES = (
    ('Root gate identity mismatch: ', 'ROOT_GATE_IDENTITY'),
    ('Reviewed caller/adapter Git blob differs: ', 'HARNESS_BLOB'),
    ('Nonfinite JSON constant refused: ', 'JSON_NONFINITE'),
)


def refusal_code(error):
    """Return only fixed labels; never format unknown exception text or fields."""
    message = error.strerror if isinstance(error, OSError) else error.args[0] if error.args else None
    if type(message) is str:
        if message in _REFUSAL_MESSAGES:
            return _REFUSAL_MESSAGES[message]
        for prefix, code in _REFUSAL_PREFIXES:
            if message.startswith(prefix):
                return code
    if isinstance(error, subprocess.TimeoutExpired):
        return 'COMMAND_TIMEOUT'
    if isinstance(error, subprocess.CalledProcessError):
        return 'COMMAND_FAILED'
    if isinstance(error, subprocess.SubprocessError):
        return 'COMMAND_ERROR'
    if isinstance(error, FileNotFoundError):
        return 'INPUT_NOT_FOUND'
    if isinstance(error, PermissionError):
        return 'INPUT_PERMISSION'
    if isinstance(error, OSError):
        return 'INPUT_OS_ERROR'
    if isinstance(error, json.JSONDecodeError):
        return 'JSON_INVALID'
    if isinstance(error, UnicodeError):
        return 'INPUT_ENCODING'
    return 'UNCLASSIFIED_SAFE_REFUSAL'


def precheck(repository, gate_path, gate_digest, harness_source, harness_tree):
    G.require(gate_path == repository / G.ROOT_GATE, 'Exact separately tracked normal Root gate required')
    G.require(gate_path.is_file(), 'Normal Root gate absent; no collection or application work permitted')
    raw = G.owned_read(gate_path, 1_000_000)
    G.require(G.sha(raw) == gate_digest, 'Actual normal Root gate digest differs')
    gate = A.validate_gate(G.unique_json(raw))
    harness = A.verify_harness(repository, harness_source, harness_tree,
                               gate['reviewed_harness_blobs'], gate_path, gate_digest)
    expected = G.originals(repository, gate)
    return gate, harness, expected


def api(endpoint):
    result = subprocess.run(['gh', 'api', '--hostname', 'github.com', 'repos/' + G.REPOSITORY + '/' + endpoint],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    G.require(result.returncode == 0 and 0 < len(result.stdout) <= 8 * 1024 * 1024,
              'Fresh read-only normal metadata request failed')
    G.unique_json(result.stdout)
    return result.stdout


def capture(repository, gate_path, gate_digest, harness_source, harness_tree, output, stage):
    parent = G.hosted_context(repository, HERE)
    gate, harness, expected = precheck(repository, gate_path, gate_digest, harness_source, harness_tree)
    G.require(stage in ('before', 'after') and output.parent == parent and
              G.re.fullmatch('mixel-normal95-live-' + stage + '-[0-9a-f]{32}', output.name) and
              not output.exists() and not output.is_symlink(), 'Fresh direct owned normal state folder required')
    output.mkdir(mode=0o700)
    started = datetime.now(timezone.utc).isoformat()
    for name, endpoint in G.ENDPOINTS.items():
        value = api(endpoint)
        with (output / (name + '.json')).open('xb') as stream:
            stream.write(value)
    hashes = G.live_state(output, gate, expected)
    record = {'scope': G.SCOPE, 'stage': stage, 'startedUtc': started,
              'completedUtc': datetime.now(timezone.utc).isoformat(), 'freshOriginalSha256': hashes,
              'harness': harness, 'rootGateSha256': gate_digest, 'source': G.SOURCE,
              'wholeProducer': 'completed/success', 'wholePreflight': 'completed/success',
              'bothComparisonLanes': 'success', 'native95Qualified': False, 'publication': False}
    with (output / 'capture.json').open('xb') as stream:
        stream.write((json.dumps(record, indent=2) + '\n').encode())
    print('PASS: fresh normal same-HEAD whole-success facts retained; actual native95 remains mandatory.')
    return record


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'restore-git-bytes':
        parser = argparse.ArgumentParser(); parser.add_argument('command')
        parser.add_argument('--repository', type=Path, required=True)
        args = parser.parse_args()
        try:
            G.restore_git_bytes(args.repository, HERE)
            print('PASS: exact scoped normal QA Git bytes restored; HEAD and Git configuration unchanged.')
            return 0
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print('FAIL: normal Git byte restoration refused before mutation. reason=' + refusal_code(error), file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--root-gate', type=Path, required=True)
    parser.add_argument('--root-gate-sha256', required=True)
    parser.add_argument('--harness-source', required=True)
    parser.add_argument('--harness-tree', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stage', choices=('before', 'after'), required=True)
    args = parser.parse_args()
    try:
        capture(args.repository, args.root_gate, args.root_gate_sha256, args.harness_source,
                args.harness_tree, args.output, args.stage)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('FAIL: normal same-HEAD admission refused; no native qualification. reason=' + refusal_code(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
