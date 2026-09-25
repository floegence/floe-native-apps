"""Explicit official portal processes for the unpublished graphical fixture."""
from pathlib import Path
from gi.repository import Gio, GLib


def start_portals(root, address, display, connection, start, wait_until):
    directories = {key: root / name for key, name in (
        ("XDG_RUNTIME_DIR", "portal-runtime"), ("XDG_CONFIG_HOME", "portal-config"),
        ("XDG_DATA_HOME", "portal-data"), ("XDG_CACHE_HOME", "portal-cache"))}
    for path in directories.values():
        path.mkdir(mode=0o700)
    config = directories["XDG_CONFIG_HOME"] / "xdg-desktop-portal"
    config.mkdir()
    (config / "portals.conf").write_text("[preferred]\ndefault=none\n"
        "org.freedesktop.impl.portal.FileChooser=gtk\norg.freedesktop.impl.portal.Settings=gtk\n")
    import os
    environment = {**os.environ, **{key: str(value) for key, value in directories.items()},
        "DBUS_SESSION_BUS_ADDRESS": address, "WAYLAND_DISPLAY": str(display),
        "GDK_BACKEND": "wayland", "GTK_USE_PORTAL": "0", "XDG_CURRENT_DESKTOP": "FloePrototype",
        "GTK_IM_MODULE": "ibus", "IBUS_ENABLE_SYNC_MODE": "2"}
    for key in ("DISPLAY", "XAUTHORITY", "GTK_PATH", "GTK_IM_MODULE_FILE"):
        environment.pop(key, None)

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
        binary = Path("/usr/libexec") / executable
        if not binary.is_file():
            raise RuntimeError("Missing fixture portal: " + executable)
        process = start([str(binary)], environment, executable)
        wait_until(lambda: owns(bus_name) or process.poll() is not None, "Portal did not start: " + executable)
        if process.poll() is not None:
            raise RuntimeError("Portal exited: " + executable)
