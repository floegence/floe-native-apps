"""Real helper attachment/frame admission around the unpublished compositor.

The production transport, frame gate and input scheduler run unchanged. This
fixture supplies native scene records and actual captured pixels; it does not
claim production launch, automatic capture scheduling or package admission.
"""
import json
from pathlib import Path
import secrets
import socket
import struct
from threading import Event, Thread
from types import SimpleNamespace

from gi.repository import GLib
from desktop_attachment import DesktopAttachment
from desktop_control import DesktopControl, GLibLoop, encode_message


class NativeFixture:
    def __init__(self, channel):
        self.channel, self.target, self.generation = channel, None, 0
        self.pending, self.attachment = None, None

    def observe(self, line):
        if not line.startswith('scene '):
            return
        _, generation, window = line.split()
        generation, window = int(generation), int(window)
        if generation <= self.generation:
            return
        self.generation = generation
        self.target = SimpleNamespace(window=window, generation=generation) if window else None
        if self.attachment:
            self.attachment.scene_changed()

    def bind(self, epoch):
        self.epoch = epoch
        self.channel.sendall(f'connection {epoch}\n'.encode())

    def release(self, epoch):
        # Input revocation must not detach a still-authenticated connection.
        self.channel.sendall(f'release {epoch}\n'.encode())
        self.cancel_capture()

    def unbind(self, epoch):
        self.channel.sendall(f'detach {epoch}\n'.encode())
        self.cancel_capture()

    def cancel_capture(self):
        if self.pending:
            _target, completed = self.pending
            self.pending = None
            completed(None, None, 'CAPTURE_CANCELLED')

    def snapshot(self):
        return {'state': 'running' if self.target else 'waiting',
                'window': self.target.window if self.target else None, 'generation': self.generation}

    def capture(self, target, completed):
        assert self.pending is None
        self.pending = (target, completed)

    def pixels(self, data):
        if not self.pending:
            return False
        target, completed = self.pending
        self.pending = None
        if self.target is not target:
            completed(None, None, 'CAPTURE_CANCELLED')
        else:
            completed({'encoding': 'bgrx', 'width': 1000, 'height': 700}, data, None)
        return True

    def validate_input(self, operation):
        if not isinstance(operation, dict) or operation.get('kind') != 'fixture':
            raise ValueError('Unknown fixture input')
        lines = operation.get('commands', '').splitlines()
        if not 0 < len(lines) <= 128 or any(not line.startswith(('key ', 'button ', 'motion ')) for line in lines):
            raise ValueError('Unknown fixture native command')
        return dict(operation)

    def deliver(self, epoch, target, operation):
        packet = ''.join(f'input {epoch} {target.window} {target.generation} {line}\n'
                         for line in operation['commands'].splitlines())
        self.channel.sendall(packet.encode())

    def close_window(self, window):
        if not self.target or self.target.window != window:
            raise ValueError('Fixture target is no longer selected')
        self.channel.sendall(f'input {self.epoch} {window} {self.target.generation} close\n'.encode())


class ControlProbe:
    def __init__(self, directory, channel, events):
        self.request_id, self.client = 0, None
        self.directory = Path(directory) / 'helper'
        self.directory.mkdir(mode=0o700)
        self.token = secrets.token_hex(32)
        self.loop = GLibLoop()
        self.native = NativeFixture(channel)
        for line in tuple(events):
            self.native.observe(line)
        self.attachment = DesktopAttachment(self.native, GLib.timeout_add, GLib.source_remove)
        self.native.attachment = self.attachment
        self.server = DesktopControl(self.directory, self.directory.name, self.token, self.attachment, self.loop)
        self.mainloop = GLib.MainLoop()
        self.thread = Thread(target=self.mainloop.run, daemon=True)
        self.thread.start()
        self.reconnect()

    def observe(self, line):
        def apply():
            self.native.observe(line)
            return False
        GLib.idle_add(apply)

    def reconnect(self):
        if self.client:
            self.client.close()
        self.client = socket.socket(socket.AF_UNIX)
        self.client.settimeout(5)
        self.client.connect(self.server.path)
        self.client.sendall(encode_message({'version': 1, 'instance': self.directory.name, 'token': self.token}))
        kind, body = self.receive()
        response = json.loads(body)
        assert kind == 1 and response['event'] == 'attached'
        self.generation = response['connection']
        return self.generation

    def receive(self):
        def read(size):
            value = bytearray()
            while len(value) < size:
                chunk = self.client.recv(size - len(value))
                if not chunk:
                    raise RuntimeError('Private fixture attachment ended')
                value.extend(chunk)
            return bytes(value)
        kind, size = struct.unpack('!BI', read(5))
        assert kind in (1, 2) and size <= 64 * 1024 * 1024
        return kind, read(size)

    def response(self, request):
        while True:
            kind, body = self.receive()
            assert kind == 1
            message = json.loads(body)
            if message.get('id') == request:
                return message

    def request(self, method, **values):
        self.request_id += 1
        self.client.sendall(encode_message({'id': self.request_id, 'method': method, **values}))
        return self.request_id

    def send(self, window, commands):
        if commands == b'close\n':
            return self.request('close_window', window=window)
        return self.request('input', connection=self.generation, window=window,
            generation=self.native.generation, operation={'kind': 'fixture', 'commands': commands.decode()})

    def offer(self, pixels):
        ready, result = Event(), []
        def offer():
            self.attachment.damage()
            result.append(self.native.pixels(pixels))
            ready.set()
            return False
        GLib.idle_add(offer)
        assert ready.wait(5) and result == [True]

    def paint(self, pixels):
        self.offer(pixels)
        while True:
            kind, body = self.receive()
            assert kind == 1
            message = json.loads(body)
            if message.get('event') == 'frame':
                break
        kind, data = self.receive()
        assert kind == 2 and data == pixels
        from PIL import Image
        frame = message['frame']
        image = Image.frombytes('RGB', (frame['width'], frame['height']), data, 'raw', 'BGRX')
        assert len(image.getcolors(frame['width'] * frame['height'])) > 16
        response = self.response(self.request('frame_ack', frame=frame['sequence']))
        assert response.get('result') == 'painted', response

    def block_frame(self, pixels):
        ready = Event()
        def block():
            self.server.current.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            self.attachment.damage()
            assert self.native.pixels(pixels)
            self.attachment.damage()
            assert self.native.pending is None and self.attachment.awaiting is not None
            ready.set()
            return False
        GLib.idle_add(block)
        assert ready.wait(5)

    def close(self):
        if self.client:
            self.client.close()
        stopped = Event()
        def stop():
            self.server.close()
            self.attachment.close()
            self.mainloop.quit()
            stopped.set()
            return False
        GLib.idle_add(stop)
        assert stopped.wait(5)
        self.thread.join(timeout=5)
