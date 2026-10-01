import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from host_desktop_media import DesktopMedia, equal_pixels


class PixelComparisonTests(unittest.TestCase):
    def test_identical_pixels_and_every_tail_byte_are_observed(self):
        for size in (0, 4, 8, 12, 32):
            source = bytearray(range(size))
            self.assertTrue(equal_pixels(memoryview(source), memoryview(bytes(source))))
            for index in range(size):
                changed = bytearray(source)
                changed[index] ^= 0xff
                self.assertFalse(equal_pixels(memoryview(source), memoryview(changed)))
        self.assertFalse(equal_pixels(memoryview(b'1234'), memoryview(b'12345678')))

    def test_motion_outside_probe_spans_is_never_declared_equal(self):
        source = bytes(1 << 20)
        for index in (129, 4001, 65535, 500123, len(source) - 129):
            changed = bytearray(source)
            changed[index] = 1
            self.assertFalse(equal_pixels(memoryview(source), memoryview(changed)))


class SchedulerTests(unittest.TestCase):
    def test_recovery_keeps_capture_and_rejects_queued_old_encoder_output(self):
        callbacks, outputs = [], []
        loop = SimpleNamespace(timeout_add=lambda *_: 1, idle_add=lambda callback: callbacks.append(callback) or len(callbacks))
        gst = SimpleNamespace(State=SimpleNamespace(NULL='null'), FlowReturn=SimpleNamespace(OK='ok'), BufferFlags=SimpleNamespace(DELTA_UNIT=1))
        media = DesktopMedia(gst, loop, 1, {'frame_rate': 60}, ('fixture', ''), lambda *args: outputs.append(args), lambda *_: None)
        capture, encoding = Mock(), Mock()
        media.capture, media.encoding = capture, encoding
        media.encoding_size = (2, 2, 2, 2)
        media.latest = ('captured', 2, 2)
        for _ in range(4):
            media.submitted.append((media.credit.reserve(), 100))
        media.encoding_busy = True
        buffer = Mock()
        buffer.get_size.return_value = 4
        buffer.extract_dup.return_value = b'\x01\x42\x00\x1e'
        buffer.has_flags.return_value = False
        sample, sink = Mock(), Mock()
        sink.emit.return_value = sample
        sample.get_buffer.return_value = buffer
        sample.get_caps.return_value.get_structure.return_value.get_value.return_value = buffer
        media._encoded(sink, 1)
        media.recover(2)
        self.assertEqual(encoding.set_state.call_args.args, ('null',))
        capture.set_state.assert_not_called()
        self.assertIs(media.capture, capture)
        self.assertEqual(media.latest, ('captured', 2, 2))
        self.assertEqual(media.credit.pending, 0)
        self.assertFalse(media.encoding_busy)
        media.submitted.append((media.credit.reserve(), 200))
        callbacks[0]()
        self.assertEqual(list(media.submitted), [(1, 200)])
        self.assertEqual(outputs, [])

    def test_repeated_receipts_keep_one_pending_producer_and_close_cancels_it(self):
        class Loop:
            def __init__(self):
                self.next = 0
                self.pending = {}
            def timeout_add(self, delay, callback):
                self.next += 1
                self.pending[self.next] = (delay, callback)
                return self.next
            def idle_add(self, callback):
                return self.timeout_add(0, callback)
            def source_remove(self, source):
                del self.pending[source]
        loop = Loop()
        media = DesktopMedia(None, loop, 1, {'frame_rate': 60}, ('fixture', ''), lambda *_: None, lambda *_: None)
        frame = media.credit.reserve()
        media._schedule_produce(12)
        for _ in range(100):
            media.acknowledge(frame)
            media._schedule_produce()
        self.assertEqual(len(loop.pending), 2)  # Refinement observer and one producer.
        self.assertEqual(loop.pending[media.produce_source][0], 12)
        media.close()
        self.assertEqual(loop.pending, {})


if __name__ == '__main__':
    unittest.main()
