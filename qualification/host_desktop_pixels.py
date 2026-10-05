"""Installed Gst/pixman pool lifetime and Wayland pixel correctness; no desktop."""
import argparse
import ctypes as C
import gc
import json
import struct
import sys
import threading
import time

parser = argparse.ArgumentParser()
parser.add_argument('--helper', required=True)
args = parser.parse_args()
sys.path.insert(0, args.helper)
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
from host_desktop_pixels import FramePool
from host_desktop_pipewire import PipeWireCapture, CursorState, _Buffer, _Chunk, _Data, _Meta
from host_desktop_media import equal_pixels
Gst.init(None)
checks = []

pool = FramePool(Gst, 2, 2)
retained = [pool.capture(bytes([i]) * 16, 8) for i in range(4)]
result, entered, done = [], threading.Event(), threading.Event()

def final_frame():
    entered.set()
    result.append(pool.capture(bytes([77]) * 16, 8))
    done.set()

thread = threading.Thread(target=final_frame)
thread.start()
assert entered.wait(1) and not done.wait(.05), 'Pool did not enforce its capacity'
for i, buffer in enumerate(retained):
    assert buffer.extract_dup(0, 16) == bytes([i]) * 16
del buffer
del retained[0]
assert done.wait(2), 'The final changed frame was lost after capacity returned'
thread.join()
assert result[0].extract_dup(0, 16) == bytes([77]) * 16
result.clear()
retained.clear()
gc.collect()

original = pool.capture(bytes([31]) * 16, 8)
shared = Gst.Buffer.new()
assert shared.copy_into(original, Gst.BufferCopyFlags.MEMORY, 0, 16)
assert shared.add_parent_buffer_meta(original) is not None
del original
retained = [pool.capture(bytes([i]) * 16, 8) for i in range(3)]
result.clear(); entered.clear(); done.clear()
thread = threading.Thread(target=final_frame); thread.start()
assert entered.wait(1) and not done.wait(.05), 'Encoder view did not retain its pool lease'
assert shared.extract_dup(0, 16) == bytes([31]) * 16
del shared
assert done.wait(2)
thread.join()
result.clear()
retained.append(pool.capture(bytes(16), 8))
entered.clear(); done.clear()
thread = threading.Thread(target=final_frame); thread.start()
assert entered.wait(1) and not done.wait(.05)
pool.interrupt()
assert done.wait(2), 'Stopping capture did not interrupt pool backpressure'
thread.join()
assert result == [None]
pool.close()
assert retained[0].extract_dup(0, 16) == bytes(16)
retained.clear()
checks.append('bounded capacity, final-frame recovery, parent buffer lease, interruptible stop, retained bytes after close')

for mode in ('separate', 'embedded'):
    for format in (7, 8, 11, 12):
        for stride in (12, 13, 20):
            cap = PipeWireCapture.__new__(PipeWireCapture)
            cap.width, cap.height, cap.format = 3, 2, format
            cap.Gst, cap.equal, cap.cursor = Gst, equal_pixels, CursorState()
            cap.previous = cap.previous_cursor = cap.previous_shape = cap.previous_sequence = None
            cap.pool = cap.painter = None
            cap.pool_lock = threading.Lock()
            cap.closed = cap.started = cap.initialized = False
            cap.loop = cap.stream = cap.core = cap.context = None
            cap.mappings = {}
            frames, shapes = [], []
            cap.captured = lambda b,w,h,t: frames.append(b)
            cap.cursor_changed = (lambda *s: shapes.append(s)) if mode == 'separate' else None
            raw_pixel = bytes([10, 20, 30, 128])
            expected = bytes([30, 20, 10, 128]) if format in (7, 11) else raw_pixel
            raw = C.create_string_buffer(raw_pixel * 3 + bytes(stride - 12) + raw_pixel * 3)
            hidden = C.create_string_buffer(bytes(28))
            visible = C.create_string_buffer(struct.pack('<IIiiiiI', 1, 0, 0, 0, 0, 0, 28) +
                struct.pack('<IIIiI', 12, 1, 1, 4, 20) + bytes([0, 0, 255, 255]))
            metas = (_Meta * 1)(_Meta(5, 28, C.addressof(hidden)))
            chunk = _Chunk(0, len(raw)-1, stride, 0)
            datas = (_Data * 1)(_Data(1, 3, -1, 0, len(raw)-1, C.addressof(raw), C.pointer(chunk)))
            buffer = _Buffer(1, 1, metas, datas)
            cap._buffer(buffer)
            assert frames[-1].extract_dup(0, 24) == expected * 6
            cap._buffer(buffer)
            assert len(frames) == 1, 'Unchanged normalized pixels must not be encoded'
            chunk.size = 0
            metas[0] = _Meta(5, len(visible)-1, C.addressof(visible))
            cap._buffer(buffer)
            if mode == 'separate':
                assert len(frames) == 1 and len(shapes) == 2
            else:
                assert frames[-1].extract_dup(0, 3) == bytes([0, 0, 255])
                assert frames[0].extract_dup(0, 24) == expected * 6
                metas[0] = _Meta(5, 28, C.addressof(hidden))
                cap._buffer(buffer)
                assert frames[-1].extract_dup(0, 24) == expected * 6, 'Embedded cursor contaminated the clean baseline'
            cap.close(); cap.close()
            assert frames[0].extract_dup(0, 24) == expected * 6
            frames.clear()
checks.append('BGRA/BGRx/RGBA/RGBx, packed/padded/unaligned rows, unchanged pixels, separate and embedded cursor, clean baseline')
print(json.dumps({'passed': True, 'checks': checks}))
