import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from host_desktop_contract import DesktopError
from host_desktop_pixels import PixelCopy, native_pixels


class NativePixelSlotTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, 'memfd_create'), 'Linux shared-slot boundary')
    def test_c_worker_rejects_invalid_descriptor_storage_and_seals(self):
        source = Path(__file__).parent / 'native/host-desktop/native_pixels_test.c'
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'pixels-test'
            subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra',
                '-Werror', str(source), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_invalid_capacity_allocates_nothing(self):
        with patch('host_desktop_pixels.os.memfd_create', create=True) as create:
            for capacity in (0, -1, (64 << 20) + 1):
                with self.assertRaises(DesktopError):
                    with native_pixels(capacity):
                        self.fail('invalid slot admitted')
            create.assert_not_called()

    @unittest.skipUnless(hasattr(os, 'memfd_create'), 'Linux shared-slot boundary')
    def test_slot_cannot_resize_and_success_transfers_only_mapping_ownership(self):
        with native_pixels(16) as (fd, pixels):
            pixels[:] = bytes(range(16))
            for size in (0, 32):
                with self.assertRaises(OSError):
                    os.ftruncate(fd, size)
            with self.assertRaises(OSError):
                fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE)
            self.assertEqual(os.pread(fd, 16, 0), bytes(range(16)))
        with self.assertRaises(OSError):
            os.fstat(fd)
        self.assertEqual(pixels[:], bytes(range(16)))
        pixels.close()

    @unittest.skipUnless(hasattr(os, 'memfd_create'), 'Linux shared-slot boundary')
    def test_failed_handoff_closes_mapping_and_descriptor(self):
        with self.assertRaisesRegex(OSError, 'launch failed'):
            with native_pixels(16) as (fd, pixels):
                raise OSError('launch failed')
        self.assertTrue(pixels.closed)
        with self.assertRaises(OSError):
            os.fstat(fd)


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
