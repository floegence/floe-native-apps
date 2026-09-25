"""Actual Flatpak editor/save receipts on the task's disposable native host."""
import json
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

from gi.repository import Gio, GLib
from scope_bridge import identity
from portal_probe import start_portals


def main():
    root, app_id = Path(sys.argv[1]).resolve(), sys.argv[2]
    assert app_id in ("org.gnome.TextEditor", "org.kde.kwrite")
    input_kind = os.environ.get("FLOE_PROBE_INPUT", "native")
    host_address = os.environ["DBUS_SESSION_BUS_ADDRESS"]
    document_bridge = None
    native = None
    evidence = Path(tempfile.mkdtemp(prefix=app_id + "-" + input_kind + "-", dir=root))
    runtime = Path(tempfile.mkdtemp(prefix="floe-editor-", dir=f"/run/user/{os.getuid()}"))
    display = runtime / "wayland-0"
    document = evidence / "document.txt"
    document.write_bytes(b"")
    events, processes, logs = [], [], []
    outcome = {"passed": False, "application": app_id, "input": input_kind,
               "evidence": str(evidence)}
    outcome["package"] = subprocess.check_output(["flatpak", "info", app_id], text=True)
    fixture_state = Path.home() / ".var/app" / app_id / ("floe-fixture-" + secrets.token_hex(8))
    fixture_state.mkdir(mode=0o700)
    mainloop = GLib.MainLoop()
    left, right = socket.socketpair()

    def record(event):
        events.append(event)
        with (evidence / "events.jsonl").open("a") as file:
            file.write(json.dumps(event) + "\n")

    def wait_until(predicate, message, seconds=25):
        deadline = time.monotonic() + seconds
        while not predicate():
            assert time.monotonic() < deadline, message
            time.sleep(0.02)

    def start(command, environment, name, **kwargs):
        log = (evidence / (name + ".log")).open("w")
        logs.append(log)
        process = subprocess.Popen(command, env=environment, stdout=log, stderr=log,
                                   start_new_session=True, **kwargs)
        processes.append((process, identity(process.pid)[1], name))
        return process

    def owns(name):
        return connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner", GLib.Variant("(s)", (name,)),
            GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]

    def capture(stage):
        destination = evidence / stage
        destination.mkdir()
        child = start(["python3", "-c", "import os; os.read(0,1); os.execl('/usr/bin/weston-screenshooter','weston-screenshooter')"],
                      {**environment, "XDG_PICTURES_DIR": str(destination)}, "capture-" + stage, stdin=subprocess.PIPE)
        left.sendall(f"capture-authorize {child.pid}\n".encode())
        wait_until(lambda: any(e.get("control") == f"capture-authorized {child.pid}" for e in events), "Capture authorization failed")
        child.stdin.write(b"x")
        child.stdin.close()
        child.wait(timeout=10)
        assert child.returncode == 0 and len(list(destination.glob("*.png"))) == 1

    def save(expected):
        record({"stage": "save-request", "expected": expected})
        left.sendall(b"key 29 1\nkey 31 1\nkey 31 0\nkey 29 0\n")
        wait_until(lambda: document.read_bytes() == expected.encode(), "Actual editor did not save exact Unicode bytes")
        record({"stage": "save-received", "actual": document.read_text()})

    def worker():
        try:
            wait_until(display.exists, "No private Wayland display")
            if os.environ.get("FLOE_PROBE_TRACE"):
                start(["dbus-monitor", "--address", address], environment, "bus-trace")
            start_portals(evidence, address, display, connection, start, wait_until,
                          host_documents=document_bridge is not None)
            portal_binary = os.environ.get("FLOE_PROBE_IBUS_PORTAL", "/usr/libexec/ibus-portal")
            outcome["ibus_portal"] = {"path": portal_binary,
                                      "sha256": hashlib.sha256(Path(portal_binary).read_bytes()).hexdigest()}
            portal = start([portal_binary], environment, "ibus-portal")
            wait_until(lambda: owns("org.freedesktop.portal.IBus") or portal.poll() is not None, "No IBus portal")
            assert portal.poll() is None, "IBus portal exited"
            ibus.activate()
            original = Path.home() / ".local/share/flatpak/exports/share/applications" / (app_id + ".desktop")
            outcome["desktop_sha256"] = hashlib.sha256(original.read_bytes()).hexdigest()
            entry = GLib.KeyFile.new()
            entry.load_from_file(str(original), GLib.KeyFileFlags.NONE)
            command = entry.get_string("Desktop Entry", "Exec")
            assert "%U" in command and "--file-forwarding" in command, "Unreviewed fixture desktop entry"
            if os.environ.get("FLOE_PROBE_DOCUMENT_PORTAL"):
                # Remove ambient filesystem grants so file forwarding must use
                # the official document portal. This only tightens the sandbox.
                command = command.replace("flatpak run ", "flatpak run --nofilesystem=host --nofilesystem=home ", 1)
            overrides = ["GSETTINGS_BACKEND=memory"]
            if input_kind == "module":
                overrides += ["QT_IM_MODULE=floe-prototype", "QT_PLUGIN_PATH=" + str(fixture_state / "input-module"),
                              "FLOE_PROBE_NATIVE_SERVICE=" + app_id + ".FloeFixtureInput"]
            if os.environ.get("FLOE_PROBE_TRACE"):
                overrides.append("WAYLAND_DEBUG=client")
            for key, leaf in (("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"),
                              ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")):
                directory = fixture_state / leaf
                directory.mkdir()
                overrides.append(key + "=" + str(directory))
            # Flatpak replaces XDG_*_HOME even when passed through --env. Set
            # fixture-only state inside its sandbox, preserving its restrictions.
            executable = "gnome-text-editor" if app_id == "org.gnome.TextEditor" else "kwrite"
            assert "--command=" + executable in command
            command = command.replace("--command=" + executable, "--command=env", 1)
            command = command.replace(" " + app_id + " ", " " + app_id + " " +
                                      " ".join(overrides) + " " + executable + " ", 1)
            if app_id == "org.gnome.TextEditor":
                # The test must never restore an unsaved document from a prior run.
                command = command.replace("@@u %U", "--standalone @@u %U")
                assert "--standalone" in command
            entry.set_string("Desktop Entry", "Exec", command.replace("%U", '"' + document.as_uri() + '"'))
            entry.set_boolean("Desktop Entry", "DBusActivatable", False)
            desktop = evidence / "fixture.desktop"
            desktop.write_text(entry.to_data()[0])
            app = start(["python3", str(root / "application.py"), str(desktop), str(evidence / "application.json")],
                        environment, "application")
            wait_until(lambda: any(e.get("control", "").startswith("frame ") for e in events), "No actual editor frame", 45)
            capture("loaded")
            left.sendall(b"motion 330 300\nbutton 272 1\nbutton 272 0\n")
            if input_kind == "module":
                wait_until(lambda: len(native.clients) == 1, "No sandbox native Qt input context")
            elif input_kind == "ibus":
                wait_until(lambda: ibus.active is not None and any(e.get("ibus") == "context" and e.get("client") != "fake" for e in events),
                           "No sandbox toolkit input context")
            else:
                wait_until(lambda: any(e.get("control", "").startswith("context ") and
                    e["control"].endswith(" 1") for e in events), "No native text-input context")
            def text_command(value):
                if input_kind == "module":
                    return native.enqueue(value)
                if input_kind == "ibus":
                    return ibus.enqueue(value)
                return ("text " + value + "\n").encode()
            text = "中文日本語한글🙂👩🏽‍💻e\u0301𠮷"
            left.sendall(text_command(text))
            if input_kind == "module":
                native.wait_completed(5)
            elif input_kind == "ibus":
                wait_until(lambda: ibus.pending is None, "First native marker was not consumed")
            # A visible suffix distinguishes Enter delivery from the editors'
            # implicit final newline, without normalizing application bytes.
            suffix = b"key 38 1\nkey 38 0\nkey 30 1\nkey 30 0\nkey 31 1\nkey 31 0\nkey 20 1\nkey 20 0\n"
            left.sendall(text_command(text))
            if input_kind == "module":
                native.wait_completed(5)
            elif input_kind == "ibus":
                ibus.transactions.wait_drained(5)
            left.sendall(b"key 28 1\nkey 28 0\n" + suffix)
            expected = text + text + "\nlast\n"
            if os.environ.get("FLOE_PROBE_STRESS"):
                assert input_kind in ("module", "ibus")
                contents = expected[:-1]
                for value in [text] * 64 + ["界🙂" * 2000]:
                    left.sendall(text_command(value))
                    if native:
                        native.wait_completed(5)
                    else:
                        ibus.transactions.wait_drained(5)
                    left.sendall(b"key 28 1\nkey 28 0\n")
                    contents += value + "\n"
                # GtkSourceView saves an implicit trailing newline even when
                # the visible buffer already ends in one. Finish with a visible
                # suffix so both editors have an unambiguous byte expectation.
                left.sendall(b"key 18 1\nkey 18 0\nkey 49 1\nkey 49 0\nkey 32 1\nkey 32 0\n")
                expected = contents + "end\n"
                outcome["stress"] = {"repeated_commits": 64, "long_commit_bytes": 14000,
                                     "completion": "toolkit event loop" if native else "IBus marker release"}
            outcome["expected"] = expected
            if os.environ.get("FLOE_PROBE_SAVE_DIALOG"):
                previous_context = ibus.active.input_path if ibus.active else None
                frames = sum(e.get("control", "").startswith("frame ") for e in events)
                left.sendall(b"key 29 1\nkey 42 1\nkey 31 1\nkey 31 0\nkey 42 0\nkey 29 0\n")
                wait_until(lambda: sum(e.get("control", "").startswith("frame ") for e in events) > frames,
                           "No remote Save As dialog")
                capture("save-dialog")
                destination = evidence / "portal-copy.txt"
                wait_until(lambda: ibus.active is not None and ibus.active.input_path != previous_context,
                           "No distinct FileChooser IBus context")
                # The official GTK backend owns this context. Its stock chooser
                # preselects the stem and retains '.txt'. Submit exactly once.
                left.sendall(ibus.enqueue("portal-copy") + b"key 28 1\nkey 28 0\n")
                wait_until(lambda: destination.exists() and destination.read_bytes() == expected.encode(),
                           "Save As did not grant and save the exact document")
                capture("saved-copy")
                outcome["portal_copy_sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
                assert document.read_bytes() == b"", "Save As changed the original document"
            else:
                save(expected)
                capture("saved")
                outcome["saved_sha256"] = hashlib.sha256(document.read_bytes()).hexdigest()
            left.sendall(b"close\n")
            try:
                app.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # GNOME may retain the original modified document after Save
                # As. Its real close dialog initially focuses Cancel, so Enter
                # is not a Save confirmation. Click the fixture's visible Save.
                if app_id != "org.gnome.TextEditor":
                    raise
                capture("close-confirmation")
                left.sendall(b"motion 610 450\nbutton 272 1\nbutton 272 0\n")
                app.wait(timeout=15)
                outcome["close_confirmation"] = "clicked Save in the captured close dialog"
            assert app.returncode == 0
            assert (destination if os.environ.get("FLOE_PROBE_SAVE_DIALOG") else document).read_bytes() == expected.encode()
            outcome["passed"] = True
        except Exception as error:
            outcome["error"] = str(error)
            outcome["actual"] = document.read_text()
            try:
                capture("failed")
            except Exception as capture_error:
                outcome["capture_error"] = str(capture_error)
        finally:
            GLib.idle_add(mainloop.quit)

    try:
        config = evidence / "bus.conf"
        config.write_text('<busconfig><type>session</type><auth>EXTERNAL</auth><listen>'
            'unix:abstract=/tmp/dbus-floe-' + secrets.token_hex(12) + '</listen><policy context="default">'
            '<allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
        bus_log = (evidence / "bus.log").open("w")
        logs.append(bus_log)
        bus = subprocess.Popen(["dbus-daemon", "--nofork", "--config-file=" + str(config), "--print-address=1"],
            stdout=subprocess.PIPE, stderr=bus_log, text=True, start_new_session=True)
        processes.append((bus, identity(bus.pid)[1], "bus"))
        address = bus.stdout.readline().strip()
        flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        connection = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
        connection.set_exit_on_close(False)
        if os.environ.get("FLOE_PROBE_DOCUMENT_PORTAL"):
            from document_bridge import DocumentBridge
            document_bridge = DocumentBridge(connection, host_address, os.getpid(), app_id, evidence, record)
        os.environ["IBUS_ADDRESS"] = "unix:abstract=/tmp/ibus/dbus-floe-" + secrets.token_hex(12)
        environment = {**os.environ, "DBUS_SESSION_BUS_ADDRESS": address, "XDG_RUNTIME_DIR": str(runtime),
            "WAYLAND_DISPLAY": display.name, "GDK_BACKEND": "wayland", "QT_QPA_PLATFORM": "wayland",
            "GTK_IM_MODULE": "ibus" if input_kind == "ibus" else "wayland", "IBUS_ENABLE_SYNC_MODE": "1"}
        if input_kind == "module":
            assert app_id == "org.kde.kwrite"
            from native_context_probe import NativeContextProbe
            shutil.copytree(root / "qt-probe/platforminputcontexts", fixture_state / "input-module/platforminputcontexts")
            native = NativeContextProbe(connection, app_id + ".FloeFixtureInput", record)
        if input_kind == "ibus":
            environment["QT_IM_MODULE"] = "ibus"
        else:
            environment.pop("QT_IM_MODULE", None)
        for key in ("DISPLAY", "XAUTHORITY", "GTK_PATH", "GTK_IM_MODULE_FILE"):
            environment.pop(key, None)
        components = evidence / "ibus-components"
        components.mkdir()
        start(["ibus-daemon", "--single", "--panel=disable", "--config=disable", "--emoji-extension=disable",
               "--cache=none", "--address=" + os.environ["IBUS_ADDRESS"]],
              {**environment, "IBUS_COMPONENT_PATH": str(components),
               "XDG_CONFIG_HOME": str(evidence / "ibus-config"), "XDG_CACHE_HOME": str(evidence / "ibus-cache")}, "ibus")
        wait_until(lambda: owns("org.freedesktop.IBus"), "No private IBus daemon")
        from ibus_probe import IBusProbe
        ibus = IBusProbe(record)
        start(["weston", "--backend=headless", "--renderer=pixman", "--shell=" + str(root / "probe/probe-shell.so"),
               "--socket=" + display.name, "--width=1000", "--height=700", "--idle-time=0", "--no-config"],
              {**environment, "FLOE_PROBE_CONTROL_FD": str(right.fileno())}, "compositor", pass_fds=(right.fileno(),))
        right.close()

        def controls():
            with left.makefile("r") as stream:
                for line in stream:
                    record({"control": line.strip()})

        threading.Thread(target=controls, daemon=True).start()
        thread = threading.Thread(target=worker)
        thread.start()
        mainloop.run()
        thread.join()
    finally:
        for process, ticks, name in reversed(processes):
            if process.poll() is None and identity(process.pid)[1] == ticks:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()
        if document_bridge:
            document_bridge.close()
        if native:
            native.close()
        left.close()
        right.close()
        outcome["processes"] = [{"pid": p.pid, "start_ticks": ticks, "name": name, "exit": p.returncode}
                                for p, ticks, name in processes]
        outcome["socket_cleaned"] = not display.exists()
        shutil.rmtree(runtime)
        shutil.rmtree(fixture_state)
        (evidence / "result.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({key: value for key, value in outcome.items()
                          if key not in ("processes", "expected", "actual")}, ensure_ascii=False, indent=2))
    if not outcome["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
