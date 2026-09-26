"""Xwayland XIM events beneath the one native context/ordering owner.

The original xcb-imdkit bridge owns the XIM protocol and bounded UTF-8 fragments.
This adapter admits the actual X11 resource owner and consumes native seat
markers. A protocol sync is required in addition to the marker release; actual
document qualification remains the evidence for toolkit consumption.
"""
import ctypes as c

from application_peer import ApplicationPeer
from input_xim import XIM, Key


class NativeXIMContexts(XIM):
    event_mask = (1 << 21) | 3

    def __init__(self, contexts):
        if contexts.xim is not None or contexts.x11 is None:
            raise ValueError('Native XIM resource admission is unavailable')
        self.contexts = contexts
        self.origins, self.clients, self.instance = {}, {}, 0
        self.closed = False
        super().__init__()
        contexts.xim = self

    def _callback(self, server, client, context, header, frame, argument, data):
        code = header.contents.major
        if code == 30:
            self.instance += 1
            self.clients[client] = self.instance
        elif code == 50 and client in self.clients:
            self.instance += 1
            self.origins[context] = (self.clients[client], self.instance)
        if code in (3, 32):
            owner = self.clients.pop(client, None)
            for ic, origin in tuple(self.origins.items()):
                if origin[0] == owner:
                    self.retire(ic)
            if owner is not None:
                for xim_context, origin in tuple(self.origins.items()):
                    if origin[0] == owner:
                        self.contexts.markers.remove_owner(self.sender(origin, xim_context))
        elif code == 52:
            self.retire(context)
        elif code == 59:
            operation = self.contexts.pending
            if operation and operation.get('xim_context') == (context, self.origins.get(context)):
                self.contexts.finish('INPUT_TARGET_UNAVAILABLE')
        super()._callback(server, client, context, header, frame, argument, data)

    def retire(self, context):
        origin = self.origins.pop(context, None)
        operation = self.contexts.pending
        if operation and operation.get('xim_context') == (context, origin):
            self.contexts.finish('INPUT_CONTEXT_UNAVAILABLE')

    def select(self, surface):
        if self.closed:
            return None
        matching = {}
        for context, origin in tuple(self.origins.items()):
            if context not in self.supported_contexts or context in self.cancelled:
                continue
            window = self.client_window(context)
            pid = self.contexts.x11.owner_pid(window)
            if not pid:
                continue
            try:
                peer = ApplicationPeer(self.contexts.tree, pid, self.contexts.runtime)
            except (OSError, ValueError):
                continue
            sender = self.sender(origin, context)
            if peer.matches(surface) and sender not in matching:
                matching[sender] = peer
            else:
                peer.close()
        if len(matching) == 1:
            return next(iter(matching.items()))
        for peer in matching.values():
            peer.close()
        return None

    def valid(self, token):
        return (not self.closed and token.xid and
                token.sender.startswith('xim/') and token.sender.rsplit('/', 1)[-1].isdigit() and
                int(token.sender.rsplit('/', 1)[-1]) in self.origins)

    @staticmethod
    def sender(origin, context):
        return 'xim/' + str(origin[0]) + '/' + str(context)

    def commit(self, token, text, completed):
        if not self.valid(token):
            completed('INPUT_CONTEXT_UNAVAILABLE')
            return
        context = int(token.sender.rsplit('/', 1)[-1])
        XIM.commit(self, context, text, completed)

    def _forward_event(self, server, context, event):
        key = c.cast(event, c.POINTER(Key)).contents
        if key.detail != 8 or (key.type & 127) not in (2, 3):
            # XIM itself queues subsequent native keys until this ordinary key's
            # synchronous forwarding reply. A marker cannot overtake that key.
            self.sync_mode(self.server, True)
            try:
                self.forward(server, context, event)
            finally:
                self.sync_mode(self.server, False)
            return
        origin = self.origins.get(context)
        if not origin:
            return
        sender, released = self.sender(origin, context), (key.type & 127) == 3
        operation = self.contexts.pending
        owned = bool(operation and operation['token'].adapter is self and
                     operation['token'].sender == sender and operation['code'] == 0)
        if not owned:
            self.contexts.markers.key(0, released, False, owner=sender)
            return
        token = operation['token']
        window = self.focus_window(context) or self.client_window(context)
        if (self.focused != context or context not in self.supported_contexts or
                not self.descendant(window, token.xid)):
            self.contexts.finish('INPUT_TARGET_UNAVAILABLE')
            return
        if released:
            if operation.get('xim_context') != (context, origin):
                self.contexts.finish('INPUT_TARGET_UNAVAILABLE')
                return
            self.contexts.released(sender, 0)
            return
        taken = self.contexts.take(sender, 0, token.xid)
        if taken is None:
            return
        operation['xim_context'] = (context, origin)
        operation['cancel_delivery'] = lambda: XIM.cancel(self, context)
        def completed(error):
            if self.contexts.pending is not operation:
                return
            if error:
                self.contexts.finish(error)
            elif self.origins.get(context) != origin:
                self.contexts.finish('INPUT_TARGET_UNAVAILABLE')
            else:
                self.contexts.done(sender, taken[0])
        XIM.commit(self, context, taken[1], completed)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.contexts.pending and self.contexts.pending['token'].adapter is self:
            self.contexts.finish('INPUT_CONTEXT_UNAVAILABLE')
        if self.contexts.bound and self.contexts.bound.adapter is self:
            self.contexts.unbind()
        for owner in self.clients.values():
            for context, origin in self.origins.items():
                self.contexts.markers.remove_owner(self.sender(origin, context))
        self.clients.clear()
        self.origins.clear()
        self.contexts.xim = None
        super().close()
