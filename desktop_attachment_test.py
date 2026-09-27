"""Frame acknowledgement and input authority follow one native target instance."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from desktop_attachment import DesktopAttachment


class Owner:
    def __init__(self, attachment):
        self.attachment = attachment
        self.messages, self.frames = [], []
        self.frame_pending = False
        self.cursor_pending = False
        self.cursors = []

    def send(self, value):
        self.messages.append(value)
        return True

    def send_cursor(self, description, data):
        self.cursors.append((description, data))
        return True

    def send_frame(self, description, data):
        self.frames.append((description, data))
        return True

    def close(self):
        self.attachment.detach(self)


class Native:
    def __init__(self):
        self.target = SimpleNamespace(window=1, generation=1)
        self.input, self.released, self.captures, self.commits, self.closed = [], [], [], [], []
        self.context = (self, 'context')
        self.cursor = SimpleNamespace(current=None, revision=0)
        self.clipboard_syncs = []

    def bind(self, epoch):
        self.epoch = epoch

    def release(self, epoch):
        self.released.append(epoch)

    def unbind(self, epoch):
        self.release(epoch)

    def snapshot(self):
        return {'state': 'running', 'window': self.target.window if self.target else None}

    def capture(self, target, completed):
        self.captures.append((target, completed))

    def deliver(self, epoch, target, operation):
        self.input.append((epoch, target, operation))

    def validate_input(self, operation):
        return dict(operation)

    def context_for(self, target):
        return self.context

    def sync_clipboard(self, epoch, target):
        self.clipboard_syncs.append((epoch, target))

    def commit(self, token, text, completed):
        self.commits.append((token, text, completed))

    def cancel(self, token):
        self.cancelled = token

    def select(self, window):
        self.target = SimpleNamespace(window=window, generation=1)

    def close_window(self, window):
        self.closed.append(window)


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.native = Native()
        self.timers, self.timer_id = {}, 0
        def timer(_milliseconds, callback):
            self.timer_id += 1
            self.timers[self.timer_id] = callback
            return self.timer_id
        self.attachment = DesktopAttachment(self.native, timer, lambda token: self.timers.pop(token, None))
        self.owner = Owner(self.attachment)
        self.attachment.attach(self.owner)
        self.request_id = 0

    def request(self, method, **params):
        self.request_id += 1
        self.attachment.request(self.owner, {'id': self.request_id, 'method': method, **params})

    def input(self, kind='key', **extra):
        target = self.native.target
        self.request('input', connection=self.attachment.epoch, window=target.window,
                     generation=target.generation, operation={'kind': kind, **extra})

    def captured(self, index=-1):
        target, completed = self.native.captures[index]
        completed({'encoding': 'png', 'width': 100, 'height': 80}, b'fixture-pixels', None)
        return target

    def acknowledge(self):
        self.request('frame_ack', frame=self.owner.frames[-1][0]['sequence'])

    def ready(self):
        self.captured()
        self.acknowledge()

    def test_termination_uses_only_the_authenticated_application_owner(self):
        terminate = Mock()
        self.attachment.terminate_application = terminate
        self.request('terminate_application', pid=123)
        terminate.assert_not_called()
        self.assertEqual(self.owner.messages[-1]['error'], 'REQUEST_INVALID')
        self.request('terminate_application')
        terminate.assert_called_once_with()
        self.assertEqual(self.owner.messages[-1]['result'], 'requested')
        self.attachment.request(Owner(self.attachment), {'id': 999, 'method': 'terminate_application'})
        self.attachment.request(self.owner, {'id': self.request_id, 'method': 'terminate_application'})
        terminate.assert_called_once_with()

    def test_detach_and_capture_failure_never_request_termination(self):
        terminate = Mock()
        self.attachment.terminate_application = terminate
        self.attachment.failed = True
        self.owner.close()
        terminate.assert_not_called()

    def test_replacement_attachment_alone_can_terminate_without_an_input_target(self):
        terminate = Mock()
        self.attachment.terminate_application = terminate
        self.native.target = None
        replacement = Owner(self.attachment)
        self.attachment.attach(replacement)
        self.attachment.request(self.owner, {'id': 100, 'method': 'terminate_application'})
        terminate.assert_not_called()
        self.attachment.request(replacement, {'id': 1, 'method': 'terminate_application'})
        terminate.assert_called_once_with()
        self.assertEqual(replacement.messages[-1]['result'], 'requested')

    def test_termination_requires_an_available_lifecycle_owner(self):
        self.request('terminate_application')
        self.assertEqual(self.owner.messages[-1]['error'], 'METHOD_UNSUPPORTED')
        self.attachment.terminate_application = Mock(side_effect=ValueError('Retired instance'))
        self.request('terminate_application')
        self.assertEqual(self.owner.messages[-1]['error'], 'APPLICATION_UNAVAILABLE')

    def test_target_release_cancels_queued_input_but_retains_painted_authority(self):
        self.ready()
        target = self.native.target
        self.input('text', text='fixture')
        self.input('key', code=28, pressed=True)
        self.request('release_input', connection=self.attachment.epoch, window=target.window,
                     generation=target.generation)
        self.assertEqual(self.owner.messages[-1]['result'], 'released')
        self.assertIs(self.attachment.ready, target)
        self.native.commits[0][2](None)
        self.assertEqual(self.native.input, [])
        self.input('key', code=30, pressed=True)
        self.assertEqual(self.native.input[-1][2]['code'], 30)

    def test_stale_release_cannot_cancel_current_target(self):
        self.ready()
        target = self.native.target
        self.request('release_input', connection=self.attachment.epoch - 1, window=target.window,
                     generation=target.generation)
        self.assertEqual(self.owner.messages[-1]['error'], 'INPUT_TARGET_UNAVAILABLE')
        self.assertEqual(self.native.released, [])
        self.assertIs(self.attachment.ready, target)

    def test_explicit_termination_cancels_pending_text_before_lifecycle_dispatch(self):
        self.ready()
        self.input('text', text='fixture')
        self.input('key', code=28, pressed=True)
        cancelled = []
        self.attachment.terminate_application = lambda: cancelled.append(len(self.native.commits))
        self.request('terminate_application')
        self.assertEqual(cancelled, [1])
        self.native.commits[0][2](None)
        self.assertEqual(self.native.input, [])
        self.assertEqual([m['error'] for m in self.owner.messages if 'error' in m],
                         ['INPUT_TARGET_UNAVAILABLE', 'INPUT_TARGET_UNAVAILABLE'])

    def test_cursor_updates_coalesce_while_peer_is_backpressured(self):
        self.native.cursor.current = ({'mode': 'image', 'sequence': 1}, b'first')
        self.attachment.cursor_changed()
        self.assertEqual(self.owner.cursors[-1][1], b'first')
        self.owner.cursor_pending = True
        for sequence in range(2, 102):
            self.native.cursor.current = ({'mode': 'image', 'sequence': sequence}, b'latest')
            self.attachment.cursor_changed()
        self.assertEqual(len(self.owner.cursors), 1)
        self.owner.cursor_pending = False
        self.attachment.writable(self.owner)
        self.assertEqual(len(self.owner.cursors), 2)
        self.assertEqual(self.owner.cursors[-1][0]['sequence'], 101)
        self.native.cursor.current = None
        self.attachment.cursor_changed()
        self.assertEqual(self.owner.cursors[-1][0]['mode'], 'default')
        self.assertIsNone(self.owner.cursors[-1][1])
        self.assertIsNone(self.attachment.ready)

    def test_native_window_and_sent_frame_do_not_grant_input(self):
        self.input()
        self.assertEqual(self.native.input, [])
        self.captured()
        self.input()
        self.assertEqual(self.native.input, [])
        self.acknowledge()
        self.input()
        self.assertEqual(len(self.native.input), 1)

    def test_clipboard_requires_a_painted_target_and_syncs_once_per_generation(self):
        target = self.native.target
        def clipboard():
            return [message for message in self.owner.messages if message.get('event') == 'clipboard']
        self.attachment.clipboard_changed((target, 1, 'not painted'))
        self.assertEqual(clipboard(), [])
        self.assertEqual(self.native.clipboard_syncs, [])
        self.ready()
        self.assertEqual(self.native.clipboard_syncs, [(1, target)])
        self.attachment.clipboard_changed((target, 1, 'visible'))
        self.assertEqual(clipboard()[-1]['clipboard']['text'], 'visible')
        self.attachment.damage()
        self.ready()
        self.assertEqual(self.native.clipboard_syncs, [(1, target)])
        self.native.target = SimpleNamespace(window=1, generation=2)
        self.attachment.scene_changed()
        self.attachment.clipboard_changed((target, 1, 'retired'))
        self.assertEqual(len(clipboard()), 1)
        self.ready()
        self.assertEqual(self.native.clipboard_syncs[-1], (1, self.native.target))

    def test_clipboard_takeover_cannot_expose_old_connection_or_target(self):
        self.ready()
        target, previous = self.native.target, self.owner
        self.owner = Owner(self.attachment)
        self.attachment.attach(self.owner)
        self.attachment.clipboard_changed((target, 1, 'old'))
        self.attachment.clipboard_changed((target, 2, 'before paint'))
        self.ready()
        self.attachment.clipboard_changed((target, 1, 'late'))
        self.attachment.clipboard_changed((target, 2, ''))
        current = [message for message in self.owner.messages if message.get('event') == 'clipboard']
        self.assertEqual(current, [{'event': 'clipboard', 'clipboard': {
            'connection': 2, 'window': 1, 'generation': 1, 'text': ''}}])
        self.assertFalse(any(message.get('event') == 'clipboard' for message in previous.messages))

    def test_scene_retirement_answers_every_cancelled_request_once(self):
        self.ready()
        self.input('text', text='fixture')
        pending = self.request_id
        self.input(code=28, pressed=True)
        queued = self.request_id
        completed = self.native.commits[-1][2]
        self.native.target = SimpleNamespace(window=1, generation=2)
        self.attachment.scene_changed()
        expected = [{'id': request, 'error': 'INPUT_TARGET_UNAVAILABLE'} for request in (pending, queued)]
        actual = [message for message in self.owner.messages if message.get('id') in (pending, queued)]
        self.assertEqual(actual, expected)
        completed(None)
        self.attachment.scene_changed()
        self.assertEqual([message for message in self.owner.messages if message.get('id') in (pending, queued)], expected)
        self.assertEqual(self.native.input, [])
        self.ready()
        self.input(code=28, pressed=True)
        self.assertEqual(len(self.native.input), 1)

    def test_metadata_update_does_not_cancel_text_or_require_another_frame(self):
        self.ready()
        self.input('text', text='document')
        self.input(code=28, pressed=True)
        target = self.attachment.ready
        captures = len(self.native.captures)
        self.attachment.metadata_changed()
        self.assertIs(self.attachment.ready, target)
        self.assertEqual(self.native.released, [])
        self.assertEqual(len(self.native.captures), captures)
        self.assertEqual(self.native.input, [])
        self.assertEqual(self.owner.messages[-1]['event'], 'state')
        self.native.commits[-1][2](None)
        self.assertEqual(len(self.native.input), 1)

    def test_stale_decode_cannot_grant_replacement_window_authority(self):
        self.captured()
        old_frame = self.owner.frames[-1][0]['sequence']
        self.native.target = SimpleNamespace(window=1, generation=2)
        self.attachment.scene_changed()
        self.request('frame_ack', frame=old_frame)
        self.input()
        self.assertEqual(self.native.input, [])
        self.ready()
        self.input()
        self.assertEqual(len(self.native.input), 1)

    def test_late_capture_is_discarded_before_new_attachment_frame(self):
        previous = self.owner
        self.attachment.detach(previous)
        self.owner = Owner(self.attachment)
        self.attachment.attach(self.owner)
        self.assertEqual(len(self.native.captures), 1, 'only one native capture may run')
        self.captured(0)
        self.assertEqual(previous.frames, [])
        self.assertEqual(self.owner.frames, [])
        self.assertEqual(len(self.native.captures), 2)
        self.ready()
        self.input()
        self.assertEqual(self.native.input[0][0], 2)

    def test_ordinary_input_waits_for_actual_native_text_completion(self):
        self.ready()
        self.input('text', text='甲🙂')
        self.input('key', code='Enter')
        self.input('text', text='甲🙂')
        self.assertEqual(self.native.input, [])
        self.native.commits[0][2](None)
        self.assertEqual(self.native.input[0][2]['code'], 'Enter')
        self.assertEqual([value[1] for value in self.native.commits], ['甲🙂', '甲🙂'])

    def test_target_change_cancels_text_and_late_completion(self):
        self.ready()
        self.input('text', text='cancelled')
        completed = self.native.commits[0][2]
        self.input()
        self.native.target = SimpleNamespace(window=2, generation=1)
        self.attachment.scene_changed()
        completed(None)
        self.assertEqual(self.native.input, [])
        self.assertEqual(self.native.cancelled, 'context')
        self.ready()
        self.input()
        self.assertEqual(len(self.native.input), 1)

    def test_context_failure_keeps_status_and_close_available(self):
        self.ready()
        self.native.context = None
        self.input('text', text='unavailable')
        self.input()
        self.assertEqual(self.native.input, [])
        self.request('status')
        self.assertEqual(self.owner.messages[-1]['result']['state'], 'running')
        self.request('close_window', window=1)
        self.assertEqual(self.native.closed, [1])

    def test_slow_consumer_has_one_frame_and_remains_interactive(self):
        self.ready()
        self.attachment.damage()
        self.captured()
        for _ in range(100):
            self.attachment.damage()
        self.assertEqual(len(self.native.captures), 2)
        self.input()
        self.assertEqual(len(self.native.input), 1)
        self.acknowledge()
        self.assertEqual(len(self.native.captures), 3)

    def test_capture_failure_has_no_timer_retry_and_does_not_end_application(self):
        self.native.captures[0][1](None, None, 'CAPTURE_UNAVAILABLE')
        self.assertEqual(len(self.native.captures), 1)
        self.assertEqual(self.timers, {})
        self.request('status')
        self.assertEqual(self.owner.messages[-1]['result']['state'], 'running')
        self.request('refresh')
        self.assertEqual(len(self.native.captures), 2)

    def test_delayed_old_cleanup_does_not_release_current_attachment(self):
        previous = self.owner
        self.attachment.detach(previous)
        self.owner = Owner(self.attachment)
        self.attachment.attach(self.owner)
        released = list(self.native.released)
        self.attachment.detach(previous)
        self.assertEqual(self.native.released, released)

    def test_close_after_text_obeys_the_same_order(self):
        self.ready()
        self.input('text', text='before close')
        self.request('close_window', window=1)
        self.assertEqual(self.native.closed, [])
        self.native.commits[0][2](None)
        self.assertEqual(self.native.closed, [1])


if __name__ == '__main__':
    unittest.main()
