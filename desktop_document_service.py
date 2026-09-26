"""Bounded private facade to the official host document authorization service."""
import os

from gi.repository import Gio, GLib
from desktop_documents import DocumentGrants, MAX_FILES

NAME, PATH = 'org.freedesktop.portal.Documents', '/org/freedesktop/portal/documents'
# Official wire signatures; expose no host document listing or arbitrary lookup.
SIGNATURES = {
    'GetMountPoint': ((), ('ay',)), 'Add': (('h', 'b', 'b'), ('s',)),
    'AddFull': (('ah', 'u', 's', 'as'), ('as', 'a{sv}')),
    'AddNamed': (('h', 'ay', 'b', 'b'), ('s',)),
    'AddNamedFull': (('h', 'ay', 'u', 's', 'as'), ('s', 'a{sv}')),
    'GrantPermissions': (('s', 's', 'as'), ()), 'RevokePermissions': (('s', 's', 'as'), ()),
    'Delete': (('s',), ()), 'Info': (('s',), ('ay', 'a{sas}')),
}
XML = '<node><interface name="' + NAME + '"><property name="version" type="u" access="read"/>' + ''.join(
    '<method name="' + method + '">' + ''.join(
        '<arg type="' + signature + '" direction="' + direction + '"/>'
        for direction, signatures in zip(('in', 'out'), pair) for signature in signatures) + '</method>'
    for method, pair in SIGNATURES.items()) + '</interface></node>'


class DocumentService:
    def __init__(self, private, host_address, app_id, authority, record):
        self.private, self.authority, self.record = private, authority, record
        self.grants, self.calls = DocumentGrants(app_id), {}
        self.host, self.registration, self.subscription = None, 0, 0
        self.host_closed = 0
        self.closed, self.owned_name = False, False
        try:
            self.host = Gio.DBusConnection.new_for_address_sync(host_address,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None, None)
            self.host.set_exit_on_close(False)
            self.host_closed = self.host.connect('closed', lambda *_args: self.close())
            if self.host.get_guid() == private.get_guid():
                raise ValueError('Document host and private buses must differ')
            self.host_owner = self.host.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', (NAME,)),
                GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
            credentials = self.credentials(self.host, self.host_owner)
            if credentials.get('UnixUserID') != os.getuid():
                raise ValueError('Host document service belongs to another user')
            self.version = self.host.call_sync(self.host_owner, PATH, 'org.freedesktop.DBus.Properties', 'Get',
                GLib.Variant('(ss)', (NAME, 'version')), GLib.VariantType.new('(v)'),
                Gio.DBusCallFlags.NONE, 3000, None).get_child_value(0).get_variant()
            if self.version.get_type_string() != 'u' or self.version.unpack() < 3:
                raise ValueError('Host document protocol is unsupported')
            self.node = Gio.DBusNodeInfo.new_for_xml(XML)
            self.registration = private.register_object(PATH, self.node.interfaces[0], self.call, self.property, None)
            self.subscription = self.host.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus',
                'NameOwnerChanged', '/org/freedesktop/DBus', self.host_owner, Gio.DBusSignalFlags.NONE,
                self.owner_changed)
            reply = private.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                'RequestName', GLib.Variant('(su)', (NAME, 4)), GLib.VariantType.new('(u)'),
                Gio.DBusCallFlags.NONE, 3000, None)
            if reply.unpack()[0] != 1:
                raise ValueError('Private document service is already owned')
            self.owned_name = True
        except BaseException:
            self.close()
            raise

    @staticmethod
    def credentials(connection, sender):
        return connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetConnectionCredentials', GLib.Variant('(s)', (sender,)),
            GLib.VariantType.new('(a{sv})'), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]

    def property(self, _connection, _sender, _path, _interface, name):
        return self.version if name == 'version' and not self.closed else None

    def call(self, connection, sender, _path, _interface, method, parameters, invocation):
        caller, request, descriptors = None, None, []
        try:
            if self.closed or parameters.get_size() > 128 * 1024:
                raise ValueError('Document operation is unavailable')
            caller = self.authority.admit(self.credentials(connection, sender))
            descriptor_list = invocation.get_message().get_unix_fd_list()
            if descriptor_list:
                if descriptor_list.get_length() > MAX_FILES:
                    raise ValueError('Document descriptor limit exceeded')
                for index in range(descriptor_list.get_length()):
                    descriptors.append(descriptor_list.get(index))
            request = self.grants.admit(caller, method, parameters.unpack(), descriptors)
            if not caller.valid():
                raise ValueError('Document caller identity changed')
        except (KeyError, TypeError, ValueError, OSError, GLib.Error):
            if request:
                self.grants.failed(request)
            if caller:
                caller.close()
            invocation.return_dbus_error('org.freedesktop.DBus.Error.AccessDenied', 'DOCUMENT_CALLER_UNAVAILABLE')
            return
        finally:
            for descriptor in descriptors:
                os.close(descriptor)
        self.calls[request] = invocation
        self.record({'event': 'document-request', 'method': method, 'role': caller.role})

        def completed(host, result, _data):
            try:
                reply, reply_fds = host.call_with_unix_fd_list_finish(result)
                if request not in self.calls:
                    return
                if reply.get_size() > 128 * 1024 or (reply_fds and reply_fds.get_length()):
                    raise ValueError('Official document reply exceeds the contract')
                self.grants.complete(request, reply.unpack())
                current = self.calls.pop(request)
                if not caller.valid():
                    current.return_dbus_error('org.freedesktop.DBus.Error.Failed', 'DOCUMENT_CALLER_ENDED')
                else:
                    current.return_value(reply)
                self.record({'event': 'document-result', 'method': method, 'result': 'completed'})
            except (ValueError, GLib.Error) as error:
                current = self.calls.pop(request, None)
                if current:
                    self.grants.failed(request)
                    name = Gio.DBusError.get_remote_error(error) if isinstance(error, GLib.Error) else None
                    current.return_dbus_error(name or 'org.freedesktop.DBus.Error.Failed', 'DOCUMENT_REQUEST_FAILED')
                    self.record({'event': 'document-result', 'method': method, 'result': 'failed'})
            finally:
                caller.close()
        # Pin the real host peer, preserve original flags and pass actual FDs.
        # A timeout does not revoke a completed host grant or replay an export.
        try:
            self.host.call_with_unix_fd_list(self.host_owner, PATH, NAME, method, parameters,
                GLib.VariantType.new('(' + ''.join(SIGNATURES[method][1]) + ')'),
                Gio.DBusCallFlags.NONE, 5000, descriptor_list, None, completed, None)
        except GLib.Error:
            self.calls.pop(request, None)
            self.grants.failed(request)
            caller.close()
            invocation.return_dbus_error('org.freedesktop.DBus.Error.Failed', 'DOCUMENT_REQUEST_FAILED')

    def owner_changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        owner, _old, new = parameters.unpack()
        if owner == self.host_owner and not new:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        for request, invocation in tuple(self.calls.items()):
            invocation.return_dbus_error('org.freedesktop.DBus.Error.ServiceUnknown', 'DOCUMENT_SERVICE_ENDED')
            request.caller.close()
        self.calls.clear()
        self.grants.close()
        if self.registration:
            self.private.unregister_object(self.registration)
        if self.owned_name and not self.private.is_closed():
            try:
                self.private.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                    'ReleaseName', GLib.Variant('(s)', (NAME,)), GLib.VariantType.new('(u)'),
                    Gio.DBusCallFlags.NONE, 2000, None)
            except GLib.Error:
                pass
        if self.host:
            if self.host_closed:
                self.host.disconnect(self.host_closed)
            if self.subscription:
                self.host.signal_unsubscribe(self.subscription)
            if not self.host.is_closed():
                self.host.close_sync(None)
