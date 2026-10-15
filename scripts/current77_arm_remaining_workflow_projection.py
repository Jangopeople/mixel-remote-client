#!/usr/bin/env python3
"""Exact inverse of the remaining-gates-only carrier to immutable c747 workflow."""
import hashlib
BASE_SHA = "561a113179fa036895ee6906e99e5b9a95241fb5e3c983ed77347103ac51c504"
OLD_CONTROL = '          python3 -B scripts/test-diagnose-macos-failed-app.py\n          python3 -B scripts/test-support-launch-macos-tray-controls.py\n          python3 -B scripts/test-diagnose-current77-arm-warm-exit.py\n'
NEW_CONTROL = '          python3 -B scripts/test-current77-arm-remaining-gates.py --with-legacy-controls\n'
NEW_JOB = '  current77-arm-remaining-gates-private:\n    if: github.repository == \'Jangopeople/mixel-remote-client\' && github.event_name == \'workflow_dispatch\' && github.ref == \'refs/heads/codex/current77-arm-remaining-gates-r2\' && github.run_attempt == 1\n    needs: static-controls\n    runs-on: macos-14\n    timeout-minutes: 30\n    steps:\n      - name: Check out the reviewed remaining-gates source\n        uses: actions/checkout@v5\n        with:\n          ref: ${{ github.sha }}\n          path: diagnostic-source\n          persist-credentials: false\n      - name: Check out immutable current77 producer helpers\n        uses: actions/checkout@v5\n        with:\n          ref: 77a0acf1b0af2070c7d5d2d1fb172a343882dfc0\n          path: producer-source\n          persist-credentials: false\n      - name: Require unchanged observer and complete source controls\n        working-directory: diagnostic-source\n        run: |\n          set -euo pipefail\n          OBSERVER_CONTROLS="$RUNNER_TEMP/current77-remaining-exit-observer-controls"\n          mkdir "$OBSERVER_CONTROLS"\n          cp scripts/darwin_exit_observer.py scripts/darwin_exit_adapter.py \\\n            scripts/test_darwin_exit_observer.py scripts/test_darwin_exit_adapter.py "$OBSERVER_CONTROLS/"\n          python3 -B "$OBSERVER_CONTROLS/test_darwin_exit_observer.py"\n          python3 -B "$OBSERVER_CONTROLS/test_darwin_exit_adapter.py"\n          python3 -B scripts/test-current77-arm-remaining-gates.py --with-legacy-controls\n      - name: Run the three exact original signed-app gates privately\n        env:\n          GH_TOKEN: ${{ github.token }}\n          PYTHONUTF8: \'1\'\n          PYTHONIOENCODING: utf-8\n        run: |\n          set -euo pipefail\n          python3 -B "$GITHUB_WORKSPACE/diagnostic-source/scripts/diagnose-current77-arm-remaining-gates.py" \\\n            --diagnostic-checkout "$GITHUB_WORKSPACE/diagnostic-source" \\\n            --producer-checkout "$GITHUB_WORKSPACE/producer-source" \\\n            --gate "$GITHUB_WORKSPACE/diagnostic-source/verification/current77-arm-remaining-gates-r2-private-gate.json" \\\n            --destination "$RUNNER_TEMP/current77-arm-private-remaining-gates" \\\n            --safe-output "$GITHUB_WORKSPACE/current77-arm-remaining-safe-facts"\n      - name: Retain only fixed private gate facts\n        if: always()\n        uses: actions/upload-artifact@v6\n        with:\n          name: current77-private-arm-remaining-gates-safe-facts\n          path: |\n            current77-arm-remaining-safe-facts/remaining-gates-validation-receipt.json\n            current77-arm-remaining-safe-facts/warm-exit-observer-safe-facts.json\n            current77-arm-remaining-safe-facts/post-desktop-tray-admission-safe-facts.json\n            current77-arm-remaining-safe-facts/original-tray-safe-facts.json\n            current77-arm-remaining-safe-facts/original-concurrency-safe-facts.json\n          if-no-files-found: warn\n          include-hidden-files: false\n          retention-days: 7\n'


def current_workflow(text):
    if not isinstance(text, str):
        raise ValueError("Workflow must be text")
    if hashlib.sha256(text.encode()).hexdigest() == BASE_SHA:
        return text
    if text.count(NEW_CONTROL) != 2 or text.count(NEW_JOB) != 1:
        raise ValueError("Exact remaining-gates carrier changed")
    # The native invocation is inside the exact job removed first.
    reduced = text.replace("\n" + NEW_JOB, "", 1)
    if reduced.count(NEW_CONTROL) != 1:
        raise ValueError("Exact remaining-gates static caller changed")
    reduced = reduced.replace(NEW_CONTROL, OLD_CONTROL, 1)
    if hashlib.sha256(reduced.encode()).hexdigest() != BASE_SHA:
        raise ValueError("Current c747 workflow has unrelated drift")
    return reduced
