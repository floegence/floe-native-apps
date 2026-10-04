"""Authorized PipeWire pixels and cursor metadata through the public C/SPA ABI.

GStreamer's pipewiresrc does not expose SPA_META_Cursor. One native stream owns
both pixels and metadata; no second portal grant or private Gst layout is used.
The pinned libpipewire client maps negotiated system-memory buffers. All bytes
are copied before returning each buffer to PipeWire.
"""
import ctypes as C
import os
import struct
import time

from host_desktop_contract import DesktopError


class CursorState:
    def __init__(self):
        self.position = (0, 0)
        self.hotspot = (0, 0)
        self.width = self.height = 0
        self.pixels = b''
        self.visible = False

    def update(self, data):
        if len(data) < 28:
            raise DesktopError('CURSOR_INVALID')
        identity, _flags, x, y, hot_x, hot_y, offset = struct.unpack_from('<IIiiiiI', data)
        if not identity:
            self.visible = False
            return
        self.position = (x, y)
        if not offset:
            self.visible = bool(self.pixels)
            return
        if offset < 28 or offset + 20 > len(data):
            raise DesktopError('CURSOR_INVALID')
        format, width, height, stride, pixels = struct.unpack_from('<IIIiI', data, offset)
        if not format or not pixels:
            if not pixels:
                self.visible = False
                self.pixels = b''
            else:
                self.visible = bool(self.pixels)
            return
        if (format not in (11, 12) or not 1 <= width <= 512 or not 1 <= height <= 512 or
                not 0 <= hot_x < width or not 0 <= hot_y < height or
                not width * 4 <= stride <= 512 * 4 or pixels < 20 or
                offset + pixels + (height - 1) * stride + width * 4 > len(data)):
            raise DesktopError('CURSOR_INVALID')
        rows = b''.join(data[offset + pixels + row * stride:offset + pixels + row * stride + width * 4]
                        for row in range(height))
        if format == 11:
            converted = bytearray(rows)
            converted[0::4], converted[2::4] = rows[2::4], rows[0::4]
            rows = bytes(converted)
        self.width, self.height = width, height
        self.hotspot, self.pixels, self.visible = (hot_x, hot_y), rows, True


def _pod(kind, body):
    return struct.pack('<II', len(body), kind) + body


def _scalar(kind, value):
    return _pod(kind, struct.pack('<i' if kind == 4 else '<I', value))


def _object(kind, identity, properties):
    body = struct.pack('<II', kind, identity)
    for key, value in properties:
        prop = struct.pack('<II', key, 0) + value
        body += prop + bytes((-len(prop)) % 8)
    return _pod(15, body)


def _choice(kind, size, values, choice):
    return _pod(19, struct.pack('<IIII', choice, 0, size, kind) + b''.join(values))


def _properties(address):
    if not address:
        return {}
    size, kind = struct.unpack('<II', C.string_at(address, 8))
    if kind != 15 or not 8 <= size <= 65536:
        raise DesktopError('CAPTURE_FORMAT_UNSUPPORTED')
    data = C.string_at(address + 8, size)
    offset, result = 8, {}
    while offset < size:
        if offset + 16 > size:
            raise DesktopError('CAPTURE_FORMAT_UNSUPPORTED')
        key, _flags, length, kind = struct.unpack_from('<IIII', data, offset)
        if offset + 16 + length > size or key in result:
            raise DesktopError('CAPTURE_FORMAT_UNSUPPORTED')
        value = data[offset + 16:offset + 16 + length]
        # Format fixation may retain a Choice(None) wrapper and its old range.
        # Its first child is the negotiated scalar; an unfixed choice is unsafe.
        if kind == 19:
            if len(value) < 16:
                raise DesktopError('CAPTURE_FORMAT_UNSUPPORTED')
            choice, _flags, child_size, child_kind = struct.unpack_from('<IIII', value)
            if choice != 0 or not child_size or len(value) < 16 + child_size:
                raise DesktopError('CAPTURE_FORMAT_UNSUPPORTED')
            kind, value = child_kind, value[16:16 + child_size]
        result[key] = (kind, value)
        offset += (16 + length + 7) & ~7
    return result


class _Meta(C.Structure):
    _fields_ = [('type', C.c_uint32), ('size', C.c_uint32), ('data', C.c_void_p)]


class _Chunk(C.Structure):
    _fields_ = [('offset', C.c_uint32), ('size', C.c_uint32), ('stride', C.c_int32), ('flags', C.c_int32)]


class _Data(C.Structure):
    _fields_ = [('type', C.c_uint32), ('flags', C.c_uint32), ('fd', C.c_int64),
                ('mapoffset', C.c_uint32), ('maxsize', C.c_uint32), ('data', C.c_void_p),
                ('chunk', C.POINTER(_Chunk))]


class _Buffer(C.Structure):
    _fields_ = [('n_metas', C.c_uint32), ('n_datas', C.c_uint32),
                ('metas', C.POINTER(_Meta)), ('datas', C.POINTER(_Data))]


class _PWBuffer(C.Structure):
    _fields_ = [('buffer', C.POINTER(_Buffer))]


class _Events(C.Structure):
    _fields_ = [('version', C.c_uint32)] + [(name, C.c_void_p) for name in
        ('destroy', 'state_changed', 'control_info', 'io_changed', 'param_changed',
         'add_buffer', 'remove_buffer', 'process', 'drained', 'command', 'trigger_done')]


def _bind(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype, function.argtypes = result, arguments
    return function


class PipeWireCapture:
    def __init__(self, Gst, fd, node, fps, captured, failed, equal_pixels, cursor_changed=None):
        self.Gst, self.captured, self.failed, self.equal = Gst, captured, failed, equal_pixels
        self.cursor_changed = cursor_changed
        self.cursor = CursorState()
        self.previous = None
        self.previous_cursor = self.previous_shape = None
        self.width = self.height = self.format = 0
        self.closed = self.broken = self.started = False
        self.initialized = False
        self.loop = self.context = self.core = self.stream = None
        self.painter = None
        self.pw = C.CDLL('libpipewire-0.3.so.0')
        p, i, u = C.c_void_p, C.c_int, C.c_uint32
        for name, result, arguments in [
            ('pw_init', None, [p, p]), ('pw_thread_loop_new', p, [C.c_char_p, p]),
            ('pw_deinit', None, []),
            ('pw_thread_loop_get_loop', p, [p]), ('pw_thread_loop_start', i, [p]),
            ('pw_thread_loop_stop', None, [p]), ('pw_thread_loop_destroy', None, [p]),
            ('pw_context_new', p, [p, p, C.c_size_t]), ('pw_context_destroy', None, [p]),
            ('pw_context_connect_fd', p, [p, i, p, C.c_size_t]), ('pw_core_disconnect', i, [p]),
            ('pw_stream_new', p, [p, C.c_char_p, p]), ('pw_stream_destroy', None, [p]),
            ('pw_stream_add_listener', None, [p, p, C.POINTER(_Events), p]),
            ('pw_stream_connect', i, [p, i, u, i, C.POINTER(p), u]),
            ('pw_stream_update_params', i, [p, C.POINTER(p), u]),
            ('pw_stream_dequeue_buffer', C.POINTER(_PWBuffer), [p]),
            ('pw_stream_queue_buffer', i, [p, C.POINTER(_PWBuffer)]),
        ]:
            _bind(self.pw, name, result, *arguments)
        self.callbacks = [C.CFUNCTYPE(None, p, i, i, C.c_char_p)(self._state),
                          C.CFUNCTYPE(None, p, u, p)(self._format),
                          C.CFUNCTYPE(None, p)(self._process)]
        self.events = _Events(version=2, state_changed=C.cast(self.callbacks[0], p),
            param_changed=C.cast(self.callbacks[1], p), process=C.cast(self.callbacks[2], p))
        # spa_hook contains list, callbacks, removed and private pointers (48 bytes).
        self.hook = (C.c_void_p * 6)()
        try:
            self.pw.pw_init(None, None)
            self.initialized = True
            self.loop = self.pw.pw_thread_loop_new(b'floe-desktop-capture', None)
            if not self.loop:
                raise DesktopError('CAPTURE_UNAVAILABLE')
            self.context = self.pw.pw_context_new(self.pw.pw_thread_loop_get_loop(self.loop), None, 0)
            if not self.context:
                raise DesktopError('CAPTURE_UNAVAILABLE')
            self.core = self.pw.pw_context_connect_fd(self.context, os.dup(fd), None, 0)
            if not self.core:
                raise DesktopError('CAPTURE_UNAVAILABLE')
            self.stream = self.pw.pw_stream_new(self.core, b'floe-desktop', None)
            if not self.stream:
                raise DesktopError('CAPTURE_UNAVAILABLE')
            self.pw.pw_stream_add_listener(self.stream, self.hook, C.byref(self.events), None)
            formats = _choice(3, 4, [struct.pack('<I', v) for v in (12, 12, 8, 11, 7)], 3)
            sizes = _choice(10, 8, [struct.pack('<II', *v) for v in ((1920, 1080), (2, 2), (32768, 32768))], 1)
            rates = _choice(11, 8, [struct.pack('<II', *v) for v in ((0, 1), (0, 1), (fps, 1))], 1)
            params, references = self._params([_object(0x40003, 3,
                [(1, _scalar(3, 2)), (2, _scalar(3, 1)), (0x20001, formats),
                 (0x20003, sizes), (0x20004, _pod(11, struct.pack('<II', 0, 1))),
                 (0x20005, rates)])])
            # Input direction, autoconnect and mmap. Callbacks run on the owned
            # loop rather than the realtime thread; no unsupported DMA-BUF path.
            if self.pw.pw_stream_connect(self.stream, 0, node, 1 | 4, params, len(references)) < 0:
                raise DesktopError('CAPTURE_UNAVAILABLE')
            if self.pw.pw_thread_loop_start(self.loop) < 0:
                raise DesktopError('CAPTURE_UNAVAILABLE')
            self.started = True
        except Exception:
            self.close()
            raise

    @staticmethod
    def _params(values):
        references = [C.create_string_buffer(value) for value in values]
        return (C.c_void_p * len(values))(*(C.cast(value, C.c_void_p) for value in references)), references

    def _fail(self, code):
        if not self.closed and not self.broken:
            self.broken = True
            self.failed(code)

    def _state(self, _data, old, state, _error):
        if state == -1 or state == 0 and old >= 2:
            self._fail('DISPLAY_STREAM_LOST')

    def _format(self, _data, identity, address):
        if identity != 4 or not address or self.closed:
            return
        try:
            values = _properties(address)
            if values.get(1) != (3, struct.pack('<I', 2)) or values.get(2) != (3, struct.pack('<I', 1)):
                raise DesktopError('CAPTURE_FORMAT_UNSUPPORTED')
            format_kind, raw_format = values[0x20001]
            size_kind, raw_size = values[0x20003]
            if format_kind != 3 or len(raw_format) != 4 or size_kind != 10 or len(raw_size) != 8:
                raise DesktopError('CAPTURE_FORMAT_UNSUPPORTED')
            format, = struct.unpack('<I', raw_format)
            width, height = struct.unpack('<II', raw_size)
            if format not in (7, 8, 11, 12) or not 2 <= width <= 32768 or not 2 <= height <= 32768:
                raise DesktopError('CAPTURE_FORMAT_UNSUPPORTED')
            if self.width and (width, height) != (self.width, self.height):
                raise DesktopError('DISPLAY_GEOMETRY_CHANGED')
            self.format, self.width, self.height = format, width, height
            metas = [_object(0x40005, 6, [(1, _scalar(3, 1)), (2, _scalar(4, 32))]),
                     _object(0x40005, 6, [(1, _scalar(3, 5)), (2, _choice(4, 4,
                         [struct.pack('<i', 28 + 20 + size * size * 4) for size in (64, 1, 512)], 1))])]
            buffers = _object(0x40004, 5, [(6, _choice(4, 4, [struct.pack('<i', (1 << 1) | (1 << 2))], 4))])
            params, references = self._params([buffers, *metas])
            if self.pw.pw_stream_update_params(self.stream, params, len(references)) < 0:
                raise DesktopError('CAPTURE_UNAVAILABLE')
        except (DesktopError, KeyError, ValueError) as error:
            self._fail(error.code if isinstance(error, DesktopError) else 'CAPTURE_FORMAT_UNSUPPORTED')

    def _process(self, _data):
        if self.closed or self.broken:
            return
        while True:
            received = self.pw.pw_stream_dequeue_buffer(self.stream)
            if not received:
                break
            try:
                self._buffer(received.contents.buffer.contents)
            except Exception as error:
                self._fail(error.code if isinstance(error, DesktopError) else 'CAPTURE_UNAVAILABLE')
            finally:
                self.pw.pw_stream_queue_buffer(self.stream, received)

    def _buffer(self, buffer):
        if (not self.width or not 1 <= buffer.n_metas <= 64 or not buffer.metas or
                buffer.n_datas != 1 or not buffer.datas):
            raise DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
        cursor_meta = None
        for index in range(buffer.n_metas):
            meta = buffer.metas[index]
            if meta.type == 1:
                if not meta.data or meta.size < 32:
                    raise DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
                if struct.unpack('<I', C.string_at(meta.data, 4))[0] & 2:
                    return
            if meta.type == 2:
                if not meta.data or meta.size < 16:
                    raise DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
                x, y, width, height = struct.unpack('<iiII', C.string_at(meta.data, 16))
                if width and height and (x, y, width, height) != (0, 0, self.width, self.height):
                    raise DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
            if meta.type == 8:
                if not meta.data or meta.size < 4 or C.string_at(meta.data, 4) != bytes(4):
                    raise DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
            if meta.type == 5:
                if not meta.data or not 28 <= meta.size <= 28 + 20 + 512 * 512 * 4:
                    raise DesktopError('CURSOR_INVALID')
                cursor_meta = C.string_at(meta.data, meta.size)
        if cursor_meta is None:
            raise DesktopError('CURSOR_METADATA_UNAVAILABLE')
        data = buffer.datas[0]
        if not data.chunk:
            raise DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
        chunk = data.chunk.contents
        if chunk.flags & 1:
            return
        self.cursor.update(cursor_meta)
        pixels = self.previous
        if chunk.size:
            row = self.width * 4
            stride = chunk.stride or row
            length = (self.height - 1) * stride + row
            if (data.type not in (1, 2) or not data.data or stride < row or stride > row + 65536 or
                    length > 256 * 1024 * 1024 or chunk.offset + length > data.maxsize or length > chunk.size):
                raise DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
            raw = C.string_at(data.data + chunk.offset, length)
            pixels = raw if stride == row else b''.join(raw[y * stride:y * stride + row] for y in range(self.height))
            if self.format in (7, 11):
                converted = bytearray(pixels)
                converted[0::4], converted[2::4] = pixels[2::4], pixels[0::4]
                pixels = bytes(converted)
        cursor = self.cursor
        shape = (cursor.visible, cursor.width, cursor.height, cursor.hotspot, cursor.pixels)
        placement = (cursor.visible, cursor.position, shape)
        if self.cursor_changed and shape != self.previous_shape:
            from host_desktop_xcapture import cursor_png
            if cursor.visible:
                self.cursor_changed(cursor.width, cursor.height, *cursor.hotspot,
                                    cursor_png(cursor.pixels, cursor.width, cursor.height))
            else:
                self.cursor_changed(1, 1, 0, 0, cursor_png(bytes(4), 1, 1))
        changed = pixels is not None and (self.previous is None or not self.equal(pixels, self.previous))
        if pixels is not None and (changed or not self.cursor_changed and placement != self.previous_cursor):
            if self.cursor_changed:
                image = self.Gst.Buffer.new_wrapped(pixels)
            else:
                from host_desktop_xcapture import CursorPainter
                if not self.painter:
                    self.painter = CursorPainter()
                if shape != self.previous_shape:
                    self.painter.update(cursor.pixels if cursor.visible else b'',
                                        cursor.width if cursor.visible else 0, cursor.height if cursor.visible else 0)
                x, y = (cursor.position[i] - cursor.hotspot[i] for i in range(2))
                image = self.painter.compose(self.Gst, pixels, self.width, self.height,
                    (0, x, y, cursor.width, cursor.height, cursor.visible))
            self.captured(image, self.width, self.height, time.monotonic())
        self.previous, self.previous_cursor, self.previous_shape = pixels, placement, shape

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.loop and self.started:
            self.pw.pw_thread_loop_stop(self.loop)
        if self.stream:
            self.pw.pw_stream_destroy(self.stream)
            self.stream = None
        if self.core:
            self.pw.pw_core_disconnect(self.core)
            self.core = None
        if self.context:
            self.pw.pw_context_destroy(self.context)
            self.context = None
        if self.loop:
            self.pw.pw_thread_loop_destroy(self.loop)
            self.loop = None
        if self.painter:
            self.painter.close()
            self.painter = None
        if self.initialized:
            self.pw.pw_deinit()
            self.initialized = False
        self.previous = None
