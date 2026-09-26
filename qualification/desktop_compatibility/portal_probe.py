"""Explicit official portal processes for the unpublished graphical fixture."""
import os
import xml.etree.ElementTree as ET
from gi.repository import Gio, GLib


def start_portals(root, address, display, connection, start, wait_until, *, host_documents=False, tools=None, package_runtime=None):
    if tools is None or tools.private.parent != root:
        raise ValueError('Verified private portal resources are required')
    from desktop_portals import DesktopPortals
    prepared = DesktopPortals(tools, address, display, os.environ,
                              host_documents=host_documents, package_runtime=package_runtime)

    def owns(name):
        return connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner", GLib.Variant("(s)", (name,)),
            GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]

    if host_documents and not owns('org.freedesktop.portal.Documents'):
        raise RuntimeError('Host document facade is unavailable')
    for executable, bus_name, command in prepared.commands:
        process = start(command, prepared.environment, executable)
        wait_until(lambda: owns(bus_name) or process.poll() is not None, "Portal did not start: " + executable)
        if process.poll() is not None:
            raise RuntimeError("Portal exited: " + executable)
    xml = connection.call_sync('org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop',
        'org.freedesktop.DBus.Introspectable', 'Introspect', None, GLib.VariantType.new('(s)'),
        Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
    interfaces = {item.get('name') for item in ET.fromstring(xml).findall('interface')}
    assert {'org.freedesktop.portal.FileChooser', 'org.freedesktop.portal.Settings'} <= interfaces
    assert not interfaces.intersection('org.freedesktop.portal.' + name for name in (
        'Screenshot', 'ScreenCast', 'RemoteDesktop', 'Notification', 'Print', 'Email', 'Wallpaper'))
    denied = []
    for name in ('Trash', 'Realtime', 'GameMode', 'Screenshot', 'ScreenCast', 'Notification'):
        try:
            # This deliberately nonexistent method has no possible side effect.
            # The broker must reject it before the real service can dispatch it.
            connection.call_sync('org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop',
                'org.freedesktop.portal.' + name, 'FloeRejectedProbe', None, None,
                Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error as error:
            assert Gio.DBusError.get_remote_error(error) == 'org.freedesktop.DBus.Error.AccessDenied', str(error)
            denied.append(name)
        else:
            raise AssertionError('Out-of-scope portal interface was admitted: ' + name)
    return {'interfaces': sorted(interfaces), 'file_chooser': 'private graphical session',
            'blocked_by_private_bus': denied,
            'documents': 'official host service' if host_documents else 'private official service'}
