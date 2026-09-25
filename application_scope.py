"""Constrained real user-systemd scope service for an admitted Snap launcher.

The application's supervisor owns this adapter and its unreaped child leases.
No caller can start services, manage other PIDs, or access the host desktop bus.
"""
import os
import re

NAME = 'org.freedesktop.systemd1'
PATH = '/org/freedesktop/systemd1'
INTERFACE = NAME + '.Manager'
XML = '''<node><interface name="org.freedesktop.systemd1.Manager">
<method name="StartTransientUnit"><arg type="s" direction="in"/>
<arg type="s" direction="in"/><arg type="a(sv)" direction="in"/>
<arg type="a(sa(sv))" direction="in"/><arg type="o" direction="out"/></method>
<signal name="JobRemoved"><arg type="u"/><arg type="o"/>
<arg type="s"/><arg type="s"/></signal></interface></node>'''


def validate_request(security_tag, caller_pid, request):
    if not re.fullmatch(r'snap\.[a-z0-9_-]+\.[a-zA-Z0-9_-]+', security_tag):
        raise ValueError('Invalid admitted package identity')
    unit, mode, properties, auxiliary = request
    uuid = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    if not re.fullmatch(re.escape(security_tag) + '-' + uuid + r'\.scope', unit):
        raise ValueError('Scope does not identify the admitted package')
    if mode != 'fail' or auxiliary or len(properties) != 1:
        raise ValueError('Only a new scope without auxiliary units is allowed')
    key, value = properties[0]
    if key != 'PIDs' or len(value) != 1 or type(value[0]) is not int or value != [caller_pid]:
        raise ValueError('A launcher may move only itself into its scope')
    return unit


class ScopeService:
    def __init__(self, private_address, host_address, security_tag, children, record):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.children, self.record, self.security_tag = children, record, security_tag
        self.pending, self.admitted = {}, set()
        self.private = self.host = None
        self.registration = self.subscription = self.owner_subscription = 0
        flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        try:
            self.private = Gio.DBusConnection.new_for_address_sync(private_address, flags, None, None)
            self.host = Gio.DBusConnection.new_for_address_sync(host_address, flags, None, None)
            for connection in (self.private, self.host):
                connection.set_exit_on_close(False)
            if self.private.get_guid() == self.host.get_guid():
                raise RuntimeError('Host services require a separate private bus')
            # Pin the actual service's unique bus identity. A later service
            # replacement cannot complete or receive an earlier launch request.
            self.host_owner = self.host.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', (NAME,)),
                GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
            credentials = self.host.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'GetConnectionCredentials', GLib.Variant('(s)', (self.host_owner,)),
                GLib.VariantType.new('(a{sv})'), Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
            if credentials['UnixUserID'] != os.getuid():
                raise RuntimeError('Scope manager does not belong to the host user')
            self.node = Gio.DBusNodeInfo.new_for_xml(XML)
            self.registration = self.private.register_object(PATH, self.node.interfaces[0], self.call, None, None)
            self.subscription = self.host.signal_subscribe(self.host_owner, INTERFACE, 'JobRemoved', PATH,
                None, Gio.DBusSignalFlags.NONE, self.job_removed)
            self.owner_subscription = self.host.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus',
                'NameOwnerChanged', '/org/freedesktop/DBus', self.host_owner, Gio.DBusSignalFlags.NONE, self.owner_changed)
            reply = self.private.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                'RequestName', GLib.Variant('(su)', (NAME, 4)), GLib.VariantType.new('(u)'),
                Gio.DBusCallFlags.NONE, 3000, None)
            if reply.unpack()[0] != 1:
                raise RuntimeError('Private scope service is already owned')
        except BaseException:
            self.close()
            raise

    def call(self, connection, sender, _path, _interface, method, parameters, invocation):
        Gio, GLib = self.Gio, self.GLib
        lease = None
        try:
            if method != 'StartTransientUnit' or len(self.admitted) >= 8:
                raise ValueError('Scope operation is unavailable')
            credentials = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'GetConnectionCredentials', GLib.Variant('(s)', (sender,)),
                GLib.VariantType.new('(a{sv})'), Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
            pid = credentials['ProcessID']
            if credentials['UnixUserID'] != os.getuid() or pid in self.admitted:
                raise ValueError('Caller is not an available launcher')
            unit = validate_request(self.security_tag, pid, parameters.unpack())
            if unit in self.pending:
                raise ValueError('Scope is already pending')
            lease = self.children.pin(pid)
            state = {'lease': lease, 'sender': sender, 'invocation': invocation,
                     'job': None, 'early': None, 'uncertain': False}
            self.pending[unit] = state
            self.admitted.add(pid)
            lease = None
        except (KeyError, TypeError, ValueError, OSError, GLib.Error):
            if lease:
                lease.close()
            invocation.return_dbus_error('org.freedesktop.DBus.Error.AccessDenied', 'SCOPE_CALLER_UNAVAILABLE')
            return

        def completed(host, result, _data):
            try:
                reply = host.call_finish(result)
            except GLib.Error as error:
                if self.pending.get(unit) is not state:
                    return
                name = Gio.DBusError.get_remote_error(error)
                self.reply_error(state, name or 'org.freedesktop.DBus.Error.Failed', 'SCOPE_REQUEST_FAILED')
                if name and name.startswith('org.freedesktop.systemd1.'):
                    self.retire(unit)
                else:
                    # A local timeout/disconnect does not cancel a numeric-PID
                    # request at systemd. Keep the child unreaped until the real
                    # job finishes or its exact systemd peer disappears.
                    state['uncertain'] = True
                    if state['early'] is not None:
                        self.retire(unit)
                return
            if self.pending.get(unit) is not state:
                return
            state['job'] = reply.unpack()[0]
            state['invocation'].return_value(reply)
            state['invocation'] = None
            self.record({'event': 'scope-accepted', 'pid': pid, 'started': state['lease'].started})
            if state['early'] is not None:
                self.relay(unit, state['early'])

        self.host.call(self.host_owner, PATH, INTERFACE, method, parameters,
            GLib.VariantType.new('(o)'), Gio.DBusCallFlags.NONE, 10000, None, completed, None)

    @staticmethod
    def reply_error(state, name, code):
        invocation = state['invocation']
        if invocation is not None:
            invocation.return_dbus_error(name, code)
            state['invocation'] = None

    def job_removed(self, _connection, _sender, _path, _interface, _signal, parameters):
        unit = parameters.unpack()[2]
        state = self.pending.get(unit)
        if state is None:
            return
        if state['uncertain']:
            self.retire(unit)
        elif state['job'] is None:
            state['early'] = parameters
        else:
            self.relay(unit, parameters)

    def relay(self, unit, parameters):
        state = self.pending.get(unit)
        if state is None or parameters.unpack()[1] != state['job']:
            return
        try:
            self.private.emit_signal(state['sender'], PATH, INTERFACE, 'JobRemoved', parameters)
            self.record({'event': 'scope-completed', 'pid': state['lease'].pid,
                         'started': state['lease'].started, 'result': parameters.unpack()[3]})
        finally:
            self.retire(unit)

    def owner_changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        name, _old, new = parameters.unpack()
        if name == self.host_owner and not new:
            for unit in tuple(self.pending):
                self.reply_error(self.pending[unit], 'org.freedesktop.DBus.Error.ServiceUnknown', 'SCOPE_SERVICE_ENDED')
                self.retire(unit)

    def retire(self, unit):
        state = self.pending.pop(unit, None)
        if state:
            state['lease'].close()

    def close(self):
        if self.pending:
            raise RuntimeError('Cannot release launch identity while systemd may still use it')
        if self.host:
            if self.subscription:
                self.host.signal_unsubscribe(self.subscription)
            if self.owner_subscription:
                self.host.signal_unsubscribe(self.owner_subscription)
            if not self.host.is_closed():
                self.host.close_sync(None)
            self.host = None
        if self.private:
            if self.registration:
                self.private.unregister_object(self.registration)
            if not self.private.is_closed():
                self.private.close_sync(None)
            self.private = None
