"""Native cursor snapshots are bounded and cannot cross a scene lifetime."""
import struct
import unittest
import zlib
from types import SimpleNamespace

from desktop_cursor import NativeCursor


class CursorTests(unittest.TestCase):
    def setUp(self):
        self.sent, self.updates = [], []
        self.target = SimpleNamespace(window=1, generation=2)
        self.epoch = 1
        self.cursor = NativeCursor(self.sent.append, lambda: self.target, lambda: self.updates.append(True), lambda: self.epoch)

    def state(self, revision=1, width=2, height=1, logical_width=2, logical_height=1, xhot=1):
        self.cursor.observe(f'cursor-state {revision} 1 2 1 image {width} {height} {logical_width} {logical_height} {xhot} 0'.split())

    def chunk(self, revision, offset, data):
        self.cursor.observe(f'cursor-data {revision} {offset} {data.hex() if data else "-"}'.split())

    def test_pixels_alpha_and_surface_size_are_preserved(self):
        self.state(logical_width=1, xhot=0)
        rgba = bytes((200, 100, 50, 128, 0, 0, 0, 0))
        self.chunk(1, 0, rgba)
        description, png = self.cursor.current
        self.assertEqual((description['width'], description['logical_width'], description['xhot']), (2, 1, 0))
        self.assertEqual(png[24:29], b'\x08\x06\0\0\0')
        offset, compressed = 8, b''
        while offset < len(png):
            size = struct.unpack('!I', png[offset:offset+4])[0]
            if png[offset+4:offset+8] == b'IDAT':
                compressed += png[offset+8:offset+8+size]
            offset += size + 12
        self.assertEqual(zlib.decompress(compressed), b'\0' + rgba)

    def test_change_during_transfer_keeps_only_latest_and_one_request(self):
        self.state(1)
        self.state(2)
        self.state(3)
        self.assertEqual(len(self.sent), 1)
        self.chunk(1, 0, b'')
        self.assertEqual(self.sent[-1], 'cursor-read 3 0\n')
        self.chunk(3, 0, bytes(8))
        self.assertEqual(self.cursor.current[0]['sequence'], 3)

    def test_hidden_reset_and_target_retirement_discard_late_pixels(self):
        self.state()
        self.cursor.observe('cursor-state 2 1 2 1 hidden'.split())
        self.chunk(1, 0, bytes(8))
        self.assertEqual(self.cursor.current[0]['mode'], 'hidden')
        self.assertIsNone(self.cursor.current[1])
        self.state(3)
        self.target = SimpleNamespace(window=1, generation=3)
        self.cursor.invalidate()
        self.chunk(3, 0, bytes(8))
        self.assertIsNone(self.cursor.current)
        self.assertIsNone(self.cursor.pending)

    def test_boundaries_and_offsets_fail_closed(self):
        for dimensions in ((0, 1), (1025, 1), (1, 1025)):
            with self.subTest(dimensions=dimensions), self.assertRaises(ValueError):
                self.state(width=dimensions[0], height=dimensions[1])
        self.state()
        with self.assertRaises(ValueError):
            self.chunk(1, 1, bytes(8))
        with self.assertRaises(ValueError):
            self.chunk(1, 0, bytes(9))

    def test_snapshot_uses_bounded_pull_chunks_and_validates_all_bytes(self):
        self.state(width=64, height=64, logical_width=32, logical_height=32)
        for offset in range(0, 64 * 64 * 4, 1024):
            self.assertEqual(self.sent[-1], f'cursor-read 1 {offset}\n')
            self.chunk(1, offset, bytes(1024))
        self.assertEqual(len(self.sent), 16)
        self.assertIsNone(self.cursor.pending)
        self.assertEqual(self.cursor.current[0]['height'], 64)

    def test_loss_and_stale_scene_cannot_publish(self):
        self.cursor.observe('cursor-state 1 1 1 1 hidden'.split())
        self.assertIsNone(self.cursor.current)
        self.state(2)
        self.cursor.close()
        self.chunk(2, 0, bytes(8))
        self.assertIsNone(self.cursor.current)

    def test_takeover_rejects_late_snapshot_from_old_connection(self):
        self.state()
        self.epoch = 2
        self.cursor.invalidate()
        self.cursor.observe('cursor-state 2 1 2 1 hidden'.split())
        self.chunk(1, 0, bytes(8))
        self.assertIsNone(self.cursor.current)
        self.cursor.observe('cursor-state 3 2 2 1 hidden'.split())
        self.assertEqual(self.cursor.current[0]['mode'], 'hidden')
