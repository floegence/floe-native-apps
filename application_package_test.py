"""Private Flatpak options must preserve GIO's original Desktop Entry arguments."""
import os
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest

from application_package import private_flatpak_command
from launch_plan import Unavailable


class PackageCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.binary = self.root / 'flatpak'
        self.binary.write_text('#!/bin/sh\nexit 0\n')
        self.binary.chmod(0o700)
        self.glib = SimpleNamespace(shell_parse_argv=lambda text: (True, shlex.split(text)))
        self.plan = {'backend': {'id': 'wayland'}, 'observation': {'package': {'kind': 'flatpak'},
                     'executable': {'path': str(self.binary)}}}
        self.environment = {'PATH': str(self.root), 'FLOE_NATIVE_FLATPAK_QT': '/private/quoted " module%U'}

    def app(self, command):
        return SimpleNamespace(get_string=lambda key: command if key == 'Exec' else str(self.root))

    def test_original_quoted_arguments_and_field_codes_remain_verbatim(self):
        prefix = '"' + str(self.binary) + '" run'
        suffix = ' --branch=stable --command=editor org.example.Editor "space in argument" @@u %U @@'
        actual = private_flatpak_command(self.app(prefix + suffix), self.plan, self.environment, self.glib)
        self.assertTrue(actual.startswith(prefix + ' "--env=QT_PLUGIN_PATH='))
        self.assertTrue(actual.endswith(suffix))
        self.assertIn('quoted \\" module%%U', actual)

    def test_exported_environment_wrapper_and_spacing_are_preserved(self):
        prefix = '/usr/bin/env DESKTOP_HINT="a b"  ' + str(self.binary) + '\trun'
        suffix = '\t--command=editor org.example.Editor %U'
        actual = private_flatpak_command(self.app(prefix + suffix), self.plan, self.environment, self.glib)
        self.assertTrue(actual.startswith(prefix + ' "--env='))
        self.assertTrue(actual.endswith(suffix))

    def test_unadapted_entries_are_not_rewritten(self):
        self.assertIsNone(private_flatpak_command(self.app('anything %U'), None, {}, self.glib))

    def test_resource_marker_cannot_change_the_selected_package_or_executable(self):
        for mutate in (lambda: self.plan['observation']['package'].update(kind='native'),
                       lambda: self.plan['observation']['executable'].update(path='/wrong/flatpak')):
            with self.subTest(mutate=mutate):
                self.plan['observation']['package']['kind'] = 'flatpak'
                mutate()
                with self.assertRaises(Unavailable):
                    private_flatpak_command(self.app(str(self.binary) + ' run org.example.Editor'),
                                            self.plan, self.environment, self.glib)


class BrowserProfileTests(unittest.TestCase):
    def test_private_profile_preserves_original_args_and_precedes_option_terminator(self):
        from application_package import private_browser_command
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            profile = str(root / 'persistent % profile')
            binary = root / 'chrome'
            binary.write_text('#!/bin/sh\nexit 0\n'); binary.chmod(0o700)
            plan = {'browser_profile': profile, 'observation': {'browser_family': 'chromium',
                    'executable': {'path': str(binary)}}}
            prefix = '/usr/bin/env HINT="a b" ' + str(binary)
            suffix = ' --new-window -- %U'
            app = SimpleNamespace(get_string=lambda key: prefix + suffix if key == 'Exec' else str(root))
            glib = SimpleNamespace(shell_parse_argv=lambda text: (True, shlex.split(text)))
            environment = {'PATH': str(root)}
            result = private_browser_command(app, plan, environment, glib)
            self.assertTrue(result.startswith(prefix + ' "--user-data-dir='))
            self.assertTrue(result.endswith(suffix))
            self.assertIn('persistent %% profile', result)
            self.assertEqual(Path(profile).stat().st_mode & 0o777, 0o700)
            (Path(profile) / 'user-data').write_text('keep')
            private_browser_command(app, plan, environment, glib)
            self.assertEqual((Path(profile) / 'user-data').read_text(), 'keep')

    def test_profile_never_follows_a_symlink_or_changes_other_applications(self):
        from application_package import private_browser_command
        app = SimpleNamespace(get_string=lambda _: '/usr/bin/editor %F')
        self.assertIsNone(private_browser_command(app, {}, {}, None))
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'link'
            profile.symlink_to(directory)
            with self.assertRaises(Unavailable):
                private_browser_command(app, {'browser_profile': str(profile),
                    'observation': {'browser_family': 'chromium'}}, {}, None)


if __name__ == '__main__':
    unittest.main()
