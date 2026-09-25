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

from gi.repository import GLib
from desktop_attachment import DesktopAttachment
from desktop_capture import NativeFrames
from desktop_control import DesktopControl, GLibLoop, encode_message
from desktop_native import NativeChannel, NativeDesktop


class ControlWire:
    """Exercise the real native socket owner from compositor startup onward."""
    def __init__(self, connection, events):
        self.events, self.loop = events, GLibLoop()
        self.native = NativeDesktop(lambda value: self.channel.send(value), None)
        self.channel = NativeChannel(connection, self.loop, self.observe, self.native.lost)
        self.mainloop = GLib.MainLoop()
        self.thread = Thread(target=self.mainloop.run, daemon=True)
        self.thread.start()

    def observe(self, line):
        self.events.append(line)
        self.native.observe(line)

    def invoke(self, callback):
        completed, result = Event(), []
        def apply():
            try:
                result.append(callback())
            except Exception as error:
                result.append(error)
            completed.set()
            return False
        GLib.idle_add(apply)
        assert completed.wait(5)
        if isinstance(result[0], Exception):
            raise result[0]
        return result[0]

    def send(self, commands):
        self.invoke(lambda: self.channel.send(commands))

    def stop_reading(self):
        # This is solely the deliberate stalled-observer qualification. Drain
        # commands before giving the fixture its socket for that destructive
        # channel test; production never changes or replaces the socket owner.
        completed = Event()
        def stop():
            if self.channel.output:
                return True
            self.loop.watch(self.channel.socket.fileno())
            self.mainloop.quit()
            completed.set()
            return False
        GLib.idle_add(stop)
        assert completed.wait(5)
        self.thread.join(timeout=5)
        self.channel.socket.setblocking(True)

    def close(self):
        if self.thread.is_alive():
            def stop():
                self.channel.close()
                self.mainloop.quit()
            self.invoke(stop)
            self.thread.join(timeout=5)
        else:
            self.channel.close()


class ObservedFrames(NativeFrames):
    def __init__(self, *args):
        super().__init__(*args)
        self.observer = None

    def capture(self, target, completed):
        def captured(description, data, error):
            completed(description, data, error)
            if self.observer:
                self.observer(error)
        super().capture(target, captured)


class ControlProbe:
    def __init__(self, directory, wire, frames):
        self.request_id, self.client = 0, None
        self.trace = []
        self.frames, self.responses, self.events = deque(), {}, []
        self.directory = Path(directory) / 'helper'
        self.directory.mkdir(mode=0o700)
        self.token = secrets.token_hex(32)
        self.wire, self.loop, self.native = wire, wire.loop, wire.native
        self.pointer = (0, 0)
        def start():
            self.native.frames = ObservedFrames(frames, self.loop, self.native.query_scene)
            self.attachment = DesktopAttachment(self.native, GLib.timeout_add, GLib.source_remove)
            self.native.attachment = self.attachment
            self.server = DesktopControl(self.directory, self.directory.name, self.token, self.attachment, self.loop)
        wire.invoke(start)

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
        self.trace.append({'received': message})
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
        self.trace.append({'sent': {'id': self.request_id, 'method': method, **values}})
        self.client.sendall(encode_message({'id': self.request_id, 'method': method, **values}))
        return self.request_id

    def send(self, window, commands):
        if commands == b'close\n':
            return self.request('close_window', window=window)
        for line in commands.decode().splitlines():
            parts = line.split()
            if parts[0] == 'motion':
                self.pointer = tuple(float(v) for v in parts[1:])
                value = {'kind': 'move', 'x': self.pointer[0], 'y': self.pointer[1]}
            elif parts[0] == 'key':
                value = {'kind': 'key', 'code': int(parts[1]), 'pressed': parts[2] == '1'}
            elif parts[0] == 'button':
                value = {'kind': 'button', 'button': {272: 0, 273: 2, 274: 1}[int(parts[1])],
                         'pressed': parts[2] == '1', 'x': self.pointer[0], 'y': self.pointer[1]}
            elif parts[0] == 'scroll':
                value = {'kind': 'scroll', 'dx': float(parts[1]), 'dy': float(parts[2]),
                         'x': self.pointer[0], 'y': self.pointer[1]}
            else:
                raise ValueError('Unknown fixture instruction')
            request = self.request('input', connection=self.generation, window=window,
                                   generation=self.native.generation, operation=value)
        return request

    def paint(self, stage, expected=None, marker=None, required=(), absent=()):
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
            colors = {color: count for count, color in image.getcolors(frame['width'] * frame['height'])}
            assert len(colors) > 16
            if any(colors.get(color, 0) for color in absent):
                continue
            if marker is not None:
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
                self.native.frames.observer = None
                assert self.attachment.awaiting is not None
                ready.set()
        def block():
            self.server.current.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            self.native.frames.observer = captured
            self.attachment.damage()
            return False
        GLib.idle_add(block)
        assert ready.wait(5)

    def close(self):
        if self.client:
            self.client.close()
        def stop():
            self.server.close()
            self.attachment.close()
            self.native.frames.close()
            self.native.attachment = None
        self.wire.invoke(stop)
        (self.directory / 'control-receipts.json').write_text(json.dumps(self.trace, indent=2) + '\n')
