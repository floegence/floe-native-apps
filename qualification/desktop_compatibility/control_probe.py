"""Real helper attachment/frame admission around the unpublished compositor.

The production transport, bounded native capture, frame gate and input scheduler
run unchanged. This fixture adapts unpublished native scene/damage records; it
does not claim production launch or complete package admission.
"""
import json
import io
from collections import deque
from pathlib import Path
import secrets
import socket
import struct
from threading import Condition, Event, Thread

from gi.repository import GLib
from desktop_control import GLibLoop, encode_message
from desktop_helper import DesktopHelper


class ControlWire:
    """Exercise the real native socket owner from compositor startup onward."""
    def __init__(self, connection, events):
        self.events, self.loop = events, GLibLoop()
        self.observed = Condition()
        self.helper = DesktopHelper(connection, self.loop)
        self.native, self.channel = self.helper.native, self.helper.channel
        self.channel.observed = self.observe
        self.mainloop = GLib.MainLoop()
        self.thread = Thread(target=self.mainloop.run, daemon=True)
        self.thread.start()

    def observe(self, line):
        with self.observed:
            self.events.append(line)
            self.native.observe(line)
            self.observed.notify_all()

    def wait_record(self, expected, timeout=15):
        # Protocol experiments must not gain accidental ordering from the
        # fixture's normal polling interval. Wake on the actual native record.
        with self.observed:
            return self.observed.wait_for(lambda: expected in self.events, timeout)

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
                self.helper.close()
                self.mainloop.quit()
            self.invoke(stop)
            self.thread.join(timeout=5)
        else:
            self.helper.close()


class ControlClient:
    """A viewer fixture using only the public authenticated local protocol."""
    def __init__(self, directory, endpoint, instance, token):
        self.request_id, self.client = 0, None
        self.trace = []
        self.frames, self.responses, self.events = deque(), {}, []
        self.directory = Path(directory)
        self.endpoint, self.instance, self.token = str(endpoint), instance, token
        self.state = None

    def reconnect(self):
        if self.client:
            self.client.close()
        self.client = socket.socket(socket.AF_UNIX)
        from input_order import DELIVERY_TIMEOUT_MS
        self.client.settimeout(DELIVERY_TIMEOUT_MS / 1000 + 5)
        self.client.connect(self.endpoint)
        self.client.sendall(encode_message({'version': 1, 'instance': self.instance, 'token': self.token}))
        kind, body = self.receive()
        response = json.loads(body)
        assert kind == 1 and response['event'] == 'attached'
        self.generation = response['connection']
        self.state = response['state']
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
        assert kind in (1, 2, 3) and size <= 64 * 1024 * 1024
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
        elif message.get('event') == 'cursor':
            from PIL import Image
            description = message['cursor']
            assert description['connection'] == self.generation
            if description['mode'] == 'image':
                kind, data = self.receive()
                assert kind == 3 and len(data) == message['bytes']
                image = Image.open(io.BytesIO(data))
                image.load()
                assert image.mode == 'RGBA' and image.size == (description['width'], description['height'])
                (self.directory.parent / f"cursor-{self.generation}-{description['sequence']}.png").write_bytes(data)
            else:
                assert description['mode'] in ('default', 'hidden') and message['bytes'] == 0
            self.events.append(message)
        elif 'id' in message:
            self.responses[message['id']] = message
        else:
            if message.get('event') == 'state':
                self.state = message['state']
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

    def paint(self, stage, expected=None, marker=None, required=(), absent=(), accept_bounds=None,
              sample_point=(200, 240), window=None):
        from PIL import Image
        import hashlib
        recorded = []
        while True:
            while not self.frames:
                self.record()
            frame, data = self.frames.popleft()
            assert frame['encoding'] == 'png'
            image = Image.open(io.BytesIO(data))
            assert image.format == 'PNG' and image.size == (frame['width'], frame['height'])
            image.load()
            image = image.convert('RGB')
            (self.directory.parent / f"{stage}-{frame['sequence']}.png").write_bytes(data)
            record = {**frame, 'stage': stage, 'sha256': hashlib.sha256(data).hexdigest(),
                      'encoded_bytes': len(data), 'decoded_sha256': hashlib.sha256(image.tobytes()).hexdigest()}
            colors = {color: count for count, color in image.getcolors(frame['width'] * frame['height'])}
            matches = not any(colors.get(color, 0) for color in absent)
            matches = matches and (window is None or frame['window'] == window)
            if marker is not None:
                matches = matches and colors.get(marker, 0) >= 20
                if matches:
                    x0, y0, x1, y1 = image.width, image.height, 0, 0
                    for index, color in enumerate(image.getdata()):
                        if color == marker:
                            x, y = index % image.width, index // image.width
                            x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
                    record['marker_bounds'] = [x0, y0, x1, y1]
                    matches = not accept_bounds or accept_bounds(record['marker_bounds'])
            if expected is not None:
                sample = image.getpixel(sample_point)
                record['selected_window_sample_point'] = sample_point
                record['selected_window_pixel'] = sample
                other = (155, 49, 19) if expected == (19, 87, 155) else (19, 87, 155)
                record['inactive_window_pixels'] = colors.get(other, 0)
                matches = matches and sample == expected
            # Decoding and inspecting all pixels can overlap a native geometry
            # change. Acknowledge only afterward, so a retired image cannot be
            # returned as the input target merely because an earlier ack passed.
            response = self.response(self.request('frame_ack', frame=frame['sequence']))
            record['admitted'] = response.get('result') == 'painted'
            recorded.append(record)
            if not record['admitted']:
                assert response['error'] == 'FRAME_TARGET_UNAVAILABLE'
                continue
            # Newly mapped native dialogs may first paint a blank surface.
            # Decode/ack it, but require actual UI pixels before interaction.
            matches = matches and len(colors) > 16
            if expected is not None:
                assert record['inactive_window_pixels'] == 0, 'Inactive native window pixels leaked into the selected frame'
            if matches:
                assert all(colors.get(color, 0) >= 20 for color in required), 'Transient surface omitted its parent'
                return recorded

    def close(self):
        if self.client:
            self.client.close()
            self.client = None


class ControlProbe(ControlClient):
    def __init__(self, directory, wire, frames):
        directory = Path(directory) / 'helper'
        directory.mkdir(mode=0o700)
        super().__init__(directory, directory / 'control.sock', directory.name, secrets.token_hex(32))
        self.wire, self.loop, self.native = wire, wire.loop, wire.native
        self.pointer = (0, 0)
        def start():
            wire.helper.listen(self.directory, self.instance, self.token, frames)
            self.attachment, self.server = wire.helper.attachment, wire.helper.server
        wire.invoke(start)

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

    def block_frame(self):
        ready = Event()
        def block():
            self.server.current.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            original = self.attachment.captured
            def captured(ticket, description, data, error):
                original(ticket, description, data, error)
                if not error and self.attachment.awaiting is not None:
                    self.attachment.captured = original
                    ready.set()
            # Observe completion at its current owner, including a capture
            # that began before this test armed backpressure. Replacing the
            # capture method would miss that already running operation.
            self.attachment.captured = captured
            awaiting = self.attachment.awaiting
            self.attachment.damage()
            return awaiting
        if self.wire.invoke(block) is not None:
            # A frame completed before the socket was constrained. Consume and
            # acknowledge it so the next capture actually meets backpressure.
            self.paint('before-blocked-frame')
        assert ready.wait(5)

    def close(self):
        if self.client:
            self.client.close()
        def stop():
            self.wire.helper.stop_sharing()
        self.wire.invoke(stop)
        (self.directory / 'control-receipts.json').write_text(json.dumps(self.trace, indent=2) + '\n')
