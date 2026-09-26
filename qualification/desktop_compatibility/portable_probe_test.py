"""Regressions for the prototype being replaced by prepared graphics resources."""
import os
from pathlib import Path
import platform
import tempfile
import unittest
from unittest.mock import patch

from qualification.desktop_compatibility.portable_probe import prepare


class PortablePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.component, self.instance = self.root / 'component', self.root / 'instance'
        self.component.mkdir()
        self.instance.mkdir(mode=0o700)
        architecture = 'aarch64' if platform.machine() in ('aarch64', 'arm64') else 'x86_64'
        for name in ('lib/ld-musl-' + architecture + '.so.1', 'usr/bin/weston', 'usr/bin/Xwayland',
                     'usr/bin/xkbcomp', 'usr/bin/xauth', 'usr/lib/libweston-14/headless-backend.so',
                     'usr/lib/libweston-14/xwayland.so', 'usr/share/X11/xkb/rules/evdev',
                     *('usr/share/weston/' + name for name in
                       ('icon_window.png', 'sign_close.png', 'sign_maximize.png', 'sign_minimize.png'))):
            path = self.component / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'reviewed-fixture:/usr/bin\0')
        self.shell = self.root / 'native/probe-shell.so'
        self.shell.parent.mkdir()
        self.shell.write_bytes(b'fixture shell')
        (self.shell.parent / 'frame-probe').write_bytes(b'fixture capture')
        self.environment = {'WAYLAND_DISPLAY': 'wayland-private', 'XDG_RUNTIME_DIR': str(self.instance),
                            'LD_PRELOAD': '/host/foreign.so', 'LD_LIBRARY_PATH': '/host/libraries',
                            'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/private/bus'}

    def prepare(self):
        with patch('subprocess.check_output', return_value='weston 14.0.2\n'):
            return prepare(self.component, self.instance, self.environment, self.shell)

    def test_preparing_again_must_not_rewrite_a_live_instance(self):
        self.prepare()
        resource = self.instance / 'Xwayland'
        resource.write_bytes(b'existing live resource')
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(resource.read_bytes(), b'existing live resource')

    def test_support_process_environment_must_not_inherit_host_loader_overrides(self):
        _command, environment, _capture, _authorize, _record = self.prepare()
        self.assertNotIn('LD_PRELOAD', environment)
        self.assertNotIn('LD_LIBRARY_PATH', environment)
        self.assertEqual(self.environment['LD_PRELOAD'], '/host/foreign.so')


if __name__ == '__main__':
    unittest.main()
