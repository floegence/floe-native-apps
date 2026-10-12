import mmap
import struct
import unittest
from unittest.mock import Mock, patch

from host_desktop_contract import DesktopError
from host_desktop_encoder import NativeEncoder, available


class EncoderBoundaryTests(unittest.TestCase):
    def test_invalid_sizes_never_start_a_worker(self):
        with patch('host_desktop_encoder.subprocess.Popen') as start:
            for dimensions in ((1, 10), (128, 127), (8192, 8192)):
                with self.assertRaises(DesktopError):
                    NativeEncoder(*dimensions, 60, 16000000)
            start.assert_not_called()

    def test_failed_probe_is_not_hardware_evidence(self):
        with patch('host_desktop_encoder.NativeEncoder') as factory:
            encoder = factory.return_value
            encoder.size = 4
            encoder.encode.side_effect = DesktopError('VIDEO_ENCODER_FAILED')
            self.assertFalse(available())
            encoder.close.assert_called_once()

    def test_reply_identity_and_length_fail_closed_and_reap_only_our_worker(self):
        for size, sequence in ((0, 1), ((64 << 20) + 1, 1), (4, 2)):
            encoder = NativeEncoder.__new__(NativeEncoder)
            encoder.process = Mock()
            child = encoder.process
            child.poll.return_value = None
            encoder.pixels = mmap.mmap(-1, 4)
            pixels = encoder.pixels
            encoder.size, encoder.sequence, encoder.timeout = 4, 0, 3
            encoder._write = Mock()
            encoder._read = Mock(return_value=struct.pack('!II', size, sequence))
            with self.assertRaises(DesktopError):
                encoder.encode(bytes(4))
            child.kill.assert_called_once()
            child.wait.assert_called_once()
            self.assertTrue(pixels.closed)
            encoder.close()
            child.kill.assert_called_once()

    def test_valid_reply_is_one_complete_access_unit(self):
        encoder = NativeEncoder.__new__(NativeEncoder)
        encoder.size, encoder.sequence, encoder.timeout = 4, 0, 3
        encoder._write = Mock()
        encoder.pixels = bytearray(4)
        encoder._read = Mock(side_effect=[struct.pack('!II', 5, 1), b'\0\0\0\1e'])
        self.assertEqual(encoder.encode(bytes(4)), b'\0\0\0\1e')
        self.assertEqual(encoder.sequence, 1)
        self.assertEqual(encoder.pixels, bytes(4))
        encoder._write.assert_called_once_with(struct.pack('!II', 4, 1), unittest.mock.ANY)

    def test_invalid_frame_or_exhausted_sequence_reaps_before_slot_reuse(self):
        for length, sequence in ((3, 0), (4, 0xffffffff)):
            encoder = NativeEncoder.__new__(NativeEncoder)
            encoder.size, encoder.sequence, encoder.timeout = 4, sequence, 3
            encoder.process = Mock()
            child = encoder.process
            child.poll.return_value = None
            encoder.pixels = mmap.mmap(-1, 4)
            pixels = encoder.pixels
            encoder._write = Mock()
            with self.assertRaises(DesktopError):
                encoder.encode(bytes(length))
            encoder._write.assert_not_called()
            child.kill.assert_called_once()
            self.assertTrue(pixels.closed)

    def test_completion_eof_or_timeout_cannot_return_pixels_to_next_frame(self):
        for failure in (OSError('pipe closed'), DesktopError('VIDEO_ENCODER_FAILED')):
            encoder = NativeEncoder.__new__(NativeEncoder)
            encoder.size, encoder.sequence, encoder.timeout = 4, 0, 3
            encoder.process = Mock()
            child = encoder.process
            child.poll.return_value = None
            encoder.pixels = mmap.mmap(-1, 4)
            encoder._write = Mock()
            encoder._read = Mock(side_effect=failure)
            with self.assertRaises(DesktopError):
                encoder.encode(bytes(4))
            child.kill.assert_called_once()
            with self.assertRaises(DesktopError):
                encoder.encode(bytes(4))
            encoder._write.assert_called_once()


if __name__ == '__main__':
    unittest.main()
