"""Xwayland uses the same native marker owner and never trusts advisory PIDs."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop_context import NativeContexts
from desktop_context_test import Peer
from desktop_native import NativeFocus, NativeTarget


class NativeX11Tests(unittest.TestCase):
    def setUp(self):
        self.sent, self.completed = [], []
        self.native = SimpleNamespace(target=NativeTarget(1, 2), epoch=1, closed=False,
            focus=NativeFocus(3, 1, 700, 19, True),
            windows={1: SimpleNamespace(protocol='x11', pid=999)}, x11_windows={1: 42},
            submit=lambda *args: self.sent.append(args))
        self.tree = {120: 'application', 700: 'Xwayland', 999: 'foreign'}
        self.owner = 120
        self.display = SimpleNamespace(owner_pid=lambda xid: self.owner if xid == 42 else None,
            focused_within=lambda xid: xid == 42)
        mock = patch('desktop_context.ApplicationPeer', Peer)
        mock.start()
        self.addCleanup(mock.stop)
        self.contexts = NativeContexts(self.native, self.tree, '/private/runtime')
        self.contexts.x11 = self.display
        self.contexts.register(':1.5', 120, 1, 'qt6-native')
        self.addCleanup(self.contexts.close)

    def begin(self):
        token = self.contexts.context_for(self.native.target)
        self.assertIsNotNone(token)
        self.contexts.commit(token, '同🙂', self.completed.append)
        return token

    def test_only_real_resource_owner_selects_a_registered_context(self):
        token = self.begin()
        self.assertEqual(token.surface_peer.pid, 120)
        self.assertEqual(token.surface, 42)
        self.assertEqual(self.sent, [(1, token.target, ['key 0 1', 'key 0 0'])])
        self.assertFalse(self.contexts.done(':1.5', 1))
        self.assertEqual(self.contexts.take(':1.5', 0, 42), (1, '同🙂'))
        self.assertTrue(self.contexts.done(':1.5', 1))
        self.assertEqual(self.completed, [])
        self.contexts.released(':1.5', 0)
        self.assertEqual(self.completed, [None])
        self.assertTrue(token.surface_peer.closed)

    def test_missing_binding_owner_or_context_never_guesses(self):
        self.owner = 999
        self.assertIsNone(self.contexts.context_for(self.native.target))
        self.owner = None
        self.assertIsNone(self.contexts.context_for(self.native.target))
        self.owner = 120
        self.native.x11_windows.clear()
        self.assertIsNone(self.contexts.context_for(self.native.target))
        self.assertEqual(self.sent, [])

    def test_connection_surface_resource_and_process_changes_revoke_late_take(self):
        for change in ('epoch', 'focus', 'target', 'resource', 'owner', 'process'):
            with self.subTest(change=change):
                token = self.begin()
                if change == 'epoch': self.native.epoch += 1
                elif change == 'focus': self.native.focus = NativeFocus(4, 1, 700, 19, True)
                elif change == 'target': self.native.target = NativeTarget(1, 3)
                elif change == 'resource': self.native.x11_windows[1] = 43
                elif change == 'owner': self.owner = 999
                elif change == 'process': del self.tree[120]
                self.assertIsNone(self.contexts.take(':1.5', 0, 42))
                self.assertEqual(self.completed[-1], 'INPUT_TARGET_UNAVAILABLE')
                self.assertTrue(token.surface_peer.closed)
                self.contexts.released(':1.5', 0)
                self.native.epoch = 1
                self.native.focus = NativeFocus(3, 1, 700, 19, True)
                self.native.target = NativeTarget(1, 2)
                self.native.x11_windows[1] = 42
                self.owner, self.tree[120] = 120, 'application'

    def test_cancelled_press_must_drain_before_another_x11_transaction(self):
        token = self.begin()
        self.contexts.cancel(token)
        self.assertTrue(token.surface_peer.closed)
        self.assertIsNone(self.contexts.take(':1.5', 0, 42))
        self.contexts.released(':1.5', 0)
        self.begin()
        self.assertFalse(self.contexts.done(':1.5', 1))
        self.assertEqual(self.contexts.take(':1.5', 0, 42), (2, '同🙂'))
        self.contexts.released(':1.5', 0)
        self.assertTrue(self.contexts.done(':1.5', 2))
        self.assertEqual(self.completed, [None])

    def test_preceding_seat_focus_is_checked_at_marker_consumption(self):
        self.display.focused_within = lambda _: False
        self.begin()
        self.display.focused_within = lambda xid: xid == 42
        self.assertEqual(self.contexts.take(':1.5', 0, 42), (1, '同🙂'))
        self.contexts.released(':1.5', 0)
        self.assertTrue(self.contexts.done(':1.5', 1))
        self.assertEqual(self.completed, [None])

    def test_pointer_root_or_changed_x11_focus_cannot_admit_or_complete(self):
        for boundary in ('take', 'done', 'release'):
            with self.subTest(boundary=boundary):
                self.display.focused_within = lambda xid: xid == 42
                self.begin()
                sequence = self.contexts.sequence
                if boundary != 'take':
                    self.assertIsNotNone(self.contexts.take(':1.5', 0, 42))
                if boundary == 'release':
                    self.assertTrue(self.contexts.done(':1.5', sequence))
                self.display.focused_within = lambda _: False
                if boundary == 'take':
                    self.assertIsNone(self.contexts.take(':1.5', 0, 42))
                elif boundary == 'done':
                    self.assertFalse(self.contexts.done(':1.5', sequence))
                self.contexts.released(':1.5', 0)
                self.assertEqual(self.completed[-1], 'INPUT_TARGET_UNAVAILABLE')
                self.assertFalse(self.contexts.markers.slots)


if __name__ == '__main__':
    unittest.main()
