"""The explicitly selected, authenticated host X11 desktop; never Xwayland capture."""
import re
import os
import hashlib

from host_desktop_contract import DesktopError


class X11Desktop:
    def __init__(self, name, GLib, clipboard_changed=lambda _: None):
        if not isinstance(name, str) or not re.fullmatch(r':[0-9]+(?:\.[0-9]+)?', name):
            raise DesktopError('X11_DISPLAY_INVALID')
        from Xlib import X, display
        from Xlib.ext import xtest
        import gi
        os.environ['GDK_BACKEND'] = 'x11'
        gi.require_version('Gdk', '3.0')
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gdk, Gtk
        self.X, self.xtest, self.Gdk = X, xtest, Gdk
        self.name, self.GLib = name, GLib
        self.connection = display.Display(name)
        if not self.connection.has_extension('XTEST') or not self.connection.has_extension('RANDR'):
            self.connection.close()
            raise DesktopError('X11_EXTENSIONS_UNAVAILABLE')
        self.display = Gdk.Display.open(name)
        if self.display is None:
            self.connection.close()
            raise DesktopError('X11_DISPLAY_UNAVAILABLE')
        self.clipboard = Gtk.Clipboard.get_for_display(self.display, Gdk.SELECTION_CLIPBOARD)
        self.clipboard_enabled = self.clipboard_sync = False
        self.clipboard_epoch = 0
        self.clipboard_changed = clipboard_changed
        self.last_clipboard = None
        self.clipboard_signal = self.clipboard.connect('owner-change', self._clipboard_changed)
        self.remainders = [0, 0]

    def displays(self):
        result = []
        for monitor in self.connection.screen().root.xrandr_get_monitors(True).monitors:
            name = self.connection.get_atom_name(monitor.name)
            # XTest and XCB acquisition share RandR pixel coordinates. GDK logical
            # monitor rectangles can differ under X11 scale settings.
            result.append({'id': 'x11-' + hashlib.sha256(name.encode()).hexdigest()[:24], 'name': name,
                'x': monitor.x, 'y': monitor.y, 'width': monitor.width_in_pixels, 'height': monitor.height_in_pixels,
                'scale': 1, 'primary': bool(monitor.primary)})
        return result

    def pointer(self, _stream, x, y):
        self.xtest.fake_input(self.connection, self.X.MotionNotify, x=int(x), y=int(y))
        self.connection.sync()

    def key(self, keycode, down):
        self.xtest.fake_input(self.connection, self.X.KeyPress if down else self.X.KeyRelease, keycode + 8)
        self.connection.sync()

    def button(self, button, down):
        buttons = {272: 1, 273: 3, 274: 2, 275: 8, 276: 9}
        self.xtest.fake_input(self.connection, self.X.ButtonPress if down else self.X.ButtonRelease, buttons[button])
        self.connection.sync()

    def scroll(self, x, y):
        for axis, delta in enumerate((x, y)):
            self.remainders[axis] += delta
            count = int(self.remainders[axis] / 40)
            self.remainders[axis] -= count * 40
            button = (7 if count > 0 else 6) if axis == 0 else (5 if count > 0 else 4)
            for _ in range(min(250, abs(count))):
                self.xtest.fake_input(self.connection, self.X.ButtonPress, button)
                self.xtest.fake_input(self.connection, self.X.ButtonRelease, button)
        self.connection.sync()

    def set_clipboard(self, text):
        self.last_clipboard = text
        self.clipboard.set_text(text, -1)

    def _clipboard_changed(self, *_):
        if not self.clipboard_enabled or not self.clipboard_sync:
            return
        epoch = self.clipboard_epoch
        def received(text, error):
            if (error is None and self.clipboard_enabled and self.clipboard_sync
                    and epoch == self.clipboard_epoch and text != self.last_clipboard):
                self.last_clipboard = text
                self.clipboard_changed(text)
        self.read_clipboard(received)

    def read_clipboard(self, completed):
        def received(_clipboard, text, _data):
            text = text or ''
            if len(text.encode()) > 1 << 20 or '\0' in text:
                completed(None, 'CLIPBOARD_TOO_LARGE')
            else:
                completed(text, None)
        self.clipboard.request_text(received, None)

    def close(self):
        self.clipboard_enabled = self.clipboard_sync = False
        self.clipboard_epoch += 1
        self.clipboard.disconnect(self.clipboard_signal)
        # Gtk owns the clipboard through its display. Release the GI wrapper
        # before GDK destroys that owner, including during interpreter shutdown.
        self.clipboard = None
        self.connection.close()
        self.display.close()
        self.display = None
