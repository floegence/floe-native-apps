"""Prove oversized native dialogs retain visible, correctly hit-tested controls."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import traceback


def application(receipt):
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk, Gio, GLib
    app = Gtk.Application(application_id='org.floegence.OversizedQualification', flags=Gio.ApplicationFlags.NON_UNIQUE)
    clicks = []
    def activate(app):
        parent = Gtk.ApplicationWindow(application=app, title='Floe oversized parent')
        parent.set_default_size(640, 320)
        parent.set_child(Gtk.Label(label='Only the task-owned parent remains after closing the dialog'))
        css = Gtk.CssProvider()
        css.load_from_data(b'.near, .far { border: 0; border-radius: 0; box-shadow: none; transition: none; } '
                           b'.near { background: #c74a39; } .far { background: #3196cb; }')
        Gtk.StyleContext.add_provider_for_display(parent.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        def open_dialog():
            dialog = Gtk.Window(title='Floe oversized dialog', application=app, transient_for=parent)
            content = Gtk.Fixed()
            content.set_size_request(1600, 1100)
            for name, x, y in [('near', 20, 20), ('far', 1430, 950)]:
                button = Gtk.Button(label='Record ' + name + ' click')
                button.add_css_class(name)
                button.set_size_request(140, 100)
                def clicked(_button, name=name):
                    clicks.append(name)
                    temporary = receipt.with_suffix('.pending')
                    temporary.write_text(json.dumps(clicks))
                    temporary.replace(receipt)
                button.connect('clicked', clicked)
                content.put(button, x, y)
            dialog.set_child(content)
            pointer = Gtk.GestureClick(button=1)
            pointer.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            def observed(controller, count, x, y):
                receipt.with_suffix('.pointer.json').write_text(json.dumps({'count': count, 'x': x, 'y': y}))
            pointer.connect('pressed', observed)
            dialog.add_controller(pointer)
            dialog.present()
            return False
        parent.present()
        GLib.idle_add(open_dialog)
    app.connect('activate', activate)
    app.run([])


def main():
    from application_processes import identity
    from control_probe import ControlClient
    from session_probe import cleanup_helper, launch_records, session_token
    from source_proof import record_sources
    root = Path(sys.argv[1]).resolve()
    protocol = sys.argv[2]
    assert protocol in ('wayland', 'x11')
    evidence = Path(tempfile.mkdtemp(prefix='oversized-session-', dir=root))
    runtime = Path(tempfile.mkdtemp(prefix='floe-session-', dir=f'/run/user/{os.getuid()}'))
    receipt = evidence / 'clicks.json'
    result = {'passed': False, 'evidence': str(evidence), 'runtime': str(runtime), 'protocol': protocol,
              'sources': record_sources(root), 'frames': []}
    (evidence / 'fixture.desktop').write_text('[Desktop Entry]\nType=Application\nName=Floe oversized qualification\n'
        f'Exec=/usr/bin/python3 "{root / "oversized_session_probe.py"}" app "{receipt}"\n')
    (evidence / 'fixture-environment.json').write_text(json.dumps({'environment': {'GDK_BACKEND': protocol,
        'GSETTINGS_BACKEND': 'memory'}, 'unset': [], 'host_bus': os.environ['DBUS_SESSION_BUS_ADDRESS']}))
    process = client = started = None
    token = secrets.token_hex(32)
    log = (evidence / 'helper.log').open('w')
    def records():
        return launch_records(evidence)
    def wait(predicate, reason):
        deadline = time.monotonic() + 30
        while not predicate():
            if time.monotonic() >= deadline or process.poll() is not None:
                raise RuntimeError(reason)
            time.sleep(.01)
    try:
        process = subprocess.Popen([sys.executable, str(root / 'session_probe.py'), str(root), 'helper',
            str(runtime), str(evidence), 'package'], stdin=subprocess.PIPE, stdout=log, stderr=log, start_new_session=True)
        started = identity(process.pid)[1]
        result['helper'] = {'pid': process.pid, 'start_ticks': started}
        process.stdin.write((token + '\n').encode())
        process.stdin.close()
        wait(lambda: any(x.get('phase') == 'sharing_ready' or x.get('state') == 'failed' for x in records()), 'Helper not ready')
        assert not any(x.get('state') == 'failed' for x in records()), records()
        client = ControlClient(evidence / 'viewer', runtime / 'control.sock', runtime.name, session_token(evidence, token))
        client.reconnect()
        colors = [(199, 74, 57), (49, 150, 203)]
        for index, color in enumerate(colors):
            client.request('refresh')
            frames = client.paint('control-' + str(index), marker=color, required=(colors[1-index],))
            result['frames'] += frames
            frame = frames[-1]
            target = {key: frame[key] for key in ('connection', 'window', 'generation')}
            state = client.response(client.request('status'))['result']
            window = next(w for w in state['windows'] if w['window'] == frame['window'])
            assert window['parent'] is not None and window['width'] > 1000 and window['height'] > 700
            assert frame['width'] >= 1600 and frame['height'] >= 1100
            assert window['protocol'] == protocol
            result['native_window'] = window
            x0, y0, x1, y1 = frame['marker_bounds']
            assert 0 <= x0 < x1 < frame['width'] and 0 <= y0 < y1 < frame['height']
            x, y = (x0 + x1) / 2, (y0 + y1) / 2
            for operation in ({'kind': 'move', 'x': x, 'y': y},
                              {'kind': 'button', 'button': 0, 'pressed': True, 'x': x, 'y': y},
                              {'kind': 'button', 'button': 0, 'pressed': False, 'x': x, 'y': y}):
                assert 'error' not in client.response(client.request('input', **target, operation=operation))
            wait(lambda: receipt.exists() and json.loads(receipt.read_text()) == ['near', 'far'][:index+1],
                 'Actual native control did not receive its displayed click')
        assert 'error' not in client.response(client.request('close_window', window=target['window']))
        restored = client.paint('parent-restored', window=window['parent'])
        result['frames'] += restored
        assert restored[-1]['width'] == 1000 and restored[-1]['height'] == 700
        assert restored[-1]['generation'] > target['generation']
        assert 'error' not in client.response(client.request('close_window', window=window['parent']))
        assert process.wait(timeout=15) == 0
        result['clicks'], result['passed'] = json.loads(receipt.read_text()), True
        result['receipt_sha256'] = hashlib.sha256(receipt.read_bytes()).hexdigest()
    except BaseException:
        result['error'] = traceback.format_exc()
    finally:
        if client:
            client.close()
            (evidence / 'control-receipts.json').write_text(json.dumps(client.trace, indent=2))
        processes = [x for x in records() if x.get('event') == 'process']
        remaining = cleanup_helper(process, started, processes)
        result['processes'] = processes
        if remaining:
            result['passed'], result['cleanup_remaining'] = False, remaining
        log.close()
        (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))
    if not result['passed']:
        sys.exit(1)


if __name__ == '__main__':
    if sys.argv[1] == 'app':
        application(Path(sys.argv[2]))
    else:
        main()
