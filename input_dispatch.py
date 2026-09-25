"""Xpra admission adapter for the shared native input scheduler."""
from weakref import WeakKeyDictionary
from input_order import OrderedInput


class InputDispatch:
    def __init__(self, server, xim, contexts, timeout_add, timeout_remove):
        self.server, self.xim, self.contexts = server, xim, contexts
        self.connections = WeakKeyDictionary()
        self.order = OrderedInput(self.available, self.admit, self.result, lambda protocol: protocol.close(),
                                  timeout_add, timeout_remove)

    @property
    def queue(self):
        return self.order.queue

    @property
    def pending(self):
        return self.order.pending

    def state(self, protocol):
        return self.connections.setdefault(protocol, {'sequence': 0, 'failed': False})

    def available(self, protocol):
        return bool(self.server.get_server_source(protocol) and not protocol.is_closed()
                    and not self.state(protocol)['failed'])

    def reject(self, protocol, sequence, error):
        # Keep the transport alive so the explicit failure reaches the viewer.
        # This attachment loses input authority; reattachment never replays input.
        self.state(protocol)['failed'] = True
        source = self.server.get_server_source(protocol)
        driver = getattr(self.server, 'ui_driver', None)
        if source and driver and driver == getattr(source, 'uuid', None):
            self.server.clear_keys_pressed()
        self.invalidate(protocol)
        if source and not protocol.is_closed():
            source.send('floe-input-result', sequence, error)

    def enqueue(self, protocol, packet, handler=None):
        if self.order.closed or not self.available(protocol):
            return
        if getattr(self.server.get_server_source(protocol), 'floe_input_version', 0) != 1:
            self.reject(protocol, 0, 'INPUT_VERSION_UNSUPPORTED')
            return
        window = None
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
            # Retain the native object, not just a reusable Xpra window number.
            window = self.server._id_to_window.get(packet[2])
        self.order.enqueue(protocol, (packet, handler, window))

    def admit(self, protocol, operation):
        packet, handler, original_window = operation
        if handler is not None:
            handler(protocol, packet)
            return None
        _, sequence, wid, text = packet
        source = self.server.get_server_source(protocol)
        state = self.state(protocol)
        if sequence <= state['sequence']:
            self.reject(protocol, sequence, 'INPUT_SEQUENCE_INVALID')
            return None
        state['sequence'] = sequence
        window = self.server._id_to_window.get(wid)
        driver = getattr(self.server, 'ui_driver', None)
        if (self.server.readonly or window is None or window is not original_window or
                not source.can_send_window(window) or (driver and driver != getattr(source, 'uuid', None))):
            self.reject(protocol, sequence, 'INPUT_TARGET_UNAVAILABLE')
            return None
        xid = window.get_property('xid')
        if not self.xim.focused_within(xid):
            self.reject(protocol, sequence, 'INPUT_CONTEXT_UNAVAILABLE')
            return None
        pid = window.get_property('pid')
        token = self.contexts.context_for(xid, pid)
        adapter = self.contexts
        if token is None:
            adapter = self.xim
            token = self.xim.context_for(xid)
        if token is None:
            self.reject(protocol, sequence, 'INPUT_CONTEXT_UNAVAILABLE')
            return None
        return sequence, adapter, token, text

    def result(self, protocol, sequence, error):
        if error:
            self.reject(protocol, sequence, error)
        else:
            self.server.get_server_source(protocol).send('floe-input-result', sequence, '')

    def invalidate(self, protocol):
        self.order.invalidate(protocol)

    def close(self):
        self.order.close()
        self.contexts.close()
        self.xim.close()
