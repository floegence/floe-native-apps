import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch, mock_open

from host_desktop_contract import DesktopError
from host_desktop_login_text import LoginText


class LoginTextTests(unittest.TestCase):
    def backend(self):
        backend = LoginText.__new__(LoginText)
        backend.closed = False
        backend.identity = SimpleNamespace(refresh=lambda: 'ready', selected={'id': 'real'}, observed={'VTNr': 2})
        backend.session, backend.compositor = 'real', '100:22'
        backend.path, backend.owner = '/session', ':1.1'
        backend.text = backend.request = backend.timer = None
        backend.subscription = 1
        backend.writes = threading.BoundedSemaphore(1)
        calls, completed, timers = [], [], []
        backend.completed = lambda request, code: completed.append((request, code))
        backend.prepared = lambda request: calls.append(('prepared', request))
        backend.GLib = SimpleNamespace(Variant=lambda signature, value: (signature, value),
            source_remove=lambda timer: timers.append(('remove', timer)),
            timeout_add=lambda delay, callback: timers.append((delay, callback)) or 1)
        backend.bus = SimpleNamespace(signal_unsubscribe=lambda _: None,
            call_sync=lambda *args: calls.append((args[3], None)))
        backend.Gio = SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0))
        backend.call = lambda method, signature=None, values=(), **_: calls.append((method, values))
        return backend, calls, completed, timers

    def test_text_remains_memory_only_and_shortcut_is_ordered(self):
        backend, calls, completed, timers = self.backend()
        with patch.object(backend, 'active'):
            backend.paste({'id': 7, 'text': 'abc\u4e2d\u6587\U0001f600'})
        self.assertEqual(calls[0][0], 'SetSelection')
        self.assertEqual(calls[1], ('prepared', 7))
        self.assertEqual(completed, [])
        self.assertEqual(timers[0][0], 800)
        timers[0][1]()
        self.assertEqual(completed, [(7, 'CLIPBOARD_TARGET_UNAVAILABLE')])
        backend.close()
        self.assertIsNone(backend.text)
        self.assertTrue(backend.closed)

    def test_reject_invalid_overlapping_or_retired_text_before_input(self):
        backend, calls, _, _ = self.backend()
        for text in ('', 'nul\x00', 'x' * 16001):
            with self.assertRaisesRegex(DesktopError, 'CLIPBOARD_INPUT_REJECTED'):
                backend.paste({'id': 1, 'text': text})
        backend.request = 1
        with self.assertRaisesRegex(DesktopError, 'CLIPBOARD_INPUT_REJECTED'):
            backend.paste({'id': 2, 'text': 'next'})
        backend.request = None
        with patch.object(backend, 'active', side_effect=DesktopError('CLIPBOARD_SESSION_RETIRED')):
            with self.assertRaisesRegex(DesktopError, 'CLIPBOARD_SESSION_RETIRED'):
                backend.paste({'id': 3, 'text': 'next'})
        self.assertEqual(calls, [])

    def test_revocation_stops_virtual_keyboard_and_completes_once(self):
        backend, calls, completed, _ = self.backend()
        backend.text, backend.request = 'temporary', 9
        backend.close()
        backend.close()
        self.assertEqual(completed, [(9, 'CLIPBOARD_SESSION_RETIRED')])
        self.assertEqual([name for name, _ in calls], ['Stop'])
        self.assertIsNone(backend.text)

    def test_every_call_requires_current_session_vt_and_compositor_identity(self):
        backend, _, _, _ = self.backend()
        stat = '100 (gnome shell) ' + ' '.join(['S'] * 19 + ['22'])
        def source(path, **_):
            return mock_open(read_data='tty2' if path.startswith('/sys/') else stat)()
        with patch('host_desktop_login_text.open', side_effect=source), \
                patch('host_desktop_login_text.os.readlink', return_value='/usr/bin/gnome-shell'):
            backend.active()
            backend.identity.selected['id'] = 'successor'
            with self.assertRaisesRegex(DesktopError, 'CLIPBOARD_SESSION_RETIRED'):
                backend.active()
            backend.identity.selected['id'] = 'real'
            backend.identity.observed['VTNr'] = 3
            with self.assertRaisesRegex(DesktopError, 'CLIPBOARD_SESSION_RETIRED'):
                backend.active()
            backend.identity.observed['VTNr'] = 2
            backend.compositor = '100:23'
            with self.assertRaisesRegex(DesktopError, 'CLIPBOARD_SESSION_RETIRED'):
                backend.active()
            backend.compositor = '100:22'
            backend.identity.refresh = lambda: 'locked'
            with self.assertRaisesRegex(DesktopError, 'CLIPBOARD_SESSION_RETIRED'):
                backend.active()


if __name__ == '__main__':
    unittest.main()
