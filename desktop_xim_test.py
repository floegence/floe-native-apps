"""Native XIM markers, document completion and process admission stay ordered."""
import ctypes
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop_context import NativeContexts
from desktop_context_test import Peer
from desktop_native import NativeFocus, NativeTarget
import input_xim_test as fixtures

xim = fixtures.xim

with patch.dict(sys.modules, {'input_xim': xim}):
    import desktop_xim as native_xim


class NativeXIMTests(unittest.TestCase):
    def setUp(self):
        import desktop_context
        for module in (desktop_context, native_xim):
            mock = patch.object(module, 'ApplicationPeer', Peer)
            mock.start()
            self.addCleanup(mock.stop)
        self.sent, self.completed = [], []
        self.native = SimpleNamespace(target=NativeTarget(1, 2), epoch=1, closed=False,
            focus=NativeFocus(3, 1, 700, 19, True), windows={1:SimpleNamespace(protocol='x11')},
            x11_windows={1:42}, submit=lambda *args:self.sent.append(args))
        self.contexts = NativeContexts(self.native, {120:'app'}, '/private/runtime')
        self.contexts.x11 = SimpleNamespace(owner_pid=lambda xid:120 if xid == 42 else None,
                                           focused_within=lambda xid:xid == 42)
        self.bridge = native_xim.NativeXIMContexts.__new__(native_xim.NativeXIMContexts)
        self.bridge.__dict__.update(fixtures.XIMTest().bridge().__dict__)
        self.bridge.contexts, self.bridge.closed = self.contexts, False
        self.bridge.origins, self.bridge.clients = {7:(10, 20)}, {99:10}
        self.bridge.supported_contexts = {7}
        self.bridge.client_window = self.bridge.focus_window = lambda _:42
        self.bridge.descendant = lambda child,parent:child == parent == 42
        self.contexts.xim = self.bridge
        self.addCleanup(self.contexts.close)

    def begin(self):
        token = self.contexts.context_for(self.native.target)
        self.assertIsNotNone(token)
        self.contexts.commit(token, '同🙂', self.completed.append)
        return token

    def key(self, released=False, code=8, context=7):
        event = xim.Key(type=3 if released else 2, detail=code)
        self.bridge._forward_event(1, context, ctypes.pointer(event))

    def test_native_release_and_last_xim_sync_are_both_required(self):
        self.begin()
        self.key()
        self.assertEqual(b''.join(self.bridge.received).decode(), '同🙂')
        self.key(True)
        self.assertFalse(self.completed)
        fixtures.XIMTest().acknowledge(self.bridge)
        self.assertEqual(self.completed, [None])

    def test_native_release_can_arrive_after_xim_sync(self):
        self.begin()
        self.key()
        fixtures.XIMTest().acknowledge(self.bridge)
        self.assertFalse(self.completed)
        self.key(True)
        self.assertEqual(self.completed, [None])

    def test_ordinary_keys_use_sync_forwarding_and_do_not_take_text(self):
        self.begin()
        events = []
        self.bridge.sync_mode = lambda _,value:events.append(value)
        self.bridge.forward = lambda *_:events.append('forward')
        self.key(code=38)
        self.assertEqual(events, [True, 'forward', False])
        self.assertFalse(self.bridge.received)
        self.assertFalse(self.completed)

    def test_foreign_x11_resource_cannot_select_an_input_route(self):
        self.bridge.client_window = lambda _:43
        self.assertIsNone(self.contexts.context_for(self.native.target))
        self.assertFalse(self.sent)

    def test_cancelled_delivery_cannot_replay_or_reuse_an_uncertain_context(self):
        token = self.begin()
        self.key()
        self.contexts.cancel(token)
        self.key(True)
        fixtures.XIMTest().acknowledge(self.bridge)
        self.assertFalse(self.completed)
        self.assertEqual(len(self.bridge.received), 1)
        self.assertIsNone(self.contexts.context_for(self.native.target))

    def test_context_replacement_cannot_complete_prior_text(self):
        self.begin()
        self.key()
        self.bridge.origins[7] = (10, 21)
        self.key(True)
        self.assertEqual(self.completed, ['INPUT_TARGET_UNAVAILABLE'])
        self.assertEqual(len(self.bridge.received), 1)


if __name__ == '__main__':
    unittest.main()
