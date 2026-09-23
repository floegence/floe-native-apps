"""One ordered input boundary for Xpra keys, pointer/focus and Unicode commits."""
from collections import deque
from weakref import WeakKeyDictionary


class InputDispatch:
    def __init__(self, server, xim, contexts, timeout_add, timeout_remove):
        self.server, self.xim, self.contexts = server, xim, contexts
        self.timeout_add, self.timeout_remove = timeout_add, timeout_remove
        self.queue = deque()
        self.pending = None
        self.timer = 0
        self.closed = False
        self.connections = WeakKeyDictionary()

    def state(self, protocol):
        return self.connections.setdefault(protocol, {'sequence': 0, 'failed': False})

    def reject(self, protocol, sequence, error):
        # Keep the transport alive so the explicit failure reaches the viewer.
        # This attachment loses input authority; reattachment never replays input.
        self.state(protocol)['failed'] = True
        self.invalidate(protocol)
        source = self.server.get_server_source(protocol)
        driver = getattr(self.server, 'ui_driver', None)
        if source and driver and driver == getattr(source, 'uuid', None):
            self.server.clear_keys_pressed()
        if source and not protocol.is_closed():
            source.send('floe-input-result', sequence, error)

    def enqueue(self, protocol, packet, handler=None):
        if self.closed or not self.server.get_server_source(protocol) or self.state(protocol)['failed']:
            return
        if getattr(self.server.get_server_source(protocol), 'floe_input_version', 0) != 1:
            self.reject(protocol, 0, 'INPUT_VERSION_UNSUPPORTED')
            return
        if len(self.queue) >= 256:
            self.invalidate(protocol)
            protocol.close()
            return
        if handler is None:
            if (len(packet) != 4 or type(packet[1]) is not int or
                    not 1 <= packet[1] <= 9007199254740991 or type(packet[2]) is not int or
                    not isinstance(packet[3], str)):
                self.invalidate(protocol)
                protocol.close()
                return
            try:
                valid = packet[3] and len(packet[3].encode('utf-8')) <= 16000 and '\x00' not in packet[3]
            except UnicodeEncodeError:
                valid = False
            if not valid:
                self.reject(protocol, packet[1], 'INPUT_TEXT_INVALID')
                return
        self.queue.append((protocol, packet, handler))
        self.drain()

    def drain(self):
        while self.queue and self.pending is None and not self.closed:
            protocol, packet, handler = self.queue.popleft()
            if not self.server.get_server_source(protocol) or protocol.is_closed() or self.state(protocol)['failed']:
                continue
            if handler is not None:
                handler(protocol, packet)
                continue
            _, sequence, wid, text = packet
            source = self.server.get_server_source(protocol)
            state = self.state(protocol)
            if sequence <= state['sequence']:
                self.reject(protocol, sequence, 'INPUT_SEQUENCE_INVALID')
                continue
            state['sequence'] = sequence
            window = self.server._id_to_window.get(wid)
            error = None
            driver = getattr(self.server, 'ui_driver', None)
            if self.server.readonly or window is None or not source.can_send_window(window) or (driver and driver != getattr(source, 'uuid', None)):
                error = 'INPUT_TARGET_UNAVAILABLE'
            if error:
                self.reject(protocol, sequence, error)
                continue
            xid = window.get_property('xid')
            if not self.xim.focused_within(xid):
                self.reject(protocol, sequence, 'INPUT_CONTEXT_UNAVAILABLE')
                continue
            pid = window.get_property('pid')
            token = self.contexts.context_for(xid, pid)
            adapter = self.contexts
            if token is None:
                adapter = self.xim
                token = self.xim.context_for(xid)
            if token is None:
                self.reject(protocol, sequence, 'INPUT_CONTEXT_UNAVAILABLE')
                continue
            operation = (protocol, sequence, adapter, token)
            self.pending = operation
            def completed(error, operation=operation):
                if self.pending != operation:
                    return
                if self.timer:
                    self.timeout_remove(self.timer)
                    self.timer = 0
                self.pending = None
                if error:
                    operation[2].cancel(operation[3])
                active = self.server.get_server_source(operation[0])
                if active and not operation[0].is_closed():
                    if error:
                        self.reject(operation[0], operation[1], error)
                    else:
                        active.send('floe-input-result', operation[1], '')
                self.drain()
            def expired():
                self.timer = 0
                completed('INPUT_DELIVERY_TIMEOUT')
                return False
            self.timer = self.timeout_add(3000, expired)
            adapter.commit(token, text, completed)

    def invalidate(self, protocol):
        self.queue = deque(item for item in self.queue if item[0] is not protocol)
        if self.pending and self.pending[0] is protocol:
            _, _, adapter, token = self.pending
            self.pending = None
            if self.timer:
                self.timeout_remove(self.timer)
                self.timer = 0
            adapter.cancel(token)

    def close(self):
        self.closed = True
        self.queue.clear()
        self.pending = None
        if self.timer:
            self.timeout_remove(self.timer)
            self.timer = 0
        self.contexts.close()
        self.xim.close()
