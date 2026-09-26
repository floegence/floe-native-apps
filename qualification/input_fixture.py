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
    from gi.repository import Gtk, GLib, Gio
    # Every qualification process owns its controls, including mixed-protocol
    # tests sharing one private bus. Never delegate to another fixture process.
    app = Gtk.Application(application_id='org.floegence.InputQualification',
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    def activate(application):
        window = Gtk.ApplicationWindow(application=application, title='Floe GTK4 input qualification')
        color = os.environ.get('FLOE_TEST_WINDOW_COLOR')
        if color:
            import re
            assert re.fullmatch('[0-9a-f]{6}', color)
            css = Gtk.CssProvider()
            css.load_from_data(('textview text { background-color: #' + color + '; }').encode())
            Gtk.StyleContext.add_provider_for_display(window.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
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
        if os.environ.get('FLOE_TEST_WINDOW_ACTIONS'):
            from gi.repository import Gdk
            Gtk.Settings.get_default().set_property('gtk-cursor-blink', False)
            Gtk.Settings.get_default().set_property('gtk-enable-animations', False)
            state = {'popup_clicks': 0, 'popup_closed': 0, 'dialog_clicks': 0, 'dialog_closed': 0,
                     'control_down': False, 'control_releases': 0, 'scroll_x': 0, 'scroll_y': 0, 'keys': []}
            state_path = receipt.with_suffix('.windows.json')
            def write_state():
                pending = state_path.with_suffix('.pending')
                pending.write_text(json.dumps(state))
                pending.replace(state_path)
            css = Gtk.CssProvider()
            css.load_from_data(b'.floe-popup { background: #13b749; padding: 20px; } '
                               b'.floe-dialog { background: #bf31bd; padding: 20px; } '
                               b'.floe-scroll text { background: #b3801a; }')
            Gtk.StyleContext.add_provider_for_display(window.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            def pressed(_controller, keyval, _keycode, _state):
                state['keys'] = (state['keys'] + [keyval])[-128:]
                write_state()
                if keyval == Gdk.KEY_Control_L:
                    state['control_down'] = True
                    write_state()
                    return False
                elif keyval == Gdk.KEY_F2:
                    popup = Gtk.Popover()
                    popup.set_parent(editors[0])
                    anchor = Gdk.Rectangle()
                    anchor.x, anchor.y, anchor.width, anchor.height = 90, 100, 20, 20
                    popup.set_pointing_to(anchor)
                    popup.set_autohide(True)
                    button = Gtk.Button(label='Record popup click')
                    button.add_css_class('floe-popup')
                    def clicked(_button):
                        state['popup_clicks'] += 1
                        write_state()
                        if state['popup_clicks'] == 1:
                            button.set_size_request(380, 140)
                            anchor.x = 210
                            popup.set_pointing_to(anchor)
                        else:
                            popup.popdown()
                    button.connect('clicked', clicked)
                    popup.set_child(button)
                    def closed(_popup):
                        popup.unparent()
                        state['popup_closed'] += 1
                        write_state()
                    popup.connect('closed', closed)
                    popup.popup()
                elif keyval == Gdk.KEY_F3:
                    dialog = Gtk.Window(title='Floe transient fixture', transient_for=window, modal=True)
                    dialog.set_default_size(360, 180)
                    button = Gtk.Button(label='Record dialog click')
                    button.add_css_class('floe-dialog')
                    def clicked(_button):
                        state['dialog_clicks'] += 1
                        write_state()
                    def closed(_dialog):
                        state['dialog_closed'] += 1
                        write_state()
                        return False
                    button.connect('clicked', clicked)
                    dialog.connect('close-request', closed)
                    dialog.set_child(button)
                    dialog.present()
                elif keyval == Gdk.KEY_F4:
                    dialog = Gtk.Window(title='Floe scroll fixture', transient_for=window, modal=True)
                    dialog.set_default_size(600, 400)
                    scroll = Gtk.ScrolledWindow()
                    content = Gtk.TextView()
                    content.get_buffer().set_text(('Scroll qualification ' * 20 + '\n') * 100)
                    content.add_css_class('floe-scroll')
                    scroll.set_child(content)
                    for axis, adjustment in (('x', scroll.get_hadjustment()), ('y', scroll.get_vadjustment())):
                        def changed(value, axis=axis):
                            state['scroll_' + axis] = value.get_value()
                            write_state()
                        adjustment.connect('value-changed', changed)
                        def bounds(value, axis=axis):
                            state['scroll_bounds_' + axis] = [value.get_upper(), value.get_page_size()]
                            write_state()
                        adjustment.connect('changed', bounds)
                    dialog.set_child(scroll)
                    dialog.present()
                elif keyval == Gdk.KEY_F5:
                    window.set_title('Document 日本語 🧑🏽\u200d💻\nSave As')
                else:
                    return False
                return True
            keys = Gtk.EventControllerKey()
            keys.connect('key-pressed', pressed)
            def released(_controller, keyval, _keycode, _state):
                if keyval == Gdk.KEY_Control_L:
                    state['control_down'] = False
                    state['control_releases'] += 1
                    write_state()
            keys.connect('key-released', released)
            window.add_controller(keys)
            write_state()
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
