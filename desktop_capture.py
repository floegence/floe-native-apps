"""One bounded native capture, bracketed by the compositor's scene authority.

The capture child owns one reusable SHM buffer. This reader never interprets a
successful capture as window identity: ordered barriers on the compositor's
control channel must match the requested native target before and after capture.
Cancellation drains an existing response before another request may use its fd.
"""
import struct


class NativeFrames:
    def __init__(self, connection, loop, scene):
        self.socket, self.loop, self.scene = connection, loop, scene
        self.pending, self.sequence, self.closed = None, 0, False
        self.socket.setblocking(False)
        self.loop.watch(self.socket.fileno(), self.read)

    def capture(self, target, completed):
        if self.pending:
            raise ValueError('A native capture is already pending')
        if self.closed:
            completed(None, None, 'CAPTURE_UNAVAILABLE')
            return
        self.sequence += 1
        if self.sequence > 0xffffffff:
            self.close()
            completed(None, None, 'CAPTURE_SEQUENCE_EXHAUSTED')
            return
        ticket = {'target': target, 'completed': completed, 'cancelled': False,
                  'sequence': self.sequence, 'input': bytearray(), 'output': b'', 'stage': 'begin'}
        self.pending = ticket
        def expired():
            if self.pending is ticket:
                ticket['timer'] = None
                self.close('CAPTURE_TIMEOUT')
        ticket['timer'] = self.loop.later(10000, expired)
        self.barrier(ticket, False)

    def barrier(self, ticket, final):
        def observed(window, generation):
            if self.closed or self.pending is not ticket:
                return
            if ticket['cancelled']:
                self.finish(ticket, error='CAPTURE_CANCELLED')
            elif (window, generation) != (ticket['target'].window, ticket['target'].generation):
                self.finish(ticket, error='CAPTURE_TARGET_CHANGED')
            elif final:
                self.finish(ticket, ticket['description'], bytes(ticket['input']))
            else:
                ticket['stage'] = 'header'
                ticket['output'] = struct.pack('=I', ticket['sequence'])
                self.loop.watch(self.socket.fileno(), self.read, self.write)
        try:
            self.scene(observed)
        except (OSError, ValueError):
            self.close('CAPTURE_UNAVAILABLE')

    def write(self):
        if self.closed:
            return
        ticket = self.pending
        if not ticket or not ticket['output']:
            return
        try:
            count = self.socket.send(ticket['output'])
            if count == 0:
                self.close()
                return
            ticket['output'] = ticket['output'][count:]
            if not ticket['output']:
                self.loop.watch(self.socket.fileno(), self.read)
        except BlockingIOError:
            pass
        except OSError:
            self.close()

    def read(self):
        if self.closed:
            return
        ticket = self.pending
        try:
            if not ticket or ticket['stage'] not in ('header', 'pixels'):
                # No unsolicited bytes are valid. In particular an old response
                # cannot silently become the payload of a later target.
                if self.socket.recv(1) is not None:
                    self.close('CAPTURE_PROTOCOL_INVALID')
                return
            total = 24 if ticket['stage'] == 'header' else ticket['length']
            chunk = self.socket.recv(min(128 * 1024, total - len(ticket['input'])))
            if not chunk:
                self.close()
                return
            ticket['input'].extend(chunk)
            if len(ticket['input']) != total:
                return
            if ticket['stage'] == 'header':
                sequence, status, width, height, fmt, length = struct.unpack('=6I', ticket['input'])
                ticket['input'].clear()
                if sequence != ticket['sequence'] or status not in (1, 2, 3):
                    self.close('CAPTURE_PROTOCOL_INVALID')
                    return
                if status != 1:
                    if length != 0:
                        self.close('CAPTURE_PROTOCOL_INVALID')
                    else:
                        self.finish(ticket, error='CAPTURE_SOURCE_CHANGED' if status == 2 else 'CAPTURE_UNAVAILABLE')
                    return
                if (not 0 < width <= 4096 or not 0 < height <= 4096 or length != width * height * 4 or
                        fmt not in (0x34325258, 0x34325241)):
                    self.close('CAPTURE_PROTOCOL_INVALID')
                    return
                ticket['description'] = {'encoding': 'bgrx' if fmt == 0x34325258 else 'bgra',
                                         'width': width, 'height': height}
                ticket['length'], ticket['stage'] = length, 'pixels'
                return
            if ticket['cancelled']:
                self.finish(ticket, error='CAPTURE_CANCELLED')
            else:
                ticket['stage'] = 'end'
                self.barrier(ticket, True)
        except BlockingIOError:
            pass
        except (OSError, ValueError):
            self.close('CAPTURE_PROTOCOL_INVALID')

    def finish(self, ticket, description=None, data=None, error=None):
        if self.pending is not ticket:
            return
        self.pending = None
        if ticket['timer'] is not None:
            self.loop.cancel(ticket['timer'])
        ticket['input'].clear()
        ticket['completed'](description, data, error)

    def cancel(self):
        if self.pending:
            self.pending['cancelled'] = True

    def close(self, error='CAPTURE_UNAVAILABLE'):
        if self.closed:
            return
        self.closed = True
        self.loop.watch(self.socket.fileno())
        self.socket.close()
        if self.pending:
            self.finish(self.pending, error=error)
