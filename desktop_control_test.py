"""Private helper connection ownership, bounded frames and authenticated reuse."""
import json
import os
from pathlib import Path
import selectors
import socket
import struct
import tempfile
import unittest
from unittest.mock import patch

from desktop_control import DesktopControl, MAX_MESSAGE, MAX_OUTPUT, encode_message


class Loop:
    def __init__(self):
        self.selector = selectors.DefaultSelector()
        self.readers, self.writers, self.timers = {}, {}, {}
        self.sequence = 0

    def watch(self, descriptor, read=None, write=None):
        try:
            self.selector.unregister(descriptor)
        except KeyError:
            pass
        if read or write:
            self.selector.register(descriptor, (selectors.EVENT_READ if read else 0) |
                                   (selectors.EVENT_WRITE if write else 0), (read, write))

    def later(self, _milliseconds, callback):
        self.sequence += 1
        self.timers[self.sequence] = callback
        return self.sequence

    def cancel(self, timer):
        self.timers.pop(timer, None)

    def step(self):
        for key, mask in self.selector.select(0.05):
            read, write = key.data
            if mask & selectors.EVENT_READ:
                read()
            if mask & selectors.EVENT_WRITE:
                write()


class Application:
    def __init__(self):
        self.attached, self.detached, self.commands = [], [], []

    def attach(self, owner):
        self.attached.append(owner)
        owner.send({'event': 'attached', 'connection': len(self.attached)})

    def detach(self, owner):
        self.detached.append(owner)

    def request(self, owner, request):
        self.commands.append((owner, request))
        owner.send({'id': request['id'], 'result': 'received'})

    def writable(self, owner):
        pass


class ControlTests(unittest.TestCase):
    def setUp(self):
        if not hasattr(socket, 'SO_PEERCRED'):
            # Linux native qualification exercises the actual kernel boundary.
            peer = patch('desktop_control.peer_uid', return_value=os.getuid())
            peer.start()
            self.addCleanup(peer.stop)
        self.directory = tempfile.TemporaryDirectory(prefix='floe-control-')
        self.loop, self.application = Loop(), Application()
        self.server = DesktopControl(self.directory.name, 'instance-one', 'a' * 64,
                                     self.application, self.loop)
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.server.close()
        self.loop.selector.close()
        self.directory.cleanup()

    def connect(self, token='a' * 64, instance='instance-one', authenticate=True):
        client = socket.socket(socket.AF_UNIX)
        client.settimeout(1)
        client.connect(self.server.path)
        self.clients.append(client)
        self.loop.step()
        if authenticate:
            client.sendall(encode_message({'version': 1, 'instance': instance, 'token': token}))
            self.loop.step()
            self.loop.step()
        return client

    def receive(self, client):
        data = bytearray()
        while len(data) < 5:
            data.extend(client.recv(5 - len(data)))
        kind, size = struct.unpack('!BI', data)
        data = bytearray()
        while len(data) < size:
            data.extend(client.recv(size - len(data)))
        return kind, bytes(data)

    def test_valid_private_connection_can_return_after_detach(self):
        first = self.connect()
        self.assertEqual(json.loads(self.receive(first)[1])['connection'], 1)
        first.close()
        self.loop.step()
        self.assertEqual(self.application.detached, self.application.attached)
        second = self.connect()
        self.assertEqual(json.loads(self.receive(second)[1])['connection'], 2)
        self.assertTrue(Path(self.server.path).exists())

    def test_bad_credentials_never_replace_current_owner(self):
        active = self.connect()
        self.receive(active)
        for token, instance in [('b' * 64, 'instance-one'), ('a' * 64, 'other'), ('🙂' * 64, 'instance-one')]:
            rejected = self.connect(token, instance)
            self.assertEqual(rejected.recv(1), b'')
        self.assertEqual(len(self.application.attached), 1)
        self.assertEqual(self.application.detached, [])

    def test_replacement_revokes_old_owner_before_admission(self):
        first = self.connect()
        self.receive(first)
        owner = self.application.attached[0]
        second = self.connect()
        self.receive(second)
        self.assertEqual(self.application.detached, [owner])
        self.assertEqual(first.recv(1), b'')
        self.assertFalse(owner.send({'event': 'late'}))
        owner.close()
        self.assertEqual(self.application.detached, [owner])
        self.assertIs(self.server.current, self.application.attached[1])

    def test_partial_records_and_multiple_requests_preserve_order(self):
        client = self.connect()
        self.receive(client)
        first = encode_message({'id': 1, 'method': 'status'})
        second = encode_message({'id': 2, 'method': 'status'})
        client.sendall(first[:3])
        self.loop.step()
        self.assertEqual(self.application.commands, [])
        client.sendall(first[3:] + second)
        self.loop.step()
        self.loop.step()
        self.assertEqual([item[1]['id'] for item in self.application.commands], [1, 2])
        self.assertEqual(json.loads(self.receive(client)[1])['id'], 1)
        self.assertEqual(json.loads(self.receive(client)[1])['id'], 2)

    def test_valid_text_limit_survives_json_escape_expansion(self):
        client = self.connect()
        self.receive(client)
        text = '\x01' * 16000
        packet = encode_message({'id': 1, 'method': 'text', 'text': text})
        for offset in range(0, len(packet), 1024):
            client.sendall(packet[offset:offset + 1024])
            self.loop.step()
        self.assertEqual(self.application.commands[0][1]['text'], text)

    def test_oversized_and_ambiguous_input_closes_only_its_attachment(self):
        for payload in [struct.pack('!BI', 1, MAX_MESSAGE + 1),
                        struct.pack('!BI', 2, 10),
                        struct.pack('!BI', 1, 17) + b'{"id":1,"id":2}  ']:
            client = self.connect()
            self.receive(client)
            client.sendall(payload)
            self.loop.step()
            self.assertEqual(client.recv(1), b'')
        self.assertEqual(self.application.commands, [])
        self.assertTrue(Path(self.server.path).exists())

    def test_blocked_frame_cannot_stop_input_or_accumulate_more_frames(self):
        client = self.connect()
        self.receive(client)
        owner = self.server.current
        owner.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        pixels = bytes(2 * 1024 * 1024)
        self.assertTrue(owner.send_frame({'sequence': 1}, pixels))
        self.assertFalse(owner.send_frame({'sequence': 2}, pixels))
        self.loop.step()
        client.sendall(encode_message({'id': 1, 'method': 'status'}))
        self.loop.step()
        self.assertEqual(len(self.application.commands), 1)
        self.assertLessEqual(owner.buffered, len(pixels) + MAX_OUTPUT)
        client.close()
        self.loop.step()
        self.assertEqual(owner.buffered, 0)
        self.assertEqual(self.application.detached, [owner])

    def test_slow_control_consumer_is_revoked_at_bound(self):
        client = self.connect()
        self.receive(client)
        owner = self.server.current
        for _ in range(200):
            owner.send({'event': 'state', 'test': 'x' * 16000})
        self.assertEqual(self.application.detached, [owner])
        self.assertEqual(owner.buffered, 0)

    def test_late_handshake_timeout_does_not_revoke_authenticated_owner(self):
        client = self.connect(authenticate=False)
        expired = next(iter(self.loop.timers.values()))
        client.sendall(encode_message({'version': 1, 'instance': 'instance-one', 'token': 'a' * 64}))
        self.loop.step()
        expired()
        self.assertEqual(len(self.application.attached), 1)
        self.assertEqual(self.application.detached, [])

    def test_wrong_os_user_is_rejected_before_reading_authentication(self):
        with patch('desktop_control.peer_uid', return_value=os.getuid() + 1):
            client = self.connect(authenticate=False)
        self.assertEqual(client.recv(1), b'')
        self.assertEqual(self.application.attached, [])

    def test_unknown_commands_are_not_interpreted_by_transport(self):
        client = self.connect()
        self.receive(client)
        client.sendall(encode_message({'id': 1, 'method': 'backend-specific', 'params': {}}))
        self.loop.step()
        self.assertEqual(self.application.commands[0][1]['method'], 'backend-specific')

    def test_existing_control_path_and_open_directory_are_rejected(self):
        with self.assertRaises(FileExistsError):
            DesktopControl(self.directory.name, 'instance-one', 'a' * 64, self.application, self.loop)
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o755)
            with self.assertRaises(ValueError):
                DesktopControl(directory, 'instance-one', 'a' * 64, self.application, self.loop)

    def test_shutdown_does_not_remove_a_replaced_socket_path(self):
        Path(self.server.path).unlink()
        Path(self.server.path).write_text('replacement')
        self.server.close()
        self.assertEqual(Path(self.server.path).read_text(), 'replacement')


if __name__ == '__main__':
    unittest.main()
