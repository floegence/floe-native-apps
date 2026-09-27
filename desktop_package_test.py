"""Package resources must be private, immutable, and owned by one session."""
import os
from pathlib import Path
import tempfile
import unittest

from unittest.mock import patch

from desktop_package import DesktopPackageResources, snap_runtime


class PackageInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.instance, self.home, self.modules = (self.root / x for x in ('instance', 'home', 'modules'))
        for path in (self.instance, self.home, self.modules):
            path.mkdir(mode=0o700)
        plugins = self.modules / 'platforminputcontexts'
        plugins.mkdir()
        for major in (5, 6):
            (plugins / f'libfloe-client-native-qt{major}.so').write_bytes(b'verified module ' + bytes([major]))
        self.gtk = self.root / 'gtk'
        self.gtk.mkdir()
        for major in (3, 4):
            (self.gtk / f'libfloe-gtk{major}-native.so').write_bytes(b'verified GTK module ' + bytes([major]))
        self.environment = {'HOME': str(self.home), 'QT_IM_MODULE': 'host-ime',
            'QT_PLUGIN_PATH': '/host/plugins', 'CUSTOM': 'untouched',
            'GTK_IM_MODULE': 'host-ime', 'GTK_IM_MODULE_FILE': '/host/gtk.cache',
            'GTK_PATH': '/host/gtk', 'GIO_EXTRA_MODULES': '/host/gio'}
        self.package = {'kind': 'flatpak', 'id': 'org.example.Editor'}
        app = self.home / '.var/app' / self.package['id']
        app.mkdir(parents=True, mode=0o700)
        (app / 'user-document').write_text('unsaved')

    def prepare(self):
        with patch('desktop_package.snap_runtime', return_value=self.instance):
            return DesktopPackageResources(self.instance, self.instance, self.package, self.environment, self.modules, self.gtk)

    def test_flatpak_uses_only_a_unique_application_private_module_directory(self):
        before = dict(self.environment)
        first, second = self.prepare(), self.prepare()
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        self.assertEqual(self.environment, before)
        self.assertNotEqual(first.directory, second.directory)
        self.assertEqual(first.directory.parent, self.home / '.var/app/org.example.Editor')
        self.assertEqual(first.environment['QT_IM_MODULE'], 'floe-client-native')
        self.assertEqual(first.environment['FLOE_NATIVE_DESKTOP_INPUT'], 'org.example.Editor.FloeClientInput')
        self.assertEqual(first.environment['FLOE_NATIVE_FLATPAK_QT'], str(first.directory))
        self.assertEqual(first.environment['CUSTOM'], 'untouched')
        for major in (5, 6):
            name = f'platforminputcontexts/libfloe-client-native-qt{major}.so'
            self.assertEqual((first.directory / name).read_bytes(), (self.modules / name).read_bytes())
        first.close()
        self.assertFalse(first.directory.exists())
        self.assertTrue(second.directory.exists())
        self.assertEqual((second.directory.parent / 'user-document').read_text(), 'unsaved')

    def test_graphics_endpoints_are_generated_per_instance_without_host_inheritance(self):
        self.environment.update(DBUS_SESSION_BUS_ADDRESS='unix:path=/host/desktop-bus',
            XDG_RUNTIME_DIR='/host/runtime', WAYLAND_DISPLAY='wayland-user', DISPLAY=':0',
            XAUTHORITY='/host/authority', WAYLAND_SOCKET='19')
        first, second = self.prepare(), self.prepare()
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        self.assertNotEqual(first.environment['DBUS_SESSION_BUS_ADDRESS'], 'unix:path=/host/desktop-bus')
        self.assertNotEqual(first.environment['WAYLAND_DISPLAY'], second.environment['WAYLAND_DISPLAY'])
        self.assertNotIn('DISPLAY', first.environment)
        self.assertNotIn('WAYLAND_SOCKET', first.environment)
        self.assertNotIn('XAUTHORITY', first.environment)

    def test_native_keeps_host_plugin_search_after_the_private_adapter(self):
        self.package = {'kind': 'deb', 'id': 'fixture'}
        prepared = self.prepare()
        self.addCleanup(prepared.close)
        self.assertEqual(prepared.directory.parent, self.instance)
        self.assertEqual(prepared.environment['QT_PLUGIN_PATH'], str(prepared.directory) + ':/host/plugins')
        self.assertNotIn('FLOE_NATIVE_FLATPAK_QT', prepared.environment)

    def test_native_gtk_uses_private_abi_paths_without_host_ibus_or_search_paths(self):
        self.package = {'kind': 'deb', 'id': 'fixture'}
        prepared = self.prepare()
        self.addCleanup(prepared.close)
        self.assertEqual(prepared.environment['GTK_IM_MODULE'], 'floe-client-native')
        self.assertNotIn('GIO_EXTRA_MODULES', prepared.environment)
        root = Path(prepared.environment['GTK_PATH'])
        self.assertTrue(root.is_relative_to(prepared.directory))
        gtk4 = root / '4.0.0/immodules/libfloe-gtk4-native.so'
        self.assertEqual(gtk4.read_bytes(), (self.gtk / gtk4.name).read_bytes())
        cache = Path(prepared.environment['GTK_IM_MODULE_FILE']).read_text()
        self.assertIn('"floe-client-native"', cache)
        self.assertIn('libfloe-gtk3-native.so', cache)
        self.assertNotIn('gtk4', cache)
        self.assertNotIn('/host/', cache)

    def test_sandbox_gtk_uses_its_own_ibus_without_native_modules(self):
        for package in (self.package, {'kind': 'snap', 'id': 'fixture', 'confinement': 'strict'}):
            self.package = package
            prepared = self.prepare()
            self.addCleanup(prepared.close)
            self.assertEqual(prepared.environment['GTK_IM_MODULE'], 'ibus')
            for key in ('GTK_PATH', 'GTK_IM_MODULE_FILE', 'GIO_EXTRA_MODULES'):
                self.assertNotIn(key, prepared.environment)
            if prepared.directory:
                self.assertFalse((prepared.directory / 'gtk').exists())

    def test_first_launch_creates_only_the_standard_package_resource_root(self):
        self.package['id'] = 'org.example.FirstLaunch'
        prepared = self.prepare()
        parent = prepared.directory.parent
        self.assertEqual(list(parent.iterdir()), [prepared.directory])
        self.assertEqual(parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn('XDG_CONFIG_HOME', prepared.environment)
        prepared.close()
        self.assertEqual(list(parent.iterdir()), [])

    def test_strict_snap_does_not_assume_host_modules_are_visible(self):
        self.package = {'kind': 'snap', 'id': 'fixture', 'confinement': 'strict'}
        prepared = self.prepare()
        self.assertIsNone(prepared.directory)
        self.assertNotIn('QT_PLUGIN_PATH', prepared.environment)
        self.assertEqual(prepared.environment['QT_IM_MODULE'], 'ibus')
        prepared.close()

    def test_snap_runtime_uses_only_its_owned_standard_directory(self):
        base = self.root / 'run-user'
        user = base / str(os.getuid())
        user.mkdir(parents=True, mode=0o700)
        def path(value):
            return base if value == '/run/user' else Path(value)
        with patch('desktop_package.Path', side_effect=path):
            runtime = snap_runtime('fixture')
            self.assertEqual(runtime, user / 'snap.fixture')
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
            runtime.chmod(0o755)
            with self.assertRaises(ValueError):
                snap_runtime('fixture')
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o755)
            with self.assertRaises(ValueError):
                snap_runtime('../desktop')

    def test_missing_or_escaped_module_never_leaves_partial_resources(self):
        module = self.modules / 'platforminputcontexts/libfloe-client-native-qt6.so'
        module.unlink()
        module.symlink_to(self.instance / 'unverified')
        with self.assertRaises((ValueError, OSError)):
            self.prepare()
        self.assertEqual(list((self.home / '.var/app/org.example.Editor').iterdir()),
                         [self.home / '.var/app/org.example.Editor/user-document'])

    def test_package_directory_symlinks_and_replacement_cleanup_are_rejected(self):
        app = self.home / '.var/app/org.example.Editor'
        moved = app.with_name('other-owner')
        app.rename(moved)
        app.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.prepare()
        app.unlink()
        moved.rename(app)
        prepared = self.prepare()
        old = prepared.directory.with_name('retained-identity')
        prepared.directory.rename(old)
        prepared.directory.mkdir(mode=0o700)
        (prepared.directory / 'other-owner').write_text('keep')
        prepared.close()
        self.assertEqual((prepared.directory / 'other-owner').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
