"""Installation-only GTK application with actual document and pixel receipts."""
import json
import os
from pathlib import Path
import sys

# This installation-owned application runs with the component's musl toolkit.
# Its verified IBus module/cache is distinct from native host glibc adapters.
os.environ['GTK_IM_MODULE'] = 'ibus'
os.environ['GTK_IM_MODULE_FILE'] = sys.argv[2]
os.environ.pop('GTK_PATH', None)
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk

receipt = Path(sys.argv[1])
window = Gtk.Window(title='Native desktop installation check')
window.set_default_size(640, 480)
editor = Gtk.TextView()
window.add(editor)
style = Gtk.CssProvider()
style.load_from_data(b'textview text { background-color: #13579b; color: white; }')
Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
buffer = editor.get_buffer()
def record(*unused):
    contents = {'pid': os.getpid(), 'text': buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)}
    temporary = receipt.with_suffix('.tmp')
    temporary.write_text(json.dumps(contents))
    temporary.replace(receipt)
buffer.connect('changed', record)
window.connect('destroy', Gtk.main_quit)
window.show_all()
editor.grab_focus()
record()
Gtk.main()
