"""Scope authority and real-job ordering without a desktop bus in source CI."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import os

from application_scope import ScopeService, validate_request


class Variant:
    def __init__(self, _signature, value):
        self.value = value

    def unpack(self):
        return self.value


class BusError(Exception):
    def __init__(self, name=None):
        self.name = name


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.unit = 'snap.firefox.firefox-77218fe2-ab29-4bc5-a7be-30a9d58bbaf2.scope'
        self.pid = 123
        self.job = '/org/freedesktop/systemd1/job/44'
        self.request = (self.unit, 'fail', [('PIDs', [self.pid])], [])
        self.lease = SimpleNamespace(pid=self.pid, started=888, close=Mock())
        service = self.service = ScopeService.__new__(ScopeService)
        service.GLib = SimpleNamespace(Variant=Variant, VariantType=SimpleNamespace(new=lambda x: x), Error=BusError)
        service.Gio = SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0),
                                     DBusError=SimpleNamespace(get_remote_error=lambda e: e.name))
        service.children = Mock(pin=Mock(return_value=self.lease))
        service.security_tag = 'snap.firefox.firefox'
        service.record = Mock()
        service.pending, service.admitted = {}, set()
        service.host_owner = ':1.8'
        service.host, service.private = Mock(), Mock()
        service.private.call_sync.return_value = Variant('', [{'UnixUserID': os.getuid(), 'ProcessID': self.pid}])
        self.invocation = Mock()

    def call(self, request=None):
        self.service.call(self.service.private, ':1.9', '', '', 'StartTransientUnit',
                          Variant('', self.request if request is None else request), self.invocation)

    def reply(self, error=None):
        self.service.host.call_finish.side_effect = error
        self.service.host.call_finish.return_value = Variant('', [self.job])
        callback = self.service.host.call.call_args.args[-2]
        callback(self.service.host, object(), None)

    def complete(self, job=None):
        self.service.job_removed(None, ':1.8', '', '', '',
                                 Variant('', [44, job or self.job, self.unit, 'done']))

    def test_exact_launcher_and_package_are_the_only_admitted_scope(self):
        for request in (
            (self.unit, 'replace', self.request[2], []),
            (self.unit, 'fail', [('PIDs', [124])], []),
            (self.unit, 'fail', [('PIDs', [123, 124])], []),
            (self.unit, 'fail', [('PIDs', [True])], []),
            (self.unit, 'fail', self.request[2] + [('Delegate', True)], []),
            (self.unit, 'fail', self.request[2], [('other.service', [])]),
            (self.unit.replace('firefox.firefox', 'other.other'), *self.request[1:]),
            (self.unit.replace('.scope', '.service'), *self.request[1:]),
        ):
            with self.subTest(request=request), self.assertRaises(ValueError):
                validate_request(self.service.security_tag, self.pid, request)
        self.assertEqual(validate_request(self.service.security_tag, self.pid, self.request), self.unit)

    def test_real_completion_is_relayed_once_and_only_to_original_caller(self):
        self.call()
        self.assertEqual(self.service.host.call.call_args.args[0], ':1.8')
        self.invocation.return_value.assert_not_called()
        self.reply()
        self.invocation.return_value.assert_called_once()
        self.lease.close.assert_not_called()
        self.complete('/org/freedesktop/systemd1/job/45')
        self.lease.close.assert_not_called()
        self.complete()
        self.complete()
        self.lease.close.assert_called_once()
        self.service.private.emit_signal.assert_called_once()
        self.assertEqual(self.service.private.emit_signal.call_args.args[0], ':1.9')

    def test_completion_before_method_reply_waits_for_actual_job_identity(self):
        self.call()
        self.complete()
        self.lease.close.assert_not_called()
        self.service.private.emit_signal.assert_not_called()
        self.reply()
        self.service.private.emit_signal.assert_called_once()
        self.lease.close.assert_called_once()

    def test_local_timeout_does_not_release_pid_until_real_systemd_completion(self):
        self.call()
        self.reply(BusError())
        self.invocation.return_dbus_error.assert_called_once()
        self.lease.close.assert_not_called()
        self.complete()
        self.lease.close.assert_called_once()
        self.service.private.emit_signal.assert_not_called()

    def test_bus_generated_no_reply_does_not_prove_remote_request_was_cancelled(self):
        self.call()
        self.reply(BusError('org.freedesktop.DBus.Error.NoReply'))
        self.lease.close.assert_not_called()
        self.complete()
        self.lease.close.assert_called_once()

    def test_exact_systemd_peer_loss_retires_pending_authority(self):
        self.call()
        self.service.owner_changed(None, '', '', '', '', Variant('', [':1.55', ':1.55', '']))
        self.lease.close.assert_not_called()
        self.service.owner_changed(None, '', '', '', '', Variant('', [':1.8', ':1.8', '']))
        self.lease.close.assert_called_once()
        self.reply()
        self.invocation.return_value.assert_not_called()

    def test_actual_service_error_is_preserved_and_releases_lease(self):
        self.call()
        self.reply(BusError('org.freedesktop.systemd1.UnitExists'))
        self.assertEqual(self.invocation.return_dbus_error.call_args.args[0], 'org.freedesktop.systemd1.UnitExists')
        self.lease.close.assert_called_once()

    def test_repeated_request_cannot_create_another_scope_for_same_launcher(self):
        self.call()
        self.call()
        self.service.host.call.assert_called_once()
        self.service.children.pin.assert_called_once_with(self.pid)
        self.invocation.return_dbus_error.assert_called_once()

    def test_unregistered_or_reused_process_never_reaches_host(self):
        self.service.children.pin.side_effect = ValueError('unregistered')
        self.call()
        self.service.host.call.assert_not_called()
        self.assertEqual(self.service.pending, {})


if __name__ == '__main__':
    unittest.main()
