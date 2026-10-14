import io
import json
import struct
import threading
import unittest
from unittest.mock import patch
from host_desktop_contract import DesktopError
from host_desktop_wire import read_command, packet, HEADER_LIMIT, Writer


class WireTests(unittest.TestCase):
    def test_blocked_media_write_cannot_block_dispatch_or_grow_its_queue(self):
        blocked, release, failures = threading.Event(), threading.Event(), []
        def write(_fd, value):
            blocked.set()
            if not release.wait(2):
                raise OSError('test writer did not release')
            return len(value)
        with patch('host_desktop_wire.os.write', write):
            writer = Writer(1, failures.append, 1, 4096)
            try:
                writer.send({'type': 'frame'}, b'first')
                self.assertTrue(blocked.wait(1))
                writer.send({'type': 'frame'}, b'second')
                writer.send({'type': 'frame'}, b'third')
                writer.send({'type': 'frame'}, b'fourth')
                self.assertEqual(failures, ['TRANSPORT_BACKPRESSURE'])
                self.assertEqual(writer.queue.qsize(), 1)
                self.assertLess(writer.pending, 4096)
            finally:
                release.set()
                writer.close(drain=True)
            self.assertFalse(writer.thread.is_alive())

    def test_partial_writes_and_shutdown_deliver_the_final_error_in_order(self):
        written = bytearray()
        def write(_fd, value):
            size = min(7, len(value))
            written.extend(value[:size])
            return size
        messages = [({'type': 'frame', 'frame_id': 1}, b'pixels'),
                    ({'type': 'error', 'code': 'DISPLAY_INACTIVE'}, b'')]
        with patch('host_desktop_wire.os.write', write):
            writer = Writer(1, self.fail, 16, 80 << 20)
            for message, payload in messages:
                writer.send(message, payload)
            writer.close(drain=True)
            writer.close()
        self.assertEqual(written, b''.join(packet(*message) for message in messages))
        self.assertFalse(writer.thread.is_alive())

    def test_pending_bytes_and_closed_pipe_fail_once_without_blocking_sender(self):
        failures = []
        blocked, release = threading.Event(), threading.Event()
        def write(_fd, _value):
            blocked.set()
            release.wait(2)
            raise BrokenPipeError()
        with patch('host_desktop_wire.os.write', write):
            writer = Writer(1, failures.append, 16, 300)
            writer.send({'type': 'frame'}, b'x' * 200)
            self.assertTrue(blocked.wait(1))
            writer.send({'type': 'frame'}, b'x' * 200)
            self.assertEqual(failures, ['TRANSPORT_BACKPRESSURE'])
            release.set()
            writer.close(drain=True)
        self.assertEqual(failures, ['TRANSPORT_BACKPRESSURE'])

    def test_framing_rejects_ambiguous_commands_before_native_dispatch(self):
        for text in ('{"version":1,"id":1,"method":"probe","method":"connect"}',
                     '{"version":true,"id":1}', '{"version":1,"id":true}',
                     '{"version":1,"id":1,"input":{"x":NaN}}'):
            raw = text.encode()
            with self.assertRaises(DesktopError):
                read_command(io.BytesIO(struct.pack('!I', len(raw)) + raw))
        with self.assertRaises(DesktopError):
            read_command(io.BytesIO(struct.pack('!I', HEADER_LIMIT + 1)))
        with self.assertRaises(DesktopError):
            read_command(io.BytesIO(b'\0\0\0\x20{}'))

    def test_binary_payload_and_next_control_message_remain_separate(self):
        first = packet({'type':'frame', 'frame_id':1}, b'\0\xff\n')
        length = struct.unpack('!I', first[:4])[0]
        metadata = json.loads(first[4:4+length])
        self.assertEqual(metadata['bytes'], 3)
        self.assertEqual(first[4+length:], b'\0\xff\n')


if __name__ == '__main__':
    unittest.main()
