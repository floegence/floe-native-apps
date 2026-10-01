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
                self.assertEqual(first.consume(), 'first-restore')
                with self.assertRaisesRegex(DesktopError, 'AUTHORIZATION_PENDING'):
                    second.acquire_start()
                first.save('rotated-restore')
            finally:
                os.close(lease)
            successor = second.acquire_start()
            try:
                self.assertEqual(second.consume(), 'rotated-restore')
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
                    grant.consume()
            self.assertEqual(Path(grant.path).read_bytes(), original)

    def test_token_is_private_and_durably_consumed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            grant = PortalGrant(directory)
            grant.save('single-use-token')
            self.assertEqual(os.stat(grant.path).st_mode & 0o777, 0o600)
            self.assertEqual(grant.consume(), 'single-use-token')
            self.assertIsNone(PortalGrant(directory).consume())

    def test_refuse_symlink_and_readable_token(self):
        with tempfile.TemporaryDirectory() as directory:
            grant = PortalGrant(directory)
            target = Path(directory, 'other')
            target.write_text('{"version":1,"token":"private"}')
            os.symlink(target, grant.path)
            with self.assertRaises(DesktopError):
                grant.consume()
            self.assertEqual(target.read_text(), '{"version":1,"token":"private"}')
            os.unlink(grant.path)
            grant.save('token')
            os.chmod(grant.path, 0o644)
            with self.assertRaises(DesktopError):
                grant.consume()

    def test_consume_rejects_invalid_token_without_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            grant = PortalGrant(directory)
            for value in ('{"version":true,"token":"wrong"}', '{"version":1,"token":"nul\\u0000"}',
                          '{"version":1,"token":"x","token":"y"}'):
                Path(grant.path).write_text(value)
                os.chmod(grant.path, 0o600)
                with self.assertRaises(DesktopError):
                    grant.consume()
                self.assertEqual(Path(grant.path).read_text(), value)


class PortalClipboardTests(unittest.TestCase):
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
