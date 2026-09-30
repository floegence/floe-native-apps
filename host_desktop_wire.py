"""Bounded helper transport. Control and media never share a blocking writer."""
import json
import os
import queue
import struct
import threading

from host_desktop_contract import DesktopError, integer

HEADER_LIMIT = 8 << 20  # 1 MiB text can require six JSON escape bytes per byte.
PAYLOAD_LIMIT = 64 << 20


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def read_command(source):
    prefix = source.read(4)
    if not prefix:
        return None
    if len(prefix) != 4:
        raise DesktopError('PROTOCOL_INVALID')
    size = struct.unpack('!I', prefix)[0]
    if not 0 < size <= HEADER_LIMIT:
        raise DesktopError('PROTOCOL_INVALID')
    data = source.read(size)
    if len(data) != size:
        raise DesktopError('PROTOCOL_INVALID')
    try:
        command = json.loads(data.decode('utf-8'), object_pairs_hook=unique_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise DesktopError('PROTOCOL_INVALID') from error
    if (not isinstance(command, dict) or type(command.get('version')) is not int or command['version'] != 1
            or not integer(command.get('id'), 1, (1 << 53) - 1)):
        raise DesktopError('PROTOCOL_INVALID')
    return command


def packet(message, data=b''):
    if len(data) > PAYLOAD_LIMIT:
        raise DesktopError('PROTOCOL_INVALID')
    header = json.dumps(dict(message, version=1, bytes=len(data)), separators=(',', ':'),
                        ensure_ascii=False, allow_nan=False).encode()
    if len(header) > HEADER_LIMIT:
        raise DesktopError('PROTOCOL_INVALID')
    return struct.pack('!I', len(header)) + header + data


class Writer:
    def __init__(self, fd, failed, capacity, maximum):
        self.fd, self.failed = fd, failed
        self.queue = queue.Queue(capacity)
        self.maximum, self.pending = maximum, 0
        self.lock = threading.Lock()
        self.closed = False
        threading.Thread(target=self._write, name='floe-desktop-output', daemon=True).start()

    def send(self, message, data=b''):
        value = packet(message, data)
        with self.lock:
            if self.closed:
                return
            if self.pending + len(value) > self.maximum or self.queue.full():
                self.closed = True
                self.failed('TRANSPORT_BACKPRESSURE')
                return
            self.pending += len(value)
            self.queue.put_nowait(value)

    def _write(self):
        try:
            while True:
                value = self.queue.get()
                if value is None:
                    return
                pending = memoryview(value)
                while pending:
                    count = os.write(self.fd, pending)
                    if not count:
                        raise OSError('closed transport')
                    pending = pending[count:]
                with self.lock:
                    self.pending -= len(value)
        except OSError:
            self.failed('TRANSPORT_CLOSED')

    def close(self):
        with self.lock:
            self.closed = True
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(None)
