"""Task-owned GTK target: verify document text, not clipboard admission."""
import json
import os
import sys

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gio, GLib

sys.path.insert(0, sys.argv[1] + '/floe/host-desktop')
from host_desktop_identity import HostIdentity, x11_credentials

identity = HostIdentity(Gio, GLib)
os.environ.update(GDK_BACKEND=identity.backend, XDG_RUNTIME_DIR='/run/user/' + str(os.getuid()))
if identity.backend == 'x11':
    display, authority = x11_credentials(identity.selected, os.getuid())
    os.environ.update(DISPLAY=display, XAUTHORITY=authority)
else:
    os.environ['WAYLAND_DISPLAY'] = 'wayland-0'
identity.close()
from gi.repository import Gtk
Gtk.init([])
window = Gtk.Window(title='Native desktop text qualification')
window.set_default_size(800, 400)
entry = Gtk.Entry()
window.add(entry)
window.show_all()
entry.grab_focus()
expected = 'abC\u4e2d\u6587\U0001f600$'
passed = False


def ready():
    if not window.is_active():
        return True
    print('TEXT_TARGET_READY', flush=True)
    return False


def activated(_entry):
    global passed
    passed = entry.get_text() == expected
    print(json.dumps({'matched': passed, 'characters': len(entry.get_text())}), flush=True)
    Gtk.main_quit()


entry.connect('activate', activated)
GLib.timeout_add(100, ready)
GLib.timeout_add_seconds(20, lambda: Gtk.main_quit())
try:
    Gtk.main()
finally:
    window.destroy()
raise SystemExit(0 if passed else 1)
