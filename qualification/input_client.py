"""Assert application receipts over the same Xpra transport as the viewer."""
import io
import json
from pathlib import Path
import socket
import sys
from urllib.parse import urlsplit
from PIL import Image
from xpra.client.base.gobject import GObjectXpraClient, GLib
from xpra.net.bytestreams import SocketConnection
from xpra.net import compression, packet_encoding
from xpra.net.websockets.common import client_upgrade
from xpra.scripts.config import make_defaults_struct, fixup_options

receipt, address, kind = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
density = int(sys.argv[4])
text = '你好日本語한글🙂👩🏽‍💻e\u0301𠮷\n第二行'
long_text = '你🙂e\u0301' * 1500
expected = (text + ('\r' if kind == 'terminal' else '\n')) * 40 + long_text
compression.init_all()
packet_encoding.init_all()


class Client(GObjectXpraClient):
    def __init__(self):
        super().__init__()
        self.started = False
        self.acknowledged = 0
        self.painted = False
        self.clicked = False
        self.hover_moves = 0
        self.failure = False
        self.editing = False
        self.fields = False
        self.field_sequence = 43
        self.pasting = False
        self.copying = False
        self.cutting = False
        self.clipboard = None
        self.display_configured = False

    def make_hello(self):
        caps = super().make_hello()
        caps.update({'ui_client': True, 'windows': True, 'mouse': True, 'keyboard': True, 'floe-input': 1,
                     'floe-display': 1, 'dpi': {'x':96, 'y':96},
                     'keyboard_sync': True, 'key_repeat': (0, 0),
                     'clipboard': {'enabled': True, 'greedy': True, 'want_targets': True,
                                   'selections': ['CLIPBOARD'], 'preferred-targets': ['UTF8_STRING']},
                     'keymap': {'layout': 'us', 'keycodes': [(65293, 'Return', 13, 0, 0),
                         (97, 'a', 65, 0, 0), (99, 'c', 67, 0, 0), (120, 'x', 88, 0, 0),
                         (118, 'v', 86, 0, 0), (65288, 'BackSpace', 8, 0, 0),
                         (65507, 'Control_L', 17, 0, 0), (65367, 'End', 35, 0, 0)]},
                     'desktop_size': (1024, 768), 'encodings': ('png',),
                     'encodings.core': ('png',), 'encoding': 'png',
                     'wants': ['features', 'display', 'encodings']})
        return caps

    def init_packet_handlers(self):
        super().init_packet_handlers()
        for name, handler in (('new-window', self.new_window), ('draw', self.draw),
                              ('configure-window', self.configure_window),
                              ('window-resized', self.window_resized),
                              ('window-move-resize', self.window_move_resize),
                              ('floe-input-result', self.result), ('encodings', lambda _: None),
                              ('clipboard-token', self.clipboard_token), ('clipboard-pending-requests', lambda _: None),
                              ('raise-window', lambda _: None)):
            self.add_packet_handler(name, handler, True)

    def new_window(self, packet):
        self.wid, x, y, width, height = packet[1:6]
        self.origin = (x, y)
        self.canvas = Image.new('RGB', (width, height))
        self.send('map-window', self.wid, x, y, width, height, {})
        self.send('focus', self.wid, [])

    def configure_window(self, packet):
        if packet[1] == self.wid:
            self.origin = tuple(packet[2:4])

    def window_resized(self, packet):
        if packet[1] == self.wid:
            canvas = Image.new('RGB', tuple(packet[2:4]))
            canvas.paste(self.canvas, (0, 0))
            self.canvas = canvas

    def window_move_resize(self, packet):
        if packet[1] == self.wid:
            self.origin = tuple(packet[2:4])
            self.window_resized(['window-resized', self.wid, *packet[4:6]])

    def draw(self, packet):
        encoding, data = packet[6:8]
        if isinstance(encoding, bytes):
            encoding = encoding.decode('ascii')
        if encoding in ('rgb24', 'rgb32'):
            options = packet[10]
            if options.get('lz4'):
                from xpra.net.lz4.lz4 import decompress
                data = decompress(data)
            frame = Image.frombytes('RGB' if encoding == 'rgb24' else 'RGBA', (packet[4], packet[5]),
                                    data, 'raw', options.get('rgb_format', 'RGB'), packet[9]).convert('RGB')
        else:
            if encoding not in ('png', 'png/P', 'png/L'):
                raise RuntimeError('Unexpected fixture image encoding')
            frame = Image.open(io.BytesIO(data)).convert('RGB')
        self.canvas.paste(frame, (packet[2], packet[3]))
        self.canvas.save(str(receipt) + '.png')
        self.painted = True
        self.send('damage-sequence', packet[8], packet[1], packet[4], packet[5], 0, '')

    def ready(self):
        if not self.painted or self.started:
            return True
        if not self.display_configured:
            self.display_configured = True
            self.send('configure-display', {'desktop-size':(1024*density,768*density),
                      'floe-display-density':density,'dpi':{'x':96*density,'y':96*density}})
            return True
        if kind == 'chromium':
            if not receipt.with_suffix('.ready').exists():
                return True
            if not receipt.with_suffix('.hover').exists():
                self.hover_moves += 1
                self.send('pointer-position', self.wid,
                          [self.origin[0] + 80 + self.hover_moves % 2, self.origin[1] + 80], [])
                return True
            if not self.clicked:
                self.clicked = True
                coords = [self.origin[0] + 80, self.origin[1] + 80]
                self.send('pointer-position', self.wid, coords, [])
                self.send('button-action', self.wid, 1, True, coords, [])
                self.send('button-action', self.wid, 1, False, coords, [])
            if not receipt.with_suffix('.clicked').exists():
                return True
        elif kind == 'terminal' and not receipt.with_suffix('.ready').exists():
            return True
        elif kind != 'terminal' and not receipt.exists():
            return True
        if kind == 'gtk':
            try:
                actual_density = json.loads(receipt.with_suffix('.density.json').read_text())
            except (OSError, ValueError):
                return True
            if actual_density['scale'] != density:
                return True
            if actual_density['dpi'] != 96:
                raise RuntimeError('GTK logical text DPI changed with backing density')
        self.started = True
        for sequence in range(1, 41):
            self.send('floe-input', sequence, self.wid, text)
            self.send('key-action', self.wid, 'Return', True, [], 65293, '', 13, 0)
            self.send('key-action', self.wid, 'Return', False, [], 65293, '', 13, 0)
        self.send('floe-input', 41, self.wid, long_text)
        return False

    def result(self, packet):
        if packet[2]:
            print('REJECTED', packet[1], packet[2], flush=True)
            self.failure = True
            self.quit(1)
            return
        self.acknowledged += 1

    def clipboard_token(self, packet):
        if len(packet) >= 8 and packet[1] == 'CLIPBOARD' and packet[3] == 'UTF8_STRING':
            self.clipboard = packet[7].decode('utf-8') if isinstance(packet[7], bytes) else packet[7]

    def shortcut(self, name, code):
        self.send('key-action', self.wid, 'Control_L', True, ['control'], 65507, '', 17, 0)
        self.send('key-action', self.wid, name, True, ['control'], ord(name), name, code, 0)
        self.send('key-action', self.wid, name, False, ['control'], ord(name), name, code, 0)
        self.send('key-action', self.wid, 'Control_L', False, [], 65507, '', 17, 0)

    def check(self):
        try:
            actual = json.loads(receipt.read_text())
        except (OSError, ValueError):
            return True
        wanted = expected if kind == 'terminal' else [expected, '']
        if not self.failure and not self.editing and self.acknowledged == 41 and actual == wanted:
            print('PASS', kind, '40 exact Unicode/Enter pairs and one 15000-byte Unicode commit', flush=True)
            if kind != 'terminal':
                receipt.with_suffix('.unicode.json').write_text(json.dumps(actual))
                self.editing = True
                self.send('key-action', self.wid, 'Control_L', True, ['control'], 65507, '', 17, 0)
                self.send('key-action', self.wid, 'a', True, ['control'], 97, 'a', 65, 0)
                self.send('key-action', self.wid, 'a', False, ['control'], 97, 'a', 65, 0)
                self.send('key-action', self.wid, 'Control_L', False, [], 65507, '', 17, 0)
                self.send('floe-input', 42, self.wid, '完成🙂Z')
                self.send('key-action', self.wid, 'BackSpace', True, [], 65288, '', 8, 0)
                self.send('key-action', self.wid, 'BackSpace', False, [], 65288, '', 8, 0)
                return True
            self.quit(0)
            return False
        if not self.failure and self.editing and not self.fields and self.acknowledged == 42 and actual == ['完成🙂', '']:
            print('PASS', kind, 'selection replacement and deletion preserve exact Unicode', flush=True)
            self.fields = True
            return True
        fields = ['完成🙂' + ''.join('[' + str(n) + ']🙂' for n in range(43, 55, 2)),
                  ''.join('[' + str(n) + ']🙂' for n in range(44, 55, 2))]
        if not self.failure and self.fields and not self.pasting and self.field_sequence < 55:
            sequence = self.field_sequence
            self.field_sequence += 1
            x = self.canvas.width * (1 if sequence % 2 else 3) // 4
            # Xpra window packets remain logical dimensions while the private
            # X11 display is native-density pixels. Pointer packets therefore
            # use the negotiated physical coordinate space.
            coords = [self.origin[0] + x * density, self.origin[1] + 80 * density]
            self.send('pointer-position', self.wid, coords, [])
            self.send('button-action', self.wid, 1, True, coords, [])
            self.send('button-action', self.wid, 1, False, coords, [])
            self.send('key-action', self.wid, 'Control_L', True, ['control'], 65507, '', 17, 0)
            self.send('key-action', self.wid, 'End', True, ['control'], 65367, '', 35, 0)
            self.send('key-action', self.wid, 'End', False, ['control'], 65367, '', 35, 0)
            self.send('key-action', self.wid, 'Control_L', False, [], 65507, '', 17, 0)
            self.send('floe-input', sequence, self.wid, '[' + str(sequence) + ']🙂')
            return True
        if not self.failure and self.fields and not self.pasting and self.acknowledged == 54 and actual == fields:
            print('PASS', kind, '12 pointer focus changes preserve exact per-field order', flush=True)
            self.pasting = True
            self.send('clipboard-token', 'CLIPBOARD', ['UTF8_STRING'], 'UTF8_STRING', 'UTF8_STRING',
                      8, 'bytes', '粘贴🙂'.encode('utf-8'), True, True, True)
            self.shortcut('v', 86)
            return True
        if self.pasting and not self.copying and actual == [fields[0], fields[1] + '粘贴🙂']:
            print('PASS', kind, 'clipboard claim precedes one native paste shortcut', flush=True)
            self.copying = True
            self.shortcut('a', 65)
            self.shortcut('c', 67)
            return True
        if self.copying and not self.cutting and self.clipboard == fields[1] + '粘贴🙂':
            print('PASS', kind, 'native copy returns the exact selected Unicode', flush=True)
            self.cutting = True
            self.shortcut('x', 88)
            return True
        if self.cutting and actual == [fields[0], ''] and self.clipboard == fields[1] + '粘贴🙂':
            print('PASS', kind, 'native cut removes the selection once', flush=True)
            self.quit(0)
            return False
        return True

    def expired(self):
        print('TIMEOUT', kind, 'painted=', self.painted, 'started=', self.started,
              'acknowledged=', self.acknowledged, 'document_ready=', receipt.with_suffix('.ready').exists(),
              'hovered=', receipt.with_suffix('.hover').exists(), 'clicked=', receipt.with_suffix('.clicked').exists(), flush=True)
        self.quit(1)
        return False


opts = make_defaults_struct()
opts.password_file = [str(receipt.with_suffix('.password'))]
fixup_options(opts)
client = Client()
client.init(opts)
endpoint = urlsplit(address)
connection = socket.create_connection((endpoint.hostname, endpoint.port), timeout=5)
client_upgrade(connection.recv, connection.send, endpoint.hostname, endpoint.port)
connection.settimeout(None)
client.setup_connection(SocketConnection(connection, 'qualification', address, address, 'ws'))
client._protocol.large_packets.append('floe-input')
GLib.timeout_add(20, client.ready)
GLib.timeout_add(20, client.check)
GLib.timeout_add_seconds(30, client.expired)
sys.exit(client.run())
