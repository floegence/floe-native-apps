"""Installed official portals need not already be running on a headless host."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class Value:
    def __init__(self, signature, values):
        self.signature, self.values = signature, values

    def unpack(self):
        return self.values

    def get_size(self):
        return 64

    def get_child_value(self, _index):
        return self

    def get_variant(self):
        return Value('u', 5)

    def get_type_string(self):
        return self.signature


class DocumentStartupTests(unittest.TestCase):
    def setUp(self):
        self.running, self.activation, self.uid = False, 1, os.getuid()
        self.host = Mock()
        self.host.is_closed.return_value = False
        self.host.get_guid.return_value = 'host'
        self.host.call_sync.side_effect = self.call
        self.private = Mock()
        self.private.get_guid.return_value = 'private'
        self.private.call_sync.return_value = Value('(u)', (1,))
        self.Gio = SimpleNamespace(DBusConnection=SimpleNamespace(new_for_address_sync=Mock(return_value=self.host)),
            DBusConnectionFlags=SimpleNamespace(AUTHENTICATION_CLIENT=1, MESSAGE_BUS_CONNECTION=2),
            DBusCallFlags=SimpleNamespace(NONE=0), DBusSignalFlags=SimpleNamespace(NONE=0),
            DBusNodeInfo=SimpleNamespace(new_for_xml=lambda _: SimpleNamespace(interfaces=[object()])))
        self.GLib = SimpleNamespace(Variant=Value, VariantType=SimpleNamespace(new=lambda value: value), Error=RuntimeError)
        module = SimpleNamespace(Gio=self.Gio, GLib=self.GLib)
        with patch.dict('sys.modules', {'gi.repository': module}):
            spec = importlib.util.spec_from_file_location('document_service_fixture',
                Path(__file__).with_name('desktop_document_service.py'))
            loaded = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(loaded)
        self.Service = loaded.DocumentService
        self.Unavailable = loaded.HostDocumentServiceUnavailable

    def call(self, destination, path, interface, method, arguments, *_rest):
        if method == 'StartServiceByName':
            self.assertEqual((destination, path, interface),
                ('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus'))
            self.assertEqual(arguments.unpack(), ('org.freedesktop.portal.Documents', 0))
            self.running = True
            return Value('(u)', (self.activation,))
        if method == 'GetNameOwner':
            if not self.running:
                raise RuntimeError('org.freedesktop.DBus.Error.NameHasNoOwner')
            return Value('(s)', (':1.9',))
        if method == 'GetConnectionCredentials':
            self.assertEqual(arguments.unpack(), (':1.9',))
            return Value('(a{sv})', ({'UnixUserID': self.uid, 'ProcessID': 123},))
        if method == 'Get':
            self.assertEqual(destination, ':1.9')
            return Value('(v)', (5,))
        self.fail('Unexpected host operation: ' + method)

    def create(self):
        return self.Service(self.private, 'unix:path=/host/bus', 'org.example.Editor', Mock(), Mock())

    def test_official_activation_precedes_unique_owner_and_version_binding(self):
        service = self.create()
        self.addCleanup(service.close)
        methods = [call.args[3] for call in self.host.call_sync.call_args_list]
        self.assertEqual(methods, ['StartServiceByName', 'GetNameOwner', 'GetConnectionCredentials', 'Get'])
        self.assertEqual(service.host_owner, ':1.9')

    def test_already_running_service_is_pinned_without_replacement(self):
        self.running, self.activation = True, 2
        service = self.create()
        self.addCleanup(service.close)
        self.assertEqual(service.host_owner, ':1.9')

    def test_missing_host_service_reports_dependency_failure_and_closes_connection(self):
        self.host.call_sync.side_effect = RuntimeError('org.freedesktop.DBus.Error.ServiceUnknown')
        with self.assertRaises(self.Unavailable):
            self.create()
        self.host.close_sync.assert_called_once()
        self.private.register_object.assert_not_called()

    def test_private_export_failure_is_not_misclassified_as_missing_host_service(self):
        self.private.register_object.side_effect = RuntimeError('private export failed')
        with self.assertRaisesRegex(RuntimeError, 'private export failed'):
            self.create()
        self.host.close_sync.assert_called_once()

    def test_unknown_activation_result_or_wrong_user_never_exports_private_service(self):
        for result, uid in ((3, os.getuid()), (1, os.getuid() + 1)):
            with self.subTest(result=result, uid=uid):
                self.activation, self.uid = result, uid
                with self.assertRaises(ValueError):
                    self.create()
                self.private.register_object.assert_not_called()

    def test_restricted_instance_requires_export_despite_installed_filesystem_permissions(self):
        # AS_NEEDED_BY_APP uses installed package metadata. It may falsely
        # return an empty document ID when this instance revoked host access.
        service = self.create()
        self.addCleanup(service.close)
        caller = Mock(role='portal')
        caller.valid.return_value = True
        service.authority.admit.return_value = caller
        service.grants = Mock()
        service.grants.admit.return_value = Mock(caller=caller)
        self.private.call_sync.return_value = Value('(a{sv})', ({'UnixUserID': os.getuid(), 'ProcessID': 123},))
        invocation = Mock()
        invocation.get_message.return_value.get_unix_fd_list.return_value = None
        for method, signature, values, expected in (
            ('AddNamedFull', '(hayusas)', (0, b'new.txt\0', 7, 'org.example.Editor', ['read', 'write']),
             (0, b'new.txt\0', 3, 'org.example.Editor', ['read', 'write'])),
            ('AddFull', '(ahusas)', ([0], 15, 'org.example.Editor', ['read']),
             ([0], 11, 'org.example.Editor', ['read'])),
        ):
            with self.subTest(method=method):
                incoming = Value(signature, values)
                service.call(self.private, ':1.4', None, None, method, incoming, invocation)
                actual = self.host.call_with_unix_fd_list.call_args.args[4]
                self.assertEqual(actual.signature, signature)
                self.assertEqual(actual.unpack(), expected)
                self.assertEqual(incoming.unpack(), values)

    def test_private_bus_cannot_activate_a_host_dependency(self):
        self.private.get_guid.return_value = 'host'
        with self.assertRaises(ValueError):
            self.create()
        self.host.call_sync.assert_not_called()


if __name__ == '__main__':
    unittest.main()
