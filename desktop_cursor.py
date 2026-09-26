"""One latest cursor snapshot, pulled in bounded chunks on the native channel.

The compositor owns surface transforms and straight RGBA bytes. This owner only
frames them as PNG and binds them to the exact native scene; CSS normalization
remains in the shared cursor client. No polling, image cache or input path exists.
"""
import math
import struct
import zlib


LIMIT = 1024
CHUNK = 1024
MAX_ID = 9007199254740991


def integer(value, low=1, high=MAX_ID):
    parsed = int(value)
    if not low <= parsed <= high:
        raise ValueError('Invalid cursor integer')
    return parsed


def rgba_png(width, height, pixels):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    stride = width * 4
    rows = b''.join(b'\0' + pixels[offset:offset + stride] for offset in range(0, len(pixels), stride))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


class NativeCursor:
    def __init__(self, send, target, changed, connection):
        self.send, self.target, self.changed, self.connection = send, target, changed, connection
        self.revision, self.current, self.pending, self.request = 0, None, None, None
        self.closed = False

    def observe(self, fields):
        if self.closed:
            return
        if fields[0] == 'cursor-state':
            if len(fields) not in (6, 12):
                raise ValueError('Invalid cursor state')
            revision = integer(fields[1])
            connection = integer(fields[2])
            generation, window = integer(fields[3], 0), integer(fields[4], 0)
            mode = fields[5]
            if revision <= self.revision or mode not in ('image', 'hidden', 'default') or (len(fields) == 12) != (mode == 'image'):
                raise ValueError('Invalid cursor revision or mode')
            description = dict(sequence=revision, generation=generation, window=window, mode=mode)
            if mode == 'image':
                width, height, logical_width, logical_height = [integer(v, 1, LIMIT) for v in fields[6:10]]
                xhot, yhot = [float(v) for v in fields[10:12]]
                if not math.isfinite(xhot) or not math.isfinite(yhot) or not 0 <= xhot < logical_width or not 0 <= yhot < logical_height:
                    raise ValueError('Invalid cursor hotspot')
                description.update(width=width, height=height, logical_width=logical_width,
                                   logical_height=logical_height, xhot=xhot, yhot=yhot, encoding='png')
            self.revision, self.pending = revision, None
            target = self.target()
            if connection != self.connection() or not target or (target.window, target.generation) != (window, generation):
                self.current = None
                self.changed()
                return
            if mode == 'image':
                self.pending = (target, description, bytearray())
                self.pump()
            else:
                self.current = (description, None)
                self.changed()
        elif fields[0] == 'cursor-data':
            if len(fields) != 4:
                raise ValueError('Invalid cursor data')
            revision, offset = integer(fields[1]), integer(fields[2], 0, LIMIT * LIMIT * 4)
            if self.request != (revision, offset):
                raise ValueError('Unsolicited cursor data')
            encoded = fields[3]
            if encoded != '-' and (not 2 <= len(encoded) <= CHUNK * 2 or len(encoded) % 2 or
                                    any(c not in '0123456789abcdef' for c in encoded)):
                raise ValueError('Invalid cursor bytes')
            ticket = self.pending
            if ticket and revision == ticket[1]['sequence']:
                target, description, data = ticket
                if self.target() is not target:
                    self.pending, self.current = None, None
                    self.changed()
                elif encoded == '-':
                    self.pending, self.current = None, None
                    self.changed()
                else:
                    expected = min(CHUNK, description['width'] * description['height'] * 4 - len(data))
                    if offset != len(data) or len(encoded) != expected * 2:
                        raise ValueError('Invalid cursor chunk extent')
                    data.extend(bytes.fromhex(encoded))
                    if len(data) == description['width'] * description['height'] * 4:
                        self.current = (description, rgba_png(description['width'], description['height'], data))
                        self.pending = None
                        self.changed()
            self.request = None
            self.pump()
        else:
            raise ValueError('Unknown cursor record')

    def pump(self):
        if not self.closed and self.pending and not self.request:
            self.request = (self.pending[1]['sequence'], len(self.pending[2]))
            self.send(f'cursor-read {self.request[0]} {self.request[1]}\n')

    def invalidate(self):
        self.pending, self.current = None, None
        self.changed()

    def close(self):
        self.closed = True
        self.request = None
        self.invalidate()
