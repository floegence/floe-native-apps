"""Read-only selection of the current user's real graphical login session."""
import os
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
