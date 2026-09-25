"""Native confirmed-text admission beneath the one shared input scheduler.

The compositor identifies the actual surface and client. The registered toolkit
must own that live client (including an official Flatpak proxy identity), consume
its native marker on that surface, then acknowledge after its event loop. Neither
bus dispatch nor a Wayland text-input serial acknowledges document consumption.
All methods run on the helper's event loop; the application supervisor alone owns
process lifetime. X11's released context protocol remains independent and intact.
"""
from dataclasses import dataclass

from application_peer import ApplicationPeer
from input_marker import MarkerTransactions
from input_order import valid_text


@dataclass(frozen=True)
class ContextToken:
    epoch: int
    target: object
    focus: object
    sender: str
    client: object
    surface_peer: object


class NativeContexts:
    def __init__(self, native, tree, runtime):
        self.native, self.tree, self.runtime = native, tree, runtime
        self.clients, self.markers = {}, MarkerTransactions()
        self.bound, self.pending, self.sequence, self.closed = None, None, 0, False

    def register(self, sender, pid, version, toolkit):
        if (self.closed or version != 1 or toolkit not in ('qt5-wayland', 'qt6-wayland') or
                sender in self.clients or len(self.clients) >= 64):
            raise ValueError('Native context registration is unavailable')
        self.clients[sender] = ApplicationPeer(self.tree, pid, self.runtime)

    def unregister(self, sender):
        peer = self.clients.pop(sender, None)
        if self.pending and self.pending['token'].sender == sender:
            self.finish('INPUT_CONTEXT_UNAVAILABLE')
        if self.bound and self.bound.sender == sender:
            self.unbind()
        self.markers.remove_owner(sender)
        if peer:
            peer.close()

    def unbind(self):
        bound, self.bound = self.bound, None
        if bound:
            bound.surface_peer.close()

    def context_for(self, target):
        if self.pending:
            return None
        self.unbind()
        focus = self.native.focus
        window = self.native.windows.get(target.window) if target else None
        if (self.closed or self.native.closed or not self.native.epoch or self.native.target is not target or
                not focus or not focus.available or not window or window.protocol != 'wayland' or
                focus.window != target.window):
            return None
        surface_peer = None
        try:
            surface_peer = ApplicationPeer(self.tree, focus.pid, self.runtime)
            matching = [(sender, peer) for sender, peer in self.clients.items() if peer.matches(surface_peer)]
            if len(matching) != 1:
                surface_peer.close()
                return None
            sender, peer = matching[0]
            token = ContextToken(self.native.epoch, target, focus, sender, peer, surface_peer)
            self.bound = token
            if not self.valid(token):
                self.unbind()
                return None
            return token
        except (OSError, ValueError):
            if surface_peer:
                surface_peer.close()
            return None

    def valid(self, token):
        return (not self.closed and self.bound is token and not self.native.closed and
                self.native.target is token.target and self.native.focus is token.focus and
                self.native.epoch == token.epoch and self.clients.get(token.sender) is token.client and
                token.client.matches(token.surface_peer))

    def commit(self, token, text, completed):
        if self.pending or not self.valid(token):
            completed('INPUT_CONTEXT_UNAVAILABLE')
            return
        if not valid_text(text):
            self.unbind()
            completed('INPUT_TEXT_INVALID')
            return
        if self.sequence >= 0xffffffff:
            self.unbind()
            completed('INPUT_SESSION_EXHAUSTED')
            return
        try:
            code = self.markers.enqueue(text, owner=token.sender)
        except RuntimeError:
            self.unbind()
            completed('INPUT_MARKER_UNAVAILABLE')
            return
        self.sequence += 1
        self.pending = {'token': token, 'code': code, 'sequence': self.sequence,
                        'taken': False, 'completed': completed}
        try:
            self.native.submit(token.epoch, token.target, [f'key {code} 1', f'key {code} 0'])
        except (ValueError, OSError):
            self.finish('INPUT_MARKER_UNAVAILABLE')

    def take(self, sender, code, surface):
        operation = self.pending
        owned = bool(operation and operation['code'] == code and operation['token'].sender == sender)
        admitted = owned and self.valid(operation['token']) and surface == operation['token'].focus.surface
        text = self.markers.key(code, False, admitted, owner=sender)
        if owned and not admitted:
            self.finish('INPUT_TARGET_UNAVAILABLE')
        if text is None or not admitted:
            return None
        operation['taken'] = True
        return operation['sequence'], text

    def released(self, sender, code):
        self.markers.key(code, True, False, owner=sender)

    def done(self, sender, sequence):
        operation = self.pending
        if (not operation or operation['token'].sender != sender or
                operation['sequence'] != sequence or not operation['taken']):
            return False
        if not self.valid(operation['token']):
            self.finish('INPUT_TARGET_UNAVAILABLE')
            return False
        self.finish(None)
        return True

    def failed(self, sender, sequence):
        if (self.pending and self.pending['token'].sender == sender and
                self.pending['sequence'] == sequence and self.pending['taken']):
            self.finish('INPUT_TARGET_UNAVAILABLE')
            return True
        return False

    def finish(self, error):
        operation, self.pending = self.pending, None
        if not operation:
            return
        if error:
            self.markers.revoke()
        self.unbind()
        operation['completed'](error)

    def cancel(self, token):
        if self.pending and self.pending['token'] is token:
            self.pending = None
            self.markers.revoke()
        if self.bound is token:
            self.unbind()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.finish('INPUT_CONTEXT_UNAVAILABLE')
        self.unbind()
        for sender in tuple(self.clients):
            self.unregister(sender)


class NativeContextService:
    """Private toolkit IPC; unique bus credentials are resolved by the real bus."""
    NAME = 'org.floegence.DesktopInput'
    PATH = '/org/floegence/DesktopInput'
    XML = '''<node><interface name="org.floegence.DesktopInput">
    <method name="Register"><arg type="u" direction="in"/><arg type="s" direction="in"/></method>
    <method name="Take"><arg type="u" direction="in"/><arg type="u" direction="in"/><arg type="u" direction="out"/><arg type="s" direction="out"/></method>
    <method name="Released"><arg type="u" direction="in"/></method>
    <method name="Done"><arg type="u" direction="in"/></method>
    <method name="Failed"><arg type="u" direction="in"/></method>
    </interface></node>'''

    def __init__(self, connection, contexts, destination=NAME):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib, self.connection, self.contexts = Gio, GLib, connection, contexts
        self.closed = False
        if not Gio.dbus_is_name(destination) or destination.startswith(':'):
            raise ValueError('Invalid native input service name')
        self.destination = destination
        result = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'RequestName', GLib.Variant('(su)', (self.destination, 4)),
            GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 3000, None)
        if result.unpack()[0] != 1:
            raise ValueError('Native input service is already owned')
        self.node = Gio.DBusNodeInfo.new_for_xml(self.XML)
        self.registration = connection.register_object(self.PATH, self.node.interfaces[0], self.call, None, None)
        self.subscription = connection.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus',
            'NameOwnerChanged', '/org/freedesktop/DBus', None, Gio.DBusSignalFlags.NONE, self.owner_changed)

    def owner_changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        sender, _old, new = parameters.unpack()
        if not new:
            self.contexts.unregister(sender)

    def call(self, connection, sender, _path, _interface, method, parameters, invocation):
        import os
        try:
            values = parameters.unpack()
            if method == 'Register':
                credentials = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                    'org.freedesktop.DBus', 'GetConnectionCredentials', self.GLib.Variant('(s)', (sender,)),
                    self.GLib.VariantType.new('(a{sv})'), self.Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
                if credentials.get('UnixUserID') != os.getuid():
                    raise ValueError('Native peer user differs')
                self.contexts.register(sender, credentials['ProcessID'], *values)
            elif method == 'Take':
                result = self.contexts.take(sender, *values)
                if result is None:
                    raise ValueError('Native marker is unavailable')
                invocation.return_value(self.GLib.Variant('(us)', result))
                return
            elif method == 'Released':
                self.contexts.released(sender, *values)
            elif method == 'Done':
                if not self.contexts.done(sender, *values):
                    raise ValueError('Native completion is unavailable')
            elif method == 'Failed':
                if not self.contexts.failed(sender, *values):
                    raise ValueError('Native failure is unavailable')
            else:
                raise ValueError('Native method is unavailable')
            invocation.return_value(None)
        except (OSError, KeyError, ValueError, self.GLib.Error):
            invocation.return_dbus_error(self.NAME + '.Unavailable', 'INPUT_CONTEXT_UNAVAILABLE')

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.connection.signal_unsubscribe(self.subscription)
        self.connection.unregister_object(self.registration)
        self.contexts.close()
        try:
            self.connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'ReleaseName', self.GLib.Variant('(s)', (self.destination,)),
                self.GLib.VariantType.new('(u)'), self.Gio.DBusCallFlags.NONE, 2000, None)
        except self.GLib.Error:
            pass
