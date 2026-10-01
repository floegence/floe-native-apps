"""Native X11 adapter qualification on a task-owned authenticated Xvfb only."""
import argparse
import json
import os
from pathlib import Path
import secrets
import select
import subprocess
import sys
import tempfile
import time

parser = argparse.ArgumentParser()
parser.add_argument('--helper', required=True)
args = parser.parse_args()
sys.path.insert(0, args.helper)

with tempfile.TemporaryDirectory(prefix='floe-host-x11-') as state:
    authority = str(Path(state, 'Xauthority'))
    Path(authority).touch(mode=0o600)
    cookie = secrets.token_hex(16)
    subprocess.run(['xauth', '-f', authority, 'add', ':0', 'MIT-MAGIC-COOKIE-1', cookie], check=True)
    reader, writer = os.pipe()
    server = subprocess.Popen(['Xvfb', '-displayfd', str(writer), '-screen', '0', '1920x1080x24',
                               '-nolisten', 'tcp', '-noreset', '-auth', authority],
                              pass_fds=(writer,), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    os.close(writer)
    try:
        if not select.select([reader], [], [], 5)[0]:
            raise RuntimeError('task Xvfb did not allocate its display')
        number = os.read(reader, 32).decode().strip()
        if not number.isdecimal() or server.poll() is not None:
            raise RuntimeError('task Xvfb exited')
        name = ':' + number
        subprocess.run(['xauth', '-f', authority, 'add', name, 'MIT-MAGIC-COOKIE-1', cookie], check=True)
        os.environ.update(DISPLAY=name, XAUTHORITY=authority, GDK_BACKEND='x11')
        # A headless helper has no Gtk application window retaining a display.
        # Exercise finalization in its own interpreter so a late GI use-after-free
        # cannot be masked by the graphical fixture's global toolkit references.
        lifecycle = """
import sys
sys.path.insert(0, sys.argv[1])
from gi.repository import GLib
from host_desktop_x11 import X11Desktop
for _ in range(3):
    backend = X11Desktop(sys.argv[2], GLib)
    backend.close()
"""
        subprocess.run([str(Path(args.helper, 'python3')), '-c', lifecycle, args.helper, name],
                       check=True, timeout=10)
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('Gst', '1.0')
        from gi.repository import Gtk, Gdk, Gst, GLib
        from host_desktop_x11 import X11Desktop
        from host_desktop_input import HeldInput, physical_key
        from host_desktop_media import DesktopMedia, select_encoder
        Gst.init(None)
        Gtk.init([])
        window = Gtk.Window(title='Floe isolated host desktop qualification')
        window.set_default_size(600, 300)
        window.move(40, 40)
        entry = Gtk.Entry()
        entry.set_margin_top(24)
        entry.set_margin_start(24)
        entry.set_margin_end(24)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        layout.add(entry)
        button = Gtk.Button(label='Task-owned pointer target')
        layout.add(button)
        window.add(layout)
        window.show_all()
        entry.grab_focus()
        window.get_window().focus(Gdk.CURRENT_TIME)
        backend = X11Desktop(name, GLib)
        held = HeldInput(backend)
        displays = backend.displays()
        assert len(displays) == 1 and displays[0]['width'] == 1920 and displays[0]['height'] == 1080
        result = {'display':displays[0], 'frames':0, 'text':False, 'keys':False, 'clipboard':False, 'pointer':False}
        button.connect('clicked', lambda _: result.update(pointer=True))
        expected = 'Remote 中文輸入 😀'
        loop = GLib.MainLoop()
        def failure(code):
            result['error'] = code
            loop.quit()
            return False
        def output(message, _payload):
            result['frames'] += 1
            GLib.idle_add(lambda: (media.acknowledge(message['frame_id']), False)[1])
        media = DesktopMedia(Gst, GLib, 1, {'mode':'clarity', 'max_dimension':1920, 'frame_rate':60, 'audio':False},
                             select_encoder(Gst), output, failure)
        media.start_x11(name, (0, 0, 1920, 1080))
        def type_key():
            held.key(physical_key('ShiftLeft'), True)
            held.key(physical_key('KeyA'), True)
            held.key(physical_key('KeyA'), False)
            held.key(physical_key('ShiftLeft'), False)
            return False
        def paste():
            result['keys'] = entry.get_text() == 'A'
            entry.select_region(0, -1)
            backend.set_clipboard(expected)
            held.paste()
            return False
        def click():
            allocation = button.get_allocation()
            x, y = window.get_position()
            backend.pointer(0, x + allocation.x + allocation.width // 2, y + allocation.y + allocation.height // 2)
            held.button(272, True)
            held.button(272, False)
            return False
        def finish():
            result['text'] = entry.get_text() == expected
            host_text = 'Host clipboard 正體中文'
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(host_text, -1)
            def received(text, error):
                result['clipboard'] = error is None and text == host_text
                loop.quit()
            backend.read_clipboard(received)
            return False
        GLib.timeout_add(300, type_key)
        GLib.timeout_add(600, paste)
        GLib.timeout_add(850, click)
        GLib.timeout_add(1100, finish)
        GLib.timeout_add_seconds(8, lambda: failure('FIXTURE_TIMEOUT'))
        try:
            loop.run()
        finally:
            held.release()
            media.close()
            backend.close()
            window.destroy()
        print(json.dumps(result), flush=True)
        assert result['frames'] > 0 and result['keys'] and result['text'] and result['clipboard'] and result['pointer'] and not result.get('error')
    finally:
        os.close(reader)
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
