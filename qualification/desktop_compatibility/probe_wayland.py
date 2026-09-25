"""Strict Snap/Wayland prototype with an inherited, private test control socket.

No production APIs or compatibility claims are implied by this experiment.
Text receipts come from input events in the task's own Firefox page, never from
the host text transport. All profiles, documents, buses and displays are owned.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

from gi.repository import Gio, GLib
from scope_bridge import ScopeBridge, identity


def main():
    root = Path(sys.argv[1]).resolve()
    evidence = root / "wayland-evidence"
    evidence.mkdir(exist_ok=False, mode=0o700)
    host_address = os.environ["DBUS_SESSION_BUS_ADDRESS"]
    input_kind = os.environ.get("FLOE_PROBE_INPUT", "native")
    runtime = Path(f"/run/user/{os.getuid()}/snap.firefox")
    assert runtime.is_dir() and runtime.stat().st_uid == os.getuid()
    display = runtime / ("wayland-" + str(secrets.randbelow(90000000) + 10000000))
    profile = Path(tempfile.mkdtemp(prefix="floe-compat-test-", dir=Path.home() / "snap/firefox/common"))
    downloads = profile / "downloads"
    downloads.mkdir()
    (profile / "user.js").write_text('user_pref("browser.download.folderList", 2);\n'
        'user_pref("browser.download.useDownloadDir", false);\n'
        'user_pref("browser.download.dir", ' + json.dumps(str(downloads)) + ');\n')
    events, receipts, processes, logs = [], [], [], []
    bridge = None
    left, right = socket.socketpair()
    mainloop = GLib.MainLoop()
    outcome = {"passed": False, "profile": str(profile), "display": str(display), "input": input_kind}
    failure = []

    def record(event):
        events.append(event)
        with (evidence / "events.jsonl").open("a") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body = b'''<!doctype html><meta charset="utf-8"><title>Floe isolated input fixture</title>
            <style>body{font:20px sans-serif;margin:30px}textarea{width:85%;height:350px;font:24px sans-serif}</style>
            <h1>Floe isolated input fixture</h1><textarea autofocus></textarea><p><button id="save">Save test text</button></p>
            <script>const field=document.querySelector('textarea'); let sequence=0;
            const report=event=>fetch('/receipt',{method:'POST',body:JSON.stringify({sequence:++sequence,event,value:field.value})});
            field.addEventListener('input',()=>report('input'));field.addEventListener('blur',()=>report('blur'));
            field.addEventListener('pointerdown',()=>report('pointerdown'));
            document.querySelector('#save').addEventListener('click',()=>{report('save-click');
            const link=document.createElement('a');link.href=URL.createObjectURL(new Blob([field.value],{type:'text/plain;charset=utf-8'}));
            link.download='floe-confirmed-text.txt';link.click();});
            fetch('/receipt',{method:'POST',body:JSON.stringify({loaded:true})});</script>'''
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            count = int(self.headers.get("Content-Length", "0"))
            if self.path != "/receipt" or count > 100000:
                self.send_error(400)
                return
            receipt = json.loads(self.rfile.read(count))
            receipts.append(receipt)
            with (evidence / "receipts.jsonl").open("a") as file:
                file.write(json.dumps(receipt, ensure_ascii=False) + "\n")
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    config = evidence / "bus.conf"
    config.write_text('''<busconfig><type>session</type><auth>EXTERNAL</auth>
    <listen>unix:abstract=/tmp/dbus-floe-''' + secrets.token_hex(12) + '''</listen><policy context="default">
    <allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/></policy></busconfig>''')

    def start(command, environment, name, **kwargs):
        log = (evidence / (name + ".log")).open("w")
        logs.append(log)
        process = subprocess.Popen(command, env=environment, stderr=log, stdout=log,
                                   start_new_session=True, **kwargs)
        processes.append((process, identity(process.pid)[1], name))
        return process

    def wait_until(predicate, message, seconds=20):
        deadline = time.monotonic() + seconds
        while not predicate():
            assert time.monotonic() < deadline, message
            time.sleep(0.02)

    def capture(stage):
        directory = evidence / stage
        directory.mkdir()
        environment = {**os.environ, "WAYLAND_DISPLAY": str(display), "XDG_RUNTIME_DIR": str(runtime),
                       "XDG_PICTURES_DIR": str(directory)}
        command = ["/lib64/ld-linux-x86-64.so.2", "--library-path", str(libraries) + ":" + str(libraries / "weston"),
                   str(root / "stack/usr/bin/weston-screenshooter")]
        child = start(["python3", "-c", "import os,sys; os.read(0,1); os.execv(sys.argv[1],sys.argv[1:])", *command],
                      environment, "capture-" + stage, stdin=subprocess.PIPE)
        left.sendall(f"capture-authorize {child.pid}\n".encode())
        wait_until(lambda: any(e.get("control") == f"capture-authorized {child.pid}" for e in events),
                   "Capture process was not authorized")
        child.stdin.write(b"x")
        child.stdin.close()
        child.wait(timeout=10)
        assert child.returncode == 0 and len(list(directory.glob("*.png"))) == 1, "No captured output"

    def worker():
        try:
            wait_until(display.exists, "Private Wayland socket did not appear")
            from portal_probe import start_portals
            start_portals(evidence, address, display, bridge.private, start, wait_until)
            environment = {**os.environ, "DBUS_SESSION_BUS_ADDRESS": address,
                "WAYLAND_DISPLAY": display.name, "GDK_BACKEND": "wayland", "MOZ_ENABLE_WAYLAND": "1",
                "GTK_IM_MODULE": "ibus" if input_kind == "ibus" else "wayland", "XDG_RUNTIME_DIR": str(runtime),
                "IBUS_ENABLE_SYNC_MODE": "1", "GTK_USE_PORTAL": "1"}
            for key in ("DISPLAY", "XAUTHORITY", "GTK_PATH", "GTK_IM_MODULE_FILE",
                        "GIO_EXTRA_MODULES", "LD_LIBRARY_PATH", "LD_PRELOAD"):
                environment.pop(key, None)
            if input_kind == "ibus":
                ibus.activate()
            original = Path("/var/lib/snapd/desktop/applications/firefox_firefox.desktop")
            entry = GLib.KeyFile.new()
            entry.load_from_file(str(original), GLib.KeyFileFlags.NONE)
            command = entry.get_string("Desktop Entry", "Exec")
            assert command == "/snap/bin/firefox %u", "Unreviewed installed desktop entry"
            # Explicit fixture-only profile: never reuse the user's browser.
            url = f"http://127.0.0.1:{server.server_port}/"
            entry.set_string("Desktop Entry", "Exec", f'/snap/bin/firefox --no-remote --profile "{profile}" {url}')
            entry.set_boolean("Desktop Entry", "DBusActivatable", False)
            desktop = evidence / "fixture.desktop"
            desktop.write_text(entry.to_data()[0])
            outcome["desktop_sha256"] = hashlib.sha256(original.read_bytes()).hexdigest()
            outcome["fixture_desktop_sha256"] = hashlib.sha256(desktop.read_bytes()).hexdigest()
            app = start(["python3", str(root / "application.py"), str(desktop), str(evidence / "application.json")],
                        environment, "application")
            wait_until(lambda: any(r.get("loaded") for r in receipts), "Snap page did not load", 35)
            capture("loaded")
            # One actual click is delivered through the compositor seat.
            left.sendall(b"motion 300 280\nbutton 272 1\nbutton 272 0\n")
            wait_until(lambda: any(r.get("event") == "pointerdown" for r in receipts), "No actual field click receipt")
            if input_kind == "ibus":
                wait_until(lambda: ibus.active is not None, "No native IBus context")
            else:
                wait_until(lambda: any(e.get("control", "").startswith("context ") and
                    e["control"].endswith(" 1") for e in events), "No native text-input context")
            def text_command(value):
                if input_kind == "ibus":
                    return ibus.enqueue(value)
                return ("text " + value + "\n").encode()
            text = "中文日本語한글🙂👩🏽‍💻e\u0301𠮷"
            left.sendall(text_command(text))
            wait_until(lambda: any(r.get("value") == text for r in receipts), "Application did not receive exact Unicode")
            left.sendall(text_command(text) + b"key 28 1\nkey 28 0\n")
            wait_until(lambda: any(r.get("value") == text + text + "\n" for r in receipts),
                       "Repeated text and following Enter were not ordered")
            capture("received")
            outcome.update(expected=text + text + "\n", composition="native protocol; client IME not exercised")
            left.sendall(b"motion 105 585\nbutton 272 1\nbutton 272 0\n")
            wait_until(lambda: any(r.get("event") == "save-click" for r in receipts), "Save was not clicked")
            wait_until(lambda: sum(e.get("control") == "window-added" for e in events) >= 2,
                       "No remote file chooser window")
            capture("save-dialog")
            left.sendall(b"key 28 1\nkey 28 0\n")
            saved = downloads / "floe-confirmed-text.txt"
            wait_until(lambda: saved.exists() and saved.read_bytes() == (text + text + "\n").encode(),
                       "Native save did not produce the exact fixture bytes")
            shutil.copyfile(saved, evidence / "saved-text.txt")
            outcome["saved_sha256"] = hashlib.sha256(saved.read_bytes()).hexdigest()
            wait_until(lambda: any(e.get("control") == "window-restored" for e in events),
                       "File chooser did not restore the application window")
            capture("saved")
            wait_until(lambda: any(r.get("event") == "blur" for r in receipts), "No actual field blur receipt")
            latest = max((r for r in receipts if "sequence" in r), key=lambda r: r["sequence"])
            assert latest["value"] == text + text + "\n", "Text changed after losing input focus"
            # Send the native window-manager close request, never force termination.
            left.sendall(b"close\n")
            app.wait(timeout=15)
            outcome["application_exit"] = app.returncode
            outcome["passed"] = app.returncode == 0
        except Exception as error:
            failure.append(error)
            outcome["error"] = str(error)
            try:
                capture("failed")
            except Exception as capture_error:
                outcome["capture_error"] = str(capture_error)
        finally:
            GLib.idle_add(mainloop.quit)

    try:
        bus = subprocess.Popen(["dbus-daemon", "--nofork", "--config-file=" + str(config),
            "--print-address=1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        processes.append((bus, identity(bus.pid)[1], "bus"))
        address = bus.stdout.readline().strip()
        bridge = ScopeBridge(address, host_address, "snap.firefox.firefox", os.getpid(), record)
        if input_kind == "ibus":
            os.environ["IBUS_ADDRESS"] = "unix:abstract=/tmp/ibus/dbus-floe-" + secrets.token_hex(12)
            components = evidence / "ibus-components"
            components.mkdir()
            daemon_environment = {**os.environ, "DBUS_SESSION_BUS_ADDRESS": address,
                "XDG_CONFIG_HOME": str(evidence / "ibus-config"), "XDG_CACHE_HOME": str(evidence / "ibus-cache"),
                "IBUS_COMPONENT_PATH": str(components)}
            start(["ibus-daemon", "--single", "--panel=disable", "--config=disable", "--emoji-extension=disable",
                   "--cache=none", "--address=" + os.environ["IBUS_ADDRESS"]], daemon_environment, "ibus")
            def daemon_ready():
                return bridge.private.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                    "org.freedesktop.DBus", "NameHasOwner", GLib.Variant("(s)", ("org.freedesktop.IBus",)),
                    GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
            wait_until(daemon_ready, "Private IBus daemon did not become ready")
            from ibus_probe import IBusProbe
            ibus = IBusProbe(record)
        libraries = root / "stack/usr/lib/x86_64-linux-gnu"
        weston_environment = {**os.environ, "XDG_RUNTIME_DIR": str(runtime),
            "DBUS_SESSION_BUS_ADDRESS": address, "FLOE_PROBE_CONTROL_FD": str(right.fileno()),
            "WESTON_MODULE_MAP": "headless-backend.so=" + str(libraries / "libweston-13/headless-backend.so")}
        compositor = start(["/lib64/ld-linux-x86-64.so.2", "--library-path", str(libraries) + ":" + str(libraries / "weston"),
            str(root / "stack/usr/bin/weston"), "--backend=headless", "--renderer=pixman",
            "--shell=" + str(root / "probe/probe-shell.so"), "--socket=" + display.name,
            "--width=1000", "--height=700", "--idle-time=0", "--no-config"],
            weston_environment, "compositor", pass_fds=(right.fileno(),))
        right.close()

        def controls():
            with left.makefile("r") as stream:
                for line in stream:
                    record({"control": line.strip()})

        control_thread = threading.Thread(target=controls, daemon=True)
        control_thread.start()
        thread = threading.Thread(target=worker)
        thread.start()
        mainloop.run()
        thread.join()
    finally:
        if bridge:
            bridge.close()
        # The application supervisor owns its descendants, including the sandbox.
        for process, start_ticks, name in reversed(processes):
            if process.poll() is None and identity(process.pid)[1] == start_ticks:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        server.shutdown()
        server.server_close()
        left.close()
        right.close()
        for log in logs:
            log.close()
        shutil.rmtree(profile)
        outcome["events"] = events
        outcome["receipts"] = receipts
        outcome["processes"] = [{"pid": p.pid, "start_ticks": ticks, "name": name, "exit": p.returncode}
                                for p, ticks, name in processes]
        outcome["socket_cleaned"] = not display.exists()
        (evidence / "result.json").write_text(json.dumps(outcome, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps({key: value for key, value in outcome.items()
                          if key not in ("events", "receipts", "processes")}, indent=2, ensure_ascii=False))
    if failure:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
