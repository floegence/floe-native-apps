"""One server boundary works across Xpra's mixin and subsystem organizations."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from input_xpra import install_server_input
from input_dispatch import InputDispatch


class ServerBoundaryTest(unittest.TestCase):
    def test_legacy_aliases_cannot_bypass_pending_text_on_system_xpra(self):
        events, completions = [], []
        aliases = {'pointer':'pointer-motion', 'wheel-motion':'pointer-wheel',
                   'focus':'window-focus', 'map-window':'window-map',
                   'unmap-window':'window-unmap', 'close-window':'window-close'}
        canonical = (*aliases.values(), 'keyboard-event', 'keyboard-config', 'window-configure', 'window-action')
        # WeakKeyDictionary retains the actual transport object, not its ID.
        class Protocol:
            def is_closed(self): return False
            def close(self): raise AssertionError('unexpected close')
        protocol = Protocol()
        source = SimpleNamespace(floe_input_version=1, send=lambda *packet: events.append(packet),
                                 can_send_window=lambda _: True)
        class Base:
            readonly = False
            _id_to_window = {1: SimpleNamespace(get_property=lambda name: {'xid':11, 'pid':12}[name])}
            def setup(self): pass
            def do_cleanup(self): pass
            def get_server_source(self, _protocol): return source
            def get_server_features(self, _source=None): return {}
            def parse_hello(self, *_args): pass
            def init_packet_handlers(self):
                self._authenticated_ui_packet_handlers = {
                    name: (lambda _protocol, packet: events.append(packet[0])) for name in canonical}
                self._authenticated_packet_handlers = {}
            def process_packet(self, protocol, packet):
                self._authenticated_ui_packet_handlers[aliases.get(packet[0], packet[0])](protocol, packet)
            def cleanup_protocol(self, _protocol): pass
            def add_packet_handler(self, name, handler, main_thread=False):
                (self._authenticated_ui_packet_handlers if main_thread else self._authenticated_packet_handlers)[name] = handler
        modules = {
            'xpra': SimpleNamespace(),
            'xpra.server.base': SimpleNamespace(ServerBase=Base),
            'xpra.x11.bindings.core': SimpleNamespace(X11CoreBindings=lambda: SimpleNamespace(XSync=lambda: None)),
            'xpra.os_util': SimpleNamespace(gi_import=lambda _: SimpleNamespace(
                timeout_add=lambda *_: 1, source_remove=lambda _: None, idle_add=lambda cb: cb())),
            'input_xim': SimpleNamespace(XIM=lambda: SimpleNamespace(
                marker=None, focused_within=lambda _: True, close=lambda: None)),
            'input_context': SimpleNamespace(Contexts=lambda *_: SimpleNamespace(
                context_for=lambda *_: 'context', commit=lambda _token, _text, done: completions.append(done),
                cancel=lambda _: None, close=lambda: None)),
        }
        with patch.dict(sys.modules, modules):
            install_server_input('unix:fixture')
        server = Base()
        server.setup()
        server.init_packet_handlers()
        server._server_sources = {protocol: source}
        server.process_packet(protocol, ['floe-input', 1, 1, 'fixture'])
        self.assertEqual(len(completions), 1)
        packets = [*aliases, *canonical]
        for name in packets:
            server.process_packet(protocol, [name])
        self.assertEqual(events, [], 'canonical handlers and legacy aliases must share the text ordering boundary')
        completions[0](None)
        self.assertEqual(events, [('floe-input-result', 1, ''), *packets])
        server.do_cleanup()

    def test_input_uses_aggregate_server_lifecycle_and_authenticated_ui_queue(self):
        events = []
        class Base:
            def setup(self): events.append('setup')
            def do_cleanup(self): events.append('cleanup')
            def get_server_features(self, source=None): return {'existing': True}
            def parse_hello(self, source, caps, *args):
                events.append(('hello', args))
                return 'result'
            def init_packet_handlers(self):
                self._authenticated_ui_packet_handlers = {'key-action': lambda *args: events.append('key')}
                self._authenticated_packet_handlers = {'clipboard-token': self._process_clipboard_packet}
            def process_packet(self, protocol, packet): events.append('dispatch')
            def cleanup_protocol(self, protocol): events.append('detach')
            def add_packet_handler(self, name, handler, main_thread=False):
                (self._authenticated_ui_packet_handlers if main_thread else self._authenticated_packet_handlers)[name] = handler
        class Dispatch(InputDispatch):
            def enqueue(self, protocol, packet, handler=None):
                events.append(('ordered', packet[0]))
                if handler: handler(protocol, packet)
            def close(self):
                events.append('closed')
                super().close()
            def invalidate(self, protocol):
                events.append('invalidated')
                super().invalidate(protocol)
        modules = {
            'xpra': SimpleNamespace(),
            'xpra.server.base': SimpleNamespace(ServerBase=Base),
            'xpra.x11.bindings.core': SimpleNamespace(X11CoreBindings=lambda: SimpleNamespace(XSync=lambda: events.append('synced'))),
            'xpra.os_util': SimpleNamespace(gi_import=lambda _: SimpleNamespace(timeout_add=None, source_remove=None, idle_add=lambda cb: cb())),
            'input_dispatch': SimpleNamespace(InputDispatch=Dispatch),
            'input_xim': SimpleNamespace(XIM=lambda: SimpleNamespace(marker=None, close=lambda: None)),
            'input_context': SimpleNamespace(Contexts=lambda *_: SimpleNamespace(close=lambda: None)),
        }
        with patch.dict(sys.modules, modules):
            install_server_input('unix:fixture')
        server = Base()
        server.setup()
        self.assertEqual(server.get_server_features(), {'existing': True, 'floe-input': 1, 'floe-input-text-limit': 16000})
        source = SimpleNamespace()
        for args in ((), (True,)):
            self.assertEqual(server.parse_hello(source, SimpleNamespace(intget=lambda *_: 1), *args), 'result')
            self.assertEqual(source.floe_input_version, 1)
        server.init_packet_handlers()
        server._authenticated_ui_packet_handlers['key-action'](None, ['key-action'])
        self.assertEqual(events[-3:], [('ordered', 'key-action'), 'key', 'synced'])
        self.assertNotIn('clipboard-token', server._authenticated_packet_handlers)
        self.assertIn('clipboard-token', server._authenticated_ui_packet_handlers)
        server.cleanup_protocol(None)
        self.assertEqual(events[-2:], ['invalidated', 'detach'])
        server.do_cleanup()
        self.assertEqual(events[-2:], ['closed', 'cleanup'])


if __name__ == '__main__':
    unittest.main()
