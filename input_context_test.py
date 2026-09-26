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

    def test_gtk4_registration_uses_authenticated_sender_and_rejects_second_owner(self):
        self.contexts.contexts.clear()
        self.contexts.Gio = SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0))
        self.contexts.GLib = SimpleNamespace(Variant=lambda _type, value: value, VariantType=SimpleNamespace(new=lambda value: value))
        looked_up = []
        def call_sync(_name, _path, _interface, method, sender, *_args):
            self.assertEqual(method, 'GetConnectionUnixProcessID')
            looked_up.append(sender)
            return SimpleNamespace(unpack=lambda: (12,))
        connection = SimpleNamespace(call_sync=call_sync)
        replies, errors = [], []
        invocation = SimpleNamespace(return_value=replies.append, return_dbus_error=lambda *args: errors.append(args))
        parameters = SimpleNamespace(unpack=lambda: (1, 'gtk4'))
        self.contexts._call(connection, ':1.5', None, None, 'Register', parameters, invocation)
        self.assertEqual(replies, [None])
        self.assertEqual(self.contexts.context_for(42, 12), (12, 42, ':1.5'))
        self.contexts._call(connection, ':1.6', None, None, 'Register', parameters, invocation)
        self.assertEqual(errors[0][0], 'org.floegence.ClientInput.DuplicateOwner')
        self.assertEqual(self.contexts.context_for(42, 12), (12, 42, ':1.5'))
        self.assertEqual(looked_up, [(':1.5',), (':1.6',)])
        parameters = SimpleNamespace(unpack=lambda: (2, 'gtk4'))
        self.contexts._call(connection, ':1.5', None, None, 'Register', parameters, invocation)
        self.assertEqual(errors[-1][0], 'org.floegence.ClientInput.InvalidVersion')
        self.assertEqual(len(looked_up), 3)
        self.contexts.commit((12, 42, ':1.5'), 'blocked', self.completed.append)
        self.assertEqual(self.completed, ['INPUT_MODULE_VERSION_UNSUPPORTED'])
        self.assertIsNone(self.contexts.pending)
        self.contexts._owner_changed(None, None, None, None, None,
                                    SimpleNamespace(unpack=lambda: (':1.5', ':1.5', '')))
        self.assertIsNone(self.contexts.context_for(42, 12))

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
