"""Reproduce delayed native geometry without requiring Xpra in source CI."""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
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


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('cc'), 'Linux compiler fixture')
class GnomeSaveObserverTests(unittest.TestCase):
    def test_readiness_observation_preserves_original_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'original.c').write_text(
                '#include <stdio.h>\n'
                'void gtk_widget_action_set_enabled(void *w, const char *n, int e) '
                '{ printf("%p:%s:%d\\n", w, n, e); }\n')
            (root / 'caller.c').write_text(
                'void gtk_widget_action_set_enabled(void *, const char *, int);\n'
                'int main(void) { '
                'gtk_widget_action_set_enabled(0, "page.save", 1); '
                'gtk_widget_action_set_enabled(0, "page.save", 0); '
                'gtk_widget_action_set_enabled(0, "page.save", 1); '
                'gtk_widget_action_set_enabled(0, "page.close", 0); return 0; }\n')
            observer = Path(__file__).with_name('gnome_save_observer.c').resolve()
            for args in (
                ['-shared', '-fPIC', str(root / 'original.c'), '-o', str(root / 'original.so')],
                ['-shared', '-fPIC', '-Wall', '-Wextra', '-Werror', str(observer), '-ldl', '-o', str(root / 'observer.so')],
                [str(root / 'caller.c'), str(root / 'original.so'), '-o', str(root / 'caller')],
            ):
                subprocess.run(['cc', *args], check=True, capture_output=True)
            baseline = subprocess.check_output([root / 'caller'])
            environment = {**os.environ, 'LD_PRELOAD': str(root / 'observer.so')}
            self.assertEqual(subprocess.check_output([root / 'caller'], env=environment), baseline)
            receipt = root / 'save.json'
            environment['FLOE_GNOME_SAVE_RECEIPT'] = str(receipt)
            self.assertEqual(subprocess.check_output([root / 'caller'], env=environment), baseline)
            self.assertEqual(json.loads(receipt.read_text()), {'enabled': True, 'disabled': 1})
            self.assertFalse(receipt.with_suffix('.json.pending').exists())


if __name__ == '__main__':
    unittest.main()
