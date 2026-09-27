"""Reproduce delayed native geometry without requiring Xpra in source CI."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest


class NativeGeometryTests(unittest.TestCase):
    def client(self, directory):
        # Execute the actual client class; only its Xpra transport is replaced.
        # Native qualification separately delivers this same burst to widgets.
        source = Path(__file__).with_name('input_client.py')
        tree = ast.parse(source.read_text(), filename=str(source))
        definition = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Client')
        receipt = Path(directory) / 'received.json'
        receipt.write_text(json.dumps(['完成🙂', '']))
        receipt.with_suffix('.density.json').write_text(json.dumps({'scale': 2, 'dpi': 96, 'size': [640, 320]}))
        namespace = {'GObjectXpraClient': object, 'receipt': receipt, 'json': json,
                     'kind': 'gtk4', 'density': 2, 'expected': 'unused'}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), 'exec'), namespace)
        client = namespace['Client']()
        client.editing, client.acknowledged = True, 42
        client.origin, client.wid = (0, 0), 1
        client.canvas = SimpleNamespace(width=2048, height=1536, size=(2048, 1536))
        packets = []
        client.send = lambda *packet: packets.append(packet)
        return client, packets

    def test_requested_size_does_not_authorize_out_of_window_focus_clicks(self):
        with tempfile.TemporaryDirectory() as directory:
            client, packets = self.client(directory)
            self.assertTrue(client.check())
            self.assertEqual(packets, [])
            self.assertFalse(client.fields)

    def test_actual_size_releases_the_entire_unsplit_focus_burst(self):
        with tempfile.TemporaryDirectory() as directory:
            client, packets = self.client(directory)
            client.canvas = SimpleNamespace(width=1280, height=640, size=(1280, 640))
            self.assertTrue(client.check())
            points = [packet[2] for packet in packets if packet[0] == 'pointer-position']
            self.assertEqual(points, [[320, 160], [960, 160]] * 6)
            self.assertEqual([packet[1] for packet in packets if packet[0] == 'floe-input'], list(range(43, 55)))


if __name__ == '__main__':
    unittest.main()
