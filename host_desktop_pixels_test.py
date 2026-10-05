import unittest
from host_desktop_contract import DesktopError
from host_desktop_pixels import PixelCopy


class PixelCopyTests(unittest.TestCase):
    def test_packed_and_padded_readonly_sources_copy_into_owned_storage(self):
        copier = PixelCopy()
        for stride in (8, 9, 16):
            raw = bytes(range(8)) + bytes(stride - 8) + bytes(range(8, 16))
            result = bytearray(16)
            copier.copy(memoryview(result), memoryview(raw), 2, 2, stride)
            self.assertEqual(result, bytes(range(16)))
            self.assertEqual(raw[:8], bytes(range(8)))

    def test_invalid_lengths_are_rejected_before_native_copy(self):
        for size, source, stride in ((15, 16, 8), (16, 15, 8), (16, 16, 7)):
            result = bytearray([99]) * size
            with self.assertRaisesRegex(DesktopError, 'CAPTURE_LAYOUT_UNSUPPORTED'):
                PixelCopy().copy(result, bytes(source), 2, 2, stride)
            self.assertEqual(result, bytes([99]) * size)
        with self.assertRaises(BufferError):
            PixelCopy().copy(bytes(16), bytes(16), 2, 2, 8)

    def test_swizzle_rejects_unaligned_destination_before_writing(self):
        result = bytearray([99]) * 17
        with self.assertRaisesRegex(DesktopError, 'CAPTURE_LAYOUT_UNSUPPORTED'):
            PixelCopy().copy(memoryview(result)[1:], bytes(16), 2, 2, 8, swap=True)
        self.assertEqual(result, bytes([99]) * 17)


if __name__ == '__main__':
    unittest.main()
