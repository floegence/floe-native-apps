"""Real private helper transport around the unpublished compositor fixture.

Only fixture input encoding lives here. Production application admission and
decoded-frame permissions are separate work; neither is inferred from this
transport qualification. Administrative capture commands never cross this API.
"""
import json
from pathlib import Path
import secrets
import socket
import struct
from threading import Event, Thread

from gi.repository import GLib
from desktop_control import DesktopControl, GLibLoop, encode_message


class ControlProbe:
    def __init__(self, directory, native):
        self.native, self.generation = native, 0
        self.request_id, self.client = 0, None
        self.directory = Path(directory) / 'helper'
        self.directory.mkdir(mode=0o700)
        self.token = secrets.token_hex(32)
        self.loop = GLibLoop()
        self.server = DesktopControl(self.directory, self.directory.name, self.token, self, self.loop)
        self.mainloop = GLib.MainLoop()
        self.thread = Thread(target=self.mainloop.run, daemon=True)
        self.thread.start()
        self.reconnect()

    def attach(self, owner):
        self.generation += 1
        owner.generation = self.generation
        self.native.sendall(f'connection {self.generation}\n'.encode())
        owner.send({'event': 'attached', 'connection': self.generation})

    def detach(self, owner):
        self.native.sendall(f'detach {owner.generation}\n'.encode())

    def request(self, owner, message):
        if message['method'] != 'fixture-input' or type(message.get('window')) is not int:
            owner.close()
            return
        lines = message.get('commands', '').splitlines()
        if not 0 < len(lines) <= 128 or any(not (
                line.startswith(('key ', 'button ', 'motion ')) or line == 'close') for line in lines):
            owner.close()
            return
        packet = ''.join(f'input {owner.generation} {message["window"]} {line}\n' for line in lines)
        self.native.sendall(packet.encode())
        owner.send({'id': message['id'], 'result': 'submitted'})

    def reconnect(self):
        if self.client:
            self.client.close()
        self.client = socket.socket(socket.AF_UNIX)
        self.client.settimeout(5)
        self.client.connect(self.server.path)
        self.client.sendall(encode_message({'version': 1, 'instance': self.directory.name, 'token': self.token}))
        response = self.receive()
        assert response['event'] == 'attached'
        return response['connection']

    def receive(self):
        def read(size):
            value = bytearray()
            while len(value) < size:
                chunk = self.client.recv(size - len(value))
                if not chunk:
                    raise RuntimeError('Private fixture attachment ended')
                value.extend(chunk)
            return value
        kind, size = struct.unpack('!BI', read(5))
        assert kind == 1 and size <= 128 * 1024
        return json.loads(read(size))

    def send(self, window, commands):
        self.request_id += 1
        self.client.sendall(encode_message({'id': self.request_id, 'method': 'fixture-input',
                                          'window': window, 'commands': commands.decode()}))

    def block_frame(self, pixels):
        ready, result = Event(), []
        def offer():
            owner = self.server.current
            owner.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            result.append(owner.send_frame({'sequence': 1, 'encoding': 'bgrx'}, pixels))
            result.append(owner.send_frame({'sequence': 2, 'encoding': 'bgrx'}, pixels))
            ready.set()
            return False
        GLib.idle_add(offer)
        assert ready.wait(5) and result == [True, False]

    def close(self):
        if self.client:
            self.client.close()
        stopped = Event()
        def stop():
            self.server.close()
            self.mainloop.quit()
            stopped.set()
            return False
        GLib.idle_add(stop)
        assert stopped.wait(5)
        self.thread.join(timeout=5)
