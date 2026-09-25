"""Deterministic input ordering and revocation; no display or toolkit required."""
import unittest
from types import SimpleNamespace
from input_dispatch import InputDispatch


class Protocol:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def is_closed(self):
        return self.closed


class Adapter:
    def __init__(self):
        self.focused = 'context'
        self.contexts = {'context': {'pid': 12}}
        self.pending = None
        self.received = []
        self.cancelled = []

    def focused_within(self, xid):
        return xid == 42

    def context_for(self, xid, pid=None):
        return self.focused if xid == 42 else None

    def commit(self, token, text, completed):
        self.received.append(text)
        self.pending = completed

    def close(self):
        pass

    def cancel(self, token):
        self.cancelled.append(token)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.protocol = Protocol()
        self.acks = []
        self.keys = []
        self.source = SimpleNamespace(floe_input_version=1, can_send_window=lambda w: True,
                                      send=lambda *p: self.acks.append(p))
        self.window = SimpleNamespace(get_property=lambda p: {'xid': 42, 'pid': 12}[p])
        self.server = SimpleNamespace(readonly=False, _id_to_window={1: self.window},
            get_server_source=lambda p: self.source if p is self.protocol else None,
            clear_keys_pressed=lambda: self.keys.append('release'))
        self.contexts, self.xim = Adapter(), Adapter()
        self.timers = {}
        self.next_timer = 0
        def timeout(_ms, callback):
            self.next_timer += 1
            self.timers[self.next_timer] = callback
            return self.next_timer
        self.dispatch = InputDispatch(self.server, self.xim, self.contexts, timeout,
                                      lambda token: self.timers.pop(token, None))

    def commit(self, sequence=1, text='你好🙂e\u0301'):
        self.dispatch.enqueue(self.protocol, ('floe-input', sequence, 1, text))

    def enter(self):
        self.dispatch.enqueue(self.protocol, ('key-action',), lambda p, data: self.keys.append('Enter'))

    def test_text_precedes_enter_and_repeated_equal_commits_are_distinct(self):
        self.commit()
        self.enter()
        self.commit(2)
        self.assertEqual(self.keys, [])
        self.assertEqual(len(self.contexts.received), 1)
        self.contexts.pending(None)
        self.assertEqual(self.keys, ['Enter'])
        self.assertEqual(len(self.contexts.received), 2)
        self.contexts.pending(None)
        self.assertEqual(self.acks, [('floe-input-result', 1, ''), ('floe-input-result', 2, '')])

    def test_unavailable_context_does_not_execute_following_enter(self):
        self.contexts.focused = None
        self.xim.focused = None
        self.commit()
        self.enter()
        self.assertEqual(self.acks, [('floe-input-result', 1, 'INPUT_CONTEXT_UNAVAILABLE')])
        self.assertEqual(self.keys, [])
        self.assertFalse(self.protocol.closed, 'allow the error result to reach the client')

    def test_delivery_failure_revokes_queued_and_later_keys(self):
        self.commit()
        self.enter()
        self.contexts.pending('INPUT_DELIVERY_FAILED')
        self.enter()
        self.assertEqual(self.keys, [])
        self.assertEqual(self.acks[-1][2], 'INPUT_DELIVERY_FAILED')
        self.assertFalse(self.protocol.closed, 'allow the error result to reach the client')

    def test_timeout_does_not_replay_on_late_completion(self):
        self.commit()
        self.enter()
        completed = self.contexts.pending
        self.timers[1]()
        completed(None)
        self.assertEqual(self.keys, [])
        self.assertEqual(len(self.acks), 1)
        self.assertEqual(self.acks[0][2], 'INPUT_DELIVERY_TIMEOUT')
        self.assertEqual(self.contexts.cancelled, ['context'])

    def test_revoke_cancels_unconsumed_native_handoff(self):
        self.commit()
        completed = self.contexts.pending
        self.enter()
        self.dispatch.invalidate(self.protocol)
        completed(None)
        self.assertEqual(self.contexts.cancelled, ['context'])
        self.assertEqual(self.acks, [])
        self.assertEqual(self.keys, [])

    def test_text_obeys_existing_xpra_input_driver(self):
        self.server.ui_driver = 'other-viewer'
        self.source.uuid = 'this-viewer'
        self.commit()
        self.assertEqual(self.contexts.received, [])
        self.assertEqual(self.acks, [('floe-input-result', 1, 'INPUT_TARGET_UNAVAILABLE')])

    def test_disconnected_source_drops_queued_input(self):
        self.commit()
        self.enter()
        self.protocol.close()
        self.contexts.pending(None)
        self.assertEqual(self.keys, [])
        self.assertEqual(self.acks, [])

    def test_revoked_pending_owner_does_not_block_another_live_connection(self):
        second = Protocol()
        self.server.get_server_source = lambda p: self.source if p in (self.protocol, second) else None
        self.commit()
        self.dispatch.enqueue(second, ('key-action',), lambda p, data: self.keys.append('second'))
        self.dispatch.invalidate(self.protocol)
        self.assertEqual(self.keys, ['second'])
        self.assertEqual(self.contexts.cancelled, ['context'])

    def test_reused_window_number_cannot_receive_queued_text_for_retired_window(self):
        self.commit()
        self.commit(2)
        self.server._id_to_window[1] = SimpleNamespace(get_property=self.window.get_property)
        self.contexts.pending(None)
        self.assertEqual(len(self.contexts.received), 1)
        self.assertEqual(self.acks[-1], ('floe-input-result', 2, 'INPUT_TARGET_UNAVAILABLE'))

    def test_old_native_callback_cannot_complete_replacement_operation(self):
        self.commit()
        previous = self.contexts.pending
        self.dispatch.invalidate(self.protocol)
        self.commit(2)
        current = self.contexts.pending
        previous(None)
        self.assertEqual(self.acks, [])
        self.assertIsNotNone(self.dispatch.pending)
        current(None)
        self.assertEqual(self.acks, [('floe-input-result', 2, '')])

    def test_retired_timeout_cannot_clear_replacement_timer(self):
        self.commit()
        previous = self.timers[1]
        self.dispatch.invalidate(self.protocol)
        self.commit(2)
        current = self.contexts.pending
        previous()
        self.assertEqual(self.acks, [])
        current(None)
        self.assertEqual(self.acks, [('floe-input-result', 2, '')])
        self.assertEqual(self.timers, {})

    def test_synchronous_native_completions_drain_without_recursive_dispatch(self):
        self.commit()
        completed = self.contexts.pending
        for sequence in range(2, 256):
            self.commit(sequence)
        self.enter()
        self.contexts.commit = lambda token, text, done: done(None)
        completed(None)
        self.assertEqual(len(self.acks), 255)
        self.assertEqual(self.keys, ['Enter'])
        self.assertIsNone(self.dispatch.pending)
        self.assertEqual(self.timers, {})

    def test_native_exception_is_private_and_revokes_following_input(self):
        def failed(*_args):
            raise ValueError('private text must not reach the result')
        self.contexts.commit = failed
        self.commit()
        self.enter()
        self.assertEqual(self.acks, [('floe-input-result', 1, 'INPUT_DELIVERY_FAILED')])
        self.assertEqual(self.contexts.cancelled, ['context'])
        self.assertEqual(self.keys, [])

    def test_driver_release_precedes_another_queued_owner(self):
        self.server.ui_driver = self.source.uuid = 'first'
        second = Protocol()
        self.server.get_server_source = lambda p: self.source if p in (self.protocol, second) else None
        self.commit()
        def activate(_protocol, _packet):
            self.server.ui_driver = 'second'
            self.keys.append('second')
        self.dispatch.enqueue(second, ('focus',), activate)
        self.contexts.pending('INPUT_DELIVERY_FAILED')
        self.assertEqual(self.keys, ['release', 'second'])

    def test_close_retires_native_work_and_discards_late_completion(self):
        self.commit()
        completed = self.contexts.pending
        self.enter()
        self.dispatch.close()
        completed(None)
        self.assertEqual(self.contexts.cancelled, ['context'])
        self.assertEqual(self.acks, [])
        self.assertEqual(self.keys, [])

    def test_sequence_replay_is_rejected(self):
        self.commit()
        self.contexts.pending(None)
        self.commit()
        self.assertEqual(len(self.contexts.received), 1)
        self.assertEqual(self.acks[-1][2], 'INPUT_SEQUENCE_INVALID')

    def test_invalid_unicode_is_reported_without_body(self):
        self.commit(text='\ud800')
        self.assertEqual(self.acks, [('floe-input-result', 1, 'INPUT_TEXT_INVALID')])
        self.assertEqual(self.contexts.received, [])

    def test_target_is_validated_when_dequeued(self):
        self.commit()
        self.commit(2)
        self.server._id_to_window.clear()
        self.contexts.pending(None)
        self.assertEqual(len(self.contexts.received), 1)
        self.assertEqual(self.acks[-1][2], 'INPUT_TARGET_UNAVAILABLE')

    def test_rejection_releases_only_the_current_input_drivers_keys(self):
        self.server.ui_driver = self.source.uuid = 'this-viewer'
        self.commit(text='\x00')
        self.assertEqual(self.keys, ['release'])

    def test_invalid_queued_text_is_rejected_before_buffering(self):
        self.commit()
        self.commit(2, 'a' * 16001)
        self.assertIsNone(self.dispatch.pending)
        self.assertEqual(len(self.dispatch.queue), 0)
        self.assertEqual(self.acks[-1][2], 'INPUT_TEXT_INVALID')

    def test_long_unicode_commit_is_delivered_as_one_operation(self):
        text = '你🙂e\u0301' * 1500
        self.commit(text=text)
        self.assertEqual(self.contexts.received, [text])

    def test_old_input_clients_cannot_enter_the_new_input_flow(self):
        self.source.floe_input_version = 0
        self.enter()
        self.commit()
        self.assertEqual(self.keys, [])
        self.assertEqual(self.contexts.received, [])
        self.assertEqual(self.acks,[('floe-input-result',0,'INPUT_VERSION_UNSUPPORTED')])


if __name__ == '__main__':
    unittest.main()
