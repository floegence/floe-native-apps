"""Package resources must be private, immutable, and owned by one session."""
import os
from pathlib import Path
import tempfile
import unittest

from desktop_package import DesktopPackageInput


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
        self.environment = {'HOME': str(self.home), 'QT_IM_MODULE': 'host-ime',
            'QT_PLUGIN_PATH': '/host/plugins', 'CUSTOM': 'untouched'}
        self.package = {'kind': 'flatpak', 'id': 'org.example.Editor'}
        app = self.home / '.var/app' / self.package['id']
        app.mkdir(parents=True, mode=0o700)
        (app / 'user-document').write_text('unsaved')

    def prepare(self):
        return DesktopPackageInput(self.instance, self.package, self.environment, self.modules)

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

    def test_native_keeps_host_plugin_search_after_the_private_adapter(self):
        self.package = {'kind': 'deb', 'id': 'fixture'}
        prepared = self.prepare()
        self.addCleanup(prepared.close)
        self.assertEqual(prepared.directory.parent, self.instance)
        self.assertEqual(prepared.environment['QT_PLUGIN_PATH'], str(prepared.directory) + ':/host/plugins')
        self.assertNotIn('FLOE_NATIVE_FLATPAK_QT', prepared.environment)

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
