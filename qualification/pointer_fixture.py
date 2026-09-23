"""Native GTK scrolling and button receipts for the prepared pointer client."""
import json
from pathlib import Path
import sys
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk
receipt = Path(sys.argv[2])
state = {'clicks': 0, 'doubles': 0, 'rights': 0, 'drag': 20, 'inset': 0}
window = Gtk.Window(title='Floe pointer qualification')
window.set_default_size(640, 480)
fixed = Gtk.Fixed()
outer = Gtk.ScrolledWindow()
outer.set_size_request(640, 480)
content = Gtk.Fixed()
content.set_size_request(2000, 3000)
outer.add_with_viewport(content)
fixed.put(outer, 0, 0)
button = Gtk.Button(label='Tap target')
button.set_size_request(180, 60)
content.put(button, 20, 220)
field = Gtk.Entry(text='Select this text')
content.put(field, 20, 350)
drag = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
drag.set_value(20)
drag.set_size_request(260, 50)
content.put(drag, 20, 100)
inner = Gtk.ScrolledWindow()
inner.set_size_request(240, 240)
rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
rows.set_size_request(1400, 1800)
for n in range(60):
    rows.pack_start(Gtk.Label(label=f'Nested row {n:02d}', xalign=0), False, False, 6)
inner.add_with_viewport(rows)
fixed.put(inner, 340, 70)
window.add(fixed)


def save(*_):
    state['outer'] = [outer.get_hadjustment().get_value(), outer.get_vadjustment().get_value()]
    state['inner'] = [inner.get_hadjustment().get_value(), inner.get_vadjustment().get_value()]
    pending = receipt.with_suffix('.pending')
    pending.write_text(json.dumps(state))
    pending.replace(receipt)


def click(*_):
    state['clicks'] += 1
    save()


def press(_widget, event):
    if event.button == 3:
        state['rights'] += 1
    if event.type == Gdk.EventType._2BUTTON_PRESS:
        state['doubles'] += 1
    save()
    return False


button.connect('clicked', click)
window.connect('button-press-event', press)
for scroll in (outer, inner):
    for adjustment in (scroll.get_hadjustment(), scroll.get_vadjustment()):
        adjustment.connect('value-changed', save)
drag.connect('value-changed', lambda *_: (state.update(drag=drag.get_value()), save()))
window.connect('destroy', Gtk.main_quit)
window.show_all()
save()
Gtk.main()
