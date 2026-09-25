"""Actual socket framing and native scene barriers protect captured pixels."""
import socket
import struct
import unittest
from types import SimpleNamespace

from desktop_capture import NativeFrames


class Loop:
    def __init__(self):
        self.watches, self.timers = {}, {}
        self.sequence = 0

    def watch(self, fd, read=None, write=None):
        self.watches[fd] = (read, write)

    def later(self, _delay, callback):
        self.sequence += 1
        self.timers[self.sequence] = callback
        return self.sequence

    def cancel(self, timer):
        self.timers.pop(timer, None)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.local, self.remote = socket.socketpair()
        self.loop, self.barriers, self.results = Loop(), [], []
        self.target = SimpleNamespace(window=3, generation=9)
        self.frames = NativeFrames(self.local, self.loop, self.barriers.append)
        self.addCleanup(self.frames.close)
        self.addCleanup(self.remote.close)

    def start(self):
        self.frames.capture(self.target, lambda *args: self.results.append(args))

    def scene(self, window=3, generation=9):
        self.barriers.pop(0)(window, generation)

    def request(self):
        self.start()
        self.scene()
        self.frames.write()
        return struct.unpack('=I', self.remote.recv(4))[0]

    def pixels(self, sequence=1):
        packet = struct.pack('=6I', sequence, 1, 2, 1, 0x34325258, 8) + b'abcdefgh'
        self.remote.sendall(packet)
        self.frames.read()
        self.frames.read()

    def test_pixels_require_barriers_on_both_sides_of_actual_capture(self):
        self.assertEqual(self.request(), 1)
        self.pixels()
        self.assertEqual(self.results, [])
        self.scene()
        self.assertEqual(self.results, [({'encoding': 'bgrx', 'width': 2, 'height': 1}, b'abcdefgh', None)])

    def test_delayed_begin_barrier_never_captures_the_replacement_window(self):
        self.start()
        self.scene(window=4, generation=10)
        self.assertEqual(self.results[0][2], 'CAPTURE_TARGET_CHANGED')
        self.assertEqual(self.loop.timers, {})

    def test_geometry_or_window_change_discards_pixels_without_relabeling(self):
        for window, generation in ((3, 10), (4, 9)):
            sequence = self.request()
            self.pixels(sequence)
            self.scene(window, generation)
            self.assertEqual(self.results[-1], (None, None, 'CAPTURE_TARGET_CHANGED'))

    def test_cancel_drains_inflight_pixels_and_late_reply_before_next_capture(self):
        sequence = self.request()
        self.frames.cancel()
        with self.assertRaises(ValueError):
            self.start()
        self.pixels(sequence)
        self.assertEqual(self.results, [(None, None, 'CAPTURE_CANCELLED')])
        sequence = self.request()
        self.pixels(sequence)
        self.scene()
        self.assertIsNone(self.results[-1][2])

    def test_partial_headers_and_payload_do_not_publish_incomplete_frames(self):
        sequence = self.request()
        packet = struct.pack('=6I', sequence, 1, 2, 1, 0x34325241, 8) + b'12345678'
        for byte in packet:
            self.remote.sendall(bytes((byte,)))
            self.frames.read()
            self.assertEqual(self.results, [])
        self.scene()
        self.assertEqual(self.results[0][0]['encoding'], 'bgra')

    def test_malformed_header_closes_only_capture_transport_before_allocation(self):
        self.request()
        self.remote.sendall(struct.pack('=6I', 1, 1, 0xffffffff, 1, 0x34325258, 0xffffffff))
        self.frames.read()
        self.assertEqual(self.results[0], (None, None, 'CAPTURE_PROTOCOL_INVALID'))
        self.assertEqual(self.local.fileno(), -1)

    def test_wrong_sequence_and_source_retry_are_not_silently_replayed(self):
        sequence = self.request()
        self.remote.sendall(struct.pack('=6I', sequence, 2, 2, 1, 0x34325258, 0))
        self.frames.read()
        self.assertEqual(self.results[0][2], 'CAPTURE_SOURCE_CHANGED')
        sequence = self.request()
        self.pixels(sequence - 1)
        self.assertEqual(self.results[-1][2], 'CAPTURE_PROTOCOL_INVALID')

    def test_timeout_revokes_late_barrier_without_restarting_or_replaying(self):
        self.start()
        expired = next(iter(self.loop.timers.values()))
        expired()
        self.scene()
        self.assertEqual(self.results, [(None, None, 'CAPTURE_TIMEOUT')])
        self.assertEqual(self.local.fileno(), -1)

    def test_cancel_after_pixels_cannot_publish_from_late_end_barrier(self):
        self.request()
        self.pixels()
        self.frames.cancel()
        self.scene()
        self.assertEqual(self.results, [(None, None, 'CAPTURE_CANCELLED')])

    def test_retired_timeout_cannot_close_the_next_capture(self):
        self.request()
        expired = next(iter(self.loop.timers.values()))
        self.pixels()
        self.scene()
        sequence = self.request()
        expired()
        self.pixels(sequence)
        self.scene()
        self.assertEqual(len(self.results), 2)
        self.assertIsNone(self.results[-1][2])


if __name__ == '__main__':
    unittest.main()
