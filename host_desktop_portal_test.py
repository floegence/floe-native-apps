import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from host_desktop_contract import DesktopError
from host_desktop_portal import PortalGrant, PortalSession


class PortalGrantTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
