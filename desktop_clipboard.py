"""Bounded text selection exchange on the existing private native channel.

Publication is an adapter of the one input scheduler, never confirmed-text
injection. Selection reads keep only the latest revision and one outstanding
chunk. Neither selection contents nor protocol exceptions enter diagnostics.
"""
from dataclasses import dataclass


LIMIT = 16000
CHUNK = 1024
MAX_ID = 9007199254740991


def integer(value, low=1, high=MAX_ID):
    result = int(value)
    if not low <= result <= high:
        raise ValueError('Invalid native clipboard integer')
    return result


@dataclass(frozen=True)
class ClipboardTarget:
    epoch: int
    target: object


class NativeClipboard:
    def __init__(self, native, changed):
        self.native, self.changed = native, changed
        self.sequence, self.revision = 0, 0
        self.pending = self.reading = self.request = None
        self.closed = False

    def token(self, target):
        if not self.closed and not self.native.closed and self.native.epoch and self.native.target is target:
            return ClipboardTarget(self.native.epoch, target)
        return None

    def valid(self, token):
        return (token is not None and not self.closed and not self.native.closed and
                self.native.target is token.target and self.native.epoch == token.epoch)

    def commit(self, token, text, completed):
        if self.pending or not self.valid(token):
            completed('INPUT_TARGET_UNAVAILABLE')
            return
        try:
            if not isinstance(text, str) or '\x00' in text:
                raise ValueError()
            data = text.encode('utf-8')
            if len(data) > LIMIT:
                raise ValueError()
        except (ValueError, UnicodeEncodeError):
            completed('CLIPBOARD_TEXT_INVALID')
            return
        self.sequence = integer(self.sequence + 1)
        self.pending = (self.sequence, token, completed)
        try:
            self.native.submit(token.epoch, token.target, [f'clipboard-set {self.sequence} {data.hex() or "-"}'])
        except (OSError, ValueError):
            self.pending = None
            completed('CLIPBOARD_UNAVAILABLE')

    def cancel(self, token):
        if self.pending and self.pending[1] is token:
            self.pending = None

    def observe(self, fields):
        if self.closed:
            return
        kind = fields[0]
        if kind in ('clipboard-published', 'clipboard-rejected'):
            if len(fields) != 2:
                raise ValueError('Invalid native clipboard publication')
            sequence = integer(fields[1], high=self.sequence)
            if self.pending and sequence == self.pending[0]:
                _, token, completed = self.pending
                self.pending = None
                error = None if kind == 'clipboard-published' else 'CLIPBOARD_UNAVAILABLE'
                completed(error if self.valid(token) else 'INPUT_TARGET_UNAVAILABLE')
        elif kind == 'clipboard-state':
            if len(fields) != 6:
                raise ValueError('Invalid native clipboard state')
            revision, epoch, generation, window = map(integer, fields[1:5])
            size = integer(fields[5], -1, LIMIT)
            if revision <= self.revision:
                raise ValueError('Retired native clipboard state')
            self.revision, self.reading = revision, None
            target = self.native.target
            if epoch != self.native.epoch or not target or (target.window, target.generation) != (window, generation):
                return
            token = self.token(target)
            if token is None:
                return
            if size <= 0:
                self.changed((target, epoch, '' if size == 0 else None))
            else:
                self.reading = (revision, token, size, bytearray())
                self.pump()
        elif kind == 'clipboard-data':
            if len(fields) != 4:
                raise ValueError('Invalid native clipboard data')
            revision, offset = integer(fields[1]), integer(fields[2], 0, LIMIT)
            if self.request != (revision, offset):
                raise ValueError('Unsolicited native clipboard data')
            encoded = fields[3]
            if encoded != '-' and (not 2 <= len(encoded) <= 2 * CHUNK or len(encoded) % 2 or
                                    any(c not in '0123456789abcdef' for c in encoded)):
                raise ValueError('Invalid native clipboard bytes')
            reading = self.reading
            self.request = None
            if reading and revision == reading[0]:
                _, token, size, data = reading
                if not self.valid(token) or encoded == '-':
                    self.reading = None
                    if self.valid(token):
                        self.changed((token.target, token.epoch, None))
                else:
                    if offset != len(data) or len(encoded) != 2 * min(CHUNK, size - offset):
                        raise ValueError('Invalid native clipboard chunk extent')
                    data.extend(bytes.fromhex(encoded))
                    if len(data) == size:
                        self.reading = None
                        try:
                            text = data.decode('utf-8')
                            if '\x00' in text:
                                raise ValueError()
                        except (UnicodeDecodeError, ValueError):
                            text = None
                        self.changed((token.target, token.epoch, text))
            self.pump()
        else:
            raise ValueError('Unknown native clipboard record')

    def pump(self):
        if self.reading and not self.request and not self.closed:
            self.request = (self.reading[0], len(self.reading[3]))
            self.native.send(f'clipboard-read {self.request[0]} {self.request[1]}\n')

    def invalidate(self):
        self.reading = None

    def close(self):
        self.closed = True
        self.reading = self.request = None
        pending, self.pending = self.pending, None
        if pending:
            pending[2]('CLIPBOARD_UNAVAILABLE')
