import unittest
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
