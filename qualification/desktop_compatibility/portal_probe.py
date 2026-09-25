"""Explicit official portal processes for the unpublished graphical fixture."""
from pathlib import Path
import os
import shutil
import xml.etree.ElementTree as ET
from gi.repository import Gio, GLib


def start_portals(root, address, display, connection, start, wait_until, *, host_documents=False, input_module="ibus", tools=None, package_runtime=None):
    directories = {key: root / name for key, name in (
        ("XDG_RUNTIME_DIR", "portal-runtime"), ("XDG_CONFIG_HOME", "portal-config"),
        ("XDG_DATA_HOME", "portal-data"), ("XDG_CACHE_HOME", "portal-cache"))}
    for path in directories.values():
        path.mkdir(mode=0o700)
    if package_runtime:
        # Portal 1.20 validates the real bwrap child through Flatpak's own
        # runtime instance metadata. Retain the private service directory but
        # resolve that read-only lookup to the actual launch authority; never
        # fabricate or copy a sandbox identity record.
        info = package_runtime.lstat()
        if not package_runtime.is_dir() or package_runtime.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError('Private Flatpak launch runtime is unavailable')
        instances = package_runtime / '.flatpak'
        instances.mkdir(mode=0o700, exist_ok=True)
        info = instances.lstat()
        if not instances.is_dir() or instances.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError('Private Flatpak instance metadata is unavailable')
        (directories['XDG_RUNTIME_DIR'] / '.flatpak').symlink_to(instances, target_is_directory=True)
    config = directories["XDG_CONFIG_HOME"] / "xdg-desktop-portal"
    config.mkdir()
    (config / "portals.conf").write_text("[preferred]\ndefault=none\n"
        "org.freedesktop.impl.portal.FileChooser=gtk\norg.freedesktop.impl.portal.Settings=gtk\n")
    if tools:
        # The publisher's explicit directory override selects BOTH backend
        # descriptions and portals.conf. Keep the unchanged .portal alongside
        # the private allowlist so no fallback interfaces are enabled.
        shutil.copyfile(tools.component / 'usr/share/xdg-desktop-portal/portals/gtk.portal', config / 'gtk.portal')
    environment = {**os.environ, **{key: str(value) for key, value in directories.items()},
        "DBUS_SESSION_BUS_ADDRESS": address, "WAYLAND_DISPLAY": str(display),
        "GDK_BACKEND": "wayland", "GTK_USE_PORTAL": "0", "XDG_CURRENT_DESKTOP": "FloePrototype",
        "GTK_IM_MODULE": input_module, "IBUS_ENABLE_SYNC_MODE": "1", "GSETTINGS_BACKEND": "memory"}
    for key in ("DISPLAY", "XAUTHORITY", "GTK_PATH", "GTK_IM_MODULE_FILE"):
        environment.pop(key, None)
    if os.environ.get("FLOE_PROBE_TRACE"):
        environment["WAYLAND_DEBUG"] = "client"

    def owns(name):
        return connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner", GLib.Variant("(s)", (name,)),
            GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]

    for executable, bus_name in (
        ("xdg-permission-store", "org.freedesktop.impl.portal.PermissionStore"),
        ("xdg-document-portal", "org.freedesktop.portal.Documents"),
        ("xdg-desktop-portal-gtk", "org.freedesktop.impl.portal.desktop.gtk"),
        ("xdg-desktop-portal", "org.freedesktop.portal.Desktop"),
    ):
        if host_documents and executable == "xdg-document-portal":
            if not owns(bus_name):
                raise RuntimeError("Host document facade is unavailable")
            continue
        if tools:
            command = tools.command('usr/libexec/' + executable)
            process_environment = tools.environment(environment)
            process_environment['XDG_DESKTOP_PORTAL_DIR'] = str(config)
        else:
            binary = Path("/usr/libexec") / executable
            if not binary.is_file():
                raise RuntimeError("Missing fixture portal: " + executable)
            command, process_environment = [str(binary)], environment
        process = start(command, process_environment, executable)
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
