"""One server boundary works across Xpra's mixin and subsystem organizations."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from input_xpra import install_server_input
from input_dispatch import InputDispatch


class ServerBoundaryTest(unittest.TestCase):
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
