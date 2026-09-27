"""Private service preparation must not leak host state or partial caches."""
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from desktop_services import DesktopServices
from launch_plan import restored_environment


class DesktopServicesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.component = self.root / 'component & resources'
        self.state = self.root / 'instance'
        self.component.mkdir(mode=0o700)
        self.state.mkdir(mode=0o700)
        loader = 'aarch64' if platform.machine() in ('aarch64', 'arm64') else 'x86_64'
        for name in ('lib/ld-musl-' + loader + '.so.1', 'usr/bin/update-mime-database',
                     'usr/bin/glib-compile-schemas', 'usr/bin/gdk-pixbuf-query-loaders',
                     'usr/bin/gtk-query-immodules-3.0', 'usr/bin/dbus-daemon',
                     'usr/bin/python3', 'usr/libexec/gio-launch-desktop',
                     'usr/share/mime/packages/types.xml', 'usr/share/glib-2.0/schemas/test.xml',
                     'usr/lib/gtk-3.0/3.0.0/immodules/im-ibus.so'):
            path = self.component / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('native fixture resource')

    def prepare(self):
        with patch('subprocess.run'), patch('subprocess.check_output', return_value=b'private cache\n'):
            return DesktopServices(self.component, self.state)

    def test_cache_failure_removes_only_its_incomplete_resources_and_allows_retry(self):
        sentinel = self.state / 'application-document'
        sentinel.write_text('unsaved user work')
        with patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, 'schema-compiler')):
            with self.assertRaises(subprocess.CalledProcessError):
                DesktopServices(self.component, self.state)
        self.assertEqual(list(self.state.iterdir()), [sentinel])
        prepared = self.prepare()
        self.assertTrue(prepared.private.is_dir())
        self.assertEqual(sentinel.read_text(), 'unsaved user work')
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertTrue(prepared.private.is_dir())

    def test_font_configuration_preserves_literal_private_paths(self):
        prepared = self.prepare()
        config = ET.parse(prepared.private / 'fonts.conf')
        self.assertIn(str(self.component.resolve() / 'usr/share/fonts'), [x.text for x in config.findall('dir')])
        self.assertEqual([x.text for x in config.findall('cachedir')], [str(prepared.private / 'font-cache')])

    def test_native_generators_receive_private_environment_before_execution(self):
        observed = []
        def run(*args, **kwargs):
            observed.append(kwargs.get('env', {}))
        with patch.dict(os.environ, {'LD_PRELOAD': '/host/injected.so', 'GIO_EXTRA_MODULES': '/host/plugins'}), \
                patch('subprocess.run', side_effect=run), \
                patch('subprocess.check_output', return_value=b'private cache\n'):
            prepared = DesktopServices(self.component, self.state)
        self.assertEqual(len(observed), 2)
        for environment in observed:
            self.assertNotIn('LD_PRELOAD', environment)
            self.assertNotIn('GIO_EXTRA_MODULES', environment)
            self.assertEqual(environment.get('GSETTINGS_SCHEMA_DIR'), str(prepared.private))

    def test_commands_cannot_escape_the_verified_component(self):
        prepared = self.prepare()
        external = self.root / 'external-command'
        external.write_text('not a component executable')
        with self.assertRaises(ValueError):
            prepared.command(str(external))
        with self.assertRaises(ValueError):
            prepared.command('../external-command')
        command = self.component / 'usr/bin/dbus-daemon'
        command.unlink()
        command.symlink_to(external)
        with self.assertRaises(ValueError):
            prepared.command('usr/bin/dbus-daemon')

    def test_preparation_rejects_a_shared_or_symlink_instance_without_mutation(self):
        self.state.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'Private instance'):
            self.prepare()
        self.assertEqual(list(self.state.iterdir()), [])
        self.state.chmod(0o700)
        link = self.root / 'linked-instance'
        link.symlink_to(self.state, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Private instance'):
            DesktopServices(self.component, link)
        self.assertEqual(list(self.state.iterdir()), [])

    def test_empty_or_timed_out_generator_never_leaves_a_ready_directory(self):
        with patch('subprocess.run'), patch('subprocess.check_output', return_value=b''):
            with self.assertRaisesRegex(ValueError, 'no data'):
                DesktopServices(self.component, self.state)
        self.assertEqual(list(self.state.iterdir()), [])
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('schema-compiler', 30)):
            with self.assertRaises(subprocess.TimeoutExpired):
                DesktopServices(self.component, self.state)
        self.assertEqual(list(self.state.iterdir()), [])

    def test_support_environment_does_not_mutate_the_application_environment(self):
        prepared = self.prepare()
        original = {'DISPLAY': ':49', 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/private/bus',
                    'LD_LIBRARY_PATH': '/host/library', 'PYTHONPATH': '/host/python',
                    'GTK_PATH': '/host/gtk', 'FONTCONFIG_PATH': '/host/fonts'}
        copied = dict(original)
        private = prepared.environment(original)
        self.assertEqual(original, copied)
        self.assertEqual(private['DISPLAY'], original['DISPLAY'])
        self.assertEqual(private['DBUS_SESSION_BUS_ADDRESS'], original['DBUS_SESSION_BUS_ADDRESS'])
        for key in ('LD_LIBRARY_PATH', 'PYTHONPATH', 'GTK_PATH', 'FONTCONFIG_PATH'):
            self.assertNotIn(key, private)
        self.assertEqual(private.get('XKB_CONFIG_ROOT'), str(prepared.component / 'usr/share/X11/xkb'))

    def test_private_supervisor_restores_exact_host_environment_before_application_exec(self):
        prepared = self.prepare()
        original = {'PATH': '/host/bin', 'HOME': '/host/home', 'DISPLAY': ':49',
            'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/private/bus', 'PYTHONHOME': '/host/python',
            'PYTHONPATH': '/host/modules', 'GIO_EXTRA_MODULES': '/host/gio',
            'GTK_IM_MODULE': 'ibus', 'IBUS_ADDRESS': 'private-input', 'CUSTOM': 'unchanged',
            'XKB_CONFIG_ROOT': '/host/keyboard'}
        before = dict(original)
        supervisor = prepared.launcher_environment(original)
        self.assertEqual(original, before)
        self.assertEqual(restored_environment(supervisor), original)
        self.assertEqual(supervisor['PYTHONHOME'], str(prepared.component / 'usr'))
        self.assertNotIn('PYTHONPATH', supervisor)
        self.assertEqual(supervisor['XKB_CONFIG_ROOT'], str(prepared.component / 'usr/share/X11/xkb'))
        self.assertTrue(Path(supervisor['GIO_LAUNCH_DESKTOP']).is_file())
        self.assertEqual(prepared.command('usr/bin/python3')[-1], str(prepared.component / 'usr/bin/python3'))


if __name__ == '__main__':
    unittest.main()
