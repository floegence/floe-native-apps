"""IBus origin, focus and native completion must agree before releasing input."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop_context import NativeContexts
from desktop_context_test import Peer
from desktop_ibus import IBusContexts, IBusSources
from desktop_native import NativeFocus, NativeTarget


class IBusTests(unittest.TestCase):
    def setUp(self):
        self.tree = {120: 'gtk', 121: 'gtk', 150: 'another sandbox'}
        for module in ('desktop_context', 'desktop_ibus'):
            mock = patch(module + '.ApplicationPeer', Peer)
            mock.start()
            self.addCleanup(mock.stop)
        self.daemon = SimpleNamespace(valid=lambda: True)
        self.portal = SimpleNamespace(pid=140, valid=lambda: True)
        self.described = (1, ':1.9', 120, os.getuid(), True, True)
        self.proxy_pid = 121
        def credentials(sender):
            return {'ProcessID': 140 if sender == 'org.freedesktop.portal.IBus' else self.proxy_pid,
                    'UnixUserID': os.getuid()}
        self.sources = IBusSources(self.tree, '/private/runtime', self.daemon, self.portal,
            lambda path: self.described, lambda path: (1, ':1.40', '/portal/context'), credentials)
        self.sent, self.completed, self.commits = [], [], []
        self.native = SimpleNamespace(target=NativeTarget(1, 2),
            focus=NativeFocus(3, 1, 120, 19, True), epoch=1, closed=False,
            windows={1: SimpleNamespace(protocol='wayland')}, submit=lambda *args: self.sent.append(args))
        self.contexts = NativeContexts(self.native, self.tree, '/private/runtime')
        self.addCleanup(self.contexts.close)
        self.adapter = IBusContexts(self.contexts, self.sources)
        self.addCleanup(self.adapter.close)
        self.engine = object()
        self.adapter.focus(self.engine, '/context/first')

    def begin(self, text='同🙂'):
        token = self.contexts.context_for(self.native.target)
        self.assertIsNotNone(token)
        self.contexts.commit(token, text, self.completed.append)
        return token, self.contexts.pending['code']

    def key(self, code, released=False, engine=None):
        return self.adapter.key(engine or self.engine, code, released, self.commits.append)

    def test_commit_then_same_context_release_complete_the_single_transaction(self):
        token, code = self.begin()
        self.key(code)
        self.assertEqual(self.commits, ['同🙂'])
        self.assertFalse(self.completed)
        self.key(code)
        self.assertEqual(self.commits, ['同🙂'])
        self.key(code, True)
        self.assertEqual(self.completed, [None])
        self.assertTrue(token.client.closed)
        self.assertFalse(self.contexts.markers.slots)
        self.key(code, True)
        self.assertEqual(self.completed, [None])

    def test_widget_is_selected_at_marker_after_preceding_pointer_input(self):
        _, code = self.begin()
        self.adapter.focus(self.engine, '/context/second')
        self.key(code)
        self.key(code, True)
        self.assertEqual(self.commits, ['同🙂'])
        self.assertEqual(self.completed, [None])

    def test_focus_change_after_take_does_not_complete_or_replay(self):
        _, code = self.begin()
        self.key(code)
        self.adapter.focus(self.engine, '/context/second')
        self.key(code, True)
        self.assertEqual(self.commits, ['同🙂'])
        self.assertEqual(self.completed, ['INPUT_TARGET_UNAVAILABLE'])
        self.assertEqual(len(self.sent), 1)

    def test_origin_change_without_engine_notification_rejects_release(self):
        _, code = self.begin()
        self.key(code)
        self.described = (1, ':1.10', 120, os.getuid(), True, True)
        self.key(code, True)
        self.assertEqual(self.completed, ['INPUT_TARGET_UNAVAILABLE'])

    def test_unrelated_process_cannot_receive_text_even_with_same_context_name(self):
        _, code = self.begin()
        self.described = (1, ':1.9', 150, os.getuid(), True, True)
        self.key(code)
        self.assertFalse(self.commits)
        self.assertEqual(self.completed, ['INPUT_TARGET_UNAVAILABLE'])

    def test_async_unfocused_dead_and_foreign_user_contexts_fail_before_input(self):
        original = self.described
        for index, value in ((0, 2), (1, 'gtk4-im-fake-identity'), (3, os.getuid() + 1),
                             (4, False), (5, False)):
            values = list(original)
            values[index] = value
            self.described = tuple(values)
            self.assertIsNone(self.contexts.context_for(self.native.target))
        self.described = original
        self.daemon.valid = lambda: False
        self.assertIsNone(self.contexts.context_for(self.native.target))
        self.assertFalse(self.sent)

    def test_flatpak_original_sender_must_match_native_sandbox(self):
        self.described = (1, ':1.9', 140, os.getuid(), True, True)
        token, code = self.begin()
        self.assertEqual(token.sender, 'ibus/:1.9/:1.40')
        self.key(code)
        self.key(code, True)
        self.assertEqual(self.completed, [None])
        self.proxy_pid = 150
        self.assertIsNone(self.contexts.context_for(self.native.target))

    def test_replaced_portal_cannot_describe_a_context(self):
        self.described = (1, ':1.9', 140, os.getuid(), True, True)
        self.portal.valid = lambda: False
        self.assertIsNone(self.contexts.context_for(self.native.target))
        self.portal.valid = lambda: True
        self.sources.credentials = lambda sender: {'ProcessID': 141, 'UnixUserID': os.getuid()}
        self.assertIsNone(self.contexts.context_for(self.native.target))

    def test_cancelled_markers_do_not_consume_identical_replacement(self):
        token, old = self.begin()
        self.contexts.cancel(token)
        _, new = self.begin()
        self.assertNotEqual(old, new)
        self.key(old)
        self.key(old, True)
        self.assertFalse(self.commits)
        self.key(new)
        self.key(new, True)
        self.assertEqual(self.commits, ['同🙂'])
        self.assertEqual(self.completed, [None])

    def test_late_other_engine_focus_out_does_not_revoke_current_engine(self):
        self.adapter.focus(object(), None)
        _, code = self.begin()
        self.key(code)
        self.key(code, True)
        self.assertEqual(self.completed, [None])

    def test_close_and_native_surface_replacement_reject_late_events(self):
        _, code = self.begin()
        self.native.focus = NativeFocus(4, 1, 120, 19, True)
        self.key(code)
        self.assertFalse(self.commits)
        self.assertEqual(self.completed, ['INPUT_TARGET_UNAVAILABLE'])
        self.adapter.close()
        self.assertIsNone(self.contexts.context_for(self.native.target))
        self.assertFalse(self.key(28))


if __name__ == '__main__':
    unittest.main()
