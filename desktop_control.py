"""Authenticated local attachment to a persistent graphical helper.

The application owns processes and native state. This transport owns only its
bounded socket buffers and one attachment; losing it never means application
exit. All callbacks run on the helper's event loop, including input admission.
"""
from collections import deque
import hmac
import json
import os
from pathlib import Path
import socket
import stat
import struct

# A 16000-byte confirmed-text operation can expand sixfold in JSON escapes.
MAX_MESSAGE = 128 * 1024
MAX_OUTPUT = 1024 * 1024
MAX_FRAME = 4096 * 4096 * 4
MAX_PEERS = 8


def encode_message(message):
    body = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode()
    if not 0 < len(body) <= MAX_MESSAGE:
        raise ValueError('Control message exceeds limit')
    return struct.pack('!BI', 1, len(body)) + body


def unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Ambiguous control message')
        result[key] = value
    return result


def peer_uid(connection):
    if not hasattr(socket, 'SO_PEERCRED'):
        raise OSError('Native helper peer credentials require Linux')
    return struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]


def reject_constant(_value):
    raise ValueError('Invalid control number')


class GLibLoop:
    def __init__(self):
        from gi.repository import GLib
        self.GLib, self.sources = GLib, {}

    def watch(self, descriptor, read=None, write=None):
        GLib = self.GLib
        previous = self.sources.pop(descriptor, None)
        if previous:
            GLib.source_remove(previous)
        if read is None and write is None:
            return
        flags = GLib.IO_HUP | GLib.IO_ERR | GLib.IO_NVAL
        if read:
            flags |= GLib.IO_IN
        if write:
            flags |= GLib.IO_OUT

        def ready(_descriptor, condition):
            # A read callback may close or replace this watch. The write
            # callback verifies the attachment again before touching its fd.
            if read and condition & (GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR | GLib.IO_NVAL):
                read()
            if write and condition & GLib.IO_OUT:
                write()
            return True
        self.sources[descriptor] = GLib.io_add_watch(descriptor, GLib.PRIORITY_DEFAULT, flags, ready)

    def later(self, milliseconds, callback):
        def expired():
            callback()
            return False
        return self.GLib.timeout_add(milliseconds, expired)

    def cancel(self, timer):
        self.GLib.source_remove(timer)


class DesktopControl:
    def __init__(self, directory, instance, token, application, loop):
        directory = Path(directory)
        info = directory.lstat()
        if (not directory.is_absolute() or not stat.S_ISDIR(info.st_mode) or
                info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError('Private helper directory is required')
        if (not isinstance(instance, str) or not 1 <= len(instance) <= 128 or
                not isinstance(token, str) or len(token) != 64 or
                any(c not in '0123456789abcdef' for c in token)):
            raise ValueError('Invalid helper identity')
        self.path = str(directory / 'control.sock')
        if os.path.lexists(self.path):
            raise FileExistsError('Helper control endpoint already exists')
        self.instance, self.token = instance, token
        self.application, self.loop = application, loop
        self.peers, self.current, self.closed = set(), None, False
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.setblocking(False)
        try:
            self.socket.bind(self.path)
            os.chmod(self.path, 0o600)
            info = os.lstat(self.path)
            self.identity = (info.st_dev, info.st_ino)
            self.socket.listen(MAX_PEERS)
            loop.watch(self.socket.fileno(), self.accept)
        except BaseException:
            self.socket.close()
            raise

    def accept(self):
        if self.closed:
            return
        for _ in range(MAX_PEERS):
            try:
                connection, _address = self.socket.accept()
            except BlockingIOError:
                return
            try:
                if len(self.peers) >= MAX_PEERS or peer_uid(connection) != os.getuid():
                    connection.close()
                    continue
                connection.setblocking(False)
                peer = Peer(self, connection)
                self.peers.add(peer)
            except (OSError, ValueError):
                connection.close()

    def authenticate(self, peer, message):
        if (set(message) != {'version', 'instance', 'token'} or type(message['version']) is not int or
                message['version'] != 1 or message['instance'] != self.instance or
                not isinstance(message['token'], str) or len(message['token']) != 64 or
                any(c not in '0123456789abcdef' for c in message['token']) or
                not hmac.compare_digest(message['token'], self.token)):
            peer.close()
            return
        # Authentication happens before takeover. Old cleanup can only revoke
        # that old object; it can never detach a replacement attachment.
        if self.current:
            self.current.close()
        self.current = peer
        peer.authenticated = True
        peer.cancel_timeout()
        self.application.attach(peer)

    def close(self):
        if self.closed:
            return
        self.closed = True
        for peer in tuple(self.peers):
            peer.close()
        self.loop.watch(self.socket.fileno())
        self.socket.close()
        try:
            info = os.lstat(self.path)
            if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self.identity:
                os.unlink(self.path)
        except FileNotFoundError:
            pass


class Peer:
    def __init__(self, server, connection):
        self.server, self.socket = server, connection
        self.authenticated, self.closed = False, False
        self.input, self.output = bytearray(), deque()
        self.buffered, self.control_buffered = 0, 0
        self.frame_pending = False
        self.timer = server.loop.later(3000, self.expired)
        self.watch()

    def expired(self):
        if not self.authenticated and not self.closed:
            self.timer = None
            self.close()

    def cancel_timeout(self):
        if self.timer:
            self.server.loop.cancel(self.timer)
            self.timer = None

    def watch(self):
        if not self.closed:
            self.server.loop.watch(self.socket.fileno(), self.read, self.write if self.output else None)

    def read(self):
        if self.closed:
            return
        try:
            # One event turn reads at most one bounded control chunk. Graphics
            # and native dispatch keep their own event-loop opportunities.
            data = self.socket.recv(MAX_MESSAGE + 5 - len(self.input))
            if not data:
                self.close()
                return
            self.input.extend(data)
            while not self.closed and len(self.input) >= 5:
                kind, size = struct.unpack('!BI', self.input[:5])
                if kind != 1 or not 0 < size <= MAX_MESSAGE:
                    self.close()
                    return
                if len(self.input) < size + 5:
                    return
                message = json.loads(self.input[5:size + 5], object_pairs_hook=unique_object,
                                     parse_constant=reject_constant)
                del self.input[:size + 5]
                if not isinstance(message, dict):
                    self.close()
                elif not self.authenticated:
                    self.server.authenticate(self, message)
                elif self.server.current is self:
                    if (type(message.get('id')) is not int or not 1 <= message['id'] <= 9007199254740991 or
                            not isinstance(message.get('method'), str) or not 1 <= len(message['method']) <= 64):
                        self.close()
                        return
                    self.server.application.request(self, message)
        except (OSError, ValueError, UnicodeError, RecursionError):
            # No parser exception or request body belongs in production logs.
            self.close()

    def append(self, data, frame=False):
        self.output.append((memoryview(data), frame))
        self.buffered += len(data)
        if not frame:
            self.control_buffered += len(data)

    def send(self, message):
        if self.closed or self.server.current is not self:
            return False
        data = encode_message(message)
        if self.control_buffered + len(data) > MAX_OUTPUT or len(self.output) >= 128:
            self.close()
            return False
        self.append(data)
        self.watch()
        return True

    def send_frame(self, description, pixels):
        if self.closed or self.server.current is not self or self.frame_pending:
            return False
        if not isinstance(pixels, bytes) or not 0 < len(pixels) <= MAX_FRAME:
            raise ValueError('Invalid native frame size')
        if not self.send({'event': 'frame', 'frame': description, 'bytes': len(pixels)}):
            return False
        self.frame_pending = True
        self.append(struct.pack('!BI', 2, len(pixels)))
        self.append(pixels, frame=True)
        self.watch()
        return True

    def write(self):
        if self.closed:
            return
        budget = MAX_MESSAGE
        try:
            while self.output and budget > 0:
                data, frame = self.output[0]
                count = self.socket.send(data[:budget])
                if not count:
                    self.close()
                    return
                self.buffered -= count
                if not frame:
                    self.control_buffered -= count
                budget -= count
                if count == len(data):
                    self.output.popleft()
                    if frame:
                        self.frame_pending = False
                else:
                    self.output[0] = (data[count:], frame)
        except BlockingIOError:
            pass
        except OSError:
            self.close()
        self.watch()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancel_timeout()
        self.server.loop.watch(self.socket.fileno())
        self.socket.close()
        self.input.clear()
        self.output.clear()
        self.buffered = self.control_buffered = 0
        self.frame_pending = False
        self.server.peers.discard(self)
        if self.server.current is self:
            self.server.current = None
            self.server.application.detach(self)
