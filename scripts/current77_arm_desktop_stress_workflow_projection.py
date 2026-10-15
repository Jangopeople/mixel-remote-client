#!/usr/bin/env python3
"""Exact inverse of the private desktop stress carrier to immutable dfe workflow."""
import hashlib

BASE_SHA = '931aec5cdecc8a3dcf9dc63cc7a693b4b9f31e7565d2b38f76da221a5c097796'
OLD_CONTROL = '          python3 -B scripts/test-current77-arm-remaining-gates.py --with-legacy-controls\n'
NEW_CONTROL = '          python3 -B scripts/test-current77-arm-desktop-stress.py --with-legacy-controls\n'
NEW_JOB = '''  current77-arm-desktop-stress-private:
    if: github.repository == 'Jangopeople/mixel-remote-client' && github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/codex/current77-arm-desktop-stress' && github.run_attempt == 1
    needs: static-controls
    runs-on: macos-14
    timeout-minutes: 30
    steps:
      - name: Check out reviewed private stress source
        uses: actions/checkout@v5
        with:
          ref: ${{ github.sha }}
          path: diagnostic-source
          persist-credentials: false
      - name: Check out exact current77 producer helpers
        uses: actions/checkout@v5
        with:
          ref: 77a0acf1b0af2070c7d5d2d1fb172a343882dfc0
          path: producer-source
          persist-credentials: false
      - name: Require unchanged exit observer and complete source controls
        working-directory: diagnostic-source
        run: |
          set -euo pipefail
          OBSERVER_CONTROLS="$RUNNER_TEMP/current77-stress-exit-observer-controls"
          mkdir "$OBSERVER_CONTROLS"
          cp scripts/darwin_exit_observer.py scripts/darwin_exit_adapter.py \\
            scripts/test_darwin_exit_observer.py scripts/test_darwin_exit_adapter.py "$OBSERVER_CONTROLS/"
          python3 -B "$OBSERVER_CONTROLS/test_darwin_exit_observer.py"
          python3 -B "$OBSERVER_CONTROLS/test_darwin_exit_adapter.py"
          python3 -B scripts/test-current77-arm-desktop-stress.py --with-legacy-controls
      - name: Run ten unchanged ordinary warm cold desktop checks
        env:
          GH_TOKEN: ${{ github.token }}
          PYTHONUTF8: '1'
          PYTHONIOENCODING: utf-8
          PYTHONDONTWRITEBYTECODE: '1'
        run: |
          set -euo pipefail
          python3 -B "$GITHUB_WORKSPACE/diagnostic-source/scripts/diagnose-current77-arm-desktop-stress.py" \\
            --diagnostic-checkout "$GITHUB_WORKSPACE/diagnostic-source" \\
            --producer-checkout "$GITHUB_WORKSPACE/producer-source" \\
            --gate "$GITHUB_WORKSPACE/diagnostic-source/verification/current77-arm-desktop-stress-private-gate.json" \\
            --destination "$RUNNER_TEMP/current77-arm-private-desktop-stress" \\
            --safe-output "$GITHUB_WORKSPACE/current77-arm-stress-safe-facts"
      - name: Retain bounded private originals and fixed stress facts
        if: always()
        uses: actions/upload-artifact@v6
        with:
          name: current77-private-arm-desktop-stress-originals
          path: |
            current77-arm-stress-safe-facts/stress-validation-receipt.json
            current77-arm-stress-safe-facts/iteration-*/warm-exit-observer-safe-facts.json
            current77-arm-stress-safe-facts/iteration-*/post-desktop-tray-admission-safe-facts.json
            current77-arm-stress-safe-facts/iteration-*/diagnostic-originals-manifest.json
            current77-arm-stress-safe-facts/iteration-*/diagnostic-originals.zip
          if-no-files-found: warn
          include-hidden-files: false
          retention-days: 7
'''


def current_workflow(text):
    if not isinstance(text, str):
        raise ValueError('Workflow must be text')
    if hashlib.sha256(text.encode('utf-8')).hexdigest() == BASE_SHA:
        return text
    if text.count(NEW_JOB) != 1 or text.count(NEW_CONTROL) != 2:
        raise ValueError('Exact private stress workflow changed')
    reduced = text.replace('\n' + NEW_JOB, '', 1)
    if reduced.count(NEW_CONTROL) != 1:
        raise ValueError('Exact stress static caller changed')
    reduced = reduced.replace(NEW_CONTROL, OLD_CONTROL, 1)
    if hashlib.sha256(reduced.encode('utf-8')).hexdigest() != BASE_SHA:
        raise ValueError('Parent workflow has unrelated drift')
    return reduced
