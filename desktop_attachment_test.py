"""Frame acknowledgement and input authority follow one native target instance."""
from types import SimpleNamespace
import unittest

from desktop_attachment import DesktopAttachment


class Owner:
    def __init__(self, attachment):
        self.attachment = attachment
        self.messages, self.frames = [], []
        self.frame_pending = False

    def send(self, value):
        self.messages.append(value)
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

    def test_native_window_and_sent_frame_do_not_grant_input(self):
        self.input()
        self.assertEqual(self.native.input, [])
        self.captured()
        self.input()
        self.assertEqual(self.native.input, [])
        self.acknowledge()
        self.input()
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
