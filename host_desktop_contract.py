"""Native target authority and bounded physical-desktop frame delivery.

Product authorization belongs to the consumer. These checks bind authorized
input to pixels actually sent and painted in the current native generation.
"""
import math


class DesktopError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def capture_size(width, height, limit, native=False):
    if (not integer(width, 2, 32768) or not integer(height, 2, 32768) or
            not integer(limit, 2, 4096)):
        raise DesktopError('INVALID_ARGUMENT')
    if native:
        if width > 8192 or height > 8192 or width * height > 16 << 20 or width % 2 or height % 2:
            raise DesktopError('NATIVE_RESOLUTION_UNSUPPORTED')
        return width, height
    scale = min(1, limit / max(width, height))
    return max(2, math.floor(width * scale / 2) * 2), max(2, math.floor(height * scale / 2) * 2)


class DesktopAuthority:
    def __init__(self):
        self.generation = 0
        self.display = None
        self.mode = 'view'
        self.state = 'disconnected'
        self.last_sent = self.last_painted = 0

    def bind(self, display, mode):
        if not isinstance(display, str) or not 0 < len(display) <= 160 or mode not in ('view', 'control'):
            raise DesktopError('INVALID_ARGUMENT')
        self.generation += 1
        self.display, self.mode, self.state = display, mode, 'active'
        self.last_sent = self.last_painted = 0
        return self.generation

    def revoke(self, state):
        self.generation += 1
        self.state = state
        self.last_sent = self.last_painted = 0

    def sent(self, frame):
        if not integer(frame, self.last_sent + 1, (1 << 53) - 1):
            raise DesktopError('INVALID_FRAME')
        self.last_sent = frame

    def paint(self, generation, frame):
        if (self.state != 'active' or generation != self.generation or
                not integer(frame, max(1, self.last_painted), self.last_sent)):
            raise DesktopError('STALE_DESKTOP')
        self.last_painted = frame

    def input(self, generation):
        if self.state != 'active' or generation != self.generation or not self.last_painted:
            raise DesktopError('STALE_DESKTOP')
        if self.mode != 'control':
            raise DesktopError('VIEW_ONLY')


class FrameCredit:
    def __init__(self, capacity=4):
        if not integer(capacity, 1, 8):
            raise DesktopError('INVALID_ARGUMENT')
        self.capacity, self.sequence, self.acknowledged = capacity, 0, 0

    @property
    def pending(self):
        return self.sequence - self.acknowledged

    def reserve(self):
        if self.pending >= self.capacity:
            return None
        self.sequence += 1
        return self.sequence

    def acknowledge(self, frame):
        if not integer(frame, 1, self.sequence):
            raise DesktopError('INVALID_FRAME')
        self.acknowledged = max(self.acknowledged, frame)

    def reset(self):
        self.acknowledged = self.sequence
