"""Launch planning must finish and bind its inputs before any app is executed."""
import copy
from pathlib import Path
import tempfile
import sys
import unittest

import launch_plan


class LaunchPlanTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.desktop = self.root / 'fixture.desktop'
        self.desktop.write_text('[Desktop Entry]\nType=Application\nExec=fixture %U\n')
        self.binary = self.root / 'fixture'
        self.binary.write_bytes(b'#!/bin/sh\nexit 0\n')
        self.binary.chmod(0o700)
        self.package = {'kind': 'native', 'id': '', 'revision': ''}
        self.backend = {'id': 'wayland', 'component': 'fixture-component',
                        'protocols': ['wayland', 'x11']}
        self.observations = 0

    def inspect(self, path, environment):
        self.observations += 1
        self.assertEqual(path, str(self.desktop))
        self.assertEqual(environment, {'PATH': str(self.root)})
        return {'desktop': launch_plan.file_identity(path, 1 << 20),
                'executable': launch_plan.file_identity(str(self.binary)),
                'package': copy.deepcopy(self.package), 'services': []}

    def plan(self, backends=None):
        return launch_plan.prepare(str(self.desktop), {'PATH': str(self.root)},
                                   [self.backend] if backends is None else backends,
                                   inspect=self.inspect)

    def test_unknown_app_protocol_defaults_to_combined_display_before_execution(self):
        plan = self.plan()
        self.assertEqual(plan['version'], 1)
        self.assertEqual(plan['backend'], self.backend)
        self.assertEqual(self.observations, 1)
        launch_plan.revalidate(plan, {'PATH': str(self.root)}, [self.backend], inspect=self.inspect)

    def test_xpra_is_not_an_implicit_fallback_for_missing_wayland(self):
        with self.assertRaises(launch_plan.Unavailable) as raised:
            self.plan([{'id': 'xpra', 'component': 'fixture-xpra', 'protocols': ['x11']}])
        self.assertEqual(raised.exception.code, 'GRAPHICAL_BACKEND_UNAVAILABLE')

    def test_desktop_edit_invalidates_plan(self):
        plan = self.plan()
        self.desktop.write_text('[Desktop Entry]\nType=Application\nExec=other\n')
        with self.assertRaises(launch_plan.StalePlan):
            launch_plan.revalidate(plan, {'PATH': str(self.root)}, [self.backend], inspect=self.inspect)

    def test_executable_replacement_invalidates_plan(self):
        plan = self.plan()
        self.binary.write_bytes(b'#!/bin/sh\nexit 46\n')
        with self.assertRaises(launch_plan.StalePlan):
            launch_plan.revalidate(plan, {'PATH': str(self.root)}, [self.backend], inspect=self.inspect)

    def test_revision_change_invalidates_same_desktop_and_binary(self):
        self.package = {'kind': 'snap', 'id': 'fixture', 'revision': '42', 'confinement': 'strict'}
        plan = self.plan()
        self.package['revision'] = '43'
        with self.assertRaises(launch_plan.StalePlan):
            launch_plan.revalidate(plan, {'PATH': str(self.root)}, [self.backend], inspect=self.inspect)

    def test_path_change_cannot_change_resolved_target(self):
        plan = self.plan()
        with self.assertRaises(launch_plan.StalePlan):
            launch_plan.revalidate(plan, {'PATH': '/different'}, [self.backend], inspect=self.inspect)
        self.assertEqual(self.observations, 1)

    def test_component_change_requires_new_plan(self):
        plan = self.plan()
        changed = {**self.backend, 'component': 'replacement'}
        with self.assertRaises(launch_plan.StalePlan):
            launch_plan.revalidate(plan, {'PATH': str(self.root)}, [changed], inspect=self.inspect)

    def test_description_cannot_modify_backend_capability(self):
        plan = self.plan()
        plan['backend']['protocols'].clear()
        self.assertEqual(self.backend['protocols'], ['wayland', 'x11'])
        with self.assertRaises(launch_plan.StalePlan):
            launch_plan.revalidate(plan, {'PATH': str(self.root)}, [self.backend], inspect=self.inspect)

    def test_unknown_plan_version_is_rejected_without_inspection(self):
        plan = self.plan()
        plan['version'] = 99
        with self.assertRaises(launch_plan.StalePlan):
            launch_plan.revalidate(plan, {'PATH': str(self.root)}, [self.backend], inspect=self.inspect)
        self.assertEqual(self.observations, 1)

    def test_nonregular_or_oversized_sources_are_rejected(self):
        with self.assertRaises(launch_plan.Unavailable):
            launch_plan.file_identity(str(self.root))
        with self.assertRaises(launch_plan.Unavailable):
            launch_plan.file_identity(str(self.binary), 2)

    def test_private_display_assignment_does_not_change_package_resolution(self):
        first = launch_plan.resolution_environment({'PATH': '/usr/bin', 'DISPLAY': ':0',
                                                    'DBUS_SESSION_BUS_ADDRESS': 'host'})
        second = launch_plan.resolution_environment({'PATH': '/usr/bin', 'DISPLAY': ':99',
                                                     'DBUS_SESSION_BUS_ADDRESS': 'private'})
        self.assertEqual(first, second)

    def test_support_tools_do_not_resolve_the_application_in_their_own_path(self):
        restored = launch_plan.restored_environment({'PATH': '/private/bin',
            'LD_LIBRARY_PATH': '/private/lib', 'FLOE_NATIVE_ROOT': '/private',
            'FLOE_NATIVE_APPLICATION_ENV': '{"PATH":"/usr/bin","LD_LIBRARY_PATH":null}'})
        self.assertEqual(restored, {'PATH': '/usr/bin'})

    def test_required_service_absence_is_not_graphical_readiness(self):
        def inspect(path, environment):
            result = self.inspect(path, environment)
            result['services'] = ['user-systemd-scope']
            return result
        with self.assertRaises(launch_plan.Unavailable) as raised:
            launch_plan.prepare(str(self.desktop), {'PATH': str(self.root)},
                                [self.backend], inspect=inspect)
        self.assertEqual(raised.exception.code, 'HOST_SERVICE_UNAVAILABLE')

    def test_metadata_output_is_bounded_and_nonzero_is_preserved(self):
        with self.assertRaises(launch_plan.Unavailable) as raised:
            launch_plan.command_output([sys.executable, '-c', 'print("x" * (2 << 20))'], {})
        self.assertEqual(raised.exception.code, 'PACKAGE_METADATA_INVALID')
        with self.assertRaises(launch_plan.Unavailable) as raised:
            launch_plan.command_output([sys.executable, '-c', 'import sys; sys.exit(46)'], {})
        self.assertEqual(raised.exception.code, 'PACKAGE_RUNTIME_UNAVAILABLE')
        self.assertIsNone(launch_plan.command_output([sys.executable, '-c', 'import sys; sys.exit(1)'], {}, missing=True))


if __name__ == '__main__':
    unittest.main()
