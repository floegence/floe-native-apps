"""One event-loop owner for native graphics and authenticated sharing.

The launch supervisor supplies the prepared compositor and capture sockets.
This helper does not discover or terminate application processes. Sharing may
disconnect and reattach while the native window registry continues unchanged.
"""
from desktop_attachment import DesktopAttachment
from desktop_capture import NativeFrames
from desktop_control import DesktopControl
from desktop_native import NativeChannel, NativeDesktop


class DesktopHelper:
    def __init__(self, connection, loop):
        self.loop, self.closed = loop, False
        self.server, self.attachment = None, None
        self.native = NativeDesktop(lambda value: self.channel.send(value), None)
        self.channel = NativeChannel(connection, loop, self.native.observe, self.native.lost)

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
        self.channel.close()
