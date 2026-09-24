"""XIM keeps one logical commit ordered across bounded native lookup buffers."""
import ctypes
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('tested_xim', Path(__file__).with_name('input_xim.py'))
xim = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'gi.repository': SimpleNamespace(GLib=SimpleNamespace(Source=object))}):
    spec.loader.exec_module(xim)


class XIMTest(unittest.TestCase):
    def bridge(self):
        bridge = xim.XIM.__new__(xim.XIM)
        bridge.focused = 7
        bridge.pending = None
        bridge.fragments = []
        bridge.cancelled = set()
        bridge.client_locales = {}
        bridge.supported_contexts = set()
        bridge.server = bridge.connection = 1
        bridge.free = lambda _: None
        bridge.flush = lambda _: None
        bridge.sync = lambda *_: None
        bridge.received = []
        bridge.buffers = []

        def encode(data, length, size):
            buffer = ctypes.create_string_buffer(data)
            bridge.buffers.append(buffer)
            ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t))[0] = length
            return ctypes.addressof(buffer)

        bridge.encode = encode
        bridge.send = lambda _server, _token, _flag, data, size, _key: bridge.received.append(ctypes.string_at(data, size))
        return bridge

    def acknowledge(self, bridge):
        bridge._callback(1, None, 7, ctypes.pointer(xim.Header(major=62)), None, None, None)

    def test_marker_addresses_owner_without_requiring_core_event_selection(self):
        bridge = self.bridge()
        bridge.keymap = lambda *_: 1
        bridge.keymap_reply = lambda *_: 2
        bridge.keysyms = lambda *_: [0]
        bridge.keysyms_count = lambda *_: 1
        bridge.get_focus = lambda *_: 3
        bridge.focus_reply = lambda *_: [0, 0, 42]
        bridge.descendant = lambda child, parent: child == parent == 42
        bridge.root = 1
        bridge.flush = lambda *_: 1
        events = []
        def send(connection, propagate, destination, mask, data):
            event = ctypes.cast(data, ctypes.POINTER(xim.Key)).contents
            events.append((propagate, destination, mask, event.type, event.detail, event.time))
        bridge.send_event = send
        self.assertTrue(bridge.marker(1234, 42))
        self.assertEqual(events, [(0, 42, 0, 2, 8, 1234)])
        bridge.keysyms = lambda *_: [97]
        self.assertFalse(bridge.marker(1235, 42))
        self.assertEqual(len(events), 1)

    def test_long_unicode_stays_one_operation_until_every_fragment_is_acknowledged(self):
        bridge = self.bridge()
        text = '你🙂e\u0301👩🏽‍💻𠮷\n' * 450
        results = []
        bridge.commit(7, text, results.append)
        while bridge.pending:
            self.assertEqual(results, [])
            self.assertLessEqual(len(bridge.received[-1]), 256)
            bridge.received[-1].decode('utf-8')
            self.acknowledge(bridge)
        self.assertEqual(results, [None])
        self.assertEqual(b''.join(bridge.received).decode('utf-8'), text)

    def test_cancel_discards_unsent_fragments_and_rejects_late_context_reuse(self):
        bridge = self.bridge()
        results = []
        bridge.commit(7, '你' * 1000, results.append)
        bridge.cancel(7)
        self.acknowledge(bridge)
        self.assertEqual(len(bridge.received), 1)
        self.assertEqual(bridge.fragments, [])
        self.assertEqual(results, [])
        bridge.commit(7, 'new', results.append)
        self.assertEqual(results, ['INPUT_CONTEXT_UNAVAILABLE'])

    def test_focus_loss_stops_a_long_commit_before_the_next_fragment(self):
        bridge = self.bridge()
        results = []
        bridge.commit(7, '你' * 1000, results.append)
        bridge.focused = None
        self.acknowledge(bridge)
        self.assertEqual(len(bridge.received), 1)
        self.assertEqual(bridge.fragments, [])
        self.assertEqual(results, ['INPUT_CONTEXT_UNAVAILABLE'])

    def test_main_loop_dispatches_buffered_xcb_events_without_a_readable_fd(self):
        source = xim.XCBSource.__new__(xim.XCBSource)
        source.owner = SimpleNamespace(queued=None, connection=1, poll_queued=lambda _: 123)
        source.fd = SimpleNamespace(revents=0)
        self.assertEqual(source.prepare(), (True, -1))
        self.assertTrue(source.check())

    def test_non_unicode_xim_context_is_rejected_before_delivery(self):
        for locale, supported in [('C', False), ('en_US.ISO8859-1', False), ('C.UTF-8', True), ('zh_CN.utf8', True)]:
            bridge = self.bridge()
            data = ctypes.create_string_buffer(locale.encode('ascii'))
            opened = xim.Open(length=len(locale), locale=ctypes.addressof(data))
            for opcode, frame in [(30, ctypes.pointer(opened)), (50, None), (58, None)]:
                bridge._callback(1, 8, 7, ctypes.pointer(xim.Header(major=opcode)), frame, None, None)
            self.assertEqual(bridge.focused, 7 if supported else None, locale)


if __name__ == '__main__':
    unittest.main()
