import threading
import unittest
from unittest.mock import Mock, patch

from host_desktop_contract import DesktopError
from host_desktop_xcapture import Surface, X11Capture


class AcquisitionTests(unittest.TestCase):
    def test_invalid_target_is_rejected_before_any_display_connection(self):
        with patch('host_desktop_xcapture.Connection') as connection:
            for display, rectangle in [('host:0', (0, 0, 16, 16)), (':0', (-1, 0, 16, 16)),
                                       (':0', (0, 0, 8192, 8192)), (':0', (0, 0, 0, 16))]:
                with self.assertRaises(DesktopError):
                    Surface(display, rectangle, threading.Event())
            connection.assert_not_called()

    def test_partial_initialization_releases_connection_mapping_and_cursor(self):
        connection, mapping, painter = Mock(), Mock(), Mock()
        def interrupted(surface):
            surface.mapping, surface.painter = mapping, painter
            raise DesktopError('CAPTURE_UNAVAILABLE')
        with patch('host_desktop_xcapture.Connection', return_value=connection), patch.object(Surface, '_initialize', interrupted):
            with self.assertRaisesRegex(DesktopError, 'CAPTURE_UNAVAILABLE'):
                Surface(':0', (0, 0, 16, 16), threading.Event())
        connection.close.assert_called_once()
        mapping.close.assert_called_once()
        painter.close.assert_called_once()

    def test_capture_fault_reports_once_and_releases_surface(self):
        surface = Mock()
        surface.read.side_effect = DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
        captured, failed = Mock(), Mock()
        with patch('host_desktop_xcapture.Surface', return_value=surface):
            capture = X11Capture(None, ':0', (0, 0, 16, 16), 60, captured, failed, lambda *_: False)
            capture.thread.join(timeout=1)
            capture.close()
        self.assertFalse(capture.thread.is_alive())
        surface.close.assert_called_once()
        captured.assert_not_called()
        failed.assert_called_once_with('CAPTURE_LAYOUT_UNSUPPORTED')

    def test_stop_during_read_never_publishes_an_obsolete_sample(self):
        entered = threading.Event()
        def surface_factory(_display, _rectangle, stopped):
            surface = Mock(width=16, height=16)
            def read():
                entered.set()
                if not stopped.wait(1):
                    raise AssertionError('capture cancellation did not reach the reader')
                return memoryview(bytes(16 * 16 * 4)), (1, 0, 0, 1, 1, True)
            surface.read.side_effect = read
            return surface
        captured, failed = Mock(), Mock()
        with patch('host_desktop_xcapture.Surface', side_effect=surface_factory):
            capture = X11Capture(None, ':0', (0, 0, 16, 16), 60, captured, failed, lambda *_: False)
            self.assertTrue(entered.wait(1))
            capture.close()
        self.assertFalse(capture.thread.is_alive())
        captured.assert_not_called()
        failed.assert_not_called()


if __name__ == '__main__':
    unittest.main()

class LocalCursorTests(unittest.TestCase):
    def test_png_unpremultiplies_xfixes_pixels(self):
        import struct
        import zlib
        from host_desktop_xcapture import cursor_png
        png = cursor_png(bytes([16, 32, 64, 128]), 1, 1)
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        offset = 8
        while offset < len(png):
            size = struct.unpack('!I', png[offset:offset + 4])[0]
            if png[offset + 4:offset + 8] == b'IDAT':
                self.assertEqual(zlib.decompress(png[offset + 8:offset + 8 + size]), bytes([0, 127, 63, 31, 128]))
                return
            offset += size + 12
        self.fail('PNG has no pixels')

    def test_cursor_only_motion_does_not_encode_desktop_in_control_mode(self):
        from types import SimpleNamespace
        captured, cursors, failures = [], [], []
        stopped = threading.Event()
        surface = Mock(width=2, height=2)
        surface.cursor = (1, 0, 0, 1, 1)
        surface.painter.pixels = bytes([0, 0, 0, 255])
        reads = 0
        def read():
            nonlocal reads
            reads += 1
            if reads == 4:
                stopped.set()
                raise DesktopError('CAPTURE_UNAVAILABLE')
            return memoryview(bytes(16)), (1, reads, 0, 1, 1, True)
        surface.read.side_effect = read
        gst = SimpleNamespace(Buffer=SimpleNamespace(new_wrapped=lambda data: data))
        with patch('host_desktop_xcapture.Surface', return_value=surface):
            capture = X11Capture(gst, ':0', (0, 0, 2, 2), 60,
                lambda *args: captured.append(args), failures.append, lambda a, b: a == b,
                lambda *args: cursors.append(args))
            self.assertTrue(stopped.wait(1))
            capture.close()
        self.assertEqual(len(captured), 1)
        self.assertEqual(len(cursors), 1)
        surface.painter.compose.assert_not_called()
