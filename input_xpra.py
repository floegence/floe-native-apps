"""Xpra entrypoint with client-owned text and one authenticated input scheduler."""
import os
import sys


def private_logging(previous):
    def log(callback, level, message, *args, **kwargs):
        name = getattr(getattr(callback, '__self__', None), 'name', '')
        if {'network', 'protocol', 'websocket', 'crypto', 'keyboard', 'clipboard'} & set(name.split('.')):
            # Xpra's raw decoder and error paths can interpolate packet bytes
            # before their application handler runs. Never forward their body,
            # arguments or exception text, even when debug logging is enabled.
            if level >= 30:
                previous(callback, level, 'Private input transport diagnostic; details withheld')
            return
        previous(callback, level, message, *args, **kwargs)
    return log


def install_server_input(address):
    from xpra.server.base import ServerBase
    from xpra.x11.bindings.core import X11CoreBindings
    from xpra.os_util import gi_import
    from input_dispatch import InputDispatch
    from input_xim import XIM
    from input_context import Contexts
    GLib = gi_import('GLib')
    original_setup = ServerBase.setup
    original_cleanup = ServerBase.do_cleanup
    original_features = ServerBase.get_server_features
    original_hello = ServerBase.parse_hello
    original_handlers = ServerBase.init_packet_handlers
    original_process_packet = ServerBase.process_packet
    original_cleanup_protocol = ServerBase.cleanup_protocol

    def setup(self):
        original_setup(self)
        xim = XIM()
        try:
            contexts = Contexts(address, xim, xim.marker)
        except Exception:
            xim.close()
            raise
        self.floe_input = InputDispatch(self, xim, contexts, GLib.timeout_add, GLib.source_remove)

    def cleanup(self):
        dispatcher = getattr(self, 'floe_input', None)
        if dispatcher:
            dispatcher.close()
        original_cleanup(self)

    def features(self, source=None):
        return {**original_features(self, source), 'floe-input': 1, 'floe-input-text-limit': 16000}

    def parse_hello(self, source, caps, *args):
        source.floe_input_version = caps.intget('floe-input', 0)
        return original_hello(self, source, caps, *args)

    def wrap(self, names):
        for name in names:
            original = (self._authenticated_ui_packet_handlers.get(name) or
                        self._authenticated_packet_handlers.get(name))
            if original:
                def ordered(protocol, packet, handler=original):
                    def apply(active, data):
                        handler(active, data)
                        # The toolkit marker uses another X connection. Flush
                        # prior input before asking the X server to deliver it.
                        X11CoreBindings().XSync()
                    self.floe_input.enqueue(protocol, packet, apply)
                # Older Xpra registration does not remove a handler from the
                # other queue. Keep exactly one authoritative dispatch entry.
                self._authenticated_packet_handlers.pop(name, None)
                self.add_packet_handler(name, ordered, True)

    def handlers(self):
        original_handlers(self)
        wrap(self, ('key-action', 'key-repeat', 'pointer-button', 'button-action',
                    'pointer', 'pointer-position', 'wheel-motion', 'layout-changed', 'keymap-changed',
                    'focus', 'close-window', 'configure-window', 'map-window', 'unmap-window',
                    'clipboard-token'))
        self.add_packet_handler('floe-input', lambda protocol, packet: self.floe_input.enqueue(protocol, packet), True)

    def clipboard_packet(self, protocol, packet):
        # Already running on Xpra's authenticated UI queue. Do not defer the
        # selection claim behind a following paste key with another idle_add.
        source = self.get_server_source(protocol)
        if self.readonly or not self.clipboard or not source:
            return
        if packet[0] == 'clipboard-status':
            self._process_clipboard_status(protocol, packet)
        elif source is self._clipboard_client and source.clipboard_enabled and self._clipboard_helper:
            self._clipboard_helper.process_clipboard_packet(packet)

    def process_packet(self, protocol, packet):
        if packet and packet[0] in ('floe-input', b'floe-input'):
            # Retain the same authentication and UI scheduling boundary while
            # excluding text bodies from Xpra's optional packet-body logger.
            if protocol not in self._server_sources:
                protocol.close()
                return
            def deliver():
                try:
                    self.floe_input.enqueue(protocol, packet)
                except Exception:
                    # A delivery error must not print an exception containing
                    # application content or continue with queued input.
                    self.floe_input.invalidate(protocol)
                    protocol.close()
                return False
            GLib.idle_add(deliver)
            return
        original_process_packet(self, protocol, packet)

    def cleanup_protocol(self, protocol):
        dispatcher = getattr(self, 'floe_input', None)
        if dispatcher:
            dispatcher.invalidate(protocol)
        return original_cleanup_protocol(self, protocol)

    # The aggregate server boundary is stable across Xpra 6's internal mixin
    # and subsystem reorganization. There is one input adapter and scheduler.
    ServerBase.setup = setup
    ServerBase.do_cleanup = cleanup
    ServerBase.get_server_features = features
    ServerBase.parse_hello = parse_hello
    ServerBase.init_packet_handlers = handlers
    ServerBase._process_clipboard_packet = clipboard_packet
    ServerBase.process_packet = process_packet
    ServerBase.cleanup_protocol = cleanup_protocol


def install(address):
    import xpra.log
    xpra.log.set_global_logging_handler(private_logging(xpra.log.global_logging_handler))
    from xpra.scripts import server
    original = server.make_seamless_server

    def make_seamless_server(*args, **kwargs):
        # Xpra chooses its feature mixins from parsed options before invoking
        # this factory. Importing ServerBase earlier re-enables disabled audio,
        # webcam and other optional components during their startup.
        install_server_input(address)
        from display import install_display
        return install_display(original(*args, **kwargs))

    server.make_seamless_server = make_seamless_server


if __name__ == '__main__':
    # Xpra removes inherited DBUS_* during startup. Capture only the bus created
    # by the caller for this application, before Xpra sanitizes its environment.
    address = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')
    if not address.startswith('unix:'):
        raise RuntimeError('A private local application bus is required')
    install(address)
    from xpra.scripts.main import main
    sys.argv[0] = 'xpra'
    sys.exit(main('xpra', sys.argv))
