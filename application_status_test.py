"""Supervisor status contract without requiring a display or GIO installation.

Only the platform spawn boundary is faked. The native qualification separately
executes a real GIO desktop entry and asserts the supervisor's OS exit status.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import application_processes


class LaunchContext:
    def setenv(self, _name, _value):
        pass

    def unsetenv(self, _name):
        pass


class Desktop:
    def should_show(self):
        return True

    def get_boolean(self, _name):
        return False

    def launch_uris_as_manager(self, _uris, _context, _flags, _setup, _data, started, _pid_data):
        started(self, 123, None)
        return True


class StatusTests(unittest.TestCase):
    def setUp(self):
        gi = ModuleType('gi')
        gi.require_version = lambda *_: None
        repository = ModuleType('gi.repository')
        repository.Gio = SimpleNamespace(AppLaunchContext=SimpleNamespace(new=LaunchContext))
        repository.GLib = SimpleNamespace(SpawnFlags=SimpleNamespace(SEARCH_PATH=1, DO_NOT_REAP_CHILD=2))
        spec = importlib.util.spec_from_file_location('application_status_fixture', Path(__file__).with_name('application.py'))
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', {'gi': gi, 'gi.repository': repository}):
            spec.loader.exec_module(self.module)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.receipt = Path(self.directory.name) / 'receipt.json'
        for item in (
            patch.object(self.module.ctypes, 'CDLL', return_value=SimpleNamespace(prctl=lambda *_: 0)),
            patch.object(self.module.os, 'pidfd_open', return_value=42, create=True),
            patch.object(self.module.os, 'close'),
            patch.object(self.module.signal, 'signal'),
            patch.dict(self.module.os.environ, {}, clear=True),
            patch.object(application_processes, 'identity', return_value=(self.module.os.getpid(), 10)),
            patch.object(self.module.os, 'P_ALL', 0, create=True),
            patch.object(self.module.os, 'WEXITED', 4, create=True),
            patch.object(self.module.os, 'WNOWAIT', 0x1000000, create=True),
        ):
            item.start()
            self.addCleanup(item.stop)

    def run_launcher(self, statuses):
        remaining = iter(statuses)
        def waitid(*_args):
            # The process tree must remain running even after its direct
            # wrapper exits; only the final ECHILD permits an ended receipt.
            self.assertEqual(json.loads(self.receipt.read_text())['state'], 'running')
            try:
                return SimpleNamespace(si_pid=next(remaining)[0])
            except StopIteration:
                raise ChildProcessError from None
        with patch.object(self.module.os, 'waitid', side_effect=waitid, create=True), \
             patch.object(self.module.os, 'waitpid', side_effect=statuses):
            code = self.module.launch(Desktop(), str(self.receipt))
        return code, json.loads(self.receipt.read_text())

    def test_failed_launcher_status_survives_successful_descendant_exit(self):
        code, receipt = self.run_launcher([(123, 46 << 8), (124, 0)])
        self.assertEqual(code, 46)
        self.assertEqual(receipt['exit_code'], 46)
        self.assertEqual(receipt['phase'], 'process_exit')
        self.assertEqual(receipt['launchers'], [{'pid': 123, 'exit_code': 46}])

    def test_successful_wrapper_waits_for_all_adopted_children(self):
        code, receipt = self.run_launcher([(123, 0), (124, 0), (125, 0)])
        self.assertEqual(code, 0)
        self.assertEqual(receipt['state'], 'exited')
        self.assertFalse(receipt['termination_requested'])

    def test_signal_remains_distinct_from_an_application_exit_code(self):
        code, receipt = self.run_launcher([(123, 11)])
        self.assertEqual(code, 139)
        self.assertEqual(receipt['exit_code'], -11)

    def test_reused_pid_cannot_overwrite_original_launcher_status(self):
        code, receipt = self.run_launcher([(123, 46 << 8), (123, 0)])
        self.assertEqual(code, 46)
        self.assertEqual(receipt['launchers'], [{'pid': 123, 'exit_code': 46}])

    def test_spawn_failure_has_a_stable_phase_and_error(self):
        with self.assertRaises(ValueError):
            self.module.launch(None, str(self.receipt))
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt, {'state': 'failed', 'phase': 'spawn',
                                   'error_code': 'APPLICATION_LAUNCH_FAILED'})


if __name__ == '__main__':
    unittest.main()
