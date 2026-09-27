"""Native scene registry and typed seat delivery behind the helper attachment.

Only the private compositor supplies window records. IDs increase for the life
of that compositor. Native decoration grabs retain the fixed viewport's input
coordinates until release; other geometry/focus changes replace the target.
Launch/process admission and toolkit context registration belong to the helper;
window metadata alone never selects an input method or proves application exit.
"""
from dataclasses import dataclass
import math
from desktop_cursor import NativeCursor
from desktop_clipboard import NativeClipboard


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
    mode: int


@dataclass(frozen=True)
class NativeTarget:
    window: int
    generation: int


@dataclass(frozen=True)
class NativeFocus:
    identity: int
    window: int
    pid: int
    surface: int
    available: bool


@dataclass(frozen=True)
class NativeTextContext:
    identity: int
    revision: int
    surface: int
    enabled: bool
    serial: int


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
        self.contexts = None
        self.windows, self.declared, self.last_window, self.generation = {}, {}, 0, 0
        self.x11_windows = {}
        self.surfaces, self.last_surface, self.focus = set(), 0, None
        self.epoch, self.last_epoch = 0, 0
        self.query, self.query_id = None, 0
        self.display_requested, self.display_query = False, None
        self.version, self.closed = None, False
        self.text_contexts, self.last_text_context = {}, 0
        self.text_request, self.last_text_request = None, 0
        self.cursor = NativeCursor(send, lambda: self.target if self.epoch else None, self.cursor_changed, lambda: self.epoch)
        self.clipboard = NativeClipboard(self, self.clipboard_changed)

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
        elif kind in ('clipboard-state', 'clipboard-data', 'clipboard-published', 'clipboard-rejected'):
            if self.version != 1:
                raise ValueError('Native clipboard is unavailable')
            self.clipboard.observe(fields)
        elif kind in ('cursor-state', 'cursor-data'):
            if self.version != 1:
                raise ValueError('Native cursor is unavailable')
            self.cursor.observe(fields)
        elif kind in ('client-barrier-done', 'client-barrier-cancelled', 'text-dispatched', 'text-rejected'):
            if self.version != 1 or len(fields) != 2:
                raise ValueError('Invalid native text response')
            sequence = integer(int(fields[1]))
            request = self.text_request
            # Cancelled requests can still have native replies in flight. They
            # cannot complete a new operation, even after the same window ID.
            if request and sequence == request[0]:
                if request[1] != ('barrier' if kind.startswith('client-barrier-') else 'text'):
                    raise ValueError('Native text response phase differs')
                self.text_request = None
                error = None if kind in ('client-barrier-done', 'text-dispatched') else 'INPUT_CONTEXT_UNAVAILABLE'
                if self.epoch != request[2] or self.target is not request[3]:
                    error = 'INPUT_TARGET_UNAVAILABLE'
                request[4](error)
        elif kind == 'text-context':
            if self.version != 1 or len(fields) != 6:
                raise ValueError('Invalid native text context')
            identity, revision, surface = (integer(int(value), minimum) for value, minimum in
                zip(fields[1:4], (1, 1, 0)))
            enabled = bool(integer(int(fields[4]), 0, 1))
            serial = integer(int(fields[5]), 0, 0xffffffff)
            previous = self.text_contexts.get(identity)
            if ((not previous and (identity <= self.last_text_context or len(self.text_contexts) >= 64)) or
                    previous and revision < previous.revision or
                    surface and surface not in self.surfaces or enabled and not surface):
                raise ValueError('Retired or invalid native text context')
            self.last_text_context = max(identity, self.last_text_context)
            self.text_contexts[identity] = NativeTextContext(identity, revision, surface, enabled, serial)
            if self.contexts:
                self.contexts.native_changed()
        elif kind == 'text-context-retired':
            if len(fields) != 2:
                raise ValueError('Invalid native text retirement')
            self.text_contexts.pop(integer(int(fields[1])), None)
            if self.contexts:
                self.contexts.native_changed()
        elif kind == 'context-surrounding':
            # This unpublished probe exposes bounded protocol metadata only.
            # State progression is not a completed confirmed-text transaction.
            if self.version != 1 or len(fields) != 5:
                raise ValueError('Invalid text context metadata')
            integer(int(fields[1]), 0, 0xffffffff)
            size = integer(int(fields[2]), 0, 4000)
            integer(int(fields[3]), 0, size)
            integer(int(fields[4]), 0, size)
        elif kind == 'native-display':
            if self.version != 1 or len(fields) != 2 or self.display_query is None:
                raise ValueError('Unexpected native display response')
            display = fields[1]
            if display != '-' and (not display.startswith(':') or not 1 <= len(display[1:]) <= 5 or
                                   any(c not in '0123456789' for c in display[1:])):
                raise ValueError('Invalid native display')
            callback, self.display_query = self.display_query, None
            callback(None if display == '-' else display)
        elif kind == 'window-instance':
            if self.version != 1 or len(fields) != 2:
                raise ValueError('Invalid native instance')
            wid = integer(int(fields[1]))
            if wid <= self.last_window or len(self.declared) >= 256:
                raise ValueError('Retired native window or window limit')
            self.declared[wid] = ''
            self.last_window = wid
        elif kind == 'window-title':
            if self.version != 1 or len(fields) != 3:
                raise ValueError('Invalid native title')
            wid = integer(int(fields[1]))
            encoded = fields[2]
            if wid not in self.declared or len(encoded) > 2048:
                raise ValueError('Unknown window or oversized native title')
            title = '' if encoded == '-' else bytes.fromhex(encoded).decode('utf-8', errors='replace')
            if self.declared[wid] != title:
                self.declared[wid] = title
                if self.attachment:
                    self.attachment.metadata_changed()
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
            if self.version != 1 or len(fields) != 8:
                raise ValueError('Invalid native window')
            wid, parent = integer(int(fields[1])), integer(int(fields[2]), 0)
            protocol = fields[3]
            pid = integer(int(fields[4]), -1, 0x7fffffff)
            width, height = (integer(int(v), 1, 65536) for v in fields[5:7])
            mode = integer(int(fields[7]), 0, 8)
            if protocol not in ('wayland', 'x11') or parent == wid:
                raise ValueError('Invalid native window')
            previous = self.windows.get(wid)
            if wid not in self.declared:
                raise ValueError('Unknown or retired native window')
            if previous and (previous.protocol, previous.pid) != (protocol, pid):
                raise ValueError('Native window identity changed')
            if parent and parent not in self.declared:
                raise ValueError('Native parent is unavailable')
            window = NativeWindow(wid, parent, protocol, pid, width, height, mode)
            self.windows[wid] = window
            if previous != window:
                geometry_changed = previous is None or (
                    previous.parent, previous.width, previous.height) != (parent, width, height)
                grab_ended = previous is not None and previous.mode == 8 and mode != 8
                if self.target and self.target.window == wid and (
                        mode & 4 or grab_ended or mode != 8 and geometry_changed):
                    self.target = None
                    self.changed()
                elif self.attachment:
                    self.attachment.metadata_changed()
        elif kind == 'window-x11':
            if self.version != 1 or len(fields) != 3:
                raise ValueError('Invalid Xwayland binding')
            wid, xid = integer(int(fields[1])), integer(int(fields[2]), 1, 0xffffffff)
            window = self.windows.get(wid)
            if (not window or window.protocol != 'x11' or
                    wid in self.x11_windows and self.x11_windows[wid] != xid or
                    any(other != wid and value == xid for other, value in self.x11_windows.items())):
                raise ValueError('Unknown or conflicting Xwayland binding')
            self.x11_windows[wid] = xid
        elif kind == 'window-retired':
            if len(fields) != 2:
                raise ValueError('Invalid retirement')
            wid = integer(int(fields[1]))
            self.windows.pop(wid, None)
            self.declared.pop(wid, None)
            self.x11_windows.pop(wid, None)
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
            if window and window.mode & 4:
                raise ValueError('Minimized native window cannot receive input')
            self.generation = generation
            self.target = NativeTarget(wid, generation) if window else None
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
                          'selection-unavailable', 'close-unavailable', 'text-queued', 'text-unavailable'):
            raise ValueError('Unsupported native record')

    def cursor_changed(self):
        if self.attachment:
            self.attachment.cursor_changed()

    def clipboard_changed(self, selection):
        if self.attachment:
            self.attachment.clipboard_changed(selection)

    def changed(self):
        self.cursor.invalidate()
        self.clipboard.invalidate()
        if self.attachment:
            self.attachment.scene_changed()

    def lost(self):
        if self.closed:
            return
        self.closed, self.target, self.focus, self.epoch = True, None, None, 0
        self.text_contexts.clear()
        self.cursor.close()
        self.clipboard.close()
        request, self.text_request = self.text_request, None
        if request:
            request[4]('INPUT_TARGET_UNAVAILABLE')
        if self.display_query:
            callback, self.display_query = self.display_query, None
            callback(None)
        if self.query:
            callback, self.query = self.query, None
            callback(0, 0)
        self.changed()

    def query_display(self, completed):
        if self.closed or self.version != 1 or self.display_requested:
            raise ValueError('Native display query is unavailable')
        self.display_requested, self.display_query = True, completed
        try:
            self.send('display-query\n')
        except (OSError, ValueError):
            self.lost()

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
                                 width=w.width, height=w.height, title=self.declared[w.window],
                                 maximized=bool(w.mode & 1), fullscreen=bool(w.mode & 2),
                                 minimized=bool(w.mode & 4), interacting=bool(w.mode & 8))
                            for w in self.windows.values()]}

    def bind(self, epoch):
        integer(epoch)
        if self.version != 1 or epoch <= self.last_epoch:
            raise ValueError('Native connection is unavailable')
        self.epoch, self.last_epoch = epoch, epoch
        # An unavailable display remains observable and explicitly terminable.
        # It has no target or native input authority to restore on attachment.
        if not self.closed:
            self.send(f'connection {epoch}\n')

    def release(self, epoch):
        if not self.closed and epoch == self.epoch:
            self.send(f'release {epoch}\n')

    def unbind(self, epoch):
        if not self.closed and epoch == self.epoch:
            self.send(f'detach {epoch}\n')
            self.epoch = 0
            self.cursor.invalidate()
            self.clipboard.invalidate()
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
        integer(window)
        if self.closed or not self.epoch or window not in self.windows:
            raise ValueError('Native window is unavailable')
        # Closing is a window lifecycle request, not pixel-coordinate input.
        # Save dialogs or damage can change the scene before the compositor
        # receives this request without changing the original window instance.
        self.send(f'close-window {self.epoch} {window}\n')

    @staticmethod
    def validate_input(operation):
        if type(operation) is not dict:
            raise ValueError('Invalid native input')
        fields = {'key': {'code', 'pressed'}, 'move': {'x', 'y'},
                  'button': {'x', 'y', 'button', 'pressed'}, 'scroll': {'x', 'y', 'dx', 'dy'},
                  'text': {'text'}, 'clipboard': {'text'}}
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
        if self.contexts:
            token = self.contexts.context_for(target)
            if token:
                return self.contexts, token
        return None

    def sync_clipboard(self, epoch, target):
        self.submit(epoch, target, ['clipboard-sync'])

    def text_operation(self, epoch, target, kind, completed, *, context=None, text=None):
        if self.text_request is not None or kind not in ('barrier', 'text'):
            raise ValueError('Native text operation is unavailable')
        integer(self.last_text_request + 1)
        self.last_text_request += 1
        sequence = self.last_text_request
        command = f'client-barrier {sequence}'
        if kind == 'text':
            command = f'text-commit {sequence} {context.identity} {text.encode("utf-8").hex()}'
        request = (sequence, kind, epoch, target, completed)
        self.text_request = request
        try:
            self.submit(epoch, target, [command])
        except BaseException:
            self.text_request = None
            raise
        return request

    def cancel_text_operation(self, request):
        if self.text_request is not request:
            return
        self.text_request = None
        if not self.closed and request[1] == 'barrier':
            self.send(f'cancel-barrier {request[0]}\n')
