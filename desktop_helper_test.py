"""The assembled helper survives viewer detach and contains partial startup."""
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

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

    def test_multiple_viewers_share_process_and_latest_viewer_controls_input(self):
        self.listen()
        first, _ = self.connect()
        first_peer = next(iter(self.helper.server.peers))
        second, _ = self.connect()
        second_peer = self.helper.server.current
        self.assertIsNot(first_peer, second_peer)
        self.assertEqual(len(self.helper.application.attachments), 2)
        self.assertIs(self.helper.application.controller, second_peer)
        self.assertFalse(first_peer.closed)
        second.close()
        self.loop.step()
        self.assertEqual(len(self.helper.application.attachments), 1)
        self.assertIs(self.helper.application.controller, first_peer)
        self.assertFalse(first_peer.closed)

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

    def configure_input(self, x11=None):
        bus, tree = Mock(), Mock()
        class Service:
            def __init__(self, connection, contexts, _destination):
                self.connection, self.contexts, self.closed = connection, contexts, False
            def close(self):
                self.closed = True
                self.contexts.close()
        with patch('desktop_helper.NativeContextService', Service):
            contexts = self.helper.configure_input(bus, tree, '/private/instance', x11=x11)
        return contexts, self.helper.context_service, bus, tree

    def test_viewer_detach_preserves_context_services_and_helper_disposal_releases_only_owned_resources(self):
        resources = Mock()
        contexts, service, bus, tree = self.configure_input(resources)
        self.listen()
        client, _ = self.connect()
        client.close()
        self.loop.step()
        self.helper.stop_sharing()
        self.assertFalse(contexts.closed)
        self.assertFalse(service.closed)
        resources.close.assert_not_called()
        self.helper.close()
        self.assertTrue(contexts.closed)
        self.assertTrue(service.closed)
        resources.close.assert_called_once_with()
        bus.close.assert_not_called()
        tree.close.assert_not_called()
        self.assertIsNone(self.helper.native.contexts)

    def test_failed_context_registration_closes_transferred_x11_without_publishing_an_owner(self):
        resources, bus, tree = Mock(), Mock(), Mock()
        observed = []
        def failed(_connection, contexts, _destination):
            observed.append(contexts)
            raise ValueError('Context registration failed')
        with patch('desktop_helper.NativeContextService', side_effect=failed):
            with self.assertRaisesRegex(ValueError, 'registration failed'):
                self.helper.configure_input(bus, tree, '/private/instance', x11=resources)
        self.assertTrue(observed[0].closed)
        resources.close.assert_called_once_with()
        self.assertIsNone(self.helper.native.contexts)
        self.assertIsNone(self.helper.context_service)
        bus.close.assert_not_called()
        tree.close.assert_not_called()

    def test_context_owner_cannot_be_replaced_or_recreated_in_the_same_native_lifetime(self):
        contexts, service, bus, tree = self.configure_input()
        with self.assertRaisesRegex(ValueError, 'assembly is unavailable'):
            self.helper.configure_input(bus, tree, '/another/runtime')
        self.assertIs(self.helper.native.contexts, contexts)
        self.assertFalse(service.closed)
        self.helper.close_input()
        self.helper.close_input()
        with self.assertRaisesRegex(ValueError, 'assembly is unavailable'):
            self.helper.configure_input(bus, tree, '/another/runtime')

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
