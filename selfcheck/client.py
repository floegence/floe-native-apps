"""Exercise window mapping, decoded pixels and input through Xpra's real protocol."""
import io
import json
import pathlib
import os
import socket
import sys
from urllib.parse import urlsplit

from PIL import Image
from xpra.client.base.gobject import GObjectXpraClient, GLib
from xpra.net.bytestreams import SocketConnection
from xpra.net import compression, packet_encoding
from xpra.scripts.config import make_defaults_struct, fixup_options

root = pathlib.Path(sys.argv[1])
address = sys.argv[2]
initial = json.loads((root / "receipt.json").read_text())
identity = root / "application-pid"
if identity.exists():
    assert int(identity.read_text()) == initial["pid"], "application changed between viewers"
else:
    identity.write_text(str(initial["pid"]))
compression.init_all()
packet_encoding.init_all()


class CheckClient(GObjectXpraClient):
    def __init__(self):
        super().__init__()
        self.picture = False
        self.clicked = False

    def make_hello(self):
        caps = super().make_hello()
        caps.update({"ui_client": True, "windows": True, "mouse": True,
                     "desktop_size": (1024, 768), "encodings": ("png",),
                     "encodings.core": ("png",), "encoding": "png",
                     "wants": ["features", "display", "encodings"]})
        return caps

    def init_packet_handlers(self):
        super().init_packet_handlers()
        self.add_packet_handler("new-window", self.new_window, True)
        self.add_packet_handler("draw", self.draw, True)
        self.add_packet_handler("encodings", lambda packet: None, False)

    def new_window(self, packet):
        wid, x, y, width, height = packet[1:6]
        self.send("map-window", wid, x, y, width, height, {})
        self.pointer = (wid, x + width // 2, y + height // 2)

    def draw(self, packet):
        wid, x, y, width, height, encoding, data, sequence = packet[1:9]
        try:
            if isinstance(encoding, bytes):
                encoding = encoding.decode("ascii")
            if encoding in ("rgb24", "rgb32"):
                options = packet[10]
                if options.get("lz4"):
                    from xpra.net.lz4.lz4 import decompress
                    data = decompress(data)
                image = Image.frombytes("RGB" if encoding == "rgb24" else "RGBA", (width, height), data,
                                        "raw", options.get("rgb_format", "RGB"), packet[9]).convert("L")
            else:
                assert encoding in ("png", "png/P", "png/L"), "unexpected picture format: " + repr(encoding)
                image = Image.open(io.BytesIO(data)).convert("L")
            assert image.width > 0 and image.height > 0, "empty picture"
            low, high = image.getextrema()
            if low != high:
                self.picture = True
            self.send("damage-sequence", sequence, wid, width, height, 0, "")
            if self.picture and not self.clicked:
                self.clicked = True
                GLib.timeout_add(200, self.click)
        except Exception as error:
            print(error, file=sys.stderr)
            self.quit(1)

    def click(self):
        wid, x, y = self.pointer
        self.send("focus", wid, [])
        self.send("pointer-position", wid, (x, y), [])
        self.send("button-action", wid, 1, True, (x, y), [])
        self.send("button-action", wid, 1, False, (x, y), [])
        return False

    def receipt(self):
        try:
            result = json.loads((root / "receipt.json").read_text())
            assert result["pid"] == initial["pid"], "application restarted during reconnect"
            if self.picture and result["input_count"] > initial["input_count"]:
                if address.startswith("ws://"):
                    # A browser may send a masked close frame with no reason.
                    # Flush it before local cleanup closes the TCP connection.
                    connection.sendall(b"\x88\x80" + os.urandom(4))
                self.quit(0)
                return False
        except (OSError, ValueError):
            pass
        return True


opts = make_defaults_struct()
fixup_options(opts)
client = CheckClient()
client.init(opts)
if address.startswith("ws://"):
    from xpra.net.websockets.common import client_upgrade
    endpoint = urlsplit(address)
    connection = socket.create_connection((endpoint.hostname, endpoint.port), timeout=5)
    client_upgrade(connection.recv, connection.send, endpoint.hostname, endpoint.port)
    connection.settimeout(None)
    client.setup_connection(SocketConnection(connection, "local", address, address, "ws"))
else:
    connection = socket.socket(socket.AF_UNIX)
    connection.connect(address)
    client.setup_connection(SocketConnection(connection, "local", address, address, "socket"))
GLib.timeout_add(100, client.receipt)
GLib.timeout_add_seconds(20, lambda: client.quit(1))
sys.exit(client.run())
