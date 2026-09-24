"""Real toolkit documents; only deterministic fixture text is persisted."""
import json
import os
from pathlib import Path
import sys

kind, filename = sys.argv[1:3]
receipt = Path(filename)


def save(value):
    pending = receipt.with_suffix('.pending')
    pending.write_text(json.dumps(value))
    pending.replace(receipt)


if kind == 'gtk':
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, GLib
    window = Gtk.Window(title='Floe client input qualification')
    editors = [Gtk.TextView(), Gtk.TextView()]
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
    buffers = [editor.get_buffer() for editor in editors]
    for editor, buffer in zip(editors, buffers):
        scroll = Gtk.ScrolledWindow()
        scroll.add(editor)
        row.pack_start(scroll, True, True, 0)
        buffer.connect('changed', lambda *_: save([b.get_text(b.get_start_iter(), b.get_end_iter(), True) for b in buffers]))
    window.add(row)
    window.set_default_size(640, 320)
    window.connect('destroy', Gtk.main_quit)
    window.show_all()
    def save_density():
        receipt.with_suffix('.density.json').write_text(json.dumps({
            'scale':window.get_scale_factor(),
            'dpi':Gtk.Settings.get_default().get_property('gtk-xft-dpi') / 1024}))
        return True
    GLib.timeout_add(100, save_density)
    editors[0].grab_focus()
    save(['', ''])
    Gtk.main()
elif kind in ('gtk4', 'gtk4-entry'):
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk, GLib
    app = Gtk.Application(application_id='org.floegence.InputQualification')
    def activate(application):
        window = Gtk.ApplicationWindow(application=application, title='Floe GTK4 input qualification')
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        editors = []
        def contents():
            if kind == 'gtk4-entry':
                return [editor.get_text() for editor in editors]
            return [editor.get_buffer().get_text(*editor.get_buffer().get_bounds(), True) for editor in editors]
        for _ in range(2):
            editor = Gtk.Entry() if kind == 'gtk4-entry' else Gtk.TextView()
            editors.append(editor)
            editor.set_hexpand(True)
            editor.set_vexpand(True)
            if kind == 'gtk4-entry':
                editor.set_valign(Gtk.Align.START)
                editor.set_margin_top(60)
                editor.connect('changed', lambda *_: save(contents()))
                row.append(editor)
            else:
                scroll = Gtk.ScrolledWindow()
                scroll.set_child(editor)
                scroll.set_hexpand(True)
                row.append(scroll)
                editor.get_buffer().connect('changed', lambda *_: save(contents()))
        window.set_child(row)
        window.set_default_size(640, 320)
        window.present()
        editors[0].grab_focus()
        save(['', ''])
        def save_density():
            receipt.with_suffix('.density.json').write_text(json.dumps({
                'scale':window.get_scale_factor(),
                'dpi':Gtk.Settings.get_default().get_property('gtk-xft-dpi') / 1024}))
            return True
        GLib.timeout_add(100, save_density)
    app.connect('activate', activate)
    app.run([])
elif kind in ('qt5', 'qt6'):
    module = __import__('PyQt' + kind[-1] + '.QtWidgets', fromlist=['QApplication', 'QTextEdit'])
    app = module.QApplication(sys.argv)
    window = module.QWidget()
    window.setWindowTitle('Floe client input qualification')
    window.resize(640, 320)
    layout = module.QHBoxLayout(window)
    editors = [module.QTextEdit(), module.QTextEdit()]
    for editor in editors:
        layout.addWidget(editor)
        editor.textChanged.connect(lambda: save([e.toPlainText() for e in editors]))
    window.show()
    editors[0].setFocus()
    window.activateWindow()
    save(['', ''])
    app.exec()
elif kind == 'terminal':
    import termios
    import tty
    tty.setraw(sys.stdin.fileno(), termios.TCSANOW)
    os.write(sys.stdout.fileno(), b'Floe client input qualification\r\n')
    receipt.with_suffix('.ready').touch()
    received = b''
    while True:
        received += os.read(sys.stdin.fileno(), 65536)
        try:
            value = received.decode('utf-8')
        except UnicodeDecodeError:
            continue
        save(value)
