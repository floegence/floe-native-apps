import ctypes as C
import struct
import unittest

from host_desktop_contract import DesktopError
from types import SimpleNamespace
from unittest.mock import patch


class CursorMetadataTests(unittest.TestCase):
    def test_bitmap_update_position_only_and_hidden_are_distinct(self):
        from host_desktop_pipewire import CursorState
        cursor = CursorState()
        header = struct.pack('<IIiiiiI', 1, 0, 80, 90, 0, 0, 28)
        bitmap = struct.pack('<IIIiI', 12, 1, 1, 4, 20)
        cursor.update(header + bitmap + bytes([8, 16, 32, 128]))
        self.assertEqual(cursor.pixels, bytes([8, 16, 32, 128]))
        self.assertEqual(cursor.position, (80, 90))
        cursor.update(struct.pack('<IIiiiiI', 1, 0, 180, 190, 0, 0, 0))
        self.assertEqual(cursor.position, (180, 190))
        self.assertTrue(cursor.visible)
        cursor.update(bytes(28))
        self.assertFalse(cursor.visible, 'An invalid cursor id has no current visible position')
        cursor.update(struct.pack('<IIiiiiI', 1, 0, 180, 190, 0, 0, 0))
        self.assertTrue(cursor.visible, 'Position-only reentry reuses the last bitmap')
        cursor.update(header + struct.pack('<IIIiI', 12, 1, 1, 4, 0))
        self.assertFalse(cursor.visible)

    def test_cursor_offsets_stride_and_hotspot_are_bounded_before_reading(self):
        from host_desktop_pipewire import CursorState
        header = struct.pack('<IIiiiiI', 1, 0, 1, 2, 0, 0, 28)
        invalid = [header[:27], header[:24] + struct.pack('<I', 0xffffffff),
                   header + struct.pack('<IIIiI', 12, 513, 1, 2052, 20),
                   header + struct.pack('<IIIiI', 12, 2, 1, 4, 20) + bytes(8),
                   header + struct.pack('<IIIiI', 12, 1, 1, 4, 24) + bytes(4),
                   struct.pack('<IIiiiiI', 1, 0, 1, 2, 5, 0, 28) +
                   struct.pack('<IIIiI', 12, 1, 1, 4, 20) + bytes(4)]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(DesktopError):
                CursorState().update(value)

    def test_rgba_padding_converts_to_premultiplied_bgra_without_padding(self):
        from host_desktop_pipewire import CursorState
        cursor = CursorState()
        cursor.update(struct.pack('<IIiiiiI', 1, 0, 1, 2, 0, 0, 28) +
                      struct.pack('<IIIiI', 11, 1, 2, 8, 20) +
                      bytes([32, 16, 8, 128, 99, 99, 99, 99, 80, 40, 20, 255]))
        self.assertEqual(cursor.pixels, bytes([8, 16, 32, 128, 20, 40, 80, 255]))


class PipeWireBufferTests(unittest.TestCase):
    def fixture(self, local=True, format=12):
        from host_desktop_pipewire import PipeWireCapture, CursorState
        capture = PipeWireCapture.__new__(PipeWireCapture)
        capture.width = capture.height = 2
        capture.format = format
        capture.cursor = CursorState()
        capture.previous = capture.previous_cursor = capture.previous_shape = None
        capture.painter = None
        frames, shapes = [], []
        capture.Gst = SimpleNamespace(Buffer=SimpleNamespace(new_wrapped=lambda pixels: pixels))
        capture.captured = lambda pixels, width, height, timestamp: frames.append(pixels)
        capture.cursor_changed = (lambda *args: shapes.append(args)) if local else None
        capture.equal = lambda a, b: a == b
        return capture, frames, shapes

    def deliver(self, capture, pixels, cursor, *, stride=8, offset=0, maximum=None, corrupt=0):
        from host_desktop_pipewire import _Buffer, _Data, _Meta, _Chunk
        raw, meta = C.create_string_buffer(pixels), C.create_string_buffer(cursor)
        chunk = _Chunk(offset, len(pixels), stride, corrupt)
        datas = (_Data * 1)(_Data(1, 1, -1, 0, maximum or len(pixels), C.addressof(raw), C.pointer(chunk)))
        metas = (_Meta * 1)(_Meta(5, len(cursor), C.addressof(meta)))
        capture._buffer(_Buffer(1, 1, metas, datas))

    def test_cursor_only_control_updates_never_reencode_identical_pixels(self):
        capture, frames, shapes = self.fixture()
        bitmap = struct.pack('<IIiiiiI', 1, 0, 1, 1, 0, 0, 28) + struct.pack('<IIIiI', 12, 1, 1, 4, 20) + bytes([0, 0, 255, 255])
        self.deliver(capture, bytes([64, 64, 64, 255]) * 4, bitmap)
        self.assertEqual((len(frames), len(shapes)), (1, 1))
        self.deliver(capture, b'', struct.pack('<IIiiiiI', 1, 0, 2, 2, 0, 0, 0))
        self.assertEqual((len(frames), len(shapes)), (1, 1))
        self.deliver(capture, b'', bytes(28))
        self.assertEqual((len(frames), len(shapes)), (1, 2))
        self.assertEqual(shapes[-1][:4], (1, 1, 0, 0))

    def test_padded_rgba_pixels_are_copied_without_padding_or_source_ownership(self):
        capture, frames, _ = self.fixture(format=11)
        self.deliver(capture, bytes([1, 2, 3, 255]) * 2 + bytes(8) + bytes([4, 5, 6, 255]) * 2,
                     bytes(28), stride=16)
        self.assertEqual(frames, [bytes([3, 2, 1, 255]) * 2 + bytes([6, 5, 4, 255]) * 2])

    def test_truncated_pixel_buffer_is_rejected_before_reading(self):
        capture, _, _ = self.fixture()
        for pixels, stride, offset in ((bytes(15), 8, 0), (bytes(16), -8, 0), (bytes(16), 8, 1)):
            with self.subTest(stride=stride, offset=offset), self.assertRaisesRegex(DesktopError, 'CAPTURE_LAYOUT_UNSUPPORTED'):
                self.deliver(capture, pixels, bytes(28), stride=stride, offset=offset)

    def test_view_mode_composites_cursor_movement_over_retained_pixels(self):
        capture, frames, _ = self.fixture(local=False)
        placements = []
        painter = SimpleNamespace(update=lambda *_: None, compose=lambda gst, pixels, w, h, cursor: placements.append(cursor) or pixels)
        with patch('host_desktop_xcapture.CursorPainter', return_value=painter):
            bitmap = struct.pack('<IIiiiiI', 1, 0, 1, 1, 0, 0, 28) + struct.pack('<IIIiI', 12, 1, 1, 4, 20) + bytes([0, 0, 255, 255])
            self.deliver(capture, bytes(16), bitmap)
            self.deliver(capture, b'', struct.pack('<IIiiiiI', 1, 0, 2, 2, 0, 0, 0))
            self.deliver(capture, b'', bytes(28))
        self.assertEqual(len(frames), 3)
        self.assertEqual([p[1:3] for p in placements], [(1, 1), (2, 2), (2, 2)])
        self.assertEqual([p[-1] for p in placements], [True, True, False])

    def test_corrupt_buffers_do_not_replace_the_visible_cursor(self):
        capture, frames, shapes = self.fixture()
        self.deliver(capture, bytes(16), bytes(28), corrupt=1)
        self.assertEqual(frames, [])
        self.assertEqual(shapes, [])

    def test_pod_object_uses_the_public_spa_object_type(self):
        from host_desktop_pipewire import _object, _scalar, _properties
        encoded = _object(0x40003, 4, [(1, _scalar(3, 2)), (0x20001, _scalar(3, 12))])
        self.assertEqual(struct.unpack_from('<II', encoded), (len(encoded) - 8, 15))
        memory = C.create_string_buffer(encoded)
        self.assertEqual(_properties(C.addressof(memory)), {1: (3, struct.pack('<I', 2)), 0x20001: (3, struct.pack('<I', 12))})

    def test_fixed_choice_preserves_negotiated_value_and_rejects_unfixed_range(self):
        from host_desktop_pipewire import _object, _choice, _properties
        for choice in (0, 1):
            encoded = _object(0x40003, 4, [(1, _choice(3, 4, [struct.pack('<I', value) for value in (2, 1, 9)], choice))])
            memory = C.create_string_buffer(encoded)
            if choice == 0:
                self.assertEqual(_properties(C.addressof(memory)), {1: (3, struct.pack('<I', 2))})
            else:
                with self.assertRaisesRegex(DesktopError, 'CAPTURE_FORMAT_UNSUPPORTED'):
                    _properties(C.addressof(memory))


if __name__ == '__main__':
    unittest.main()
