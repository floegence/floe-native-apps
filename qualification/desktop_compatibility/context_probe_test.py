"""Qualification must exercise the requested toolkit, never a substitute."""
import os
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from qualification.desktop_compatibility import context_probe
from qualification.desktop_compatibility.source_proof import record_sources


class ContextSelectionTests(unittest.TestCase):
    def test_stale_native_driver_cannot_claim_the_current_source_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            driver = root / 'probe_editors.py'
            driver.write_text('QT_IM_MODULE=floe-client-native\n')
            manifest = {'kind': 'floe-desktop-qualification-source-v1', 'commit': 'reviewed-source',
                'modified': False, 'files': {driver.name: {
                    'source': 'qualification/desktop_compatibility/probe_editors.py',
                    'sha256': hashlib.sha256(driver.read_bytes()).hexdigest()}}}
            (root / 'source-manifest.json').write_text(json.dumps(manifest))
            proof = record_sources(root)
            self.assertEqual(proof['files'], 1)
            self.assertEqual(proof['commit'], 'reviewed-source')
            driver.write_text('QT_IM_MODULE=floe-client-wayland\n')
            with self.assertRaisesRegex(ValueError, 'source snapshot differs: probe_editors.py'):
                record_sources(root)

    def test_unknown_toolkit_cannot_silently_run_the_terminal_probe(self):
        bridge = Mock()
        with patch.dict(os.environ, {'FLOE_PROBE_CONTEXT_TOOLKIT': 'unsupported'}), \
                patch.dict('sys.modules', {'gi': Mock(), 'gi.repository': Mock(), 'xim_context_probe': bridge}):
            with self.assertRaisesRegex(ValueError, 'Unsupported native context toolkit'):
                context_probe.qualify(*([None] * 8))
        bridge.qualify.assert_not_called()

    def test_terminal_requires_an_x11_display(self):
        bridge = Mock()
        with patch.dict(os.environ, {'FLOE_PROBE_CONTEXT_TOOLKIT': 'terminal'}), \
                patch.dict('sys.modules', {'gi': Mock(), 'gi.repository': Mock(), 'xim_context_probe': bridge}):
            with self.assertRaisesRegex(ValueError, 'XIM qualification requires an X11 display'):
                context_probe.qualify(*([None] * 8))
        bridge.qualify.assert_not_called()

    def test_terminal_is_explicitly_dispatched_to_xim(self):
        bridge = Mock()
        with patch.dict(os.environ, {'FLOE_PROBE_CONTEXT_TOOLKIT': 'terminal'}), \
                patch.dict('sys.modules', {'gi': Mock(), 'gi.repository': Mock(), 'xim_context_probe': bridge}):
            result = context_probe.qualify(*([None] * 8), display=':42')
        bridge.qualify.assert_called_once_with(*([None] * 8), ':42')
        self.assertIs(result, bridge.qualify.return_value)


if __name__ == '__main__':
    unittest.main()
