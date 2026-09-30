import io
import json
import struct
import unittest
from host_desktop_contract import DesktopError
from host_desktop_wire import read_command, packet, HEADER_LIMIT


class WireTests(unittest.TestCase):
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
