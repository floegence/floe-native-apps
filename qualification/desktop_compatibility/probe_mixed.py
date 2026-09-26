"""Actual Wayland/Xwayland window and seat coexistence on a disposable host.

The two child fixtures represent a single owned application process tree. This
does not qualify production X11 authorization, Unicode or sandbox admission.
"""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback

from application_processes import identity


def main():
    root = Path(sys.argv[1]).resolve()
    control_loss = sys.argv[2] if len(sys.argv) > 2 else 'disconnect'
    assert control_loss in ('disconnect', 'stall')
    evidence = Path(tempfile.mkdtemp(prefix="mixed-", dir=root))
    runtime = Path(tempfile.mkdtemp(prefix="floe-mixed-", dir=f"/run/user/{os.getuid()}"))
    left, right = socket.socketpair()
    processes, logs, events = [], [], []
    result = {"passed": False, "evidence": str(evidence)}
    frame_socket, frame_process = None, None
    control = None
    from control_probe import ControlWire, ControlProbe
    wire = ControlWire(left, events)
    capture_command = [str(root / 'frame-probe/frame-probe')]

    def wait(predicate, reason):
        deadline = time.monotonic() + 15
        while not predicate():
            if time.monotonic() > deadline:
                raise RuntimeError(reason)
            time.sleep(0.01)

    def start(command, environment, name, **kwargs):
        log = (evidence / (name + ".log")).open("w")
        logs.append(log)
        process = subprocess.Popen(command, env=environment, stdout=log, stderr=log,
                                   start_new_session=True, **kwargs)
        processes.append((process, identity(process.pid)[1], name))
        (evidence / 'processes.json').write_text(json.dumps([
            {'pid': p.pid, 'start_ticks': ticks, 'name': kind} for p, ticks, kind in processes], indent=2))
        return process

    def start_capture():
        nonlocal frame_socket, frame_process
        frame_socket, child_socket = socket.socketpair()
        frame_process = start(["python3", "-c", "import os,sys; os.read(0,1); os.execv(sys.argv[1],sys.argv[1:])",
                               *capture_command],
            {**environment, "FLOE_PROBE_FRAME_FD": str(child_socket.fileno())},
            "frame-capture", stdin=subprocess.PIPE, pass_fds=(child_socket.fileno(),))
        child_socket.close()
        wire.send(f"capture-authorize {frame_process.pid}\n")
        wait(lambda: f"capture-authorized {frame_process.pid}" in events, "Frame capture was not authorized")
        frame_process.stdin.write(b"x")
        frame_process.stdin.close()

    def paint(stage, expected=None, **options):
        recorded = control.paint(stage, expected, **options)
        result.setdefault('frames', []).extend(recorded)
        return recorded[-1]

    def click_marker(frame):
        x0, y0, x1, y1 = frame['marker_bounds']
        control.send(frame['window'], f'motion {(x0 + x1) / 2} {(y0 + y1) / 2}\nbutton 272 1\nbutton 272 0\n'.encode())


    try:
        config = evidence / "bus.conf"
        config.write_text('<busconfig><type>session</type><auth>EXTERNAL</auth><listen>unix:tmpdir=/tmp</listen>'
                          '<policy context="default"><allow send_destination="*"/>'
                          '<allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
        bus_log = (evidence / "bus.log").open("w")
        logs.append(bus_log)
        bus = subprocess.Popen(["dbus-daemon", "--config-file=" + str(config), "--nofork", "--print-address=1"],
                               stdout=subprocess.PIPE, stderr=bus_log, text=True, start_new_session=True)
        processes.append((bus, identity(bus.pid)[1], "bus"))
        environment = {**os.environ, "DBUS_SESSION_BUS_ADDRESS": bus.stdout.readline().strip(),
                       "XDG_RUNTIME_DIR": str(runtime), "WAYLAND_DISPLAY": "wayland-0",
                       "GTK_IM_MODULE": "gtk-im-context-simple", "GSETTINGS_BACKEND": "memory"}
        for key in ("DISPLAY", "XAUTHORITY", "GTK_PATH", "GTK_IM_MODULE_FILE", "IBUS_ADDRESS", "QT_IM_MODULE"):
            environment.pop(key, None)
        command = ["weston", "--backend=headless", "--renderer=pixman", "--xwayland",
                            "--shell=" + str(root / "probe/probe-shell.so"), "--socket=wayland-0",
                            "--width=1000", "--height=700", "--idle-time=0", "--no-config"]
        server_environment, authorize = dict(environment), None
        if os.environ.get('FLOE_PROBE_COMPONENT'):
            from portable_probe import prepare
            command, server_environment, capture_command, authorize, result['portable'] = prepare(
                os.environ['FLOE_PROBE_COMPONENT'], evidence, environment,
                root / 'alpine-wayland-probe/probe-shell.so')
        compositor = start(command,
                           {**server_environment, "FLOE_PROBE_CONTROL_FD": str(right.fileno())},
                           "compositor", pass_fds=(right.fileno(),))
        right.close()
        wait(lambda: (runtime / "wayland-0").exists(), "No private compositor socket")
        pattern = r"xserver listening on display (:[0-9]+)"
        log = evidence / "compositor.log"
        wait(lambda: re.search(pattern, log.read_text()) or compositor.poll() is not None,
             "Xwayland did not reserve a display")
        assert compositor.poll() is None, "Compositor exited"
        display = re.search(pattern, log.read_text()).group(1)
        result["xwayland_display"] = display
        if authorize:
            authorize(display)
            denied = subprocess.run(['python3', '-c', 'from Xlib.display import Display; import sys; Display(sys.argv[1])', display],
                env={**environment, 'XAUTHORITY': str(evidence / 'missing-authority')},
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
            assert denied.returncode != 0, 'Private Xwayland admitted an unauthenticated client'
            result['x11_unauthenticated_client_rejected'] = True
        receipts = [evidence / "wayland.json", evidence / "xwayland.json"]
        wayland = start(["python3", str(root / "input_fixture.py"), "gtk4", str(receipts[0])],
                        {**environment, "WAYLAND_DEBUG": os.environ.get("FLOE_PROBE_PROTOCOL_DEBUG", ""), "GDK_BACKEND": "wayland", 'FLOE_TEST_WINDOW_COLOR': '13579b',
                         'FLOE_TEST_WINDOW_ACTIONS': '1'}, "wayland")
        wait(lambda: receipts[0].exists() and any(e.startswith("frame ") for e in events),
             "No mapped Wayland fixture frame")
        start_capture()
        control = ControlProbe(evidence, wire, frame_socket)
        control.reconnect()
        first = int(next(e.split()[1] for e in events if e.startswith('window-instance ')))
        before = control.send(first, b'key 45 1\nkey 45 0\n')
        assert control.response(before)['error'] == 'INPUT_TARGET_UNAVAILABLE'
        paint("wayland", (19, 87, 155))
        result['first_frame_admission'] = True
        control.send(first, b"motion 250 180\nbutton 272 1\nbutton 272 0\nkey 30 1\nkey 30 0\n")
        wait(lambda: json.loads(receipts[0].read_text()) == ["a", ""], "No actual Wayland seat input")
        assert control.native.snapshot()['windows'][0]['title'] == 'Floe GTK4 input qualification'
        target = control.native.target
        control.send(first, b'key 63 1\nkey 63 0\n')
        wait(lambda: control.native.snapshot()['windows'][0]['title'] == 'Document 日本語 🧑🏽\u200d💻\nSave As',
             'Native title update did not reach the window registry')
        assert control.native.target is target, 'Title update revoked the active input target'
        result['native_title_preserves_input'] = True
        actions = receipts[0].with_suffix('.windows.json')
        original = control.native.snapshot()['windows'][0]
        result['window_mode_receipts'] = []
        for key, field, expected in ((64, 'maximized', True), (66, 'fullscreen', True),
                                     (67, 'fullscreen', False), (65, 'maximized', False),
                                     (66, 'fullscreen', True), (67, 'fullscreen', False)):
            control.send(first, f'key {key} 1\nkey {key} 0\n'.encode())
            wait(lambda: json.loads(actions.read_text()).get(field) is expected,
                 f'Application did not receive its requested {field} state {expected}')
            wait(lambda: control.native.snapshot()['windows'][0][field] is expected,
                 'Window registry did not reflect the committed native mode')
            paint(f'{field}-{expected}', (19, 87, 155))
            observed = control.native.snapshot()['windows'][0]
            assert observed['width'] >= 640 and observed['height'] >= 320
            if not observed['fullscreen'] and not observed['maximized']:
                assert (observed['width'], observed['height']) == (original['width'], original['height']), \
                    'Restoring the native mode changed the previous normal window size'
            result['window_mode_receipts'].append(observed)
        control.send(first, b'key 68 1\nkey 68 0\n')
        wait(lambda: control.native.snapshot()['state'] == 'waiting', 'Minimized window kept input authority')
        assert control.native.snapshot()['windows'][0]['minimized']
        assert wayland.poll() is None, 'Minimization terminated the actual application'
        assert control.response(control.request('select_window', window=first)).get('result') == 'requested'
        paint('unminimized', (19, 87, 155))
        assert not control.native.snapshot()['windows'][0]['minimized']
        result['native_window_modes'] = ['maximize', 'fullscreen', 'restore-maximized', 'restore',
                                         'fullscreen', 'restore', 'minimize', 'select']
        control.send(first, b'key 60 1\nkey 60 0\n')
        popup = paint('popup', marker=(19, 183, 73), required=((19, 87, 155),))
        click_marker(popup)
        actions = receipts[0].with_suffix('.windows.json')
        wait(lambda: json.loads(actions.read_text())['popup_clicks'] == 1, 'Popup did not receive the actual click')
        wait(lambda: control.native.generation > popup['generation'],
             'Popup resize/reposition did not revoke the previous frame and coordinates')
        resized = paint('popup-resized', marker=(19, 183, 73),
                        accept_bounds=lambda bounds: bounds[2] - bounds[0] >= 370)
        assert resized['marker_bounds'][2] - resized['marker_bounds'][0] >= 370
        assert resized['generation'] > popup['generation']
        click_marker(resized)
        wait(lambda: json.loads(actions.read_text())['popup_clicks'] == 2, 'Resized popup did not receive the actual click')
        wait(lambda: json.loads(actions.read_text())['popup_closed'] == 1, 'Application has not closed its popup')
        paint('popup-dismissed', (19, 87, 155), absent=((19, 183, 73),))
        control.send(first, b'key 61 1\nkey 61 0\n')
        dialog = paint('dialog', marker=(191, 49, 189), required=((19, 87, 155),))
        click_marker(dialog)
        wait(lambda: json.loads(actions.read_text())['dialog_clicks'] == 1, 'Transient dialog did not receive the actual click')
        control.send(dialog['window'], b'close\n')
        wait(lambda: json.loads(actions.read_text())['dialog_closed'] == 1, 'Transient dialog did not receive the native close')
        paint('dialog-restored', (19, 87, 155))
        result['popup_and_dialog'] = json.loads(actions.read_text())
        control.send(first, b'key 62 1\nkey 62 0\n')
        scrolling = paint('scroll-dialog', marker=(179, 128, 26), required=((19, 87, 155),))
        x0, y0, x1, y1 = scrolling['marker_bounds']
        control.send(scrolling['window'], f'motion {(x0 + x1) / 2 - 30} {(y0 + y1) / 2 - 30}\nscroll 1.25 7.5\n'.encode())
        wait(lambda: all(json.loads(actions.read_text())['scroll_' + axis] > 0 for axis in ('x', 'y')),
             'Native fractional diagonal scroll did not move the actual GTK adjustments')
        forward = json.loads(actions.read_text())
        control.send(scrolling['window'], b'scroll -1.75 -2.5\n')
        wait(lambda: all(json.loads(actions.read_text())['scroll_' + axis] < forward['scroll_' + axis]
                         for axis in ('x', 'y')), 'Native reverse scroll did not reverse both GTK adjustments')
        reverse = json.loads(actions.read_text())
        result['actual_scroll'] = {key: [forward['scroll_' + key], reverse['scroll_' + key]] for key in ('x', 'y')}
        control.send(scrolling['window'], b'scroll 0.25 0.25\n' * 8)
        wait(lambda: all(json.loads(actions.read_text())['scroll_' + axis] > reverse['scroll_' + axis]
                         for axis in ('x', 'y')), 'Native sub-pixel wheel remainder was lost')
        fractional = json.loads(actions.read_text())
        for axis in ('x', 'y'):
            result['actual_scroll'][axis].append(fractional['scroll_' + axis])
        control.send(scrolling['window'], b'close\n')
        paint('scroll-restored', (19, 87, 155), absent=((179, 128, 26),))
        from window_probe import qualify as qualify_windows
        result['native_window_manipulation'] = qualify_windows(control, first, receipts[0], wait, paint)
        xwayland = start(["python3", str(root / "input_fixture.py"), "gtk4", str(receipts[1])],
                         {**environment, "GDK_BACKEND": "x11", "DISPLAY": display,
                          'FLOE_TEST_WINDOW_COLOR': '9b3113', 'FLOE_TEST_WINDOW_ACTIONS': '1'}, "xwayland")
        wait(lambda: receipts[1].exists() and events.count("window-added") == 5 and
             sum(e.startswith("frame ") for e in events) == 5, "No distinct mapped Xwayland fixture")
        second = int([e.split()[1] for e in events if e.startswith('window-instance ')][-1])
        paint("mixed", (155, 49, 19))
        assert wire.native.x11_windows.get(second), 'Native Xwayland surface has no authoritative X11 binding'
        control.send(second, b"motion 250 180\nbutton 272 1\nbutton 272 0\nkey 48 1\nkey 48 0\n")
        wait(lambda: json.loads(receipts[1].read_text()) == ["b", ""], "No actual Xwayland seat input")
        result['xwayland_window_manipulation'] = qualify_windows(
            control, second, receipts[1], wait, paint, (155, 49, 19))
        for window, stage in ((first, 'selected-wayland'), (second, 'selected-xwayland')):
            previous_generation = control.native.generation
            request = control.request('select_window', window=window)
            assert control.response(request).get('result') == 'requested'
            wait(lambda: control.native.target is not None and control.native.target.window == window and
                 control.native.generation > previous_generation, 'Native window selection did not change the scene')
            paint(stage, (19, 87, 155) if window == first else (155, 49, 19))
        result['native_window_selection'] = [first, second]
        for index in range(8):
            request = control.request('refresh')
            assert control.response(request)['result'] == 'requested'
            paint('frame-' + str(index), (155, 49, 19))
        # Withhold the next helper frame from the client. Automatic native
        # capture and the bounded attachment must retain just one pending frame,
        # while the application's seat remains responsive.
        control.block_frame()
        control.send(second, b"key 32 1\nkey 32 0\n")
        wait(lambda: json.loads(receipts[1].read_text()) == ["bd", ""],
             "Frame backpressure blocked application input")
        result["backpressure_input"] = ["bd", ""]
        control.send(second, b"key 29 1\n")
        previous_connection = control.generation
        assert control.reconnect() == previous_connection + 1
        result['authenticated_helper_reconnected'] = True
        assert compositor.poll() is None and wayland.poll() is None and xwayland.poll() is None
        before = control.send(second, b'key 45 1\nkey 45 0\n')
        assert control.response(before)['error'] == 'INPUT_TARGET_UNAVAILABLE'
        paint("reattached", (155, 49, 19))
        control.send(second, b"key 18 1\nkey 18 0\n")
        wait(lambda: json.loads(receipts[1].read_text()) == ["bde", ""],
             "Attachment replacement retained the old Control modifier")
        result['modifier_released_on_reattach'] = True
        from Xlib import Xatom, display as xdisplay
        from Xlib.ext import res
        from unittest.mock import patch
        with patch.dict(os.environ, {'XAUTHORITY': environment.get('XAUTHORITY', '')}):
            xconnection = xdisplay.Display(display)
        xid = wire.native.x11_windows[second]
        window = xconnection.create_resource_object('window', xid)
        atom = xconnection.intern_atom('_NET_WM_PID')
        original = window.get_full_property(atom, Xatom.CARDINAL)
        assert original and list(original.value) == [xwayland.pid]
        def owner():
            reply = xconnection.res_query_client_ids([{'client': xid, 'mask': res.LocalClientPIDMask}])
            assert len(reply.ids) == 1 and reply.ids[0].spec.mask == res.LocalClientPIDMask
            assert len(reply.ids[0].value) == 1
            return reply.ids[0].value[0]
        assert owner() == xwayland.pid
        window.change_property(atom, Xatom.CARDINAL, 32, [os.getpid()])
        xconnection.sync()
        assert list(window.get_full_property(atom, Xatom.CARDINAL).value) == [os.getpid()]
        assert owner() == xwayland.pid, 'Advisory X11 PID changed trusted resource ownership'
        assert wire.native.windows[second].pid == -1
        window.change_property(atom, Xatom.CARDINAL, 32, list(original.value))
        xconnection.sync()
        xconnection.close()
        result['x11_resource_identity'] = {'native_window': second, 'xid': xid,
            'actual_pid': xwayland.pid, 'forged_advisory_pid_rejected': True}
        control.send(second, b"close\n")
        xwayland.wait(timeout=10)
        assert xwayland.returncode == 0 and wayland.poll() is None
        wait(lambda: "window-restored" in events, "Closing Xwayland did not restore Wayland")
        paint("restored", (19, 87, 155))
        identities = [int(e.split()[1]) for e in events if e.startswith("window-instance ")]
        assert len(identities) == 6 and len(set(identities)) == 6
        current_connection = control.generation
        wait(lambda: f"connection-ready {current_connection}" in events, "Connection replacement was not admitted")
        # A retired native window and an old viewer generation must both reject
        # their late input. The following live key proves the stream progressed.
        generation = control.native.generation
        stale = (f"input {previous_connection} {first} {generation} key 45 1\ninput {previous_connection} {first} {generation} key 45 0\n"
                 f"input {current_connection} {second} {generation} key 45 1\ninput {current_connection} {second} {generation} key 45 0\n")
        wire.send(stale)
        control.send(first, b"key 46 1\nkey 46 0\n")
        wait(lambda: json.loads(receipts[0].read_text()) == ["ac", ""], "Restored Wayland window did not receive input")
        assert events.count(f"input-rejected {previous_connection} {identities[0]}") == 2
        assert events.count(f"input-rejected {current_connection} {second}") == 2
        result["rejected_stale_input"] = {"old_connection": 2, "retired_window": 2}
        if os.environ.get('FLOE_PROBE_CONTEXT'):
            from context_probe import qualify
            result['native_context'] = qualify(root, evidence, environment, control, wire, start, wait, paint, display)
            paint('context-restored', (19, 87, 155))
        control.close()
        control = None
        wire.send('key 29 1\n')
        wait(lambda: json.loads(actions.read_text())['control_down'], 'Native key press was not received')
        releases = json.loads(actions.read_text())['control_releases']
        wire.stop_reading()
        if control_loss == 'stall':
            left.settimeout(5)
            try:
                left.sendall(b'scene-query 900000\n' * 40000)
            except BrokenPipeError:
                pass
            except ConnectionResetError:
                pass
            except socket.timeout:
                raise AssertionError('Stalled native observer froze the compositor control channel') from None
            wait(lambda: json.loads(actions.read_text())['control_releases'] == releases + 1,
                 'Stalled native observer did not revoke its held modifier')
        else:
            left.shutdown(socket.SHUT_RDWR)
        try:
            compositor.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        assert compositor.poll() is None and wayland.poll() is None, 'Native controller loss terminated the graphical application'
        wait(lambda: json.loads(actions.read_text())['control_releases'] == releases + 1,
             'Native controller loss left the Control modifier pressed')
        result['native_controller_loss'] = {'mode': control_loss, 'application_preserved': True, 'modifier_released': True}
        result["actual"] = [json.loads(path.read_text()) for path in receipts]
        protocols = [line.split()[2] for line in events if line.startswith('window-protocol ')]
        expected_protocols = ['wayland', 'wayland', 'wayland', 'wayland', 'x11', 'x11']
        if os.environ.get('FLOE_PROBE_CONTEXT'):
            expected_protocols.append(os.environ.get('FLOE_PROBE_CONTEXT_PROTOCOL', 'wayland'))
        assert protocols == expected_protocols, 'Compositor did not confirm actual surface protocols'
        result['actual_protocols'] = protocols
        result["passed"] = True
    except Exception as error:
        result["error"] = str(error)
        result['traceback'] = traceback.format_exc()
    finally:
        for close in ([control.close] if control else []) + [wire.close]:
            try:
                close()
            except Exception:
                result.setdefault('cleanup_errors', []).append(traceback.format_exc())
                result['passed'] = False
        if frame_socket:
            frame_socket.close()
        for process, ticks, _name in reversed(processes):
            if process.poll() is None and identity(process.pid)[1] == ticks:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        left.close()
        right.close()
        for log in logs:
            log.close()
        (evidence / 'Xauthority').unlink(missing_ok=True)
        result["processes"] = [{"pid": p.pid, "start_ticks": ticks, "name": name, "exit": p.returncode}
                               for p, ticks, name in processes]
        (evidence / "events.json").write_text(json.dumps(events, indent=2) + "\n")
        shutil.rmtree(runtime)
        (evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({key: value for key, value in result.items() if key != "processes"}, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
