"""Private glibc/NVIDIA boundary; no host media tool or application environment.

The worker receives one packed BGRA frame at a time. Its inherited pipes contain
no capture permission: the current-generation media owner supplies every pixel.
"""
import os
from pathlib import Path
import select
import struct
import subprocess
import time

from host_desktop_contract import DesktopError

WORKER = Path(__file__).resolve().with_name('desktop-nvenc')
LIMIT = 64 << 20


class NVEncoder:
    def __init__(self, width, height, fps, bitrate, timeout=3):
        self.process = None
        self.sequence = 0
        self.size = width * height * 4
        if (not 2 <= width <= 8192 or not 2 <= height <= 8192 or width % 2 or height % 2 or
                self.size > LIMIT or fps not in (15, 30, 60) or not 1000000 <= bitrate <= 100000000):
            raise DesktopError('VIDEO_ENCODER_UNAVAILABLE')
        self.timeout = timeout
        # The system loader must resolve only its own ABI and installed driver.
        # No shell, PATH lookup, user-selected executable, LD_* or private musl
        # environment crosses this boundary. The worker has no network endpoint.
        try:
            self.process = subprocess.Popen([str(WORKER)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
                env={'LANG': 'C'}, close_fds=True)
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
        if len(pixels) != self.size or self.sequence >= 0xffffffff:
            raise DesktopError('VIDEO_ENCODER_FAILED')
        self.sequence += 1
        deadline = time.monotonic() + self.timeout
        try:
            self._write(struct.pack('!II', self.size, self.sequence), deadline)
            self._write(pixels, deadline)
            size, sequence = struct.unpack('!II', self._read(8, deadline))
            if not 0 < size <= LIMIT or sequence != self.sequence:
                raise DesktopError('VIDEO_ENCODER_FAILED')
            return self._read(size, deadline)
        except (OSError, ValueError, DesktopError):
            self.close()
            raise DesktopError('VIDEO_ENCODER_FAILED') from None

    def close(self):
        process, self.process = self.process, None
        if process:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            process.stdin.close()
            process.stdout.close()


def available():
    """Synthetic encode, not device enumeration, proves the driver API works."""
    encoder = None
    try:
        encoder = NVEncoder(256, 256, 60, 16000000)
        return bool(encoder.encode(bytes(256 * 256 * 4)))
    except (OSError, DesktopError):
        return False
    finally:
        if encoder:
            encoder.close()
