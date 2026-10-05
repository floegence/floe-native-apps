"""Bounded native pixel ownership for physical desktop capture."""
from contextlib import contextmanager
import ctypes

from host_desktop_contract import DesktopError


class _BufferView(ctypes.Structure):
    # CPython's stable Py_buffer ABI. The pinned interpreter owns both exports;
    # no guessed GstBuffer address or native object layout is accessed.
    _fields_ = [('buf', ctypes.c_void_p), ('obj', ctypes.c_void_p),
                ('len', ctypes.c_ssize_t), ('itemsize', ctypes.c_ssize_t),
                ('readonly', ctypes.c_int), ('ndim', ctypes.c_int),
                ('format', ctypes.c_void_p), ('shape', ctypes.c_void_p),
                ('strides', ctypes.c_void_p), ('suboffsets', ctypes.c_void_p), ('internal', ctypes.c_void_p)]


_get_buffer = ctypes.pythonapi.PyObject_GetBuffer
_get_buffer.argtypes = [ctypes.py_object, ctypes.POINTER(_BufferView), ctypes.c_int]
_get_buffer.restype = ctypes.c_int
_release_buffer = ctypes.pythonapi.PyBuffer_Release
_release_buffer.argtypes = [ctypes.POINTER(_BufferView)]
_release_buffer.restype = None
_memcmp = ctypes.CDLL(None).memcmp
_memcmp.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
_memcmp.restype = ctypes.c_int


def equal_pixels(left, right):
    """Compare mapped bytes without copies and release the GIL during memcmp."""
    a, b = _BufferView(), _BufferView()
    _get_buffer(left, ctypes.byref(a), 0)
    try:
        _get_buffer(right, ctypes.byref(b), 0)
        try:
            if a.len != b.len:
                return False
            # Discover distributed motion before walking a large unchanged
            # prefix. These probes only prove inequality; equality still requires
            # every byte, including changes outside all probe spans.
            if a.len >= 8192:
                for index in range(32):
                    offset = index * (a.len - 128) // 31
                    if _memcmp(a.buf + offset, b.buf + offset, 128):
                        return False
            return not a.len or _memcmp(a.buf, b.buf, a.len) == 0
        finally:
            _release_buffer(ctypes.byref(b))
    finally:
        _release_buffer(ctypes.byref(a))


_memmove = ctypes.CDLL(None).memmove
_memmove.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
_memmove.restype = ctypes.c_void_p


@contextmanager
def mapped_pixels(Gst, buffer, write=False):
    ok, mapped = buffer.map(Gst.MapFlags.WRITE if write else Gst.MapFlags.READ)
    if not ok:
        raise DesktopError('CAPTURE_UNAVAILABLE')
    try:
        yield mapped.data
    finally:
        buffer.unmap(mapped)


class PixelCopy:
    """Copy packed or padded rows outside the GIL, with optional native swizzle."""
    def __init__(self):
        self.pixman = None

    def _swizzle(self, target, source, width, height, stride):
        if self.pixman is None:
            library = ctypes.CDLL('libpixman-1.so.0')
            p, i = ctypes.c_void_p, ctypes.c_int
            for name, result, arguments in (
                ('pixman_image_create_bits', p, [ctypes.c_uint32, i, i, p, i]),
                ('pixman_image_unref', i, [p]),
                ('pixman_image_composite32', None, [i, p, p, p] + [i] * 8),
            ):
                function = getattr(library, name)
                function.restype, function.argtypes = result, arguments
            self.pixman = library
        # Public pixman format codes: 32-bit ABGR -> ARGB on little-endian
        # hosts. SRC preserves the alpha byte; the video encoder treats it as x.
        src = self.pixman.pixman_image_create_bits(0x20038888, width, height, source, stride)
        dst = self.pixman.pixman_image_create_bits(0x20028888, width, height, target, width * 4)
        try:
            if not src or not dst:
                raise DesktopError('CAPTURE_UNAVAILABLE')
            self.pixman.pixman_image_composite32(1, src, None, dst, 0, 0, 0, 0, 0, 0, width, height)
        finally:
            if dst:
                self.pixman.pixman_image_unref(dst)
            if src:
                self.pixman.pixman_image_unref(src)

    def copy(self, target, source, width, height, stride, swap=False):
        a, b = _BufferView(), _BufferView()
        _get_buffer(target, ctypes.byref(a), 1)
        try:
            _get_buffer(source, ctypes.byref(b), 0)
            try:
                row = width * 4
                if (width < 1 or height < 1 or stride < row or a.len != row * height or
                        b.len < (height - 1) * stride + row or swap and a.buf % 4):
                    raise DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
                if swap:
                    if stride % 4 == 0 and b.buf % 4 == 0:
                        self._swizzle(a.buf, b.buf, width, height, stride)
                    else:
                        # Pixman requires aligned rows. Pack into the owned
                        # destination first, then swizzle there in place.
                        for y in range(height):
                            _memmove(a.buf + y * row, b.buf + y * stride, row)
                        self._swizzle(a.buf, a.buf, width, height, row)
                elif stride == row:
                    _memmove(a.buf, b.buf, a.len)
                else:
                    for y in range(height):
                        _memmove(a.buf + y * row, b.buf + y * stride, row)
            finally:
                _release_buffer(ctypes.byref(b))
        finally:
            _release_buffer(ctypes.byref(a))


class FramePool:
    """Capture-thread backpressure, interruptible before joining that thread."""
    def __init__(self, Gst, width, height, embedded_cursor=False):
        self.Gst, self.width, self.height = Gst, width, height
        self.copy = PixelCopy()
        self.pool = Gst.BufferPool.new()
        config = self.pool.get_config()
        caps = Gst.Caps.from_string(f'video/x-raw,format=BGRx,width={width},height={height},framerate=0/1')
        # Separate cursor: previous/latest, encoding, refinement, new capture.
        # Embedded cursor additionally retains a clean baseline and composition.
        Gst.BufferPool.config_set_params(config, caps, width * height * 4, 0, 6 if embedded_cursor else 4)
        if not self.pool.set_config(config) or not self.pool.set_active(True):
            raise DesktopError('CAPTURE_UNAVAILABLE')

    def capture(self, pixels, stride, swap=False):
        # Waiting releases the interpreter lock. Do not drop the final changed
        # frame: the PipeWire owner retains its buffer until capacity returns.
        flow, buffer = self.pool.acquire_buffer(None)
        if flow == self.Gst.FlowReturn.FLUSHING:
            return None
        if flow != self.Gst.FlowReturn.OK:
            raise DesktopError('CAPTURE_UNAVAILABLE')
        with mapped_pixels(self.Gst, buffer, write=True) as target:
            self.copy.copy(target, pixels, self.width, self.height, stride, swap)
        return buffer

    def interrupt(self):
        self.pool.set_flushing(True)

    def close(self):
        self.interrupt()
        self.pool.set_active(False)
