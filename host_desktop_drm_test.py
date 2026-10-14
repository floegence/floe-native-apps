import array
import io
import mmap
import os
import socket
import struct
import threading
import time
import unittest
import zlib
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from host_desktop_drm import DRMCapture, cursor_png
from host_desktop_contract import DesktopError


class DRMWorkerTests(unittest.TestCase):
    def run_capture_workload(self, fps, inputs=(), motion=False):
        clock = SimpleNamespace(now=10., end=14.2, value=0)
        capture = DRMCapture.__new__(DRMCapture)
        capture.lock = threading.Lock()
        capture.stop = SimpleNamespace(is_set=lambda: clock.now >= clock.end)
        capture.enabled, capture.probing, capture.epoch = True, None, 1
        capture.reset, capture.fps = False, fps
        capture.hot_until = 10.
        capture.width = capture.height = 0
        capture.previous = capture.pool = None
        captured, delivered = [], []
        events = []
        for at in inputs:
            events.extend(((10. + at, capture.interacted),
                           (10. + at + .008, lambda: setattr(clock, 'value', clock.value + 1))))
        events.sort(key=lambda item: item[0])

        class Wake:
            signaled = False

            def clear(self):
                self.signaled = False

            def set(self):
                self.signaled = True

            def wait(self, duration):
                until = min(clock.end, clock.now + duration)
                while events and events[0][0] <= until and not self.signaled:
                    clock.now, callback = events.pop(0)
                    callback()
                if not self.signaled:
                    clock.now = until

        capture.wake = Wake()

        def descriptor(_):
            captured.append(clock.now)
            value = int((clock.now - 10.) * fps + 1e-6) if motion else clock.value
            return memoryview(bytes((value % 256,)) * 16), 2, 2, 8, 0x34325258

        @contextmanager
        def mapped(_, pixels):
            yield pixels

        capture.descriptor = descriptor
        capture.queue_sample = lambda *_: delivered.append((clock.now, clock.value))
        capture.Gst = None
        capture.GLib = SimpleNamespace(idle_add=lambda callback, *args: callback(*args))
        capture.failed = lambda error: self.fail(error)
        pool = SimpleNamespace(capture=lambda pixels, *_, **__: bytes(pixels))
        with patch('host_desktop_drm.time.monotonic', lambda: clock.now), \
                patch('host_desktop_drm.FramePool', lambda *_: pool), \
                patch('host_desktop_drm.mapped_pixels', mapped), \
                patch('host_desktop_drm.equal_pixels', lambda a, b: a == b):
            capture.capture()
        return captured, delivered

    def test_static_typing_returns_to_idle_between_inputs_and_paints_promptly(self):
        inputs = [.42 * n for n in range(1, 10)]
        captured, delivered = self.run_capture_workload(60, inputs)
        self.assertLess(len(captured), 80,
                        'unchanged frames must not keep full-rate conversion between typing events')
        for value, at in enumerate(inputs, 1):
            painted = next(t for t, v in delivered if v == value)
            self.assertLess(painted - (10. + at), .06,
                            'returning to idle must retain immediate input response')

    def test_continuous_motion_retains_configured_capture_cadence(self):
        for fps in (15, 30, 60):
            with self.subTest(fps=fps):
                captured, _ = self.run_capture_workload(fps, motion=True)
                self.assertGreaterEqual(len(captured), int(4.2 * fps) - 1)
                self.assertLessEqual(max(b - a for a, b in zip(captured, captured[1:])),
                                     1 / fps + 1e-6)

    def test_unchanged_desktop_keeps_five_hz_polling_after_startup(self):
        captured, _ = self.run_capture_workload(60)
        self.assertLessEqual(len(captured), 27)

    def test_probe_and_retired_epoch_release_the_converter_slot(self):
        for enabled, retire in ((False, False), (True, True)):
            capture = DRMCapture.__new__(DRMCapture)
            capture.lock = threading.Lock()
            capture.stop, capture.wake = threading.Event(), threading.Event()
            capture.enabled, capture.probing, capture.epoch = enabled, (lambda *_: None), 1
            capture.hot_until = 0
            capture.reset, capture.fps = False, 60
            capture.GLib = SimpleNamespace(idle_add=lambda *_: None)
            capture.failed = lambda *_: self.fail('unexpected capture error')
            pixels = memoryview(bytes(16))
            def descriptor(_):
                capture.stop.set()
                if retire:
                    capture.epoch = 2
                return pixels, 2, 2, 8, 0x34325258
            capture.descriptor = descriptor
            capture.capture()
            with self.assertRaises(ValueError, msg='every converter-slot borrow must end before teardown'):
                len(pixels)

    def descriptor_fixture(self, header):
        capture = DRMCapture.__new__(DRMCapture)
        capture.export_lock = threading.Lock()
        capture.exporter = SimpleNamespace(send=lambda _: None,
            recvmsg=lambda *_: (struct.pack('<iI', 0, 1) + bytes(72), [], 0, None))
        capture.owner = SimpleNamespace(sendmsg=lambda *_: None)
        capture.converter = SimpleNamespace(stdout=io.BytesIO(header))
        capture.pixels = mmap.mmap(-1, 64 << 20)
        return capture

    def test_conversion_reads_only_completion_header_and_copies_before_reuse(self):
        capture = self.descriptor_fixture(struct.pack('<i5I', 0, 2, 2, 8, 0x34324258, 16))
        # Use a real inherited descriptor in the export reply.
        fd = os.open(os.devnull, os.O_RDONLY)
        capture.exporter.recvmsg = lambda *_: (struct.pack('<iI', 0, 1) + bytes(72),
            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [os.dup(fd)]).tobytes())], 0, None)
        try:
            capture.pixels[:16] = bytes(range(16))
            pixels, width, height, stride, format = capture.descriptor(False)
            self.assertEqual((width, height, stride, format), (2, 2, 8, 0x34324258))
            self.assertIsInstance(pixels, memoryview)
            captured = bytes(pixels)
            pixels.release()
            capture.pixels[:16] = b'x' * 16
            self.assertEqual(captured, bytes(range(16)), 'the owned capture copy survives converter reuse')
            self.assertEqual(capture.converter.stdout.tell(), 24, 'pixel bytes never traverse the completion pipe')
        finally:
            os.close(fd)
            capture.pixels.close()

    def test_conversion_rejects_invalid_completion_before_exposing_shared_pixels(self):
        cases = ((0, 2, 2, 7, 0x34325258, 14),
                 (0, 2, 2, 8, 0x34325258, 17),
                 (0, 8192, 8192, 32768, 0x34325258, 268435456),
                 (0, 2, 2, 8, 0x12345678, 16),
                 (-5, 0, 0, 0, 0, 0))
        for header in cases:
            with self.subTest(header=header):
                capture = self.descriptor_fixture(struct.pack('<i5I', *header))
                fd = os.open(os.devnull, os.O_RDONLY)
                capture.exporter.recvmsg = lambda *_: (struct.pack('<iI', 0, 1) + bytes(72),
                    [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [os.dup(fd)]).tobytes())], 0, None)
                try:
                    with self.assertRaises(DesktopError):
                        capture.descriptor(False)
                finally:
                    os.close(fd)
                    capture.pixels.close()

    def test_first_input_wakes_idle_capture_without_bypassing_active_cadence(self):
        capture = DRMCapture.__new__(DRMCapture)
        capture.lock, capture.wake = threading.Lock(), threading.Event()
        capture.hot_until, capture.fps = -1, 60
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
