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
        except (OSError, ValueError, subprocess.SubprocessError):
            print('FAIL: normal Git byte restoration refused before mutation.', file=sys.stderr)
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
    except (OSError, ValueError, subprocess.SubprocessError):
        print('FAIL: normal same-HEAD admission refused; no native qualification.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
