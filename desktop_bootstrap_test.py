"""The installed helper reads one immutable private launch specification."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from desktop_bootstrap import read_configuration, LaunchReceipt, assemble


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / 'launch.json'
        self.config = {'version': 1, 'instance': 'fixture', 'token': 'a' * 64,
            'directory': str(self.directory), 'runtime': str(self.directory),
            'environment': {'PATH': '/usr/bin:/bin'}, 'plan': {'version': 1},
            'resources': {key: '/verified/' + key for key in
                ('component', 'shell', 'capture', 'library', 'xwayland', 'ibus_daemon', 'python')},
            'host_bus': None, 'initial_documents': []}

    def write(self, value):
        self.path.write_text(json.dumps(value))
        self.path.chmod(0o600)

    def test_private_configuration_preserves_authorized_environment(self):
        self.config['environment']['CUSTOM_VALUE'] = 'unchanged '
        self.write(self.config)
        self.assertEqual(read_configuration(self.path), self.config)

    def test_unknown_versions_fields_and_ambiguous_paths_are_rejected(self):
        for change in ({'version': True}, {'version': 2}, {'unexpected': 1},
                       {'runtime': 'relative'}, {'token': 'bad'},
                       {'environment': {'INVALID=NAME': 'value'}},
                       {'environment': {'VALID': 'nul\x00'}}, {'initial_documents': ['relative']}):
            with self.subTest(change=change):
                self.write({**self.config, **change})
                with self.assertRaises(ValueError):
                    read_configuration(self.path)

    def test_symlink_public_oversized_and_duplicate_configurations_are_rejected(self):
        self.write(self.config)
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            read_configuration(self.path)
        self.path.chmod(0o600)
        self.path.write_text('{"version":1,"version":1}')
        with self.assertRaises(ValueError):
            read_configuration(self.path)
        self.path.write_bytes(b' ' * (1048576 + 1))
        with self.assertRaises(ValueError):
            read_configuration(self.path)
        self.path.unlink()
        self.path.symlink_to(self.directory / 'missing')
        with self.assertRaises((ValueError, OSError)):
            read_configuration(self.path)

    def test_receipt_is_bounded_and_never_includes_launch_secrets_or_document_data(self):
        receipt = LaunchReceipt(self.directory, 'fixture', (123, 456))
        receipt.record({'event': 'process', 'service': 'application', 'pid': 124, 'start_ticks': 457})
        receipt.record({'state': 'prepared', 'phase': 'sharing_ready',
                        'helper_pid': 123, 'helper_start_ticks': 456, 'socket': '/private/control.sock'})
        for _ in range(100):
            receipt.record({'event': 'document-result', 'method': 'Add', 'result': 'completed'})
        value = json.loads(receipt.path.read_text())
        self.assertEqual(len(value['processes']), 1)
        self.assertEqual(len(value['transitions']), 1)
        self.assertEqual(value['service_events'], [{'event': 'document-result', 'method': 'Add', 'result': 'completed'}])
        self.assertEqual(value['instance'], 'fixture')
        self.assertEqual(receipt.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            receipt.record({'state': 'failed', 'phase': 'startup', 'error_code': 'BAD', 'text': 'secret'})
        with self.assertRaises(FileExistsError):
            LaunchReceipt(self.directory, 'fixture', (123, 456))

    def test_receipt_cannot_overwrite_a_replacement(self):
        receipt = LaunchReceipt(self.directory, 'fixture', (123, 456))
        receipt.path.unlink()
        receipt.path.write_text('other owner')
        with self.assertRaises(ValueError):
            receipt.record({'state': 'starting', 'phase': 'graphics'})
        self.assertEqual(receipt.path.read_text(), 'other owner')

    def test_plan_revalidation_precedes_resource_preparation_or_execution(self):
        self.config['plan']['backend'] = {'id': 'wayland'}
        modules = {name: SimpleNamespace(**{constructor: Mock()}) for name, constructor in
            (('desktop_services', 'DesktopServices'), ('desktop_graphics', 'DesktopGraphics'),
             ('desktop_session', 'DesktopSession'))}
        modules['launch_plan'] = SimpleNamespace(revalidate=Mock(side_effect=ValueError('stale plan')))
        with patch.dict('sys.modules', modules), self.assertRaisesRegex(ValueError, 'stale plan'):
            assemble(self.config, Mock(), Mock())
        for module, constructor in (('desktop_services', 'DesktopServices'), ('desktop_graphics', 'DesktopGraphics'),
                                    ('desktop_session', 'DesktopSession')):
            getattr(modules[module], constructor).assert_not_called()


if __name__ == '__main__':
    unittest.main()
