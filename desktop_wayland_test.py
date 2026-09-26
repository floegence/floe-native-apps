"""Native text protocol admission and callback ordering at the shared owner."""
import unittest
from unittest.mock import patch

from desktop_context import NativeContexts
from desktop_context_test import Peer
from desktop_native import NativeDesktop
from desktop_native_test import Frames


class WaylandTests(unittest.TestCase):
    def setUp(self):
        self.sent, self.completed = [], []
        self.native = NativeDesktop(self.sent.append, Frames())
        for line in ('native-version 1', 'window-instance 1',
                     'window-state 1 0 wayland 120 1000 700 0', 'surface-instance 1',
                     'focus 1 1 120 19 1', 'scene 1 1'):
            self.native.observe(line)
        self.native.bind(1)
        mock = patch('desktop_context.ApplicationPeer', Peer)
        mock.start()
        self.addCleanup(mock.stop)
        self.contexts = NativeContexts(self.native, {120: 'app'}, '/private/runtime')
        self.native.contexts = self.contexts
        self.addCleanup(self.contexts.close)

    def begin(self):
        self.native.observe('text-context 1 1 1 1 2')
        token = self.contexts.context_for(self.native.target)
        self.assertIsNotNone(token)
        self.contexts.commit(token, '同🙂\n', self.completed.append)
        return token

    def test_dispatch_requires_both_callback_boundaries_and_native_acceptance(self):
        self.begin()
        self.assertEqual(self.sent[-1], 'input 1 1 1 client-barrier 1\n')
        self.assertEqual(self.completed, [])
        self.native.observe('client-barrier-done 1')
        self.assertEqual(self.sent[-1], 'input 1 1 1 text-commit 2 1 1 e5908cf09f99820a\n')
        self.native.observe('text-dispatched 2')
        self.assertEqual(self.sent[-1], 'input 1 1 1 client-barrier 3\n')
        self.assertEqual(self.completed, [])
        self.native.observe('client-barrier-done 3')
        self.assertEqual(self.completed, [None])
        self.assertFalse(self.contexts.markers.slots)
        self.assertIsNone(self.contexts.pending)
        self.native.observe('client-barrier-done 3')
        self.assertEqual(self.completed, [None])

    def test_context_revocation_cancels_without_sending_text_or_replaying(self):
        self.begin()
        self.native.observe('text-context 1 2 1 0 3')
        self.assertEqual(self.completed, ['INPUT_CONTEXT_UNAVAILABLE'])
        self.native.observe('client-barrier-done 1')
        self.assertFalse(any('text-commit' in line for line in self.sent))
        self.assertIsNone(self.contexts.context_for(self.native.target))

    def test_surrounding_serial_progress_does_not_complete_or_revoke_text(self):
        self.begin()
        self.native.observe('text-context 1 1 1 1 3')
        self.assertEqual(self.completed, [])
        self.native.observe('client-barrier-done 1')
        self.native.observe('text-rejected 2')
        self.assertEqual(self.completed, ['INPUT_CONTEXT_UNAVAILABLE'])
        self.assertIsNone(self.contexts.pending)

    def test_connection_replacement_rejects_old_native_callback(self):
        self.begin()
        self.native.bind(2)
        self.native.observe('client-barrier-done 1')
        self.assertEqual(self.completed, ['INPUT_TARGET_UNAVAILABLE'])
        self.assertFalse(any('text-commit' in line for line in self.sent))

    def test_ambiguous_and_retired_protocol_contexts_are_not_adapters(self):
        self.native.observe('text-context 1 1 1 1 2')
        self.native.observe('text-context 2 1 1 1 1')
        self.assertIsNone(self.contexts.context_for(self.native.target))
        self.native.observe('text-context-retired 1')
        with self.assertRaises(ValueError):
            self.native.observe('text-context 1 1 1 1 2')

    def test_disposal_makes_delayed_callback_inert(self):
        self.begin()
        self.contexts.close()
        self.native.observe('client-barrier-done 1')
        self.assertEqual(self.completed, ['INPUT_CONTEXT_UNAVAILABLE'])
        self.assertFalse(any('text-commit' in line for line in self.sent))


if __name__ == '__main__':
    unittest.main()
