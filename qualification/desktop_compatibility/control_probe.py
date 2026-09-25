"""Real helper attachment/frame admission around the unpublished compositor.

The production transport, bounded native capture, frame gate and input scheduler
run unchanged. This fixture adapts unpublished native scene/damage records; it
does not claim production launch or complete package admission.
"""
import json
from collections import deque
from pathlib import Path
import secrets
import socket
import struct
from threading import Event, Thread
from types import SimpleNamespace

from gi.repository import GLib
from desktop_attachment import DesktopAttachment
from desktop_capture import NativeFrames
from desktop_control import DesktopControl, GLibLoop, encode_message


class NativeFixture:
    def __init__(self, channel, frames, loop):
        self.channel, self.target, self.generation = channel, None, 0
        self.attachment, self.capture_observer = None, None
        self.query_id, self.queries = 0, {}
        self.frames = NativeFrames(frames, loop, self.query_scene)

    def query_scene(self, completed):
        assert not self.queries
        self.query_id += 1
        self.queries[self.query_id] = completed
        self.channel.sendall(f'scene-query {self.query_id}\n'.encode())

    def observe(self, line):
        if line.startswith('scene-at '):
            _, query, generation, window = line.split()
            completed = self.queries.pop(int(query), None)
            if completed:
                completed(int(window), int(generation))
            return
        if line.startswith('damage '):
            if self.attachment:
                self.attachment.damage()
            return
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
        self.frames.cancel()

    def snapshot(self):
        return {'state': 'running' if self.target else 'waiting',
                'window': self.target.window if self.target else None, 'generation': self.generation}

    def capture(self, target, completed):
        def captured(description, data, error):
            completed(description, data, error)
            if self.capture_observer:
                self.capture_observer(error)
        self.frames.capture(target, captured)

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

    def select(self, window):
        self.channel.sendall(f'select {self.epoch} {window}\n'.encode())


class ControlProbe:
    def __init__(self, directory, channel, events, frames):
        self.request_id, self.client = 0, None
        self.frames, self.responses, self.events = deque(), {}, []
        self.directory = Path(directory) / 'helper'
        self.directory.mkdir(mode=0o700)
        self.token = secrets.token_hex(32)
        self.loop = GLibLoop()
        self.native = NativeFixture(channel, frames, self.loop)
        for line in tuple(events):
            self.native.observe(line)
        self.attachment = DesktopAttachment(self.native, GLib.timeout_add, GLib.source_remove)
        self.native.attachment = self.attachment
        self.server = DesktopControl(self.directory, self.directory.name, self.token, self.attachment, self.loop)
        self.mainloop = GLib.MainLoop()
        self.thread = Thread(target=self.mainloop.run, daemon=True)
        self.thread.start()

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
        self.frames.clear()
        self.responses.clear()
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

    def record(self):
        kind, body = self.receive()
        assert kind == 1
        message = json.loads(body)
        if message.get('event') == 'frame':
            kind, data = self.receive()
            assert kind == 2 and len(data) == message['bytes']
            self.frames.append((message['frame'], data))
            assert len(self.frames) <= 4, 'Fixture consumer accumulated unbounded native frames'
        elif 'id' in message:
            self.responses[message['id']] = message
        else:
            self.events.append(message)

    def response(self, request):
        while request not in self.responses:
            self.record()
        return self.responses.pop(request)

    def request(self, method, **values):
        self.request_id += 1
        self.client.sendall(encode_message({'id': self.request_id, 'method': method, **values}))
        return self.request_id

    def send(self, window, commands):
        if commands == b'close\n':
            return self.request('close_window', window=window)
        return self.request('input', connection=self.generation, window=window,
            generation=self.native.generation, operation={'kind': 'fixture', 'commands': commands.decode()})

    def paint(self, stage, expected=None, marker=None, required=()):
        from PIL import Image
        import hashlib
        recorded = []
        while True:
            while not self.frames:
                self.record()
            frame, data = self.frames.popleft()
            assert frame['encoding'] == 'bgrx'
            image = Image.frombytes('RGB', (frame['width'], frame['height']), data, 'raw', 'BGRX')
            image.save(self.directory.parent / f"{stage}-{frame['sequence']}.png")
            response = self.response(self.request('frame_ack', frame=frame['sequence']))
            record = {**frame, 'stage': stage, 'sha256': hashlib.sha256(data).hexdigest(),
                      'admitted': response.get('result') == 'painted'}
            recorded.append(record)
            if not record['admitted']:
                assert response['error'] == 'FRAME_TARGET_UNAVAILABLE'
                continue
            assert len(image.getcolors(frame['width'] * frame['height'])) > 16
            if marker is not None:
                colors = {color: count for count, color in image.getcolors(frame['width'] * frame['height'])}
                if colors.get(marker, 0) < 20:
                    continue
                assert all(colors.get(color, 0) >= 20 for color in required), 'Transient surface omitted its parent'
                x0, y0, x1, y1 = image.width, image.height, 0, 0
                for index, color in enumerate(image.getdata()):
                    if color == marker:
                        x, y = index % image.width, index // image.width
                        x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
                record['marker_bounds'] = [x0, y0, x1, y1]
            if expected is None:
                return recorded
            sample = image.getpixel((200, 240))
            record['selected_window_pixel'] = sample
            other = (155, 49, 19) if expected == (19, 87, 155) else (19, 87, 155)
            count = sum(count for count, color in image.getcolors(frame['width'] * frame['height']) if color == other)
            record['inactive_window_pixels'] = count
            assert count == 0, 'Inactive native window pixels leaked into the selected frame'
            if sample == expected:
                return recorded

    def block_frame(self):
        ready = Event()
        def captured(error):
            if not error:
                self.native.capture_observer = None
                assert self.attachment.awaiting is not None
                ready.set()
        def block():
            self.server.current.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            self.native.capture_observer = captured
            self.attachment.damage()
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
            self.native.frames.close()
            self.mainloop.quit()
            stopped.set()
            return False
        GLib.idle_add(stop)
        assert stopped.wait(5)
        self.thread.join(timeout=5)
