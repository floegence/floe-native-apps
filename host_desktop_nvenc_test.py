import io
import struct
import unittest
from unittest.mock import Mock, patch

from host_desktop_contract import DesktopError
from host_desktop_nvenc import NVEncoder, available


class EncoderBoundaryTests(unittest.TestCase):
    def test_invalid_sizes_never_start_a_worker(self):
        with patch('host_desktop_nvenc.subprocess.Popen') as start:
            for dimensions in ((1, 10), (128, 127), (8192, 8192)):
                with self.assertRaises(DesktopError):
                    NVEncoder(*dimensions, 60, 16000000)
            start.assert_not_called()

    def test_failed_probe_is_not_hardware_evidence(self):
        with patch('host_desktop_nvenc.NVEncoder') as factory:
            encoder = factory.return_value
            encoder.encode.side_effect = DesktopError('VIDEO_ENCODER_FAILED')
            self.assertFalse(available())
            encoder.close.assert_called_once()

    def test_reply_identity_and_length_fail_closed_and_reap_only_our_worker(self):
        for size, sequence in ((0, 1), ((64 << 20) + 1, 1), (4, 2)):
            encoder = NVEncoder.__new__(NVEncoder)
            encoder.process = Mock()
            child = encoder.process
            child.poll.return_value = None
            encoder.size, encoder.sequence, encoder.timeout = 4, 0, 3
            encoder._write = Mock()
            encoder._read = Mock(return_value=struct.pack('!II', size, sequence))
            with self.assertRaises(DesktopError):
                encoder.encode(bytes(4))
            child.kill.assert_called_once()
            child.wait.assert_called_once()
            encoder.close()
            child.kill.assert_called_once()

    def test_valid_reply_is_one_complete_access_unit(self):
        encoder = NVEncoder.__new__(NVEncoder)
        encoder.size, encoder.sequence, encoder.timeout = 4, 0, 3
        encoder._write = Mock()
        encoder._read = Mock(side_effect=[struct.pack('!II', 5, 1), b'\0\0\0\1e'])
        self.assertEqual(encoder.encode(bytes(4)), b'\0\0\0\1e')
        self.assertEqual(encoder.sequence, 1)


if __name__ == '__main__':
    unittest.main()
