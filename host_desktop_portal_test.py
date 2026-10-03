import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from host_desktop_contract import DesktopError
from host_desktop_portal import PortalGrant, PortalSession


class PortalGrantTests(unittest.TestCase):
    def test_only_the_pending_start_owns_token_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = PortalGrant(directory), PortalGrant(directory)
            lease = first.acquire_start()
            try:
                first.save('first-restore')
                self.assertEqual(first.begin_restore(), 'first-restore')
                with self.assertRaisesRegex(DesktopError, 'AUTHORIZATION_PENDING'):
                    second.acquire_start()
                first.save('rotated-restore')
            finally:
                os.close(lease)
            successor = second.acquire_start()
            try:
                self.assertEqual(second.begin_restore(), 'rotated-restore')
                # The preceding PortalSession can keep its granted media open.
                self.assertEqual(os.stat(Path(directory, 'portal-start.lock')).st_mode & 0o777, 0o600)
            finally:
                os.close(successor)

    def test_failed_durable_consume_never_returns_a_restore_token(self):
        with tempfile.TemporaryDirectory() as directory:
            grant = PortalGrant(directory)
            grant.save('not-yet-consumed')
            original = Path(grant.path).read_bytes()
            with patch.object(grant, '_save', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(DesktopError, 'RESTORE_TOKEN_STORAGE_FAILED'):
                    grant.begin_restore()
            self.assertEqual(Path(grant.path).read_bytes(), original)

    def test_token_is_private_and_durably_consumed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            grant = PortalGrant(directory)
            grant.save('single-use-token')
            self.assertEqual(os.stat(grant.path).st_mode & 0o777, 0o600)
            self.assertEqual(grant.begin_restore(), 'single-use-token')
            self.assertIsNone(PortalGrant(directory).begin_restore())

    def test_refuse_symlink_and_readable_token(self):
        with tempfile.TemporaryDirectory() as directory:
            grant = PortalGrant(directory)
            target = Path(directory, 'other')
            target.write_text('{"version":1,"token":"private"}')
            os.symlink(target, grant.path)
            with self.assertRaises(DesktopError):
                grant.begin_restore()
            self.assertEqual(target.read_text(), '{"version":1,"token":"private"}')
            os.unlink(grant.path)
            grant.save('token')
            os.chmod(grant.path, 0o644)
            with self.assertRaises(DesktopError):
                grant.begin_restore()

    def test_consume_rejects_invalid_token_without_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            grant = PortalGrant(directory)
            for value in ('{"version":true,"token":"wrong"}', '{"version":1,"token":"nul\\u0000"}',
                          '{"version":1,"token":"x","token":"y"}'):
                Path(grant.path).write_text(value)
                os.chmod(grant.path, 0o600)
                with self.assertRaises(DesktopError):
                    grant.begin_restore()
                self.assertEqual(Path(grant.path).read_text(), value)


    def test_inspection_never_consumes_legacy_grant(self):
        with tempfile.TemporaryDirectory() as directory:
            grant = PortalGrant(directory)
            Path(grant.path).write_text('{"version":1,"token":"legacy-token"}')
            os.chmod(grant.path, 0o600)
            original = Path(grant.path).read_bytes()
            self.assertEqual(grant.inspect(), 'saved')
            self.assertEqual(grant.inspect(), 'saved')
            self.assertEqual(Path(grant.path).read_bytes(), original)

    def test_submitted_grant_is_retained_but_never_replayed_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            grant = PortalGrant(directory)
            grant.save('single-use-token')
            self.assertEqual(grant.begin_restore(), 'single-use-token')
            self.assertIn('single-use-token', Path(grant.path).read_text())
            restarted = PortalGrant(directory)
            self.assertEqual(restarted.inspect(), 'unknown')
            self.assertIsNone(restarted.begin_restore())
            restarted.save('replacement-token')
            self.assertEqual(restarted.inspect(), 'saved')
            self.assertEqual(restarted.begin_restore(), 'replacement-token')

    def test_forget_serializes_with_restore_and_never_requests_system_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            grant = PortalGrant(directory)
            grant.save('saved-token')
            lease = grant.acquire_start()
            try:
                with self.assertRaisesRegex(DesktopError, 'AUTHORIZATION_PENDING'):
                    grant.forget()
                self.assertEqual(grant.inspect(), 'saved')
            finally:
                os.close(lease)
            grant.forget()
            self.assertEqual(grant.inspect(), 'needs_consent')
            self.assertNotIn('saved-token', Path(grant.path).read_text())


class PortalRecoveryTests(unittest.TestCase):
    def run_portal(self, directory, failure=None, version=2, replacement='new-token'):
        grant = PortalGrant(directory)
        gio = SimpleNamespace(DBusSignalFlags=SimpleNamespace(NONE=0), DBusCallFlags=SimpleNamespace(NONE=0))
        loop = SimpleNamespace(Error=RuntimeError, Variant=lambda signature, value: (signature, value), VariantType=SimpleNamespace(new=lambda v: v))
        calls, results, states = [], [], []
        def pipewire(*_):
            if failure == 'OpenPipeWireRemote':
                raise RuntimeError('fixture')
            return SimpleNamespace(unpack=lambda: (0,)), SimpleNamespace(get=lambda _: os.open(os.devnull, os.O_RDONLY))
        bus = SimpleNamespace(signal_subscribe=lambda *_: 1, signal_unsubscribe=lambda _: None,
            call_sync=lambda *_: None, call_with_unix_fd_list_sync=pipewire)
        portal = PortalSession(bus, gio, loop, grant, states.append)
        portal.version = lambda interface: 0 if interface == portal.CLIPBOARD else version
        def request(_interface, method, _prefix, options, done):
            calls.append((method, options))
            if method == failure:
                done(None, 'PORTAL_UNAVAILABLE')
            elif method == 'CreateSession':
                done({'session_handle': portal.PATH + '/session/fixture'}, None)
            elif method == 'Start':
                done({'streams': [(1, {'size': (1920, 1080)})], 'devices': 3, 'restore_token': replacement}, None)
            else:
                done({}, None)
        portal._request = request
        portal.start(True, lambda streams, error: results.append(error))
        portal.close()
        return calls, results, states

    def test_request_failures_never_erase_evidence_or_replay_submitted_tokens(self):
        for failure in ('CreateSession', 'SelectDevices', 'SelectSources', 'Start'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                grant = PortalGrant(directory)
                grant.save('old-token')
                _, results, _ = self.run_portal(directory, failure)
                self.assertEqual(results, ['PORTAL_UNAVAILABLE'])
                self.assertIn('old-token', Path(grant.path).read_text())
                self.assertEqual(grant.inspect(), 'saved' if failure == 'CreateSession' else 'unknown')
                calls, results, _ = self.run_portal(directory)
                devices = dict(calls)['SelectDevices']
                self.assertEqual('restore_token' in devices, failure == 'CreateSession')
                self.assertEqual(results, [None])
                self.assertEqual(grant.inspect(), 'saved')

    def test_media_failure_retains_new_single_use_token_for_next_process(self):
        with tempfile.TemporaryDirectory() as directory:
            grant = PortalGrant(directory)
            grant.save('old-token')
            _, results, _ = self.run_portal(directory, 'OpenPipeWireRemote')
            self.assertEqual(results, ['PIPEWIRE_UNAVAILABLE'])
            self.assertEqual(grant.inspect(), 'unknown')
            self.assertIn('new-token', Path(grant.path).read_text())
            calls, results, _ = self.run_portal(directory, replacement='next-token')
            self.assertEqual(dict(calls)['SelectDevices']['restore_token'], ('s', 'new-token'))
            self.assertEqual(results, [None])
            self.assertNotIn('old-token', Path(grant.path).read_text())
            self.assertEqual(grant.inspect(), 'saved')

    def test_missing_replacement_is_not_reported_as_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            _, results, states = self.run_portal(directory, replacement=None)
            self.assertEqual(results, [None])
            self.assertEqual(PortalGrant(directory).inspect(), 'needs_consent')
            self.assertNotIn('authorization_saved', states)

    def test_old_portal_can_share_without_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            calls, results, _ = self.run_portal(directory, version=1)
            self.assertEqual(results, [None])
            self.assertNotIn('persist_mode', dict(calls)['SelectDevices'])
            self.assertEqual(PortalGrant(directory).inspect(), 'needs_consent')

    def test_candidate_survives_failed_final_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            grant = PortalGrant(directory)
            grant.save('old-token')
            grant.begin_restore()
            grant.stage('new-token')
            with patch.object(grant, '_save', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(DesktopError, 'RESTORE_TOKEN_STORAGE_FAILED'):
                    grant.save('new-token')
            self.assertEqual(PortalGrant(directory).begin_restore(), 'new-token')

    def test_future_schema_cannot_be_overwritten_by_save_or_forget(self):
        with tempfile.TemporaryDirectory() as directory:
            grant = PortalGrant(directory)
            data = '{"version":99,"token":"future"}'
            Path(grant.path).write_text(data)
            os.chmod(grant.path, 0o600)
            for operation in (grant.inspect, grant.begin_restore, grant.forget, lambda: grant.save('replacement')):
                with self.assertRaisesRegex(DesktopError, 'RESTORE_TOKEN_INVALID'):
                    operation()
                self.assertEqual(Path(grant.path).read_text(), data)


class PortalClipboardTests(unittest.TestCase):
    def test_selection_types_from_standard_and_gnome_46_portals(self):
        loop = SimpleNamespace(Error=RuntimeError)
        gio = SimpleNamespace(DBusSignalFlags=SimpleNamespace(NONE=0))
        bus = SimpleNamespace(signal_subscribe=lambda *_: 1)
        portal = PortalSession(bus, gio, loop, None, lambda _: None)
        portal.session = '/fixture'
        for formats in (['text/plain;charset=utf-8'], (['text/plain;charset=utf-8'],)):
            with self.subTest(formats=formats):
                parameters = SimpleNamespace(unpack=lambda: ('/fixture', {'mime_types': formats}))
                portal._selection_changed(None, None, None, None, None, parameters)
                self.assertEqual(portal.clipboard_types, ['text/plain;charset=utf-8'])

    def test_pending_reads_are_bounded_and_revocation_suppresses_completed_text(self):
        dispatched, workers, results, descriptors = [], [], [], []
        loop = SimpleNamespace(Error=RuntimeError, idle_add=dispatched.append)
        gio = SimpleNamespace(DBusSignalFlags=SimpleNamespace(NONE=0))
        bus = SimpleNamespace(signal_subscribe=lambda *_: 1)
        portal = PortalSession(bus, gio, loop, None, lambda _: None)
        portal.session = '/fixture'
        portal.clipboard = portal.clipboard_enabled = True
        portal.clipboard_types = ['text/plain']
        def transfer(*_):
            reader, writer = os.pipe()
            os.write(writer, b'fixture clipboard'); os.close(writer)
            descriptors.append(reader)
            return reader
        portal._clipboard_fd = transfer
        def thread(**options):
            return SimpleNamespace(start=lambda: workers.append(options['target']))
        try:
            with patch('host_desktop_portal.threading.Thread', side_effect=thread):
                for _ in range(3):
                    portal.read_clipboard(lambda text, error: results.append((text, error)))
            self.assertEqual(results, [(None, 'CLIPBOARD_BUSY')])
            self.assertEqual(len(workers), 2)
            for worker in workers:
                worker()
            descriptors.clear()  # Workers closed the real descriptors.
            portal.clipboard_epoch += 1
            for callback in dispatched:
                callback()
            self.assertEqual(results, [(None, 'CLIPBOARD_BUSY')])
            self.assertTrue(portal.clipboard_reads.acquire(blocking=False))
            self.assertTrue(portal.clipboard_reads.acquire(blocking=False))
        finally:
            for descriptor in descriptors:
                os.close(descriptor)


class PortalStartTests(unittest.TestCase):
    def test_cancelled_consent_releases_start_for_another_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            loop = SimpleNamespace(Error=RuntimeError, Variant=lambda signature, value: (signature, value))
            gio = SimpleNamespace(DBusSignalFlags=SimpleNamespace(NONE=0))
            bus = SimpleNamespace(signal_subscribe=lambda *_: 1, signal_unsubscribe=lambda _: None)
            first = PortalSession(bus, gio, loop, PortalGrant(directory), lambda _: None)
            second = PortalSession(bus, gio, loop, PortalGrant(directory), lambda _: None)
            first.version = second.version = lambda _: 2
            requests, results = [], []
            first._request = second._request = lambda *args: requests.append(args[-1])
            first.start(True, lambda streams, error: results.append(error))
            second.start(True, lambda streams, error: results.append(error))
            self.assertEqual(results, ['AUTHORIZATION_PENDING'])
            self.assertEqual(len(requests), 1)
            requests.pop()(None, 'PERMISSION_CANCELLED')
            self.assertEqual(results, ['AUTHORIZATION_PENDING', 'PERMISSION_CANCELLED'])
            second.start(True, lambda streams, error: results.append(error))
            self.assertEqual(len(requests), 1)
            first.close()
            with self.assertRaisesRegex(DesktopError, 'AUTHORIZATION_PENDING'):
                PortalGrant(directory).acquire_start()
            second.close()
            successor = PortalGrant(directory).acquire_start()
            os.close(successor)


if __name__ == '__main__':
    unittest.main()
