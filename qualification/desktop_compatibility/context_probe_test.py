"""Qualification must exercise the requested toolkit, never a substitute."""
import os
import unittest
from unittest.mock import Mock, patch

from qualification.desktop_compatibility import context_probe


class ContextSelectionTests(unittest.TestCase):
    def test_chromium_cannot_silently_run_the_terminal_probe(self):
        bridge = Mock()
        with patch.dict(os.environ, {'FLOE_PROBE_CONTEXT_TOOLKIT': 'chromium'}), \
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
