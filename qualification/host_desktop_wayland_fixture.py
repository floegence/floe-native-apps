"""Current-desktop interaction fixture; only its focused window accepts test input.

Uses the installed native helper and the host's existing Wayland compositor.
Never changes desktop policy or saves desktop pixels or original clipboard text.
"""
import argparse
import os
from pathlib import Path
import signal
import json
import struct
import subprocess
import sys
import threading


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--helper', required=True)
    parser.add_argument('--state', required=True)
    parser.add_argument('--media-fd', type=int, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.helper).resolve()))
    os.environ['GDK_BACKEND'] = 'wayland'
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, Gdk, GLib
    from host_desktop_wire import Writer, read_command
    Gtk.init([])
    window = Gtk.Window(title='Floe task-owned remote desktop qualification')
    window.set_default_size(900, 650)
    window.fullscreen()
    layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    layout.set_border_width(24)
    entry = Gtk.Entry()
    entry.set_placeholder_text('Task-owned input fixture')
    layout.pack_start(entry, False, False, 0)
    button = Gtk.Button(label='Task-owned pointer target')
    button.set_size_request(-1, 70)
    layout.pack_start(button, False, False, 0)
    marker = Gtk.EventBox()
    marker.set_size_request(-1, 80)
    layout.pack_start(marker, False, False, 0)
    scroll = Gtk.ScrolledWindow()
    text = Gtk.TextView()
    text.set_editable(False)
    text.get_buffer().set_text(''.join('Office fixture row %d: text and 0123456789.\n' % i for i in range(2500)))
    scroll.add(text)
    layout.pack_start(scroll, True, True, 0)
    window.add(layout)
    window.show_all()
    entry.grab_focus()
    clicks, changes = 0, 0
    motion = None
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    original_text = None
    original_saved = False
    task_clipboard = set()

    def stopped(_code):
        GLib.idle_add(Gtk.main_quit)
    control = Writer(1, stopped, 64, 16 << 20)
    native = subprocess.Popen([str(Path(args.helper, 'python3')), str(Path(args.helper, 'host_desktop_helper.py')),
        '--state', args.state, '--media-fd', str(args.media_fd)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        pass_fds=(args.media_fd,))
    generation = 0
    def native_command(value):
        encoded = json.dumps(value, separators=(',', ':')).encode()
        native.stdin.write(struct.pack('!I', len(encoded)) + encoded)
        native.stdin.flush()
    def observe_native():
        nonlocal generation
        try:
            while True:
                prefix = native.stdout.read(4)
                if not prefix:
                    break
                size = struct.unpack('!I', prefix)[0]
                if not 0 < size <= 8 << 20:
                    raise RuntimeError('invalid native control header')
                value = json.loads(native.stdout.read(size))
                if value.get('bytes', 0):
                    raise RuntimeError('media arrived on native control pipe')
                if value.get('type') == 'state':
                    generation = value['generation']
                control.send(value)
        finally:
            stopped('NATIVE_CLOSED')
    threading.Thread(target=observe_native, daemon=True).start()

    def changed(*_):
        nonlocal changes
        changes += 1
        marker.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 0, 0, 1) if changes % 2 else Gdk.RGBA(0, 0, 1, 1))
    def clicked(*_):
        nonlocal clicks
        clicks += 1
        changed()
    entry.connect('changed', changed)
    button.connect('clicked', clicked)
    marker.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 1, 1))
    window.connect('destroy', lambda *_: Gtk.main_quit())

    def status():
        allocation = button.get_allocation()
        size = window.get_allocation()
        control.send({'type': 'fixture', 'active': window.is_active(), 'text': entry.get_text(),
            'clicks': clicks, 'changes': changes, 'width': size.width, 'height': size.height,
            'x': (allocation.x + allocation.width / 2) / size.width,
            'y': (allocation.y + allocation.height / 2) / size.height})

    def remember_then(action):
        nonlocal original_text, original_saved
        if original_saved:
            action()
            return
        def received(_clipboard, text, _data):
            nonlocal original_text, original_saved
            original_text, original_saved = text, True
            action()
        clipboard.request_text(received, None)

    def command(value):
        nonlocal original_text, original_saved, motion
        method = value['method']
        if method == 'fixture_status':
            status()
        elif method == 'fixture_focus':
            window.present()
            entry.grab_focus()
            status()
        elif method == 'fixture_clipboard':
            if not window.is_active():
                control.send({'type': 'error', 'id': value['id'], 'code': 'FIXTURE_NOT_FOCUSED'})
                return False
            def apply():
                if 'text' in value:
                    task_clipboard.add(value['text'])
                    clipboard.set_text(value['text'], -1)
                if 'expected' in value:
                    def received(_clipboard, text, _data):
                        matches = text == value['expected']
                        if matches:
                            task_clipboard.add(value['expected'])
                        control.send({'type': 'fixture_clipboard', 'matches': matches})
                    clipboard.request_text(received, None)
            remember_then(apply) if 'text' in value else apply()
        elif method == 'fixture_animate':
            if motion:
                GLib.source_remove(motion)
                motion = None
            if value.get('scene') == 'scroll':
                def advance():
                    adjustment = scroll.get_vadjustment()
                    maximum = adjustment.get_upper() - adjustment.get_page_size()
                    adjustment.set_value((adjustment.get_value() + 4) % max(1, maximum))
                    return True
                motion = GLib.timeout_add(16, advance)
            control.send({'type': 'fixture_motion', 'scene': value.get('scene')})
        elif method in ('input', 'set_clipboard', 'get_clipboard', 'set_clipboard_sync', 'lock') and not window.is_active():
            native_command({'version': 1, 'id': value['id'] + 1000000, 'method': 'release_input', 'generation': generation})
            control.send({'type': 'error', 'id': value['id'], 'code': 'FIXTURE_NOT_FOCUSED'})
        else:
            if method == 'input' and value.get('input', {}).get('kind') in ('text', 'paste'):
                task_clipboard.add(value['input']['text'])
                remember_then(lambda: native_command(value))
            elif method == 'set_clipboard':
                task_clipboard.add(value.get('text'))
                remember_then(lambda: native_command(value))
            else:
                native_command(value)
        return False

    def read():
        source = os.fdopen(os.dup(0), 'rb')
        try:
            while True:
                value = read_command(source)
                if value is None:
                    break
                GLib.idle_add(command, value)
        finally:
            stopped('EOF')
    threading.Thread(target=read, daemon=True).start()
    for signum in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signum, lambda: (Gtk.main_quit(), False)[1])
    try:
        Gtk.main()
    finally:
        if original_saved and window.is_active():
            # Do not block the same main loop that serves portal clipboard I/O.
            restored = []
            def restore(_clipboard, text, _data):
                if text in task_clipboard:
                    clipboard.set_text(original_text or '', -1)
                    clipboard.store()
                restored.append(True)
            clipboard.request_text(restore, None)
            import time
            deadline = time.monotonic() + 1
            while not restored and time.monotonic() < deadline:
                GLib.MainContext.default().iteration(False)
                time.sleep(.005)
        native.stdin.close()
        try:
            native.wait(timeout=5)
        except subprocess.TimeoutExpired:
            native.terminate()
            native.wait(timeout=5)
        window.destroy()
        control.close()


if __name__ == '__main__':
    main()
