"""One event-loop owner for native graphics and authenticated sharing.

The launch supervisor supplies the prepared compositor and capture sockets.
This helper does not discover or terminate application processes. Sharing may
disconnect and reattach while the native window registry continues unchanged.
"""
from desktop_attachment import DesktopAttachment
from desktop_capture import NativeFrames
from desktop_control import DesktopControl
from desktop_context import NativeContexts, NativeContextService
from desktop_native import NativeChannel, NativeDesktop


class DesktopHelper:
    def __init__(self, connection, loop):
        self.loop, self.closed = loop, False
        self.server, self.attachment = None, None
        self.context_service, self.ibus, self.xim = None, None, None
        self.input_initialized = False
        self.native = NativeDesktop(lambda value: self.channel.send(value), None)
        self.channel = NativeChannel(connection, loop, self.native.observe, self.native.lost)

    def configure_input(self, connection, tree, runtime, *, x11=None, destination=NativeContextService.NAME):
        """Borrow the private bus/process authority; take ownership of x11.

        This is the only context assembly for a helper. The caller creates its
        authenticated native X11 connection before launch. Failed initialization
        releases transferred resources without closing the bus or process tree.
        """
        if (self.closed or self.native.closed or self.native.version != 1 or
                self.input_initialized or self.native.contexts is not None):
            raise ValueError('Native input assembly is unavailable')
        contexts = NativeContexts(self.native, tree, runtime)
        contexts.x11 = x11
        try:
            service = NativeContextService(connection, contexts, destination)
        except BaseException:
            contexts.close()
            if x11:
                x11.close()
            raise
        self.native.contexts, self.context_service = contexts, service
        self.input_initialized = True
        return contexts

    def enable_ibus(self, daemon, completed, *, portal=None):
        if (self.closed or self.native.closed or self.context_service is None or
                self.native.contexts.closed or self.ibus is not None):
            raise ValueError('Native IBus assembly is unavailable')
        from desktop_ibus_service import NativeIBusService
        self.ibus = NativeIBusService(self.native.contexts, self.context_service.connection, daemon, portal)
        try:
            self.ibus.activate(completed)
        except BaseException:
            self.ibus.close()
            self.ibus = None
            raise
        return self.ibus

    def enable_xim(self):
        if (self.closed or self.native.closed or self.context_service is None or
                self.native.contexts.closed or self.xim is not None):
            raise ValueError('Native XIM assembly is unavailable')
        from desktop_xim import NativeXIMContexts
        self.xim = NativeXIMContexts(self.native.contexts)
        return self.xim

    def close_input(self):
        # Revoke admission before releasing toolkit objects. Their late markers
        # cannot create a new context or complete an old transaction.
        contexts = self.native.contexts
        if contexts:
            contexts.close()
        if self.ibus:
            self.ibus.close()
            self.ibus = None
        if self.xim:
            self.xim.close()
            self.xim = None
        if self.context_service:
            self.context_service.close()
            self.context_service = None
        if contexts and contexts.x11:
            contexts.x11.close()
            contexts.x11 = None
        self.native.contexts = None

    def listen(self, directory, instance, token, capture):
        if self.closed or self.attachment is not None or self.native.closed or self.native.version != 1:
            raise ValueError('Prepared native helper is unavailable')
        frames = NativeFrames(capture, self.loop, self.native.query_scene)
        self.native.frames = frames
        attachment = DesktopAttachment(self.native, self.loop.later, self.loop.cancel)
        self.native.attachment = attachment
        try:
            server = DesktopControl(directory, instance, token, attachment, self.loop)
        except BaseException:
            self.native.attachment = None
            attachment.close()
            frames.close()
            raise
        self.server, self.attachment = server, attachment

    def stop_sharing(self):
        # Stop the public endpoint before retiring callbacks. Neither operation
        # interprets detach, unavailable frames or native EOF as application exit.
        if self.server:
            self.server.close()
            self.server = None
        if self.attachment:
            self.attachment.close()
            self.native.attachment = None
        if self.native.frames:
            self.native.frames.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop_sharing()
        self.close_input()
        self.channel.close()
