"""Confirmed-text handoff to toolkit input contexts in the private application bus.

The marker enters the application's native event queue after preceding pointer
and keyboard events. The toolkit takes the text from its key filter, commits it
to its current editable object, and acknowledges after returning to its event
loop. D-Bus transport acknowledgements are not application acknowledgements.
"""
from time import monotonic

_NAME = 'org.floegence.ClientInput'
_PATH = '/org/floegence/ClientInput'
_XML = '''<node><interface name="org.floegence.ClientInput">
 <method name="Register"><arg type="u" direction="in"/><arg type="s" direction="in"/></method>
 <method name="Take"><arg type="u" direction="in"/><arg type="u" direction="in"/><arg type="u" direction="out"/><arg type="s" direction="out"/></method>
 <method name="Done"><arg type="u" direction="in"/></method>
</interface></node>'''


class Contexts:
    def __init__(self, address, display, marker):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.display, self.marker = display, marker
        self.contexts = {}
        self.pending = None
        self.sequence = 0
        self.marker_clock = lambda: int(monotonic() * 1000) & 0xffffffff
        self.connection = Gio.DBusConnection.new_for_address_sync(
            address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
            Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        self.connection.set_exit_on_close(False)
        result = self.connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'RequestName', GLib.Variant('(su)', (_NAME, 4)),
            GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 3000, None)
        if result.unpack()[0] != 1:
            self.connection.close_sync(None)
            raise RuntimeError('Private input service is already owned')
        self.node = Gio.DBusNodeInfo.new_for_xml(_XML)
        self.registration = self.connection.register_object(
            _PATH, self.node.interfaces[0], self._call, None, None)
        self.subscription = self.connection.signal_subscribe(
            'org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
            '/org/freedesktop/DBus', None, Gio.DBusSignalFlags.NONE, self._owner_changed)

    def _owner_changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        sender, _old, new = parameters.unpack()
        if new:
            return
        for pid, context in tuple(self.contexts.items()):
            if context['sender'] == sender:
                del self.contexts[pid]
        if self.pending and self.pending['sender'] == sender:
            self._finish('INPUT_CONTEXT_UNAVAILABLE')

    def context_for(self, xid, pid):
        context = self.contexts.get(pid)
        return (pid, xid, context['sender']) if context else None

    def commit(self, token, text, completed):
        pid, xid, sender = token
        if self.pending or self.contexts.get(pid, {}).get('sender') != sender:
            completed('INPUT_CONTEXT_UNAVAILABLE')
            return
        # This ID travels in X11's event timestamp. Chromium normalizes stale
        # timestamps, so use that field's native clock while keeping every ID
        # strictly distinct. No timer, text comparison, or delayed dispatch is
        # involved in transaction identity or duplicate prevention.
        self.sequence = max(self.sequence + 1, self.marker_clock())
        if self.sequence > 0xffffffff:
            completed('INPUT_SESSION_EXHAUSTED')
            return
        self.pending = {'token': token, 'sender': sender, 'xid': xid,
                        'sequence': self.sequence, 'text': text, 'completed': completed}
        if not self.marker(self.sequence, xid):
            self._finish('INPUT_MARKER_UNAVAILABLE')

    def cancel(self, token):
        if self.pending and self.pending['token'] == token:
            # Removing the payload also rejects a marker arriving after revoke.
            self.pending = None

    def take(self, sender, sequence, xid):
        operation = self.pending
        if not operation or operation['sender'] != sender or operation['sequence'] != sequence or 'text' not in operation:
            return None
        if not self.display.descendant(xid, operation['xid']) or not self.display.focused_within(operation['xid']):
            self._finish('INPUT_TARGET_UNAVAILABLE')
            return None
        return operation['sequence'], operation.pop('text')

    def done(self, sender, sequence):
        operation = self.pending
        if not operation or operation['sender'] != sender or operation['sequence'] != sequence or 'text' in operation:
            return False
        self._finish(None)
        return True

    def _finish(self, error):
        operation, self.pending = self.pending, None
        if operation:
            operation['completed'](error)

    def _call(self, connection, sender, _path, _interface, method, parameters, invocation):
        Gio, GLib = self.Gio, self.GLib
        if method == 'Register':
            version, toolkit = parameters.unpack()
            if version != 1 or toolkit not in ('gtk3', 'qt5', 'qt6'):
                invocation.return_dbus_error(_NAME + '.InvalidVersion', 'Input module is unsupported')
                return
            pid = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'GetConnectionUnixProcessID', GLib.Variant('(s)', (sender,)),
                GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
            previous = self.contexts.get(pid)
            if previous and previous['sender'] != sender:
                invocation.return_dbus_error(_NAME + '.DuplicateOwner', 'Application already has an input owner')
                return
            self.contexts[pid] = {'sender': sender, 'toolkit': toolkit}
            invocation.return_value(None)
            return
        if method == 'Take':
            result = self.take(sender, *parameters.unpack())
            if result is not None:
                invocation.return_value(GLib.Variant('(us)', result))
                return
        elif method == 'Done' and self.done(sender, parameters.unpack()[0]):
            invocation.return_value(None)
            return
        invocation.return_dbus_error(_NAME + '.Unavailable', 'Input operation is unavailable')

    def close(self):
        self.pending = None
        self.contexts.clear()
        self.connection.signal_unsubscribe(self.subscription)
        self.connection.unregister_object(self.registration)
        self.connection.close_sync(None)
