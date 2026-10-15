#!/usr/bin/env python3
"""Exact two-hunk inverse of the private current77 ARM diagnostic carrier."""
import hashlib

HISTORICAL_SHA = "398579cbf8d089b9e193a78d7d867f94f9786dc219d0c1616ba263868bd100a0"
ADDED_CONTROL = '          python3 -B scripts/test-diagnose-current77-arm-warm-exit.py\n'
ADDED_JOB = '  current77-arm-warmexit-private:\n    if: github.repository == \'Jangopeople/mixel-remote-client\' && github.event_name == \'workflow_dispatch\' && github.ref == \'refs/heads/codex/current77-arm-warmexit\' && github.run_attempt == 1\n    needs: static-controls\n    runs-on: macos-14\n    timeout-minutes: 20\n    steps:\n      - name: Check out the reviewed private replay source\n        uses: actions/checkout@v5\n        with:\n          ref: ${{ github.sha }}\n          path: diagnostic-source\n          persist-credentials: false\n      - name: Check out exact current77 producer helpers\n        uses: actions/checkout@v5\n        with:\n          ref: 77a0acf1b0af2070c7d5d2d1fb172a343882dfc0\n          path: producer-source\n          persist-credentials: false\n      - name: Recheck complete nonnative current77 controls\n        working-directory: diagnostic-source\n        run: |\n          set -euo pipefail\n          OBSERVER_CONTROLS="$RUNNER_TEMP/current77-exit-observer-controls"\n          mkdir "$OBSERVER_CONTROLS"\n          cp scripts/darwin_exit_observer.py scripts/darwin_exit_adapter.py \\\n            scripts/test_darwin_exit_observer.py scripts/test_darwin_exit_adapter.py "$OBSERVER_CONTROLS/"\n          python3 -B "$OBSERVER_CONTROLS/test_darwin_exit_observer.py"\n          python3 -B "$OBSERVER_CONTROLS/test_darwin_exit_adapter.py"\n          python3 -B scripts/test-diagnose-current77-arm-warm-exit.py\n      - name: Replay exact failed ARM signed app privately\n        env:\n          GH_TOKEN: ${{ github.token }}\n          PYTHONUTF8: \'1\'\n          PYTHONIOENCODING: utf-8\n        run: |\n          set -euo pipefail\n          python3 -B "$GITHUB_WORKSPACE/diagnostic-source/scripts/diagnose-current77-arm-warm-exit.py" \\\n            --diagnostic-checkout "$GITHUB_WORKSPACE/diagnostic-source" \\\n            --producer-checkout "$GITHUB_WORKSPACE/producer-source" \\\n            --gate "$GITHUB_WORKSPACE/diagnostic-source/verification/current77-arm-private-replay-gate.json" \\\n            --destination "$RUNNER_TEMP/current77-arm-private-warmexit" \\\n            --safe-output "$GITHUB_WORKSPACE/current77-arm-safe-facts"\n      - name: Retain only content-free private warm-exit facts\n        if: always()\n        uses: actions/upload-artifact@v6\n        with:\n          name: current77-private-arm-warmexit-safe-facts\n          path: |\n            current77-arm-safe-facts/validation-receipt.json\n            current77-arm-safe-facts/warm-exit-observer-safe-facts.json\n          if-no-files-found: warn\n          include-hidden-files: false\n          retention-days: 7\n'


def historical_workflow(text):
    if not isinstance(text, str):
        raise ValueError("Workflow must be text")
    if hashlib.sha256(text.encode()).hexdigest() == HISTORICAL_SHA:
        return text
    if text.count(ADDED_JOB) != 1:
        raise ValueError("Exact current77 private carrier job changed")
    anchor = "          python3 -B scripts/test-support-launch-macos-tray-controls.py\n"
    pair = anchor + ADDED_CONTROL
    if text.count(pair) != 1:
        raise ValueError("Exact current77 static controls changed")
    projected = text.replace(pair, anchor, 1).replace("\n" + ADDED_JOB, "", 1)
    if hashlib.sha256(projected.encode()).hexdigest() != HISTORICAL_SHA:
        raise ValueError("Historical workflow has unrelated drift")
    return projected
