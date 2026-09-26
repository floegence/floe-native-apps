"""Durable helper process results must retain the supervisor's sole authority."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class SessionTests(unittest.TestCase):
    def setUp(self):
        repository = ModuleType('gi.repository')
        repository.Gio, repository.GLib = SimpleNamespace(), SimpleNamespace()
        spec = importlib.util.spec_from_file_location('session_fixture', Path(__file__).with_name('desktop_session.py'))
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', {'gi': ModuleType('gi'), 'gi.repository': repository}):
            spec.loader.exec_module(self.module)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'application.json'

    def write(self, value):
        self.path.write_text(json.dumps(value))
        self.path.chmod(0o600)

    def test_preserve_authoritative_launch_failure(self):
        value = {'state': 'failed', 'phase': 'host_services', 'error_code': 'APPLICATION_HOST_SERVICE_UNAVAILABLE'}
        self.write(value)
        self.assertEqual(self.module.read_application_result(self.path, 1), value)

    def test_preserve_launcher_exit_and_explicit_termination(self):
        for code in (0, 46, -9):
            value = {'state': 'exited', 'phase': 'process_exit', 'exit_code': code,
                     'launchers': [{'pid': 4102, 'exit_code': code}], 'termination_requested': code == -9}
            self.write(value)
            self.assertEqual(self.module.read_application_result(self.path, 128 - code if code < 0 else code), value)

    def test_missing_partial_or_inconsistent_receipt_is_not_application_exit(self):
        reader = self.module.read_application_result
        with self.assertRaises((ValueError, OSError)):
            reader(self.path, 0)
        for value in ({'state': 'running', 'phase': 'spawn', 'launcher_pids': [100]},
                      {'state': 'exited', 'phase': 'process_exit', 'exit_code': 46,
                       'launchers': [{'pid': 100, 'exit_code': 46}], 'termination_requested': False},
                      {'state': 'failed', 'phase': 'spawn', 'error_code': 'unexpected user content'},
                      {'state': 'failed', 'phase': 'spawn', 'error_code': 'APPLICATION_LAUNCH_FAILED', 'text': 'private'},
                      {'state': 'exited', 'phase': 'process_exit', 'exit_code': False,
                       'launchers': [], 'termination_requested': False}):
            self.write(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                reader(self.path, 0)

    def test_nonzero_exit_before_any_native_window_is_a_startup_failure(self):
        value = {'state': 'exited', 'phase': 'process_exit', 'exit_code': 46,
                 'launchers': [{'pid': 4102, 'exit_code': 46}], 'termination_requested': False}
        self.write(value)
        records = []
        session = self.module.DesktopSession.__new__(self.module.DesktopSession)
        session.application_receipt, session.record = self.path, records.append
        session.helper = SimpleNamespace(native=SimpleNamespace(last_window=0))
        session.application_exited(46)
        self.assertEqual(records, [{**value, 'state': 'failed', 'phase': 'application_start',
                                    'error_code': 'APPLICATION_LAUNCHER_EXITED'}])
        session.helper.native.last_window = 1
        session.application_exited(46)
        self.assertEqual(records[-1], value)

    def test_native_launch_plan_does_not_require_document_portal_or_fuse(self):
        session = self.module.DesktopSession.__new__(self.module.DesktopSession)
        session.plan = {'observation': {'services': []}}
        session.transition, session.launch_application = Mock(), Mock()
        session.services, session.bus_address = Mock(), 'unix:path=/private/bus'
        session.application_environment = {'XDG_RUNTIME_DIR': '/private', 'WAYLAND_DISPLAY': 'wayland-0'}
        session.spawn, session.service = Mock(), Mock()
        with patch.object(self.module, 'DesktopPortals', side_effect=OSError('No document/FUSE support')) as portals:
            session.start_portals()
        portals.assert_not_called()
        session.launch_application.assert_called_once()
        session.spawn.assert_not_called()

    def test_scope_requires_an_explicit_distinct_host_bus_before_creating_resources(self):
        plan = {'backend': {}, 'observation': {'services': ['user-systemd-scope']}}
        graphics = SimpleNamespace(application_environment={'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/private/bus'})
        for address in (None, 'unix:path=/private/bus', 'tcp:host=localhost'):
            with self.subTest(address=address), patch.object(self.module, 'revalidate', return_value=plan):
                with self.assertRaises(ValueError):
                    self.module.DesktopSession(self.directory.name, self.directory.name, 'fixture', 'token',
                        Mock(), graphics, ibus_command=(), plan=plan, application_launcher=(),
                        application_environment={'FLOE_NATIVE_HOST_BUS': 'unix:path=/ambient/host'},
                        completed=Mock(), record=Mock(), host_bus=address)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_dead_bus_cannot_interrupt_support_disposal(self):
        session = self.module.DesktopSession.__new__(self.module.DesktopSession)
        completed, tree, process = Mock(), Mock(), Mock()
        connection = Mock()
        connection.is_closed.return_value = True
        connection.close_sync.side_effect = RuntimeError('Bus already closed')
        session.closed, session.processes = False, {'compositor': process}
        session.timeout, session.kill_timeout, session.watches = None, None, {}
        session.loop, session.helper, session.capture = Mock(), Mock(), None
        session.connection, session.peers, session.tree = connection, [], tree
        session.completed = completed
        session.close()
        connection.close_sync.assert_not_called()
        tree.close.assert_called_once()
        process.send_signal.assert_called_once_with(self.module.signal.SIGTERM)
        completed.assert_not_called()
        session.processes.clear()
        session.finish()
        completed.assert_called_once()

    def test_receipt_cannot_follow_links_or_read_unbounded_or_public_file(self):
        reader = self.module.read_application_result
        target = self.path.with_name('other.json')
        target.write_text('{}')
        self.path.symlink_to(target)
        with self.assertRaises((ValueError, OSError)):
            reader(self.path, 0)
        self.path.unlink()
        self.path.write_bytes(b' ' * 65537)
        self.path.chmod(0o600)
        with self.assertRaises(ValueError):
            reader(self.path, 0)
        self.path.write_text('{}')
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            reader(self.path, 0)


if __name__ == '__main__':
    unittest.main()
