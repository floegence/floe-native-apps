"""Native X11 text never uses advisory window properties or a replacement target."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop_context_test import Peer
from desktop_native import NativeFocus, NativeTarget
from input_context import Contexts
from desktop_x11 import NativeX11Contexts


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
            descendant=lambda child, parent: child in (42, 43) and parent == 42,
            focused_within=lambda xid: xid == 42)
        self.modules = Contexts.__new__(Contexts)
        self.modules.contexts = {120: {'sender': ':1.5', 'toolkit': 'gtk3'}}
        self.modules.pending, self.modules.sequence = None, 0
        self.modules.marker_clock = lambda: 0
        self.modules.display = self.display
        mock = patch('desktop_x11.ApplicationPeer', Peer)
        mock.start()
        self.addCleanup(mock.stop)
        self.adapter = NativeX11Contexts(self.native, self.tree, '/private/runtime', self.display, self.modules)
        self.addCleanup(self.adapter.close)

    def begin(self):
        token = self.adapter.context_for(self.native.target)
        self.assertIsNotNone(token)
        self.adapter.commit(token, '同🙂', self.completed.append)
        return token

    def test_only_real_x11_resource_owner_selects_a_registered_context(self):
        token = self.begin()
        self.assertEqual(token.peer.pid, 120)
        self.assertEqual(self.sent, [(1, token.target, ['x11-marker 1'])])
        self.assertFalse(self.modules.done(':1.5', 1))
        self.assertEqual(self.modules.take(':1.5', 1, 43), (1, '同🙂'))
        self.assertEqual(self.completed, [])
        self.assertTrue(self.modules.done(':1.5', 1))
        self.assertEqual(self.completed, [None])
        self.assertTrue(token.peer.closed)
        self.adapter.commit(token, 'late', self.completed.append)
        self.assertEqual(self.completed[-1], 'INPUT_CONTEXT_UNAVAILABLE')

    def test_missing_binding_owner_or_context_never_guesses_or_retries(self):
        self.owner = 999
        self.assertIsNone(self.adapter.context_for(self.native.target))
        self.owner = None
        self.assertIsNone(self.adapter.context_for(self.native.target))
        self.owner = 120
        self.native.x11_windows.clear()
        self.assertIsNone(self.adapter.context_for(self.native.target))
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
                self.assertIsNone(self.modules.take(':1.5', self.modules.sequence, 42))
                self.assertEqual(self.completed[-1], 'INPUT_TARGET_UNAVAILABLE')
                self.assertTrue(token.peer.closed)
                self.native.epoch = 1
                self.native.focus = NativeFocus(3, 1, 700, 19, True)
                self.native.target = NativeTarget(1, 2)
                self.native.x11_windows[1] = 42
                self.owner, self.tree[120] = 120, 'application'

    def test_cancellation_drops_text_and_old_done_cannot_complete_equal_text(self):
        token = self.begin()
        self.adapter.cancel(token)
        self.assertTrue(token.peer.closed)
        self.begin()
        self.assertIsNone(self.modules.take(':1.5', 1, 42))
        self.assertFalse(self.modules.done(':1.5', 1))
        self.assertEqual(self.modules.take(':1.5', 2, 42), (2, '同🙂'))
        self.assertTrue(self.modules.done(':1.5', 2))
        self.assertEqual(self.completed, [None])


if __name__ == '__main__':
    unittest.main()
