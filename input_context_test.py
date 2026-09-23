"""Handoff authority and cancellation without GTK, Qt, D-Bus or an X server."""
import unittest
from types import SimpleNamespace
from input_context import Contexts


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.contexts = Contexts.__new__(Contexts)
        self.contexts.contexts = {12: {'sender': ':1.5', 'toolkit': 'gtk3'}}
        self.contexts.pending = None
        self.contexts.sequence = 0
        self.contexts.marker_clock = lambda: 0
        self.contexts.marker = lambda sequence, xid: True
        self.contexts.display = SimpleNamespace(descendant=lambda child, parent: child in (42, 43) and parent == 42,
                                               focused_within=lambda xid: xid == 42)
        self.completed = []
        self.token = self.contexts.context_for(42, 12)

    def begin(self):
        self.contexts.commit(self.token, '你好🙂', self.completed.append)

    def test_transport_or_take_alone_does_not_acknowledge_application_delivery(self):
        self.begin()
        self.assertFalse(self.contexts.done(':1.5', 1))
        self.assertEqual(self.contexts.take(':1.5', 1, 43), (1, '你好🙂'))
        self.assertEqual(self.completed, [])
        self.assertTrue(self.contexts.done(':1.5', 1))
        self.assertEqual(self.completed, [None])

    def test_cancel_removes_text_before_late_native_event(self):
        self.begin()
        self.contexts.cancel(self.token)
        self.assertIsNone(self.contexts.take(':1.5', 1, 42))
        self.assertFalse(self.contexts.done(':1.5', 1))
        self.assertEqual(self.completed, [])

    def test_foreign_process_cannot_take_or_acknowledge_text(self):
        self.begin()
        self.assertIsNone(self.contexts.take(':1.6', 1, 42))
        self.assertFalse(self.contexts.done(':1.6', 1))
        self.assertEqual(self.contexts.take(':1.5', 1, 42), (1, '你好🙂'))

    def test_window_is_validated_when_application_takes_text(self):
        self.begin()
        self.assertIsNone(self.contexts.take(':1.5', 1, 99))
        self.assertEqual(self.completed, ['INPUT_TARGET_UNAVAILABLE'])
        self.assertIsNone(self.contexts.pending)

    def test_old_ack_cannot_complete_new_equal_text(self):
        self.begin()
        self.contexts.take(':1.5', 1, 42)
        self.contexts.done(':1.5', 1)
        self.begin()
        self.assertFalse(self.contexts.done(':1.5', 1))
        self.assertIsNone(self.contexts.take(':1.5', 1, 42))
        self.assertEqual(self.contexts.take(':1.5', 2, 42), (2, '你好🙂'))
        self.contexts.done(':1.5', 2)
        self.assertEqual(self.completed, [None, None])

    def test_unavailable_marker_drops_pending_payload(self):
        self.contexts.marker = lambda sequence, xid: False
        self.begin()
        self.assertEqual(self.completed, ['INPUT_MARKER_UNAVAILABLE'])
        self.assertIsNone(self.contexts.pending)

    def test_same_native_clock_tick_still_has_distinct_transaction_ids(self):
        self.contexts.marker_clock = lambda: 5000
        self.begin()
        self.assertEqual(self.contexts.take(':1.5', 5000, 42), (5000, '你好🙂'))
        self.contexts.done(':1.5', 5000)
        self.begin()
        self.assertIsNone(self.contexts.take(':1.5', 5000, 42))
        self.assertEqual(self.contexts.take(':1.5', 5001, 42), (5001, '你好🙂'))

    def test_application_exit_revokes_its_pending_operation(self):
        self.begin()
        self.contexts._owner_changed(None, None, None, None, None,
                                    SimpleNamespace(unpack=lambda: (':1.5', ':1.5', '')))
        self.assertEqual(self.completed, ['INPUT_CONTEXT_UNAVAILABLE'])
        self.assertIsNone(self.contexts.context_for(42,12))
        self.assertIsNone(self.contexts.take(':1.5',1,42))

    def test_replacement_application_cannot_consume_previous_process_text(self):
        self.begin()
        self.contexts.cancel(self.token)
        self.contexts.contexts[12] = {'sender':':1.9','toolkit':'gtk3'}
        self.contexts.commit(self.token,'late',self.completed.append)
        self.assertEqual(self.completed, ['INPUT_CONTEXT_UNAVAILABLE'])
        self.assertIsNone(self.contexts.take(':1.9',1,42))


if __name__ == '__main__':
    unittest.main()
