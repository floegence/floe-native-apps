"""A failed portal preparation must not strand an instance before launch."""
import importlib
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


class PortalPreparationTests(unittest.TestCase):
    def test_missing_backend_description_does_not_leave_partial_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            instance = root / 'instance'
            instance.mkdir(mode=0o700)
            tools = SimpleNamespace(component=root / 'missing-component')
            with patch.dict('sys.modules', {'gi.repository': SimpleNamespace(Gio=None, GLib=None)}):
                module = importlib.import_module('qualification.desktop_compatibility.portal_probe')
            with self.assertRaises(FileNotFoundError):
                module.start_portals(instance, 'unix:path=/private/bus', Path('/private/wayland'),
                                     None, None, None, tools=tools)
            self.assertEqual(list(instance.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
