"""Confirmed text must belong to one live native surface and registered toolkit."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop_context import NativeContexts
from desktop_native import NativeFocus, NativeTarget


class Peer:
    def __init__(self, tree, pid, _runtime):
        self.tree, self.pid, self.closed = tree, pid, False

    def valid(self):
        return self.pid in self.tree and not self.closed

    def matches(self, other):
        return self.valid() and other.valid() and self.tree[self.pid] == self.tree[other.pid]

    def close(self):
        self.closed = True


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.sent, self.completed = [], []
        self.native = SimpleNamespace(target=NativeTarget(1, 2),
            focus=NativeFocus(3, 1, 120, 19, True), epoch=1, closed=False,
            windows={1: SimpleNamespace(protocol='wayland')},
            submit=lambda *args: self.sent.append(args))
        self.tree = {120: 'application', 121: 'application', 150: 'other instance'}
        mock = patch('desktop_context.ApplicationPeer', Peer)
        mock.start()
        self.addCleanup(mock.stop)
        self.contexts = NativeContexts(self.native, self.tree, '/private/runtime')
        self.addCleanup(self.contexts.close)
        self.contexts.register(':1.7', 121, 1, 'qt6-wayland')

    def begin(self, text='同🙂'):
        token = self.contexts.context_for(self.native.target)
        self.assertIsNotNone(token)
        self.contexts.commit(token, text, self.completed.append)
        return token, next(reversed(self.contexts.markers.slots))

    def test_registration_and_matching_do_not_grant_other_window_or_toolkit(self):
        for args in ((':1.8', 121, 2, 'qt6-wayland'), (':1.8', 121, 1, 'unknown'),
                     (':1.7', 121, 1, 'qt6-wayland')):
            with self.assertRaises(ValueError):
                self.contexts.register(*args)
        self.native.focus = NativeFocus(4, 1, 150, 19, True)
        self.assertIsNone(self.contexts.context_for(self.native.target))
        self.native.focus = NativeFocus(3, 1, 120, 19, False)
        self.assertIsNone(self.contexts.context_for(self.native.target))

    def test_payload_is_taken_once_by_the_exact_surface_then_completed_by_toolkit(self):
        token, code = self.begin()
        self.assertEqual(self.sent, [(1, token.target, [f'key {code} 1', f'key {code} 0'])])
        self.assertFalse(self.contexts.done(':1.7', 1))
        self.assertEqual(self.contexts.take(':1.7', code, 19), (1, '同🙂'))
        self.assertIsNone(self.contexts.take(':1.7', code, 19))
        self.assertEqual(self.completed, [])
        self.assertTrue(self.contexts.done(':1.7', 1))
        self.assertEqual(self.completed, [None])
        self.assertFalse(self.contexts.done(':1.7', 1))
        self.contexts.released(':1.7', code)
        self.assertFalse(self.contexts.markers.slots)

    def test_unrelated_sender_cannot_take_or_complete_other_context(self):
        _, code = self.begin()
        self.contexts.register(':1.8', 150, 1, 'qt6-wayland')
        self.assertIsNone(self.contexts.take(':1.8', code, 19))
        self.assertFalse(self.contexts.done(':1.8', 1))
        self.assertEqual(self.contexts.take(':1.7', code, 19), (1, '同🙂'))

    def test_wrong_surface_is_not_delivered_or_retried(self):
        _, code = self.begin()
        self.assertIsNone(self.contexts.take(':1.7', code, 20))
        self.assertEqual(self.completed, ['INPUT_TARGET_UNAVAILABLE'])
        self.assertIsNone(self.contexts.take(':1.7', code, 19))

    def test_toolkit_focus_loss_after_take_cannot_acknowledge_or_retry(self):
        _, code = self.begin()
        self.contexts.take(':1.7', code, 19)
        self.assertFalse(self.contexts.failed(':1.8', 1))
        self.assertTrue(self.contexts.failed(':1.7', 1))
        self.assertFalse(self.contexts.done(':1.7', 1))
        self.assertEqual(self.completed, ['INPUT_TARGET_UNAVAILABLE'])
        self.assertEqual(len(self.sent), 1)

    def test_native_focus_change_after_take_invalidates_toolkit_completion(self):
        _, code = self.begin()
        self.contexts.take(':1.7', code, 19)
        self.native.focus = NativeFocus(9, 1, 120, 19, True)
        self.assertFalse(self.contexts.done(':1.7', 1))
        self.assertEqual(self.completed, ['INPUT_TARGET_UNAVAILABLE'])

    def test_reconnect_and_focus_reuse_cannot_receive_late_commit(self):
        for change in ('epoch', 'focus', 'target'):
            with self.subTest(change=change):
                token, code = self.begin()
                old = getattr(self.native, change)
                value = 2 if change == 'epoch' else (
                    NativeFocus(4, 1, 120, 19, True) if change == 'focus' else NativeTarget(1, 3))
                setattr(self.native, change, value)
                self.assertIsNone(self.contexts.take(':1.7', code, 19))
                self.assertEqual(self.completed[-1], 'INPUT_TARGET_UNAVAILABLE')
                setattr(self.native, change, old)
                self.contexts.released(':1.7', code)

    def test_cancellation_and_equal_text_keep_distinct_transaction_identity(self):
        token, stale = self.begin()
        self.contexts.cancel(token)
        _, current = self.begin()
        self.assertNotEqual(stale, current)
        self.assertIsNone(self.contexts.take(':1.7', stale, 19))
        self.contexts.released(':1.7', stale)
        self.assertFalse(self.contexts.done(':1.7', 1))
        self.assertEqual(self.contexts.take(':1.7', current, 19), (2, '同🙂'))
        self.assertTrue(self.contexts.done(':1.7', 2))
        self.assertEqual(self.completed, [None])

    def test_dead_proxy_and_bus_owner_loss_revoke_transaction(self):
        _, code = self.begin()
        del self.tree[121]
        self.assertIsNone(self.contexts.take(':1.7', code, 19))
        self.assertEqual(self.completed, ['INPUT_TARGET_UNAVAILABLE'])
        self.tree[121] = 'application'
        _, code = self.begin()
        self.contexts.unregister(':1.7')
        self.assertEqual(self.completed[-1], 'INPUT_CONTEXT_UNAVAILABLE')
        self.assertFalse(self.contexts.markers.slots)
        self.assertIsNone(self.contexts.context_for(self.native.target))

    def test_two_matching_registrations_are_ambiguous_without_fallback(self):
        self.contexts.register(':1.8', 120, 1, 'qt6-wayland')
        self.assertIsNone(self.contexts.context_for(self.native.target))
        self.assertFalse(self.sent)

    def test_completed_token_cannot_submit_again_or_retain_process_reference(self):
        token, code = self.begin()
        self.contexts.take(':1.7', code, 19)
        self.contexts.done(':1.7', 1)
        self.assertTrue(token.surface_peer.closed)
        self.contexts.commit(token, 'late', self.completed.append)
        self.assertEqual(self.completed, [None, 'INPUT_CONTEXT_UNAVAILABLE'])

    def test_qualified_ibus_context_can_use_the_same_native_transaction(self):
        self.contexts.unregister(':1.7')
        peer = Peer(self.tree, 120, '/private/runtime')
        ibus = SimpleNamespace(select=lambda surface: ('ibus:1.9', peer),
                               valid=lambda token: peer.matches(token.surface_peer))
        self.contexts.ibus = ibus
        token, code = self.begin()
        self.assertIs(token.adapter, ibus)
        self.assertEqual(self.contexts.take('ibus:1.9', code, 19), (1, '同🙂'))
        self.contexts.released('ibus:1.9', code)
        self.assertTrue(self.contexts.done('ibus:1.9', 1))
        self.assertTrue(peer.closed)

    def test_selected_module_never_replays_through_ibus(self):
        self.contexts.ibus = SimpleNamespace(select=lambda _: self.fail('Unexpected input adapter fallback'))
        _, code = self.begin()
        self.assertIsNone(self.contexts.take(':1.7', code, 20))
        self.assertEqual(self.completed, ['INPUT_TARGET_UNAVAILABLE'])

    def test_ibus_origin_revocation_blocks_a_late_marker(self):
        self.contexts.unregister(':1.7')
        peer = Peer(self.tree, 120, '/private/runtime')
        ibus = SimpleNamespace(select=lambda surface: ('ibus:1.9', peer), valid=lambda token: True)
        self.contexts.ibus = ibus
        _, code = self.begin()
        ibus.valid = lambda token: False
        self.assertIsNone(self.contexts.take('ibus:1.9', code, 19))
        self.assertEqual(self.completed, ['INPUT_TARGET_UNAVAILABLE'])
        self.assertTrue(peer.closed)


if __name__ == '__main__':
    unittest.main()
