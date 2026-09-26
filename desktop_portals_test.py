"""Private portal preparation is immutable, bounded and isolated from the host."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from desktop_portals import DesktopPortals, PORTAL_INTERFACES, bus_configuration
from desktop_services import DesktopServices


class PortalPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.component = self.root / 'component'
        self.instance = self.root / 'instance'
        self.component.mkdir()
        self.instance.mkdir(mode=0o700)
        # Isolate resource preparation from native cache generation. The actual
        # generators and official services are exercised in native qualification.
        self.tools = DesktopServices.__new__(DesktopServices)
        self.tools.component = self.component.resolve()
        self.tools.private = self.instance / 'desktop-services'
        self.tools.private.mkdir(mode=0o700)
        self.tools.loader, self.tools.libraries = self.component / 'loader', str(self.component / 'lib')
        for name in ('usr/share/xdg-desktop-portal/portals/gtk.portal',
                     *('usr/libexec/' + name for name in (
                         'xdg-permission-store', 'xdg-document-portal', 'xdg-desktop-portal-gtk', 'xdg-desktop-portal'))):
            path = self.component / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('original publisher resource')
        self.base = {'LD_PRELOAD': '/host/interceptor', 'LD_LIBRARY_PATH': '/host/libraries',
                     'GTK_THEME': 'host theme', 'GTK_PATH': '/host/gtk', 'IBUS_ADDRESS': 'unix:path=/private/input'}
        self.runtime = self.root / 'flatpak-runtime'
        self.runtime.mkdir(mode=0o700)

    def prepare(self, **options):
        return DesktopPortals(self.tools, 'unix:path=/private/bus', '/private/wayland', self.base, **options)

    def test_missing_backend_description_does_not_leave_partial_resources(self):
        backend = self.component / 'usr/share/xdg-desktop-portal/portals/gtk.portal'
        backend.unlink()
        with self.assertRaises(FileNotFoundError):
            self.prepare()
        self.assertEqual(list(self.tools.private.iterdir()), [])
        backend.write_text('original publisher resource')
        self.prepare()

    def test_partial_preparation_cleans_its_new_directory_and_does_not_remove_real_flatpak_records(self):
        instances = self.runtime / '.flatpak'
        instances.mkdir(mode=0o700)
        record = instances / 'owned-by-flatpak'
        record.write_text('sandbox identity')
        with patch('shutil.copyfile', side_effect=OSError('incomplete native preparation')):
            with self.assertRaises(OSError):
                self.prepare(package_runtime=self.runtime)
        self.assertEqual(list(self.tools.private.iterdir()), [])
        self.assertEqual(record.read_text(), 'sandbox identity')
        prepared = self.prepare(package_runtime=self.runtime)
        linked = Path(prepared.environment['XDG_RUNTIME_DIR']) / '.flatpak'
        self.assertEqual(linked.resolve(), instances.resolve())
        self.assertEqual((linked / record.name).read_text(), 'sandbox identity')

    def test_failed_preparation_removes_only_an_empty_new_flatpak_lookup_directory(self):
        with patch('shutil.copyfile', side_effect=OSError('incomplete native preparation')):
            with self.assertRaises(OSError):
                self.prepare(package_runtime=self.runtime)
        self.assertFalse((self.runtime / '.flatpak').exists())
        self.assertEqual(list(self.tools.private.iterdir()), [])

    def test_preparing_again_preserves_existing_configuration_and_authorization(self):
        prepared = self.prepare()
        content = (prepared.config / 'portals.conf').read_bytes()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual((prepared.config / 'portals.conf').read_bytes(), content)
        self.assertEqual((prepared.config / 'gtk.portal').read_text(), 'original publisher resource')

    def test_services_share_one_private_configuration_and_do_not_inherit_host_input_or_themes(self):
        original = dict(self.base)
        prepared = self.prepare(host_documents=True)
        self.assertEqual(self.base, original)
        self.assertEqual([item[0] for item in prepared.commands], [
            'xdg-permission-store', 'xdg-desktop-portal-gtk', 'xdg-desktop-portal'])
        for key in ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'GTK_THEME', 'GTK_PATH', 'DISPLAY', 'XAUTHORITY'):
            self.assertNotIn(key, prepared.environment)
        self.assertEqual(prepared.environment['GTK_IM_MODULE'], 'ibus')
        self.assertEqual(prepared.environment['IBUS_ADDRESS'], original['IBUS_ADDRESS'])
        self.assertEqual(prepared.environment['XDG_DESKTOP_PORTAL_DIR'], str(prepared.config))
        self.assertIn('default=none\n', (prepared.config / 'portals.conf').read_text())
        self.assertNotIn('org.freedesktop.impl.portal.ScreenCast', (prepared.config / 'portals.conf').read_text())

    def test_untrusted_runtime_or_instance_links_cannot_supply_flatpak_identity(self):
        (self.runtime / '.flatpak').symlink_to(self.instance, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Private portal'):
            self.prepare(package_runtime=self.runtime)
        self.assertEqual(list(self.tools.private.iterdir()), [])
        (self.runtime / '.flatpak').unlink()
        self.runtime.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'Private portal'):
            self.prepare(package_runtime=self.runtime)
        self.assertEqual(list(self.tools.private.iterdir()), [])

    def test_private_bus_allows_only_reviewed_portal_methods_and_escapes_address_data(self):
        address = 'unix:path=/private/bus&instance'
        root = ET.fromstring(bus_configuration(address))
        self.assertEqual(root.find('listen').text, address)
        rules = list(root.find('policy'))
        deny = next(index for index, item in enumerate(rules) if item.tag == 'deny')
        allowed = [item for item in rules[deny + 1:]]
        self.assertEqual(tuple(item.attrib['send_interface'] for item in allowed), PORTAL_INTERFACES)
        self.assertTrue(all(item.attrib['send_destination'] == 'org.freedesktop.portal.Desktop' for item in allowed))
        for invalid in ('tcp:host=localhost', 'unix:path=/one;unix:path=/two', 'unix:path=/bad\nname'):
            with self.assertRaises(ValueError):
                bus_configuration(invalid)


if __name__ == '__main__':
    unittest.main()
