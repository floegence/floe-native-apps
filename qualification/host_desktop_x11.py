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
import threading
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
        from host_desktop_xcapture import Surface
        from host_desktop_contract import DesktopError
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
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        displays = backend.displays()
        assert len(displays) == 1 and displays[0]['width'] == 1920 and displays[0]['height'] == 1080
        # Compare raw acquisition against an independent XGetImage request on
        # the fixture's own X server. Repeated mappings must not retain FDs.
        descriptor_count = len(list(Path('/proc/self/fd').iterdir()))
        for _ in range(10):
            surface = Surface(name, (800, 600, 64, 64), threading.Event())
            try:
                pixels, cursor = surface.read()
                try:
                    reference = backend.connection.screen().root.get_image(800, 600, 64, 64, 2, 0xffffffff)
                    assert bytes(pixels) == reference.data
                finally:
                    pixels.release()
            finally:
                surface.close()
        assert len(list(Path('/proc/self/fd').iterdir())) == descriptor_count
        surface = Surface(name, (0, 0, 1920, 1080), threading.Event())
        try:
            fixture_display = window.get_display()
            # Stay in the toplevel's margin; Gtk.Entry owns a separate I-beam
            # cursor that would override a cursor set on its parent window.
            backend.pointer(0, 45, 45)
            fixture_display.sync()
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            pixels, first_cursor = surface.read()
            pixels.release()
            window.get_window().set_cursor(Gdk.Cursor.new_for_display(fixture_display, Gdk.CursorType.CROSSHAIR))
            fixture_display.sync()
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            deadline = time.monotonic() + 1
            while True:
                pixels, cursor = surface.read()
                if cursor[0] != first_cursor[0]:
                    break
                pixels.release()
                if time.monotonic() >= deadline:
                    raise AssertionError('cursor shape notification was not observed')
                time.sleep(.01)
            try:
                composed = surface.painter.compose(Gst, pixels, 1920, 1080, cursor)
                image = composed.extract_dup(0, composed.get_size())
                # A fully opaque cursor pixel must replace the corresponding
                # RGB triplet, including the server-provided hotspot offset.
                shape = surface.painter.pixels
                opaque = next(index for index in range(cursor[3] * cursor[4]) if shape[index * 4 + 3] == 255)
                x, y = cursor[1] + opaque % cursor[3], cursor[2] + opaque // cursor[3]
                offset = (y * 1920 + x) * 4
                assert image[offset:offset + 3] == shape[opaque * 4:opaque * 4 + 3]
            finally:
                pixels.release()
            cached_shape = surface.painter.pixels
            backend.pointer(0, 75, 45)
            pixels, moved_cursor = surface.read()
            pixels.release()
            assert surface.painter.pixels is cached_shape and moved_cursor[1] == cursor[1] + 30
        finally:
            surface.close()
        # X11 protocol errors stay within this connection and cannot abort GTK
        # or change another client's process-wide error handler.
        surface = Surface(name, (2000, 0, 64, 64), threading.Event())
        try:
            try:
                surface.read()
                raise AssertionError('out-of-bounds acquisition was accepted')
            except DesktopError as error:
                assert error.code == 'DISPLAY_GEOMETRY_CHANGED'
        finally:
            surface.close()
        result = {'display':displays[0], 'frames':0, 'text':False, 'keys':False, 'clipboard':False, 'pointer':False}
        result.update(raw_pixels=True, mapping_cleanup=True, protocol_failure_isolated=True,
                      cursor_shape=True, cursor_hotspot=True, cursor_position=True)
        recovering_at = None
        button.connect('clicked', lambda _: result.update(pointer=True))
        expected = 'Remote 中文輸入 😀'
        loop = GLib.MainLoop()
        def failure(code):
            result['error'] = code
            loop.quit()
            return False
        def output(message, _payload):
            result['frames'] += 1
            if message['generation'] == 2 and 'recovery_ms' not in result:
                assert message['codec'] == 'h264' and message['key'] and message['frame_id'] == 1
                result['recovery_ms'] = 1000 * (time.monotonic() - recovering_at)
            GLib.idle_add(lambda: (media.acknowledge(message['frame_id']), False)[1] if message['generation'] == media.generation else False)
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
        def recover():
            global recovering_at
            capture = media.x11_capture
            recovering_at = time.monotonic()
            media.recover(2)
            assert media.x11_capture is capture
            return False
        GLib.timeout_add(300, type_key)
        GLib.timeout_add(600, paste)
        GLib.timeout_add(850, click)
        GLib.timeout_add(950, recover)
        GLib.timeout_add(1800, finish)
        GLib.timeout_add_seconds(8, lambda: failure('FIXTURE_TIMEOUT'))
        try:
            loop.run()
        finally:
            held.release()
            media.close()
            backend.close()
            window.destroy()
        print(json.dumps(result), flush=True)
        assert result['frames'] > 0 and result['keys'] and result['text'] and result['clipboard'] and result['pointer'] and 'recovery_ms' in result and not result.get('error')
    finally:
        os.close(reader)
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
