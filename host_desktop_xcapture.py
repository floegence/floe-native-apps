"""Thread-confined XCB acquisition from an authenticated local X11 root.

MIT-SHM owns a private anonymous mapping. Requests never grab the X server;
checked replies report geometry/connection failures without a global Xlib error
handler. Cursor pixels are refreshed only after XFixes CursorNotify.
"""
from contextlib import contextmanager
import ctypes as C
import mmap
import os
import re
import select
import struct
import threading
import time
import zlib

from host_desktop_contract import DesktopError


class Cookie(C.Structure):
    _fields_ = [('sequence', C.c_uint)]


class Iterator(C.Structure):
    _fields_ = [('data', C.c_void_p), ('remaining', C.c_int), ('index', C.c_int)]


def bind(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype, function.argtypes = result, arguments
    return function


class Connection:
    """Public libxcb ABI; no process-wide Xlib state or callbacks."""
    def __init__(self, display, stopped):
        self.stopped = stopped
        self.x = C.CDLL('libxcb.so.1')
        self.shm = C.CDLL('libxcb-shm.so.0')
        self.fixes = C.CDLL('libxcb-xfixes.so.0')
        self.libc = C.CDLL(None)
        pointer, uint, byte = C.c_void_p, C.c_uint32, C.c_uint8
        bind(self.libc, 'free', None, pointer)
        bind(self.x, 'xcb_connect', pointer, C.c_char_p, C.POINTER(C.c_int))
        bind(self.x, 'xcb_disconnect', None, pointer)
        bind(self.x, 'xcb_connection_has_error', C.c_int, pointer)
        bind(self.x, 'xcb_get_file_descriptor', C.c_int, pointer)
        bind(self.x, 'xcb_flush', C.c_int, pointer)
        bind(self.x, 'xcb_generate_id', uint, pointer)
        bind(self.x, 'xcb_get_setup', pointer, pointer)
        bind(self.x, 'xcb_setup_roots_iterator', Iterator, pointer)
        bind(self.x, 'xcb_setup_pixmap_formats_iterator', Iterator, pointer)
        bind(self.x, 'xcb_screen_next', None, C.POINTER(Iterator))
        bind(self.x, 'xcb_screen_allowed_depths_iterator', Iterator, pointer)
        bind(self.x, 'xcb_depth_next', None, C.POINTER(Iterator))
        bind(self.x, 'xcb_depth_visuals_iterator', Iterator, pointer)
        bind(self.x, 'xcb_query_extension', Cookie, pointer, C.c_uint16, C.c_char_p)
        bind(self.x, 'xcb_query_pointer', Cookie, pointer, uint)
        bind(self.x, 'xcb_poll_for_event', pointer, pointer)
        bind(self.x, 'xcb_poll_for_reply', C.c_int, pointer, C.c_uint,
             C.POINTER(pointer), C.POINTER(pointer))
        bind(self.shm, 'xcb_shm_query_version', Cookie, pointer)
        bind(self.shm, 'xcb_shm_create_segment', Cookie, pointer, uint, uint, byte)
        bind(self.shm, 'xcb_shm_create_segment_reply_fds', C.POINTER(C.c_int), pointer, pointer)
        bind(self.shm, 'xcb_shm_get_image', Cookie, pointer, uint, C.c_int16, C.c_int16,
             C.c_uint16, C.c_uint16, uint, byte, uint, uint)
        bind(self.fixes, 'xcb_xfixes_query_version', Cookie, pointer, uint, uint)
        bind(self.fixes, 'xcb_xfixes_select_cursor_input', Cookie, pointer, uint, uint)
        bind(self.fixes, 'xcb_xfixes_get_cursor_image', Cookie, pointer)
        self.screen = C.c_int()
        self.handle = self.x.xcb_connect(display.encode(), C.byref(self.screen))
        if not self.handle or self.x.xcb_connection_has_error(self.handle):
            self.close()
            raise DesktopError('X11_DISPLAY_UNAVAILABLE')

    @contextmanager
    def reply(self, cookie, protocol_error='CAPTURE_UNAVAILABLE'):
        result, error = C.c_void_p(), C.c_void_p()
        deadline = time.monotonic() + 2
        self.x.xcb_flush(self.handle)
        try:
            while not self.x.xcb_poll_for_reply(self.handle, cookie.sequence, C.byref(result), C.byref(error)):
                if (self.stopped.is_set() or self.x.xcb_connection_has_error(self.handle)
                        or time.monotonic() >= deadline):
                    raise DesktopError('CAPTURE_UNAVAILABLE')
                select.select([self.x.xcb_get_file_descriptor(self.handle)], [], [], .02)
            if error:
                raise DesktopError(protocol_error)
            if not result:
                raise DesktopError('CAPTURE_UNAVAILABLE')
            yield result
        finally:
            self.libc.free(error)
            self.libc.free(result)

    def extension(self, name):
        with self.reply(self.x.xcb_query_extension(self.handle, len(name), name)) as reply:
            present, _opcode, event, _error = struct.unpack_from('=4B', C.string_at(reply, 12), 8)
            if not present:
                raise DesktopError('X11_EXTENSIONS_UNAVAILABLE')
            return event

    def close(self):
        if self.handle:
            self.x.xcb_disconnect(self.handle)
            self.handle = None


class CursorPainter:
    def __init__(self):
        self.library = C.CDLL('libcairo.so.2')
        p, i, d = C.c_void_p, C.c_int, C.c_double
        bind(self.library, 'cairo_image_surface_create_for_data', p, p, i, i, i, i)
        bind(self.library, 'cairo_surface_status', i, p)
        bind(self.library, 'cairo_surface_destroy', None, p)
        bind(self.library, 'cairo_surface_flush', None, p)
        bind(self.library, 'cairo_create', p, p)
        bind(self.library, 'cairo_status', i, p)
        bind(self.library, 'cairo_destroy', None, p)
        bind(self.library, 'cairo_rectangle', None, p, d, d, d, d)
        bind(self.library, 'cairo_clip', None, p)
        bind(self.library, 'cairo_set_source_surface', None, p, p, d, d)
        bind(self.library, 'cairo_paint', None, p)
        self.surface = None
        self.pixels = None

    def image(self, pixels, format, width, height):
        surface = self.library.cairo_image_surface_create_for_data(
            (C.c_ubyte * len(pixels)).from_buffer(pixels), format, width, height, width * 4)
        if not surface or self.library.cairo_surface_status(surface):
            if surface:
                self.library.cairo_surface_destroy(surface)
            raise DesktopError('CAPTURE_UNAVAILABLE')
        return surface

    def update(self, pixels, width, height):
        self.close()
        if not width or not height:
            return
        # XFixes and converted PipeWire cursor pixels use premultiplied BGRA,
        # matching Cairo ARGB32 on the supported little-endian hosts.
        self.pixels = bytearray(pixels)
        self.surface = self.image(self.pixels, 0, width, height)

    def compose(self, Gst, pixels, width, height, cursor):
        buffer = Gst.Buffer.new_allocate(None, len(pixels), None)
        ok, mapped = buffer.map(Gst.MapFlags.WRITE)
        if not ok:
            raise DesktopError('CAPTURE_UNAVAILABLE')
        target = context = None
        try:
            mapped.data[:] = pixels
            # RGB24 on these explicitly validated little-endian desktop pixels is
            # BGRx. Never describe its unused byte as meaningful alpha.
            target = self.image(mapped.data, 1, width, height)
            context = self.library.cairo_create(target)
            _, x, y, w, h, visible = cursor
            if visible:
                self.library.cairo_rectangle(context, x, y, w, h)
                self.library.cairo_clip(context)
                self.library.cairo_set_source_surface(context, self.surface, x, y)
                self.library.cairo_paint(context)
            self.library.cairo_surface_flush(target)
            if self.library.cairo_status(context):
                raise DesktopError('CAPTURE_UNAVAILABLE')
        finally:
            if context:
                self.library.cairo_destroy(context)
            if target:
                self.library.cairo_surface_destroy(target)
            buffer.unmap(mapped)
        return buffer

    def close(self):
        if self.surface:
            self.library.cairo_surface_destroy(self.surface)
            self.surface = None
        self.pixels = None


class Surface:
    def __init__(self, display, rectangle, stopped):
        self.connection = self.mapping = self.painter = None
        self.cursor = None
        self.cursor_changed = True
        if not re.fullmatch(r':[0-9]+(?:\.[0-9]+)?', display):
            raise DesktopError('X11_DISPLAY_INVALID')
        self.x, self.y, self.width, self.height = rectangle
        if not (0 <= self.x <= 32767 and 0 <= self.y <= 32767 and
                2 <= self.width <= 8192 and 2 <= self.height <= 8192 and
                self.width * self.height <= 16 * 1024 * 1024):
            raise DesktopError('DISPLAY_SIZE_UNSUPPORTED')
        try:
            self.connection = Connection(display, stopped)
            self._initialize()
        except BaseException:
            self.close()
            raise

    def _initialize(self):
        c = self.connection
        setup = c.x.xcb_get_setup(c.handle)
        if C.string_at(setup, 40)[30] != 0:
            raise DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
        screens = c.x.xcb_setup_roots_iterator(setup)
        for _ in range(c.screen.value):
            if screens.remaining <= 1:
                raise DesktopError('X11_DISPLAY_UNAVAILABLE')
            c.x.xcb_screen_next(C.byref(screens))
        if not screens.remaining:
            raise DesktopError('X11_DISPLAY_UNAVAILABLE')
        screen = C.string_at(screens.data, 40)
        self.root = struct.unpack_from('=I', screen)[0]
        visual = struct.unpack_from('=I', screen, 32)[0]
        if screen[38] != 24:
            raise DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
        formats = c.x.xcb_setup_pixmap_formats_iterator(setup)
        if not any(C.string_at(formats.data + index * 8, 3) == bytes([24, 32, 32])
                   for index in range(formats.remaining)):
            raise DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
        depths = c.x.xcb_screen_allowed_depths_iterator(screens.data)
        matched = False
        while depths.remaining:
            visuals = c.x.xcb_depth_visuals_iterator(depths.data)
            for index in range(visuals.remaining):
                value = C.string_at(visuals.data + index * 24, 24)
                if struct.unpack_from('=I', value)[0] == visual:
                    matched = value[4] == 4 and struct.unpack_from('=III', value, 8) == (0xff0000, 0xff00, 0xff)
            c.x.xcb_depth_next(C.byref(depths))
        if not matched:
            raise DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
        c.extension(b'MIT-SHM')
        self.cursor_event = c.extension(b'XFIXES') + 1
        with c.reply(c.shm.xcb_shm_query_version(c.handle)) as reply:
            if struct.unpack_from('=HH', C.string_at(reply, 12), 8) < (1, 2):
                raise DesktopError('X11_EXTENSIONS_UNAVAILABLE')
        with c.reply(c.fixes.xcb_xfixes_query_version(c.handle, 5, 0)):
            pass
        c.fixes.xcb_xfixes_select_cursor_input(c.handle, self.root, 1)
        self.segment = c.x.xcb_generate_id(c.handle)
        self.size = self.width * self.height * 4
        with c.reply(c.shm.xcb_shm_create_segment(c.handle, self.segment, self.size, 0)) as reply:
            count = C.string_at(reply, 2)[1]
            descriptors = c.shm.xcb_shm_create_segment_reply_fds(c.handle, reply)
            try:
                if count != 1:
                    raise DesktopError('CAPTURE_UNAVAILABLE')
                self.mapping = mmap.mmap(descriptors[0], self.size, flags=mmap.MAP_SHARED,
                                         prot=mmap.PROT_READ | mmap.PROT_WRITE)
            finally:
                for index in range(count):
                    os.close(descriptors[index])
        self.painter = CursorPainter()

    def read(self):
        c = self.connection
        while True:
            event = c.x.xcb_poll_for_event(c.handle)
            if not event:
                break
            try:
                kind = C.string_at(event, 1)[0] & 0x7f
                if kind == 0:
                    raise DesktopError('CAPTURE_UNAVAILABLE')
                if kind == self.cursor_event:
                    self.cursor_changed = True
            finally:
                c.libc.free(event)
        # Batch the independent image and pointer requests into one socket flush.
        image = c.shm.xcb_shm_get_image(c.handle, self.root, self.x, self.y,
            self.width, self.height, 0xffffffff, 2, self.segment, 0)
        pointer = c.x.xcb_query_pointer(c.handle, self.root)
        cursor = c.fixes.xcb_xfixes_get_cursor_image(c.handle) if self.cursor_changed else None
        with c.reply(image, protocol_error='DISPLAY_GEOMETRY_CHANGED') as reply:
            data = C.string_at(reply, 16)
            if data[1] != 24 or struct.unpack_from('=I', data, 12)[0] != self.size:
                raise DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
        if cursor:
            with c.reply(cursor) as reply:
                data = C.string_at(reply, 32)
                width, height, hot_x, hot_y, serial = struct.unpack_from('=HHHHI', data, 12)
                if not (width <= 512 and height <= 512 and
                        struct.unpack_from('=I', data, 4)[0] == width * height):
                    raise DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
                self.painter.update(C.string_at(reply.value + 32, width * height * 4), width, height)
                self.cursor = (serial, hot_x, hot_y, width, height)
                self.cursor_changed = False
        with c.reply(pointer) as reply:
            data = C.string_at(reply, 28)
            x, y = struct.unpack_from('=hh', data, 16)
            serial, hot_x, hot_y, width, height = self.cursor
            position = (serial, x - hot_x - self.x, y - hot_y - self.y, width, height,
                        bool(data[1]) and width > 0 and height > 0)
        return memoryview(self.mapping), position

    def close(self):
        if self.painter:
            self.painter.close()
            self.painter = None
        # Closing the private XCB connection retires every server-side resource,
        # including a segment created before any subsequent setup failure.
        if self.connection:
            self.connection.close()
            self.connection = None
        if self.mapping:
            self.mapping.close()
            self.mapping = None


def cursor_png(pixels, width, height):
    # Capture cursors are premultiplied BGRA; PNG requires straight RGBA.
    rgba = bytearray(len(pixels))
    for offset in range(0, len(pixels), 4):
        b, g, r, a = pixels[offset:offset + 4]
        rgba[offset:offset + 4] = bytes((min(255, r * 255 // a) if a else 0,
            min(255, g * 255 // a) if a else 0, min(255, b * 255 // a) if a else 0, a))
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    rows = b''.join(b'\0' + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


class X11Capture:
    def __init__(self, Gst, display, rectangle, fps, captured, failed, equal_pixels, cursor_changed=None):
        self.stopped = threading.Event()
        def run():
            source = view = None
            try:
                source = Surface(display, rectangle, self.stopped)
                prior = prior_cursor = prior_shape = None
                next_sample = time.monotonic()
                while not self.stopped.is_set():
                    if self.stopped.wait(max(0, next_sample - time.monotonic())):
                        break
                    # Oversample asynchronous compositor commits. Only changed
                    # pixels reach the encoder, which retains its negotiated FPS.
                    next_sample = max(next_sample, time.monotonic()) + 1 / min(120, fps * 2)
                    view, cursor = source.read()
                    timestamp = time.monotonic()
                    changed = prior is None or not equal_pixels(view, prior)
                    if cursor_changed:
                        serial, hot_x, hot_y, cursor_width, cursor_height = source.cursor
                        shape = (serial, cursor[-1])
                        if shape != prior_shape:
                            visible = cursor[-1] and bool(source.painter.pixels)
                            cursor_changed(cursor_width if visible else 1, cursor_height if visible else 1,
                                min(hot_x, cursor_width - 1) if visible else 0,
                                min(hot_y, cursor_height - 1) if visible else 0,
                                cursor_png(source.painter.pixels, cursor_width, cursor_height) if visible else
                                cursor_png(bytes(4), 1, 1))
                            prior_shape = shape
                    if changed or (not cursor_changed and cursor != prior_cursor):
                        if changed:
                            prior = bytes(view)
                        if cursor_changed:
                            buffer = Gst.Buffer.new_wrapped(prior)
                        else:
                            buffer = source.painter.compose(Gst, prior, source.width, source.height, cursor)
                        prior_cursor = cursor
                        if not self.stopped.is_set():
                            captured(buffer, source.width, source.height, timestamp)
                    view.release()
                    view = None
            except Exception as error:
                if not self.stopped.is_set():
                    failed(error.code if isinstance(error, DesktopError) else 'CAPTURE_UNAVAILABLE')
            finally:
                if view is not None:
                    view.release()
                if source:
                    source.close()
        self.thread = threading.Thread(target=run, name='floe-desktop-x11', daemon=True)
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=3)
