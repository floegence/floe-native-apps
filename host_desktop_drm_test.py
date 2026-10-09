import struct
import threading
import time
import unittest
import zlib
from types import SimpleNamespace
from host_desktop_drm import DRMCapture, cursor_png


class DRMWorkerTests(unittest.TestCase):
    def test_first_input_wakes_idle_capture_without_bypassing_active_cadence(self):
        capture = DRMCapture.__new__(DRMCapture)
        capture.lock, capture.wake = threading.Lock(), threading.Event()
        capture.hot_until = capture.changed_at = -1
        waiting = threading.Event()
        elapsed = []
        def idle():
            started = time.monotonic()
            waiting.set()
            capture.wake.wait(.2)
            elapsed.append(time.monotonic() - started)
        thread = threading.Thread(target=idle)
        thread.start()
        self.assertTrue(waiting.wait(1))
        capture.interacted()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertLess(elapsed[0], .1, 'idle polling must not delay the first input')
        capture.wake.clear()
        for _ in range(100):
            capture.interacted()
        self.assertFalse(capture.wake.is_set(), 'continuous input must retain the configured frame-rate bound')

    def test_display_suspend_is_once_per_current_epoch_and_bounded_during_wake(self):
        delivered = []
        capture = DRMCapture.__new__(DRMCapture)
        capture.lock = threading.Lock()
        capture.epoch, capture.enabled, capture.settle_until = 3, True, float('inf')
        capture.pending_sample, capture.pending_cursor = object(), object()
        capture.GLib = SimpleNamespace(idle_add=lambda callback, epoch: callback(epoch))
        capture.suspended = delivered.append
        capture.inactive(3)
        self.assertTrue(capture.enabled)
        capture.settle_until = 0
        capture.inactive(2)
        self.assertTrue(capture.enabled)
        capture.inactive(3)
        capture.inactive(3)
        self.assertEqual(delivered, [3])
        self.assertFalse(capture.enabled)
        self.assertIsNone(capture.pending_sample)
        self.assertIsNone(capture.pending_cursor)

    def test_cursor_unpremultiplication_preserves_alpha_and_channels(self):
        png = cursor_png(bytes((20, 40, 60, 128, 0, 0, 0, 0)), 2, 1)
        offset, rows = 8, None
        while offset < len(png):
            size = struct.unpack_from('!I', png, offset)[0]
            if png[offset + 4:offset + 8] == b'IDAT':
                rows = zlib.decompress(png[offset + 8:offset + 8 + size])
            offset += size + 12
        self.assertEqual(rows, bytes((0, 120, 80, 40, 128, 0, 0, 0, 0)))

    def test_preencode_capture_keeps_only_latest_sample(self):
        callbacks, delivered = [], []
        capture = DRMCapture.__new__(DRMCapture)
        capture.lock, capture.stop = threading.Lock(), threading.Event()
        capture.pending_sample, capture.sample_scheduled = None, False
        capture.GLib = SimpleNamespace(idle_add=lambda callback: callbacks.append(callback))
        capture.sample = lambda *sample: delivered.append(sample)
        for frame in range(100):
            capture.queue_sample(1, frame, 1920, 1280)
        self.assertEqual(len(callbacks), 1)
        callbacks.pop()()
        self.assertEqual(delivered, [(1, 99, 1920, 1280)])
        capture.stop.set()
        capture.queue_sample(1, 100, 1920, 1280)
        callbacks.pop()()
        self.assertEqual(len(delivered), 1)

    def test_cursor_queue_keeps_shape_and_latest_position_with_bounded_work(self):
        callbacks, delivered = [], []
        capture = DRMCapture.__new__(DRMCapture)
        capture.lock, capture.stop = threading.Lock(), threading.Event()
        capture.pending_cursor, capture.cursor_scheduled = None, False
        capture.GLib = SimpleNamespace(idle_add=lambda callback: callbacks.append(callback))
        capture.cursor = lambda *sample: delivered.append(sample)
        capture.queue_cursor(1, {'cursor_visible': True, 'cursor_position': {'x': 0}, 'width': 64}, b'png')
        for x in range(100):
            capture.queue_cursor(1, {'cursor_visible': True, 'cursor_position': {'x': x}}, b'')
        self.assertEqual(len(callbacks), 1)
        callbacks.pop()()
        self.assertEqual(delivered, [(1, {'cursor_visible': True, 'cursor_position': {'x': 99}, 'width': 64}, b'png')])
        capture.queue_cursor(2, {'cursor_visible': False}, b'')
        callbacks.pop()()
        self.assertEqual(delivered[-1], (2, {'cursor_visible': False}, b''))


if __name__ == '__main__':
    unittest.main()
