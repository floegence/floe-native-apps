"""Read-only selection of the current user's real graphical login session."""
import os
import pwd
import re
import stat

from host_desktop_contract import DesktopError


def select_session(records, uid):
    candidates = [record for record in records if record.get('User', [None])[0] == uid
                  and record.get('Type') in ('wayland', 'x11') and record.get('Class') == 'user'
                  and record.get('Active') is True and record.get('Remote') is False
                  and record.get('Seat', [''])[0]]
    if len(candidates) != 1:
        raise DesktopError('DESKTOP_SESSION_UNAVAILABLE' if not candidates else 'DESKTOP_SESSION_AMBIGUOUS')
    return candidates[0]


def x11_credentials(session, uid, proc_root='/proc'):
    """Resolve X11 before connection from processes in the selected login scope.

    GDM can leave logind's Display property empty. The login's own process
    environment supplies its display and cookie path; the Runtime's inherited
    DISPLAY/XAUTHORITY may belong to SSH or an application-private desktop.
    """
    scope = session.get('Scope', '')
    display = session.get('Display', '')
    if session.get('Type') != 'x11' or not re.fullmatch(r'session-[A-Za-z0-9]+\.scope', scope):
        raise DesktopError('X11_SESSION_UNAVAILABLE')
    pairs = set()
    def read_at(directory, name):
        fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(fd, 'rb') as source:
            data = source.read((1 << 20) + 1)
        if len(data) > 1 << 20:
            raise ValueError('oversized session metadata')
        return data
    with os.scandir(proc_root) as entries:
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            directory = None
            try:
                directory = os.open(entry.path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
                if os.fstat(directory).st_uid != uid:
                    continue
                paths = [line.split(':', 2)[-1].split('/') for line in read_at(directory, 'cgroup').decode().splitlines()]
                if not any(scope in path for path in paths):
                    continue
                environment = dict(item.split(b'=', 1) for item in read_at(directory, 'environ').split(b'\0') if b'=' in item)
                name = environment.get(b'DISPLAY', b'').decode()
                if not re.fullmatch(r':[0-9]+(?:\.[0-9]+)?', name) or display and name != display:
                    continue
                authority = environment.get(b'XAUTHORITY', b'').decode()
                if not authority:
                    authority = os.path.join(pwd.getpwuid(uid).pw_dir, '.Xauthority')
                if not os.path.isabs(authority):
                    continue
                info = os.lstat(authority)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
                    continue
                pairs.add((name, authority))
            except (OSError, UnicodeError, ValueError, KeyError):
                continue
            finally:
                if directory is not None:
                    os.close(directory)
    if len(pairs) != 1:
        raise DesktopError('X11_SESSION_AMBIGUOUS' if pairs else 'X11_SESSION_UNAVAILABLE')
    return pairs.pop()


class HostIdentity:
    def __init__(self, Gio, GLib, changed=lambda _state: None):
        self.Gio, self.GLib = Gio, GLib
        self.changed = changed
        self.system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.selected = self.current()
        self.backend = self.selected['Type']
        self.observed = dict(self.selected)
        directory = '/run/user/' + str(os.getuid())
        info = os.lstat(directory)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise DesktopError('SESSION_BUS_UNAVAILABLE')
        info = os.lstat(directory + '/bus')
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise DesktopError('SESSION_BUS_UNAVAILABLE')
        # A Runtime started over SSH must address the selected user's real bus,
        # never a private application bus inherited from a launch environment.
        self.bus = Gio.DBusConnection.new_for_address_sync('unix:path=' + directory + '/bus',
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        self.subscriptions = [self.system.signal_subscribe('org.freedesktop.login1',
            'org.freedesktop.DBus.Properties', 'PropertiesChanged', self.selected['path'],
            'org.freedesktop.login1.Session', Gio.DBusSignalFlags.NONE, self._properties_changed)]

    def _call(self, path, interface, method, signature=None, values=()):
        return self.system.call_sync('org.freedesktop.login1', path, interface, method,
            self.GLib.Variant(signature, values) if signature else None, None,
            self.Gio.DBusCallFlags.NONE, 3000, None).unpack()

    def current(self):
        sessions = self._call('/org/freedesktop/login1', 'org.freedesktop.login1.Manager', 'ListSessions')[0]
        records = []
        for session, uid, _name, _seat, path in sessions:
            if uid != os.getuid():
                continue
            props = self._call(path, 'org.freedesktop.DBus.Properties', 'GetAll',
                '(s)', ('org.freedesktop.login1.Session',))[0]
            props['path'], props['id'] = path, session
            records.append(props)
        return select_session(records, os.getuid())

    def state(self):
        current = self.observed
        if current is None:
            return 'session_unavailable'
        if current['id'] != self.selected['id'] or current['Type'] != self.backend:
            return 'user_switched'
        if not current.get('Active', False):
            return 'session_unavailable'
        return 'locked' if current.get('LockedHint', True) else 'ready'

    def _properties_changed(self, _bus, _sender, _path, _interface, _signal, parameters):
        interface, values, invalidated = parameters.unpack()
        if interface != 'org.freedesktop.login1.Session':
            return
        if invalidated or self.observed is None:
            self.refresh()
        else:
            self.observed.update(values)
        self.changed(self.state())

    def refresh(self):
        try:
            self.observed = self.current()
        except (DesktopError, self.GLib.Error):
            self.observed = None
        return self.state()

    def close(self):
        for subscription in self.subscriptions:
            self.system.signal_unsubscribe(subscription)
        self.subscriptions.clear()
        if not self.bus.is_closed():
            self.bus.close_sync(None)

    def lock(self):
        if self.state() != 'ready':
            raise DesktopError('DESKTOP_NOT_ACTIVE')
        self._call(self.selected['path'], 'org.freedesktop.login1.Session', 'Lock')
