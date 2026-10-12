"""Private glibc hardware-encoder boundary; no host media tool or application environment.

The worker receives one packed frame at a time in a sealed-size shared slot.
Only completion transfers slot ownership back to the parent. Inherited pipes contain
no capture permission: the current-generation media owner supplies every pixel.
"""
import os
from pathlib import Path
import select
import struct
import subprocess
import time

from host_desktop_contract import DesktopError
from host_desktop_pixels import native_pixels

WORKERS = {'nvidia-nvenc': ('desktop-nvenc', 'BGRA'), 'rockchip-mpp': ('desktop-mpp', 'NV12')}
LIMIT = 64 << 20


class NativeEncoder:
    def __init__(self, width, height, fps, bitrate, backend='nvidia-nvenc', timeout=3):
        self.process = None
        self.pixels = None
        self.sequence = 0
        if backend not in WORKERS:
            raise DesktopError('VIDEO_ENCODER_UNAVAILABLE')
        worker, self.input_format = WORKERS[backend]
        self.size = width * height * 4 if self.input_format == 'BGRA' else width * height * 3 // 2
        if (not 2 <= width <= 8192 or not 2 <= height <= 8192 or width % 2 or height % 2 or
                width * height * 4 > LIMIT or fps not in (15, 30, 60) or not 1000000 <= bitrate <= 100000000):
            raise DesktopError('VIDEO_ENCODER_UNAVAILABLE')
        self.timeout = timeout
        # The system loader must resolve only its own ABI and installed driver.
        # No shell, PATH lookup, user-selected executable, LD_* or private musl
        # environment crosses this boundary. The worker has no network endpoint.
        try:
            with native_pixels(self.size) as (pixels_fd, self.pixels):
                self.process = subprocess.Popen([str(Path(__file__).resolve().with_name(worker)), '--pixels', str(pixels_fd)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
                    env={'LANG': 'C'}, pass_fds=(pixels_fd,), close_fds=True)
            for pipe in (self.process.stdin, self.process.stdout):
                os.set_blocking(pipe.fileno(), False)
            self._write(struct.pack('!5I', 0x46445631, width, height, fps, bitrate), time.monotonic() + timeout)
        except (OSError, DesktopError):
            self.close()
            raise DesktopError('VIDEO_ENCODER_UNAVAILABLE') from None

    def _wait(self, read, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self.process:
            raise DesktopError('VIDEO_ENCODER_FAILED')
        fd = (self.process.stdout if read else self.process.stdin).fileno()
        ready = select.select([fd] if read else [], [] if read else [fd], [], remaining)
        if not ready[0 if read else 1]:
            raise DesktopError('VIDEO_ENCODER_FAILED')
        return fd

    def _write(self, data, deadline):
        view = memoryview(data)
        while view:
            count = os.write(self._wait(False, deadline), view[:65536])
            if not count:
                raise DesktopError('VIDEO_ENCODER_FAILED')
            view = view[count:]

    def _read(self, size, deadline):
        parts = bytearray()
        while len(parts) < size:
            data = os.read(self._wait(True, deadline), min(65536, size - len(parts)))
            if not data:
                raise DesktopError('VIDEO_ENCODER_FAILED')
            parts.extend(data)
        return bytes(parts)

    def encode(self, pixels):
        try:
            if self.pixels is None or len(pixels) != self.size or self.sequence >= 0xffffffff:
                raise DesktopError('VIDEO_ENCODER_FAILED')
            self.sequence += 1
            deadline = time.monotonic() + self.timeout
            self.pixels[:] = pixels
            self._write(struct.pack('!II', self.size, self.sequence), deadline)
            size, sequence = struct.unpack('!II', self._read(8, deadline))
            if not 0 < size <= LIMIT or sequence != self.sequence:
                raise DesktopError('VIDEO_ENCODER_FAILED')
            return self._read(size, deadline)
        except (OSError, ValueError, DesktopError):
            self.close()
            raise DesktopError('VIDEO_ENCODER_FAILED') from None

    def close(self):
        pixels, self.pixels = getattr(self, 'pixels', None), None
        process, self.process = self.process, None
        if process:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            process.stdin.close()
            process.stdout.close()
        if pixels is not None:
            pixels.close()


def available(backend='nvidia-nvenc'):
    """Synthetic encode, not device enumeration, proves the driver API works."""
    encoder = None
    try:
        encoder = NativeEncoder(256, 256, 60, 16000000, backend)
        return bool(encoder.encode(bytes(encoder.size)))
    except (OSError, DesktopError):
        return False
    finally:
        if encoder:
            encoder.close()
