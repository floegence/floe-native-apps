"""The assembled helper survives viewer detach and contains partial startup."""
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from unittest.mock import patch

from desktop_control import encode_message
from desktop_control_test import Loop
from desktop_helper import DesktopHelper


class DesktopHelperTests(unittest.TestCase):
    def setUp(self):
        if not hasattr(socket, 'SO_PEERCRED'):
            credentials = patch('desktop_control.peer_uid', return_value=os.getuid())
            credentials.start()
            self.addCleanup(credentials.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.loop = Loop()
        self.addCleanup(self.loop.selector.close)
        native, self.compositor = socket.socketpair()
        self.capture, self.producer = socket.socketpair()
        for channel in (self.compositor, self.capture, self.producer):
            self.addCleanup(channel.close)
        self.helper = DesktopHelper(native, self.loop)
        self.addCleanup(self.helper.close)
        self.compositor.sendall(b'native-version 1\n')
        self.loop.step()

    def listen(self):
        self.helper.listen(self.temporary.name, 'owned-instance', 'b' * 64, self.capture)

    def connect(self):
        client = socket.socket(socket.AF_UNIX)
        self.addCleanup(client.close)
        client.settimeout(1)
        client.connect(self.helper.server.path)
        client.sendall(encode_message({'version': 1, 'instance': 'owned-instance', 'token': 'b' * 64}))
        for _ in range(3):
            self.loop.step()
        return client, self.receive(client)

    def receive(self, client):
        def read(length):
            data = bytearray()
            while len(data) < length:
                value = client.recv(length - len(data))
                self.assertTrue(value, 'Helper closed the authenticated stream')
                data.extend(value)
            return data
        kind, size = struct.unpack('!BI', read(5))
        self.assertEqual(kind, 1)
        return json.loads(read(size))

    def test_viewer_replacement_keeps_one_native_owner_and_fresh_connection(self):
        self.listen()
        first, attached = self.connect()
        self.assertEqual(attached['connection'], 1)
        original = self.helper.native
        first.close()
        self.loop.step()
        self.assertFalse(self.helper.channel.closed)
        second, attached = self.connect()
        self.assertEqual(attached['connection'], 2)
        self.assertIs(self.helper.native, original)
        self.assertFalse(self.helper.native.frames.closed)
        second.close()
        self.loop.step()
        self.assertTrue(Path(self.helper.server.path).is_socket())

    def test_compositor_loss_reports_unavailable_without_inventing_application_exit(self):
        self.listen()
        client, _ = self.connect()
        self.compositor.close()
        for _ in range(2):
            self.loop.step()
        state = self.receive(client)
        self.assertEqual(state['event'], 'state')
        self.assertEqual(state['state']['state'], 'unavailable')
        client.sendall(encode_message({'id': 1, 'method': 'status'}))
        for _ in range(2):
            self.loop.step()
        response = self.receive(client)
        self.assertEqual(response['id'], 1)
        self.assertEqual(response['result']['state'], 'unavailable')

    def test_failed_listener_closes_capture_without_removing_existing_endpoint(self):
        path = Path(self.temporary.name) / 'control.sock'
        path.write_text('another prepared owner')
        with self.assertRaises(FileExistsError):
            self.listen()
        self.assertEqual(path.read_text(), 'another prepared owner')
        self.assertTrue(self.helper.native.frames.closed)
        self.assertIsNone(self.helper.native.attachment)
        self.assertFalse(self.helper.channel.closed)
        self.assertEqual(len(self.loop.selector.get_map()), 1)

    def test_helper_disposal_releases_its_native_input_context(self):
        from desktop_context import NativeContexts
        contexts = NativeContexts(self.helper.native, object(), '/private/instance')
        # Reproduce the previous native fixture's direct service wiring.
        self.helper.native.contexts = contexts
        self.helper.close()
        self.assertTrue(contexts.closed)

    def test_sharing_shutdown_never_closes_the_native_channel(self):
        self.listen()
        self.connect()
        self.helper.stop_sharing()
        self.helper.stop_sharing()
        self.assertFalse((Path(self.temporary.name) / 'control.sock').exists())
        self.assertFalse(self.helper.channel.closed)
        self.assertTrue(self.helper.native.frames.closed)
        self.helper.close()
        self.assertEqual(len(self.loop.selector.get_map()), 0)
        self.assertEqual(self.loop.timers, {})


if __name__ == '__main__':
    unittest.main()
