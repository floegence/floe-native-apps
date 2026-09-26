"""Private official file/settings portals beneath one prepared service owner.

Only preparation owns these directories and commands. The launch owner starts
and observes the actual services; a command description is not readiness. A
Flatpak document facade, when required, must already bind the official host
service through DesktopDocuments. No desktop-wide bus proxy is introduced.
"""
import os
from pathlib import Path
import shutil
import stat
from xml.sax.saxutils import escape


PORTAL_INTERFACES = (
    'org.freedesktop.host.portal.Registry',
    'org.freedesktop.portal.Settings', 'org.freedesktop.portal.FileChooser',
    'org.freedesktop.portal.Request', 'org.freedesktop.DBus.Properties',
    'org.freedesktop.DBus.Introspectable', 'org.freedesktop.DBus.Peer',
)


def bus_configuration(address):
    if (not isinstance(address, str) or not address.startswith('unix:') or
            any(c in address for c in (';', '\n', '\r', '\0'))):
        raise ValueError('A single private local bus address is required')
    rules = ''.join('<allow send_destination="org.freedesktop.portal.Desktop" '
                    'send_type="method_call" send_interface="' + name + '"/>' for name in PORTAL_INTERFACES)
    return ('<busconfig><type>session</type><auth>EXTERNAL</auth><listen>' + escape(address) +
            '</listen><policy context="default"><allow send_destination="*"/>'
            '<allow receive_sender="*"/><allow own="*"/>'
            '<deny send_destination="org.freedesktop.portal.Desktop" send_type="method_call"/>' +
            rules + '</policy></busconfig>')


class DesktopPortals:
    def __init__(self, tools, address, display, base, *, host_documents=False, package_runtime=None):
        bus_configuration(address)
        display = Path(display)
        if not display.is_absolute():
            raise ValueError('An absolute private Wayland endpoint is required')
        backend = tools.resource('usr/share/xdg-desktop-portal/portals/gtk.portal')
        self.commands = tuple((name, bus_name, tools.command('usr/libexec/' + name)) for name, bus_name in (
            ('xdg-permission-store', 'org.freedesktop.impl.portal.PermissionStore'),
            ('xdg-document-portal', 'org.freedesktop.portal.Documents'),
            ('xdg-desktop-portal-gtk', 'org.freedesktop.impl.portal.desktop.gtk'),
            ('xdg-desktop-portal', 'org.freedesktop.portal.Desktop'),
        ) if not (host_documents and name == 'xdg-document-portal'))
        instances = None
        if package_runtime is not None:
            package_runtime = Path(package_runtime)
            self.private_directory(package_runtime)
            instances = package_runtime / '.flatpak'
            # Flatpak will populate this directory with its own authoritative
            # instance records. Never copy or invent the sandbox identity.
            if os.path.lexists(instances):
                self.private_directory(instances)
        self.private = tools.private / 'portals'
        self.private_directory(tools.private)
        self.private.mkdir(mode=0o700)
        identity = self.private.stat()
        created_instances = None
        try:
            if instances is not None and not os.path.lexists(instances):
                instances.mkdir(mode=0o700)
                created_instances = instances.stat()
            directories = {key: self.private / name for key, name in (
                ('XDG_RUNTIME_DIR', 'runtime'), ('XDG_CONFIG_HOME', 'config'),
                ('XDG_DATA_HOME', 'data'), ('XDG_CACHE_HOME', 'cache'))}
            for path in directories.values():
                path.mkdir(mode=0o700)
            if instances is not None:
                (directories['XDG_RUNTIME_DIR'] / '.flatpak').symlink_to(instances, target_is_directory=True)
            self.config = directories['XDG_CONFIG_HOME'] / 'xdg-desktop-portal'
            self.config.mkdir(mode=0o700)
            (self.config / 'portals.conf').write_text('[preferred]\ndefault=none\n'
                'org.freedesktop.impl.portal.FileChooser=gtk\norg.freedesktop.impl.portal.Settings=gtk\n')
            shutil.copyfile(backend, self.config / 'gtk.portal')
            environment = {key: value for key, value in base.items()
                if key in ('PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'IBUS_ADDRESS')}
            environment.update({key: str(value) for key, value in directories.items()})
            environment.update(DBUS_SESSION_BUS_ADDRESS=address, WAYLAND_DISPLAY=str(display),
                GDK_BACKEND='wayland', GTK_USE_PORTAL='0', XDG_CURRENT_DESKTOP='Floe',
                GTK_IM_MODULE='ibus', IBUS_ENABLE_SYNC_MODE='1', GSETTINGS_BACKEND='memory')
            self.environment = tools.environment(environment)
            self.environment['XDG_DESKTOP_PORTAL_DIR'] = str(self.config)
        except BaseException:
            if created_instances is not None:
                try:
                    current = instances.lstat()
                    if (current.st_dev, current.st_ino) == (created_instances.st_dev, created_instances.st_ino):
                        # A concurrently created real Flatpak instance owns its
                        # records. rmdir cannot remove a populated directory.
                        instances.rmdir()
                except OSError:
                    pass
            try:
                current = self.private.lstat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    shutil.rmtree(self.private)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def private_directory(path):
        if not path.is_absolute():
            raise ValueError('Private portal directory is required')
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError('Private portal directory is required')
