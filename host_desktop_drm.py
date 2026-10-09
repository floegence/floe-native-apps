"""Private DRM media worker. Runs without root or Linux capabilities.

One exporter owns DRM device access; the converter owns host GPU-driver calls.
DesktopMedia owns all encoding, credits and refinement. This worker cannot
inject input or decide the physical seat's authority.
"""
import argparse
import array
import errno
import json
import os
import signal
import socket
import struct
import subprocess
import threading
import time
import zlib

from host_desktop_contract import DesktopError
from host_desktop_pixels import FramePool, equal_pixels, mapped_pixels


def capture_error(status):
    if status == -errno.ENODEV:
        from pathlib import Path
        connected = any(p.read_text().strip() == 'connected' for p in Path('/sys/class/drm').glob('card*-*/status'))
        return 'DISPLAY_INACTIVE' if connected else 'DISPLAY_DISCONNECTED'
    if status in (-errno.ENOTSUP, -errno.ENOSYS):
        return 'GPU_SCANOUT_UNSUPPORTED'
    if status in (-errno.EACCES, -errno.EPERM):
        return 'DRM_PERMISSION_UNAVAILABLE'
    return 'DRM_CAPTURE_UNAVAILABLE'


def read_exact(stream, length):
    data = bytearray(length)
    view = memoryview(data)
    offset = 0
    while offset < length:
        count = stream.readinto(view[offset:])
        if not count:
            raise DesktopError('DRM_CAPTURE_UNAVAILABLE')
        offset += count
    return data


def cursor_png(pixels, width, height):
    # DRM cursor pixels are premultiplied ARGB in little-endian memory.
    rows = bytearray((width * 4 + 1) * height)
    for y in range(height):
        for x in range(width):
            src = (y * width + x) * 4
            dst = y * (width * 4 + 1) + 1 + x * 4
            b, g, r, a = pixels[src:src + 4]
            rows[dst:dst + 4] = bytes((min(255, (r * 255 + a // 2) // a) if a else 0,
                min(255, (g * 255 + a // 2) // a) if a else 0,
                min(255, (b * 255 + a // 2) // a) if a else 0, a))
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', width, height, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows, 1)) + chunk(b'IEND', b'')


class DRMCapture:
    def __init__(self, Gst, GLib, exporter, worker, sample, cursor, geometry, suspended, failed):
        self.Gst, self.GLib, self.exporter = Gst, GLib, exporter
        self.sample, self.cursor, self.failed = sample, cursor, failed
        self.geometry = geometry
        self.suspended = suspended
        self.lock = threading.Lock()
        self.export_lock = threading.Lock()
        self.stop = threading.Event()
        self.enabled = False
        self.probing = None
        self.reset = True
        self.epoch = 0
        self.fps = 30
        self.hot_until = self.changed_at = 0
        self.settle_until = 0
        self.width = self.height = 0
        self.previous = self.pool = None
        self.cursor_previous = None
        self.cursor_shape = self.cursor_encoded = None
        self.pending_sample = None
        self.sample_scheduled = False
        self.pending_cursor = None
        self.cursor_scheduled = False
        self.owner, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.exporter.settimeout(3)
        self.owner.settimeout(3)
        self.converter = subprocess.Popen([worker, 'convert', str(child.fileno())], pass_fds=(child.fileno(),),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        child.close()
        self.thread = threading.Thread(target=self.capture, name='floe-drm-capture')
        self.cursor_thread = threading.Thread(target=self.capture_cursor, name='floe-drm-cursor')
        self.thread.start()
        self.cursor_thread.start()

    def configure(self, fps, reset=False, settle=False):
        with self.lock:
            self.epoch += 1
            self.enabled = True
            self.fps = fps
            self.reset |= reset
            self.previous = None
            self.cursor_previous = None
            self.cursor_shape = None
            self.pending_cursor = None
            self.hot_until = time.monotonic() + .5
            self.settle_until = time.monotonic() + 2 if settle else 0

    def probe(self, callback):
        with self.lock:
            self.probing = callback
            self.settle_until = time.monotonic() + 2

    def inactive(self, epoch):
        with self.lock:
            if epoch != self.epoch or not self.enabled or time.monotonic() < self.settle_until:
                return
            self.enabled = False
            self.pending_sample = self.pending_cursor = None
        self.GLib.idle_add(self.suspended, epoch)

    def interacted(self):
        with self.lock:
            self.hot_until = time.monotonic() + .3

    def descriptor(self, reset):
        with self.export_lock:
            self.exporter.send(bytes((2 if reset else 1,)))
            packet, control, flags, _ = self.exporter.recvmsg(80, socket.CMSG_SPACE(32))
        fds = []
        for level, kind, data in control:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                rights = array.array('i')
                rights.frombytes(data[:len(data) - len(data) % rights.itemsize])
                fds.extend(rights)
        try:
            if len(packet) != 80 or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                raise DesktopError('DRM_CAPTURE_UNAVAILABLE')
            status, version = struct.unpack_from('<iI', packet)
            if version != 1:
                raise DesktopError('DRM_CAPTURE_UNAVAILABLE')
            if status:
                raise DesktopError(capture_error(status))
            if len(fds) != 1:
                raise DesktopError('DRM_CAPTURE_UNAVAILABLE')
            self.owner.sendmsg([packet], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', fds))])
        finally:
            for fd in fds:
                os.close(fd)
        header = read_exact(self.converter.stdout, 24)
        status, width, height, stride, format, length = struct.unpack('<iIIIII', header)
        if status:
            raise DesktopError(capture_error(status))
        if not 2 <= width <= 8192 or not 2 <= height <= 8192 or stride < width * 4 or length != stride * height or length > 64 << 20:
            raise DesktopError('DRM_CAPTURE_UNAVAILABLE')
        if format not in (0x34325258, 0x34325241, 0x34324258, 0x34324241):
            raise DesktopError('GPU_SCANOUT_UNSUPPORTED')
        return read_exact(self.converter.stdout, length), width, height, stride, format

    def capture(self):
        try:
            while not self.stop.is_set():
                with self.lock:
                    enabled, probe, epoch = self.enabled, self.probing, self.epoch
                    reset = self.reset
                    fps = self.fps if time.monotonic() < max(self.hot_until, self.changed_at + .5) else 5
                if not enabled and not probe:
                    self.stop.wait(.02)
                    continue
                with self.lock:
                    self.reset = False
                started = time.monotonic()
                try:
                    pixels, width, height, stride, format = self.descriptor(reset)
                except DesktopError as error:
                    if error.code == 'DISPLAY_INACTIVE':
                        with self.lock:
                            settling = time.monotonic() < self.settle_until
                        if settling:
                            self.stop.wait(.05)
                            continue
                        if enabled:
                            self.inactive(epoch)
                            continue
                    if probe and not enabled:
                        with self.lock: self.probing = None
                        self.GLib.idle_add(probe, None, error.code)
                        continue
                    raise
                with self.lock:
                    if epoch != self.epoch:
                        continue
                    if probe:
                        self.probing = None
                        self.GLib.idle_add(probe, (width, height), None)
                    if not enabled:
                        continue
                    if self.width and (width, height) != (self.width, self.height):
                        self.previous = None
                        self.pending_sample = None
                        self.enabled = False
                        retired, self.pool = self.pool, None
                        current = previous = None
                        self.width = self.height = 0
                        self.GLib.idle_add(self.geometry, epoch, width, height, retired)
                        continue
                    self.width, self.height = width, height
                if self.pool is None:
                    self.pool = FramePool(self.Gst, width, height)
                current = self.pool.capture(pixels, stride, swap=format in (0x34324258, 0x34324241))
                if current is None:
                    break
                previous = self.previous
                if previous is not None:
                    with mapped_pixels(self.Gst, previous) as a, mapped_pixels(self.Gst, current) as b:
                        if equal_pixels(a, b):
                            self.stop.wait(max(0, 1 / fps - (time.monotonic() - started)))
                            continue
                with self.lock:
                    if epoch != self.epoch:
                        continue
                    self.previous = current
                    self.changed_at = time.monotonic()
                self.queue_sample(epoch, current, width, height)
                self.stop.wait(max(0, 1 / fps - (time.monotonic() - started)))
        except DesktopError as error:
            if not self.stop.is_set(): self.GLib.idle_add(self.failed, error.code)
        except Exception:
            if not self.stop.is_set(): self.GLib.idle_add(self.failed, 'DRM_CAPTURE_UNAVAILABLE')

    def queue_sample(self, epoch, current, width, height):
        with self.lock:
            self.pending_sample = (epoch, current, width, height)
            if self.sample_scheduled:
                return
            self.sample_scheduled = True
        def publish():
            with self.lock:
                latest, self.pending_sample = self.pending_sample, None
                self.sample_scheduled = False
            if latest and not self.stop.is_set():
                self.sample(*latest)
            return False
        self.GLib.idle_add(publish)

    def capture_cursor(self):
        try:
            while not self.stop.wait(1 / 60):
                with self.lock:
                    if not self.enabled or not self.width:
                        continue
                    epoch, width, height = self.epoch, self.width, self.height
                with self.export_lock:
                    self.exporter.send(b'\x03')
                    packet, control, flags, _ = self.exporter.recvmsg(40, socket.CMSG_SPACE(32), socket.MSG_CMSG_CLOEXEC)
                fds = []
                for level, kind, data in control:
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        rights = array.array('i')
                        rights.frombytes(data[:len(data) - len(data) % rights.itemsize])
                        fds.extend(rights)
                try:
                    if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(packet) != 40:
                        raise DesktopError('CURSOR_INVALID')
                    status, visible, x, y, w, h, hot_x, hot_y, valid, length = struct.unpack_from('<10i', packet)
                    if status:
                        code = capture_error(status)
                        if code == 'DISPLAY_INACTIVE':
                            self.inactive(epoch)
                            continue
                        raise DesktopError(code)
                    if visible not in (0, 1) or valid not in (0, 1):
                        raise DesktopError('CURSOR_INVALID')
                    if visible and (not 1 <= w <= 512 or not 1 <= h <= 512 or length != w * h * 4 or
                            len(fds) != 1 or (valid and (not 0 <= hot_x < w or not 0 <= hot_y < h))):
                        raise DesktopError('CURSOR_INVALID')
                    if not visible and (length or fds):
                        raise DesktopError('CURSOR_INVALID')
                    pixels = os.pread(fds[0], length + 1, 0) if visible else b''
                    if len(pixels) != length:
                        raise DesktopError('CURSOR_INVALID')
                finally:
                    for fd in fds:
                        os.close(fd)
                with self.lock:
                    identity = packet, pixels
                    if epoch != self.epoch or identity == self.cursor_previous:
                        continue
                    self.cursor_previous = identity
                metadata = {'cursor_visible': bool(visible)}
                data = b''
                if visible:
                    metadata['cursor_position'] = {'x': x, 'y': y, 'width': width, 'height': height}
                    shape = (epoch, pixels, w, h, hot_x, hot_y, valid)
                    if shape != self.cursor_shape:
                        self.cursor_shape = shape
                        self.cursor_encoded = cursor_png(pixels, w, h)
                        metadata.update(codec='png', width=w, height=h, hot_x=hot_x if valid else 0,
                            hot_y=hot_y if valid else 0, hotspot_valid=bool(valid))
                        data = self.cursor_encoded
                self.queue_cursor(epoch, metadata, data)
        except DesktopError as error:
            if not self.stop.is_set(): self.GLib.idle_add(self.failed, error.code)
        except Exception:
            if not self.stop.is_set(): self.GLib.idle_add(self.failed, 'CURSOR_INVALID')

    def queue_cursor(self, epoch, metadata, data):
        with self.lock:
            pending = self.pending_cursor
            if pending and pending[0] == epoch and pending[2] and not data and metadata['cursor_visible']:
                pending[1]['cursor_position'] = metadata['cursor_position']
            else:
                self.pending_cursor = (epoch, metadata, data)
            if self.cursor_scheduled:
                return
            self.cursor_scheduled = True
        def publish():
            with self.lock:
                latest, self.pending_cursor = self.pending_cursor, None
                self.cursor_scheduled = False
            if latest and not self.stop.is_set():
                self.cursor(*latest)
            return False
        self.GLib.idle_add(publish)

    def close(self):
        self.stop.set()
        if self.pool: self.pool.interrupt()
        self.exporter.close()
        self.owner.close()
        self.converter.terminate()
        try: self.converter.wait(timeout=2)
        except subprocess.TimeoutExpired: self.converter.kill(); self.converter.wait()
        self.thread.join(timeout=4)
        self.cursor_thread.join(timeout=4)
        self.converter.stdout.close()
        self.previous = self.pending_sample = None
        if self.pool: self.pool.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', required=True)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise SystemExit(77)
    # The service launcher clears capabilities before Python or GI loads. Verify
    # again here so a direct, incorrectly privileged launch fails closed.
    with open('/proc/self/status', encoding='ascii') as status:
        for line in status:
            if line.startswith(('CapEff:', 'CapPrm:', 'CapInh:', 'CapAmb:')) and int(line.split()[1], 16):
                raise SystemExit(77)
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst, GLib
    from host_desktop_media import DesktopMedia, select_encoder
    Gst.init(None)
    encoder = select_encoder(Gst)
    loop = GLib.MainLoop()
    write_lock = threading.Lock()
    state = {'media': None, 'epoch': 0, 'cursor': None, 'failed': False}
    def emit(metadata, payload=b''):
        metadata.update(version=1, bytes=len(payload))
        header = json.dumps(metadata, separators=(',', ':')).encode()
        packet = struct.pack('!I', len(header)) + header + payload
        with write_lock:
            view = memoryview(packet)
            while view:
                count = os.write(1, view)
                if count <= 0: raise BrokenPipeError()
                view = view[count:]
    def failed(code):
        if not state['failed']:
            state['failed'] = True
            emit({'type': 'error', 'code': code})
            loop.quit()
        return False
    def sample(epoch, buffer, width, height):
        media = state['media']
        if media and epoch == state['epoch']:
            media._changed(buffer, width, height, time.monotonic())
        return False
    def cursor(epoch, metadata, data):
        media = state['media']
        if media and epoch == state['epoch']:
            presentation = 'separate' if metadata['cursor_visible'] else 'embedded'
            if media.cursor_presentation != presentation:
                media.cursor_presentation = presentation
                with media.lock:
                    latest = media.latest
                if latest:
                    media._changed(*latest, time.monotonic())
            state['cursor'] = metadata, data
            emit({'type': 'cursor', 'generation': media.generation, **metadata}, data)
        return False
    def geometry(epoch, width, height, retired):
        media = state['media']
        if media and epoch == state['epoch']:
            media.close()
            state['media'] = None
            emit({'type': 'displays', 'generation': media.generation, 'displays': [
                {'id': 'physical-0', 'name': 'Physical display', 'width': width, 'height': height, 'scale': 1, 'primary': True}]})
        if retired: retired.close()
        return False
    def suspended(epoch):
        media = state['media']
        if media and epoch == state['epoch']:
            emit({'type': 'error', 'generation': media.generation, 'code': 'DISPLAY_INACTIVE'})
        return False
    capture = DRMCapture(Gst, GLib, socket.socket(fileno=3), args.worker, sample, cursor, geometry, suspended, failed)
    def dispatch(command):
        if state['failed']: return False
        method = command.get('method')
        media = state['media']
        if method == 'probe':
            def probed(size, code):
                cap = {'backend': 'linux-drm-kms', 'state': 'unavailable' if code else 'ready', 'screen': not code,
                    'input': not code, 'unattended': True, 'unlock': not code, 'encoder': encoder[0], 'displays': []}
                if code: cap['reason'] = code
                else: cap['displays'] = [{'id': 'physical-0', 'name': 'Physical display', 'width': size[0], 'height': size[1], 'scale': 1, 'primary': True}]
                emit({'type': 'capabilities', 'id': command['id'], 'capabilities': cap})
                return False
            capture.probe(probed)
        elif method == 'configure':
            if media: media.close()
            picture = dict(command['picture'], audio=False)
            media = DesktopMedia(Gst, GLib, command['generation'], picture, encoder, emit, failed)
            media.pixel_format = 'BGRx'
            state['media'] = media
            state['cursor'] = None
            capture.configure(picture['frame_rate'], command.get('reset', False), command.get('settle', False))
            state['epoch'] = capture.epoch
        elif method == 'ack' and media and command['generation'] == media.generation:
            media.acknowledge(command['frame_id'])
        elif method == 'interacted' and media and command['generation'] == media.generation:
            media.interacted()
            capture.interacted()
        elif method in ('ack', 'interacted') and (not media or command['generation'] != media.generation):
            pass
        else:
            return failed('MEDIA_PROTOCOL_INVALID')
        return False
    def control():
        try:
            with os.fdopen(4, 'rb', buffering=0) as source:
                while True:
                    length = struct.unpack('!I', read_exact(source, 4))[0]
                    if not 0 < length <= 4096: break
                    command = json.loads(read_exact(source, length))
                    GLib.idle_add(dispatch, command)
        except Exception:
            pass
        GLib.idle_add(loop.quit)
    reader = threading.Thread(target=control, name='floe-drm-control', daemon=True)
    reader.start()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: loop.quit())
    try: loop.run()
    finally:
        if state['media']: state['media'].close()
        capture.close()


if __name__ == '__main__':
    main()
