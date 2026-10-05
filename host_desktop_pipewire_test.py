import ctypes as C
import mmap
import struct
import tempfile
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
        capture.previous_sequence = None
        capture.painter = None
        capture.mappings = {}
        frames, shapes = [], []
        capture.Gst = SimpleNamespace(Buffer=SimpleNamespace(new_wrapped=lambda pixels: pixels))
        capture.captured = lambda pixels, width, height, timestamp: frames.append(pixels)
        capture.cursor_changed = (lambda *args: shapes.append(args)) if local else None
        capture.equal = lambda a, b: a == b
        return capture, frames, shapes

    def deliver(self, capture, pixels, cursor, *, stride=8, offset=0, maximum=None, corrupt=0,
                damage=None, sequence=None, flags=0):
        from host_desktop_pipewire import _Buffer, _Data, _Meta, _Chunk
        raw, meta = C.create_string_buffer(pixels), C.create_string_buffer(cursor)
        chunk = _Chunk(offset, len(pixels), stride, corrupt)
        datas = (_Data * 1)(_Data(1, 1, -1, 0, maximum or len(pixels), C.addressof(raw), C.pointer(chunk)))
        values = [_Meta(5, len(cursor), C.addressof(meta))]
        if damage is not None:
            regions = C.create_string_buffer(damage)
            values.append(_Meta(3, len(damage), C.addressof(regions)))
        if sequence is not None:
            header = C.create_string_buffer(struct.pack('<IIqqQ', flags, 0, 0, 0, sequence))
            values.append(_Meta(1, 32, C.addressof(header)))
        metas = (_Meta * len(values))(*values)
        capture._buffer(_Buffer(len(values), 1, metas, datas))

    def test_empty_damage_skips_pixel_copy_only_with_a_contiguous_baseline(self):
        capture, frames, _ = self.fixture()
        self.deliver(capture, bytes(16), bytes(28), damage=bytes(16), sequence=1)
        capture.equal = lambda *_: self.fail('Empty damage must not scan pixels')
        with patch('host_desktop_pipewire.C.string_at', wraps=C.string_at) as read:
            self.deliver(capture, bytes(16), bytes(28), damage=bytes(16), sequence=2)
        self.assertEqual([call.args[1] for call in read.call_args_list], [28, 16, 32])
        self.assertEqual(frames, [bytes(16)])

    def test_missing_damage_discontinuity_and_dropped_sequence_require_full_pixels(self):
        for damage, sequence, flags in ((None, 2, 0), (bytes(16), 3, 0), (bytes(16), 2, 1),
                                        (bytes(16), None, 0)):
            capture, frames, _ = self.fixture()
            self.deliver(capture, bytes(16), bytes(28), damage=bytes(16), sequence=1)
            self.deliver(capture, bytes([50]) * 16, bytes(28), damage=damage, sequence=sequence, flags=flags)
            self.assertEqual(frames[-1], bytes([50]) * 16)

    def test_damage_region_compares_only_changed_rows_and_keeps_full_frame(self):
        capture, frames, _ = self.fixture()
        self.deliver(capture, bytes(16), bytes(28), sequence=1)
        compared = []
        capture.equal = lambda a, b: compared.append(len(a)) or a == b
        region = struct.pack('<iiII', 1, 1, 1, 1)
        self.deliver(capture, bytes(16), bytes(28), sequence=2, damage=region)
        self.assertEqual(compared, [4])
        self.assertEqual(len(frames), 1)
        self.deliver(capture, bytes(12) + bytes([1, 2, 3, 255]), bytes(28), sequence=3, damage=region)
        self.assertEqual(frames[-1], bytes(12) + bytes([1, 2, 3, 255]))

    def test_corrupt_frame_breaks_damage_continuity(self):
        capture, frames, _ = self.fixture()
        self.deliver(capture, bytes(16), bytes(28), sequence=1)
        self.deliver(capture, bytes(16), bytes(28), sequence=2, corrupt=1)
        self.deliver(capture, bytes([7]) * 16, bytes(28), sequence=3, damage=bytes(16))
        self.assertEqual(frames[-1], bytes([7]) * 16)

    def test_cursor_only_buffer_cannot_restore_pixels_lost_in_a_sequence_gap(self):
        capture, frames, _ = self.fixture()
        self.deliver(capture, bytes(16), bytes(28), sequence=1)
        self.deliver(capture, b'', bytes(28), sequence=3)
        self.deliver(capture, bytes([7]) * 16, bytes(28), sequence=4, damage=bytes(16))
        self.assertEqual(frames[-1], bytes([7]) * 16)

    def test_missing_cursor_metadata_never_claims_embedded_pixels(self):
        from host_desktop_pipewire import _Buffer, _Data, _Meta, _Chunk
        capture, frames, _ = self.fixture()
        raw, header = C.create_string_buffer(bytes(16)), C.create_string_buffer(bytes(32))
        chunk = _Chunk(0, 16, 8, 0)
        datas = (_Data * 1)(_Data(1, 1, -1, 0, 16, C.addressof(raw), C.pointer(chunk)))
        metas = (_Meta * 1)(_Meta(1, 32, C.addressof(header)))
        with self.assertRaisesRegex(DesktopError, 'CURSOR_METADATA_UNAVAILABLE'):
            capture._buffer(_Buffer(1, 1, metas, datas))
        self.assertEqual(frames, [])

    def test_invalid_damage_is_rejected_before_reading_pixels(self):
        for damage in (bytes(15), struct.pack('<iiII', -1, 0, 1, 1),
                       struct.pack('<iiII', 1, 1, 2, 2), bytes(16 * 17)):
            capture, frames, _ = self.fixture()
            with self.assertRaisesRegex(DesktopError, 'CAPTURE_LAYOUT_UNSUPPORTED'):
                self.deliver(capture, bytes(16), bytes(28), damage=damage, sequence=1)
            self.assertEqual(frames, [])

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

    def test_producer_memfd_without_mappable_flag_uses_bounded_readonly_mapping(self):
        from host_desktop_pipewire import _Buffer, _Data, _Meta, _Chunk, _PWBuffer
        capture, frames, _ = self.fixture()
        cursor = C.create_string_buffer(bytes(28))
        metas = (_Meta * 1)(_Meta(5, 28, C.addressof(cursor)))
        chunk = _Chunk(4, 16, 8, 0)
        with tempfile.TemporaryFile() as source:
            prefix = mmap.ALLOCATIONGRANULARITY + 7
            source.write(bytes(prefix) + b'pad!' + bytes([17]) * 16)
            source.flush()
            # Mutter 46 with PipeWire 1.0.5 supplies READWRITE (3), without
            # MAPPABLE (8). PipeWire 1.4 leaves its data pointer unset.
            datas = (_Data * 1)(_Data(2, 3, source.fileno(), prefix, 20, None, C.pointer(chunk)))
            buffer = _Buffer(1, 1, metas, datas)
            capture._buffer(buffer)
            self.assertEqual(frames, [bytes([17]) * 16])
            self.assertEqual(len(capture.mappings), 1)
            mapping = next(iter(capture.mappings.values()))[1]
            with self.assertRaises(TypeError):
                mapping[0] = 0
            source.seek(prefix + 4)
            source.write(bytes([29]) * 16)
            source.flush()
            capture._buffer(buffer)
            self.assertEqual(frames, [bytes([17]) * 16, bytes([29]) * 16])
            self.assertIs(next(iter(capture.mappings.values()))[1], mapping)
            datas[0].mapoffset += 1
            with self.assertRaisesRegex(DesktopError, 'CAPTURE_LAYOUT_UNSUPPORTED'):
                capture._buffer(buffer)
            datas[0].mapoffset -= 1
            received = _PWBuffer(C.pointer(buffer))
            capture._remove_buffer(None, C.pointer(received))
            self.assertTrue(mapping.closed)
            self.assertEqual(capture.mappings, {})
            self.assertFalse(source.closed, 'Capture must not close the producer descriptor')
            # A removed buffer address may be reused by a successor allocation.
            capture._buffer(buffer)
            replacement = next(iter(capture.mappings.values()))[1]
            self.assertIsNot(replacement, mapping)
            capture.closed = capture.started = capture.initialized = False
            capture.loop = capture.stream = capture.core = capture.context = None
            capture.close()
            capture.close()
            self.assertTrue(replacement.closed)
            self.assertEqual(capture.mappings, {})
            self.assertFalse(source.closed)

    def test_unmapped_memfd_rejects_invalid_backing_size_before_reading(self):
        from host_desktop_pipewire import _Buffer, _Data, _Meta, _Chunk
        capture, frames, _ = self.fixture()
        cursor = C.create_string_buffer(bytes(28))
        metas = (_Meta * 1)(_Meta(5, 28, C.addressof(cursor)))
        chunk = _Chunk(0, 16, 8, 0)
        with tempfile.TemporaryFile() as source:
            source.write(bytes(16))
            source.flush()
            for kind, fd, offset, maximum in ((2, -1, 0, 16), (2, source.fileno(), 1, 16),
                    (2, source.fileno(), 0, 268435457), (1, source.fileno(), 0, 16)):
                with self.subTest(kind=kind, offset=offset, maximum=maximum):
                    datas = (_Data * 1)(_Data(kind, 3, fd, offset, maximum, None, C.pointer(chunk)))
                    with self.assertRaisesRegex(DesktopError, 'CAPTURE_LAYOUT_UNSUPPORTED'):
                        capture._buffer(_Buffer(1, 1, metas, datas))
            self.assertEqual(frames, [])
            self.assertEqual(capture.mappings, {})

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
