"""Regressions for the prototype being replaced by prepared graphics resources."""
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from desktop_graphics import DesktopGraphics, prepare_x11_socket_directory


class DesktopGraphicsTests(unittest.TestCase):
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
        self.library = self.shell.parent / 'libweston-14.so.0'
        self.library.write_bytes(b'fixture library')
        self.xwayland = self.shell.parent / 'xwayland.so'
        self.xwayland.write_bytes(b'fixture XWM')
        self.environment = {'WAYLAND_DISPLAY': 'wayland-private', 'XDG_RUNTIME_DIR': str(self.instance),
                            'LD_PRELOAD': '/host/foreign.so', 'LD_LIBRARY_PATH': '/host/libraries',
                            'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/private/bus'}
        self.socket_preparation = patch('desktop_graphics.prepare_x11_socket_directory')
        self.socket_preparation.start()
        self.addCleanup(self.socket_preparation.stop)

    def test_headless_host_prepares_only_the_missing_standard_socket_directory(self):
        directory = self.root / '.X11-unix'
        prepare_x11_socket_directory(directory)
        self.assertEqual(directory.stat().st_mode & 0o7777, 0o1777)
        marker = directory / 'X99'
        marker.write_text('existing session')
        prepare_x11_socket_directory(directory)
        self.assertEqual(marker.read_text(), 'existing session')

    def test_socket_directory_rejects_links_and_unsafe_existing_permissions(self):
        directory = self.root / '.X11-unix'
        directory.symlink_to(self.instance, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            prepare_x11_socket_directory(directory)
        directory.unlink()
        directory.mkdir(mode=0o777)
        with self.assertRaises(ValueError):
            prepare_x11_socket_directory(directory)

    def prepare(self, **options):
        with patch('subprocess.check_output', return_value='weston 14.0.2\n'):
            return DesktopGraphics(self.component, self.instance, self.environment, shell=self.shell,
                capture=self.shell.parent / 'frame-probe', library=self.library, xwayland=self.xwayland, **options)

    def test_preparing_again_must_not_rewrite_a_live_instance(self):
        self.prepare()
        resource = self.instance / 'desktop-graphics/Xwayland'
        resource.write_bytes(b'existing live resource')
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(resource.read_bytes(), b'existing live resource')

    def test_support_process_environment_must_not_inherit_host_loader_overrides(self):
        environment = self.prepare().environment
        self.assertNotIn('LD_PRELOAD', environment)
        self.assertNotIn('LD_LIBRARY_PATH', environment)
        self.assertEqual(self.environment['LD_PRELOAD'], '/host/foreign.so')

    def test_original_component_and_application_environment_remain_unchanged(self):
        original = {path.relative_to(self.component): path.read_bytes()
                    for path in self.component.rglob('*') if path.is_file()}
        environment = dict(self.environment)
        graphics = self.prepare()
        self.assertEqual(original, {path.relative_to(self.component): path.read_bytes()
                                  for path in self.component.rglob('*') if path.is_file()})
        self.assertEqual(self.environment, environment)
        self.assertEqual((graphics.private / 'Xwayland').stat().st_size,
                         (self.component / 'usr/bin/Xwayland').stat().st_size)
        self.assertEqual((graphics.private / 'Xwayland').read_bytes().count(b'/usr/bin\0'), 0)
        self.assertIn('--renderer=pixman', graphics.command)

    def test_failed_generator_cleans_only_new_resources_and_allows_fresh_preparation(self):
        marker = self.instance / 'application-document'
        marker.write_text('user work')
        original = self.component / 'usr/bin/Xwayland'
        data = original.read_bytes()
        original.write_bytes(b'unknown source layout')
        with self.assertRaisesRegex(ValueError, 'compiler path'):
            self.prepare()
        self.assertEqual(list(self.instance.iterdir()), [marker])
        original.write_bytes(data)
        self.prepare()
        self.assertEqual(marker.read_text(), 'user work')

    def test_native_version_timeout_leaves_no_partial_preparation(self):
        with patch('subprocess.check_output', side_effect=subprocess.TimeoutExpired('weston', 10)):
            with self.assertRaises(subprocess.TimeoutExpired):
                DesktopGraphics(self.component, self.instance, self.environment, shell=self.shell,
                    capture=self.shell.parent / 'frame-probe', library=self.library, xwayland=self.xwayland)
        self.assertEqual(list(self.instance.iterdir()), [])
        self.prepare()

    def test_authority_creation_never_overwrites_an_existing_sandbox_resource(self):
        authority = self.root / 'existing-authority'
        authority.write_bytes(b'current authorization')
        with self.assertRaises(FileExistsError):
            self.prepare(authentication=authority)
        self.assertEqual(authority.read_bytes(), b'current authorization')
        self.assertEqual(list(self.instance.iterdir()), [])

    def test_authorization_is_one_shot_and_keeps_support_overrides_out_of_the_application(self):
        graphics = self.prepare()
        observed = []
        def run(command, **options):
            observed.append((command, options))
        with patch('subprocess.run', side_effect=run):
            application = graphics.authorize(':94')
            with self.assertRaisesRegex(ValueError, 'already consumed'):
                graphics.authorize(':95')
        self.assertEqual(application['LD_PRELOAD'], self.environment['LD_PRELOAD'])
        self.assertNotIn('WESTON_MODULE_MAP', application)
        self.assertEqual(application['DISPLAY'], ':94')
        self.assertNotIn('XAUTHORITY', self.environment)
        self.assertEqual(len(observed), 1)
        command, options = observed[0]
        self.assertNotIn('LD_PRELOAD', options['env'])
        self.assertNotIn('MIT-MAGIC-COOKIE-1', ' '.join(command))
        self.assertRegex(options['input'], r'^add :94 MIT-MAGIC-COOKIE-1 [a-f0-9]{32}\n$')
        self.assertEqual(options['timeout'], 10)

    def test_disposal_removes_the_authority_created_by_atomic_xauth_update(self):
        graphics = self.prepare()
        retained = graphics.authentication.with_name('retained-initial-inode')
        def update(*args, **kwargs):
            graphics.authentication.rename(retained)
            graphics.authentication.write_bytes(b'new private authorization')
            graphics.authentication.chmod(0o600)
        with patch('subprocess.run', side_effect=update):
            graphics.authorize(':94')
        graphics.close()
        graphics.close()
        self.assertFalse(graphics.authentication.exists())
        self.assertTrue(retained.exists())

    def test_disposal_preserves_a_replacement_authority(self):
        graphics = self.prepare()
        graphics.authentication.rename(graphics.authentication.with_name('retained-owned-authority'))
        graphics.authentication.write_bytes(b'other owner')
        graphics.close()
        self.assertEqual(graphics.authentication.read_bytes(), b'other owner')

    def test_replaced_authority_is_rejected_before_executing_a_tool(self):
        graphics = self.prepare()
        # Retain the original inode so the fixture cannot accidentally reuse it.
        graphics.authentication.rename(graphics.private / 'original-authority')
        graphics.authentication.write_text('foreign owner')
        with patch('subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, 'replaced'):
                graphics.authorize(':94')
            run.assert_not_called()
        self.assertEqual(graphics.authentication.read_text(), 'foreign owner')

    def test_unknown_or_escaping_native_resources_never_create_a_prepared_instance(self):
        source = self.component / 'usr/bin/Xwayland'
        source.unlink()
        source.symlink_to(self.shell)
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.prepare()
        self.assertEqual(list(self.instance.iterdir()), [])
        source.unlink()
        source.write_bytes(b'reviewed:/usr/bin\0')
        self.xwayland.unlink()
        with self.assertRaises(FileNotFoundError):
            self.prepare()
        self.assertEqual(list(self.instance.iterdir()), [])

    def test_shared_runtime_or_existing_display_cannot_be_claimed(self):
        self.instance.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'Private graphics'):
            self.prepare()
        self.instance.chmod(0o700)
        display = self.instance / self.environment['WAYLAND_DISPLAY']
        display.write_text('another live endpoint')
        with self.assertRaisesRegex(FileExistsError, 'endpoint already exists'):
            self.prepare()
        self.assertEqual(list(self.instance.iterdir()), [display])
        self.assertEqual(display.read_text(), 'another live endpoint')


if __name__ == '__main__':
    unittest.main()
