"""Native scene registry and typed seat delivery behind the helper attachment.

Only the private compositor supplies window records. IDs increase for the life
of that compositor and every geometry/focus change replaces the input target.
Launch/process admission and toolkit context registration belong to the helper;
window metadata alone never selects an input method or proves application exit.
"""
from dataclasses import dataclass
import math


MAX_BUFFER = 128 * 1024
MAX_RECORD = 4096
MAX_ID = 9007199254740991


class NativeChannel:
    """One nonblocking owner of the inherited compositor control socket."""
    def __init__(self, connection, loop, observed, lost):
        self.socket, self.loop, self.observed, self.lost = connection, loop, observed, lost
        self.input, self.output, self.closed = bytearray(), bytearray(), False
        connection.setblocking(False)
        loop.watch(connection.fileno(), self.read)

    def send(self, commands):
        data = commands.encode('ascii')
        if self.closed:
            raise OSError('Native control is unavailable')
        if not data or not data.endswith(b'\n') or len(self.output) + len(data) > MAX_BUFFER:
            self.close()
            raise OSError('Native control capacity exceeded')
        self.output.extend(data)
        self.loop.watch(self.socket.fileno(), self.read, self.write)

    def read(self):
        if self.closed:
            return
        try:
            data = self.socket.recv(16 * 1024)
            if not data:
                self.close()
                return
            self.input.extend(data)
            while b'\n' in self.input:
                boundary = self.input.index(b'\n')
                if boundary > MAX_RECORD:
                    raise ValueError('Native record exceeds limit')
                line = bytes(self.input[:boundary]).decode('ascii')
                del self.input[:boundary + 1]
                self.observed(line)
                if self.closed:
                    return
            if len(self.input) > MAX_RECORD:
                raise ValueError('Native record exceeds limit')
        except BlockingIOError:
            pass
        except (OSError, ValueError):
            self.close()

    def write(self):
        if self.closed or not self.output:
            return
        try:
            count = self.socket.send(self.output[:16 * 1024])
            if count == 0:
                self.close()
                return
            del self.output[:count]
            if not self.output:
                self.loop.watch(self.socket.fileno(), self.read)
        except BlockingIOError:
            pass
        except OSError:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.loop.watch(self.socket.fileno())
        self.socket.close()
        self.input.clear()
        self.output.clear()
        self.lost()


@dataclass(frozen=True)
class NativeWindow:
    window: int
    parent: int
    protocol: str
    pid: int
    width: int
    height: int


@dataclass(frozen=True)
class NativeTarget:
    window: int
    generation: int
    width: int
    height: int


@dataclass(frozen=True)
class NativeFocus:
    identity: int
    window: int
    pid: int
    surface: int
    available: bool


def integer(value, minimum=1, maximum=MAX_ID):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Invalid native integer')
    return value


def number(value, minimum, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError('Invalid native coordinate')
    return value


class NativeDesktop:
    def __init__(self, send, frames):
        self.send, self.frames = send, frames
        self.attachment, self.target = None, None
        self.windows, self.declared, self.last_window, self.generation = {}, set(), 0, 0
        self.surfaces, self.last_surface, self.focus = set(), 0, None
        self.epoch, self.last_epoch = 0, 0
        self.query, self.query_id = None, 0
        self.version, self.closed = None, False

    def observe(self, line):
        if self.closed:
            return
        fields = line.split()
        if not fields:
            raise ValueError('Empty native record')
        kind = fields[0]
        if kind == 'native-version':
            if fields != ['native-version', '1'] or self.version is not None:
                raise ValueError('Unsupported native version')
            self.version = 1
        elif kind == 'window-instance':
            if self.version != 1 or len(fields) != 2:
                raise ValueError('Invalid native instance')
            wid = integer(int(fields[1]))
            if wid <= self.last_window or len(self.declared) >= 256:
                raise ValueError('Retired native window or window limit')
            self.declared.add(wid)
            self.last_window = wid
        elif kind == 'surface-instance':
            if self.version != 1 or len(fields) != 2:
                raise ValueError('Invalid native surface')
            sid = integer(int(fields[1]))
            if sid <= self.last_surface or len(self.surfaces) >= 4096:
                raise ValueError('Retired native surface or surface limit')
            self.last_surface = sid
            self.surfaces.add(sid)
        elif kind == 'surface-retired':
            if len(fields) != 2:
                raise ValueError('Invalid native surface retirement')
            sid = integer(int(fields[1]))
            self.surfaces.discard(sid)
            if self.focus and self.focus.identity == sid:
                self.focus, self.target = None, None
                self.changed()
        elif kind == 'focus':
            if self.version != 1 or len(fields) != 6:
                raise ValueError('Invalid native focus')
            sid, wid, pid, surface, ready = (integer(int(v), 0) for v in fields[1:])
            if not sid:
                if any((wid, pid, surface, ready)):
                    raise ValueError('Invalid cleared native focus')
                focus = None
            else:
                if sid not in self.surfaces or wid not in self.declared or not pid or not surface or ready > 1:
                    raise ValueError('Unknown native focus')
                focus = NativeFocus(sid, wid, pid, surface, bool(ready))
            if focus != self.focus:
                self.focus, self.target = focus, None
                self.changed()
        elif kind == 'window-state':
            if self.version != 1 or len(fields) != 7:
                raise ValueError('Invalid native window')
            wid, parent = integer(int(fields[1])), integer(int(fields[2]), 0)
            protocol = fields[3]
            pid = integer(int(fields[4]), -1, 0x7fffffff)
            width, height = (integer(int(v), 1, 65536) for v in fields[5:])
            if protocol not in ('wayland', 'x11') or parent == wid:
                raise ValueError('Invalid native window')
            previous = self.windows.get(wid)
            if wid not in self.declared:
                raise ValueError('Unknown or retired native window')
            if previous and (previous.protocol, previous.pid) != (protocol, pid):
                raise ValueError('Native window identity changed')
            if parent and parent not in self.declared:
                raise ValueError('Native parent is unavailable')
            window = NativeWindow(wid, parent, protocol, pid, width, height)
            self.windows[wid] = window
            if previous != window and self.target and self.target.window == wid:
                self.target = None
                self.changed()
        elif kind == 'window-retired':
            if len(fields) != 2:
                raise ValueError('Invalid retirement')
            wid = integer(int(fields[1]))
            self.windows.pop(wid, None)
            self.declared.discard(wid)
            if self.target and self.target.window == wid:
                self.target = None
                self.changed()
        elif kind == 'scene':
            if self.version != 1 or len(fields) != 3:
                raise ValueError('Invalid native scene')
            generation, wid = integer(int(fields[1])), integer(int(fields[2]), 0)
            if generation <= self.generation or (wid and wid not in self.windows):
                raise ValueError('Stale or unknown native scene')
            if wid and (not self.focus or not self.focus.available or self.focus.window != wid):
                raise ValueError('Native scene has no ready focus')
            window = self.windows.get(wid)
            self.generation = generation
            self.target = NativeTarget(wid, generation, window.width, window.height) if window else None
            self.changed()
        elif kind == 'scene-at':
            if len(fields) != 4 or self.query is None or int(fields[1]) != self.query_id:
                raise ValueError('Unexpected scene barrier')
            generation, window = integer(int(fields[2]), 0), integer(int(fields[3]), 0)
            callback, self.query = self.query, None
            callback(window, generation)
        elif kind == 'damage':
            if len(fields) != 3:
                raise ValueError('Invalid damage')
            integer(int(fields[1]))
            integer(int(fields[2]), 0)
            if self.attachment:
                self.attachment.damage()
        elif kind not in ('ready', 'frame', 'window-added', 'window-mapped',
                          'window-protocol', 'window-restored', 'window-removed', 'context', 'pointer',
                          'capture-authorized', 'connection-ready', 'input-rejected',
                          'selection-unavailable', 'text-queued', 'text-unavailable'):
            raise ValueError('Unsupported native record')

    def changed(self):
        if self.attachment:
            self.attachment.scene_changed()

    def lost(self):
        if self.closed:
            return
        self.closed, self.target, self.focus, self.epoch = True, None, None, 0
        if self.query:
            callback, self.query = self.query, None
            callback(0, 0)
        self.changed()

    def query_scene(self, completed):
        if self.closed or self.query is not None:
            raise ValueError('Native scene query is unavailable')
        self.query_id = integer(self.query_id + 1)
        self.query = completed
        self.send(f'scene-query {self.query_id}\n')

    def snapshot(self):
        return {'state': 'unavailable' if self.closed else 'running' if self.target else 'waiting',
                'window': self.target.window if self.target else None, 'generation': self.generation,
                'windows': [dict(window=w.window, parent=w.parent or None, protocol=w.protocol,
                                 width=w.width, height=w.height) for w in self.windows.values()]}

    def bind(self, epoch):
        integer(epoch)
        if self.closed or self.version != 1 or epoch <= self.last_epoch:
            raise ValueError('Native connection is unavailable')
        self.epoch, self.last_epoch = epoch, epoch
        self.send(f'connection {epoch}\n')

    def release(self, epoch):
        if not self.closed and epoch == self.epoch:
            self.send(f'release {epoch}\n')
        self.frames.cancel()

    def unbind(self, epoch):
        if not self.closed and epoch == self.epoch:
            self.send(f'detach {epoch}\n')
            self.epoch = 0
        self.frames.cancel()

    def capture(self, target, completed):
        if self.closed or self.target is not target:
            raise ValueError('Native frame is unavailable')
        self.frames.capture(target, completed)

    def select(self, window):
        integer(window)
        if self.closed or not self.epoch or window not in self.windows:
            raise ValueError('Native window is unavailable')
        self.send(f'select {self.epoch} {window}\n')

    def close_window(self, window):
        if not self.target or self.target.window != window:
            raise ValueError('Native window is unavailable')
        self.submit(self.epoch, self.target, ['close'])

    @staticmethod
    def validate_input(operation):
        if type(operation) is not dict:
            raise ValueError('Invalid native input')
        fields = {'key': {'code', 'pressed'}, 'move': {'x', 'y'},
                  'button': {'x', 'y', 'button', 'pressed'}, 'scroll': {'x', 'y', 'dx', 'dy'},
                  'text': {'text'}}
        kind = operation.get('kind')
        if not isinstance(kind, str) or kind not in fields or set(operation) != fields[kind] | {'kind'}:
            raise ValueError('Invalid native input')
        if kind == 'key':
            integer(operation['code'], 1, 767)
        if kind == 'button':
            integer(operation['button'], 0, 4)
        if 'pressed' in operation and type(operation['pressed']) is not bool:
            raise ValueError('Invalid native key state')
        for axis in ('x', 'y'):
            if axis in operation:
                number(operation[axis], 0, 4096)
        for axis in ('dx', 'dy'):
            if axis in operation:
                number(operation[axis], -4096, 4096)
        return dict(operation)

    def deliver(self, epoch, target, operation):
        value = self.validate_input(operation)
        kind, commands = value['kind'], []
        if 'x' in value:
            commands.append(f"motion {value['x']:g} {value['y']:g}")
        if kind == 'key':
            commands.append(f"key {value['code']} {int(value['pressed'])}")
        elif kind == 'button':
            # Public buttons follow the existing remote-pointer 0/1/2 contract:
            # left, middle, right. Linux evdev orders right before middle.
            code = {0: 272, 1: 274, 2: 273}.get(value['button'], 272 + value['button'])
            commands.append(f"button {code} {int(value['pressed'])}")
        elif kind == 'scroll':
            commands.append(f"scroll {value['dx']:g} {value['dy']:g}")
        elif kind != 'move':
            raise ValueError('Text requires an admitted native context')
        self.submit(epoch, target, commands)

    def submit(self, epoch, target, commands):
        if self.closed or not epoch or epoch != self.epoch or self.target is not target:
            raise ValueError('Native input target is unavailable')
        self.send(''.join(f'input {epoch} {target.window} {target.generation} {command}\n' for command in commands))

    def context_for(self, target):
        # A mapped surface or text-input serial is not toolkit completion.
        # Only a registered adapter with an explicit completion contract may
        # enter the shared scheduler. There is deliberately no guessed adapter.
        return None
