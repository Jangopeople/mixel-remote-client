from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

import darwin_exit_adapter as adapter
from darwin_exit_observer import Identity

EXPECTED = Identity(123456, os.geteuid(), 1700000000, 123456,
                    '/private/Owned.app/Contents/MacOS/Owned',
                    '/private/Owned.app', 'ch.mixel.remote', '77')


class FakeObserver:
    def __init__(self, expected, reader):
        assert expected == reader(expected.pid) == EXPECTED
        self.polls, self.closed = 0, False
    def poll(self):
        self.polls += 1
        return self.receipt()
    def receipt(self):
        return {'diagnostic_only': True, 'qualifies_application': False,
                'state': 'pending', 'polls': self.polls}
    def close(self):
        self.closed = True


class AdapterControls(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='owned-exit-adapter-control-')
        self.path = Path(self.directory.name).resolve() / 'exit.json'
        self.calls, self.created = [], []
        calls = self.calls
        class Evidence:
            pid = EXPECTED.pid
            phase = 'ordinary-gui-launch'
            clock = time.monotonic()
            def mark(self, event, **fields):
                calls.append(('mark', self, event, fields))
                return fields.get('return_value')
            def request(self, helper, action, *arguments):
                calls.append(('request', self, helper, action, arguments))
                self.mark('original-nested-request-mark')
                if isinstance(helper, Exception):
                    raise helper
                return helper
        self.module = SimpleNamespace(RuntimeEvidence=Evidence)
        self.originals = (Evidence.mark, Evidence.request)
        self.evidence = Evidence()
    def tearDown(self):
        self.assertEqual(self.originals, (self.module.RuntimeEvidence.mark,
                                         self.module.RuntimeEvidence.request))
        self.directory.cleanup()
    def factory(self, expected, reader):
        observer = FakeObserver(expected, reader)
        self.created.append(observer)
        return observer
    def install(self, **kwargs):
        return adapter.install(self.module, self.path,
                               identity_reader=lambda evidence, pid: EXPECTED,
                               observer_factory=kwargs.get('observer_factory', self.factory))
    def receipt(self):
        return json.loads(self.path.read_text())
    def test_original_calls_arguments_results_and_nested_mark_are_unchanged(self):
        returned = object()
        with self.install():
            self.assertIs(self.evidence.mark('owned-ordinary-application-launched',
                                            return_value=returned), returned)
            self.assertIs(self.evidence.request(returned, 'launch', 'arg1', 'arg2'), returned)
        self.assertEqual([v[0] for v in self.calls], ['mark', 'request', 'mark'])
        self.assertEqual(self.calls[1][2:], (returned, 'launch', ('arg1', 'arg2')))
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.created[0].polls, 5)
        self.assertTrue(self.created[0].closed)
        self.assertTrue(self.receipt()['original_methods_restored'])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
    def test_same_exception_object_passes_through_and_methods_restored(self):
        original_error = RuntimeError('synthetic original failure')
        with self.assertRaises(RuntimeError) as caught:
            with self.install():
                self.evidence.mark('owned-ordinary-application-launched')
                self.evidence.request(original_error, 'launch', 'not-recorded')
        self.assertIs(caught.exception, original_error)
        self.assertTrue(self.receipt()['original_methods_restored'])
    def test_registration_failure_is_diagnostic_and_does_not_replace_result(self):
        def broken(expected, reader):
            raise OSError('synthetic secret must not be copied')
        with self.install(observer_factory=broken):
            self.evidence.mark('owned-ordinary-application-launched')
            self.assertEqual(self.evidence.request(17, 'launch'), 17)
        receipt = self.receipt()
        self.assertEqual(receipt['errors'],
                         [{'stage': 'register-owned-ordinary-PID', 'error_type': 'OSError'}])
        self.assertNotIn('secret', self.path.read_text())
    def test_poll_failure_is_diagnostic_and_original_exception_is_unchanged(self):
        class Broken(FakeObserver):
            def poll(self):
                raise ValueError('fixture poll failure')
        failure = RuntimeError('original')
        with self.assertRaises(RuntimeError) as caught:
            with self.install(observer_factory=Broken):
                self.evidence.mark('owned-ordinary-application-launched')
                self.evidence.request(failure, 'launch')
        self.assertIs(caught.exception, failure)
        self.assertEqual(len(self.receipt()['errors']), 1)
        self.assertEqual(self.receipt()['errors'][0]['error_type'], 'ValueError')
    def test_duplicate_mark_does_not_attach_another_observer(self):
        with self.install():
            self.evidence.mark('owned-ordinary-application-launched')
            self.evidence.mark('owned-ordinary-application-launched')
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.receipt()['errors'][0]['stage'], 'duplicate-ordinary-mark')
    def test_existing_output_is_refused_before_hooks_change(self):
        self.path.write_text('preserve')
        with self.assertRaises(FileExistsError):
            with self.install():
                self.fail('body must not run')
        self.assertEqual(self.path.read_text(), 'preserve')
    def test_original_mark_exception_not_replaced(self):
        original_error = ValueError('original mark')
        original = self.module.RuntimeEvidence.mark
        def failing_mark(self, event, **fields):
            raise original_error
        self.module.RuntimeEvidence.mark = failing_mark
        try:
            with self.assertRaises(ValueError) as caught:
                with self.install():
                    self.evidence.mark('owned-ordinary-application-launched')
            self.assertIs(caught.exception, original_error)
            self.assertIs(self.module.RuntimeEvidence.mark, failing_mark)
            self.assertEqual(self.receipt()['observers'], [])
        finally:
            self.module.RuntimeEvidence.mark = original
    def test_body_exception_and_no_registration_keep_original_result(self):
        original_error = RuntimeError('body error')
        with self.assertRaises(RuntimeError) as caught:
            with self.install():
                raise original_error
        self.assertIs(caught.exception, original_error)
        self.assertEqual(self.receipt()['observers'], [])


    def run_phase_exit(self, exit_phase, following_phase):
        evidence = self.evidence
        class PhaseExit(FakeObserver):
            exited = False
            def poll(self):
                self.polls += 1
                if evidence.phase == exit_phase:
                    self.exited = True
                return self.receipt()
            def receipt(self):
                return {'diagnostic_only': True, 'qualifies_application': False,
                        'state': 'exit_observed' if self.exited else 'pending',
                        'raw_events': [{'fflags': 2248146944, 'data': 15}]
                        if self.exited else []}
        with self.install(observer_factory=PhaseExit):
            evidence.mark('owned-ordinary-application-launched')
            evidence.phase = exit_phase
            self.assertEqual(evidence.request(77, 'launch', 'synthetic-argument'), 77)
            evidence.phase = following_phase
            evidence.mark('cleanup-result')
        receipt = self.receipt()['observers'][0]
        first = receipt['first_exit_observation']
        self.assertEqual(first['phase'], exit_phase)
        self.assertEqual(first['sampling_stage'], 'before-request')
        self.assertIsNone(first['current_mark'])
        self.assertGreaterEqual(first['elapsed_seconds'], 0)
        self.assertIsNotNone(datetime.fromisoformat(first['utc']).tzinfo)
        self.assertEqual(receipt['raw_events'], [{'fflags': 2248146944, 'data': 15}])
        self.assertEqual([call[0] for call in self.calls], ['mark', 'request', 'mark', 'mark'])
        return first
    def test_first_exit_during_warm_is_not_overwritten_by_owned_cleanup(self):
        self.run_phase_exit('fresh-warm-url-launch', 'ordinary-to-warm-owned-cleanup')
    def test_first_exit_during_cleanup_is_labeled_cleanup_not_warm(self):
        self.run_phase_exit('ordinary-to-warm-owned-cleanup', 'cold-url-launch')
    def test_current_mark_is_allowlisted_and_invalid_clock_is_not_invented(self):
        class ExitOnSecondPoll(FakeObserver):
            def receipt(self):
                return {'state': 'exit_observed' if self.polls >= 2 else 'pending'}
        self.evidence.clock = float('nan')
        with self.install(observer_factory=ExitOnSecondPoll):
            self.evidence.mark('owned-ordinary-application-launched')
            self.evidence.phase = 'unknown-phase-containing-a-synthetic-secret'
            self.evidence.mark('unknown-mark-containing-a-synthetic-secret')
            self.evidence.phase = 'owned-cleanup'
            self.evidence.mark('cleanup-result')
        first = self.receipt()['observers'][0]['first_exit_observation']
        self.assertEqual(first['sampling_stage'], 'mark')
        self.assertEqual(first['current_mark'], 'unlisted-mark')
        self.assertEqual(first['phase'], 'unlisted-phase')
        self.assertIsNone(first['elapsed_seconds'])
        self.assertNotIn('synthetic-secret', self.path.read_text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
