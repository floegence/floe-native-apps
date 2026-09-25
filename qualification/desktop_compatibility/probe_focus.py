"""Actual native toolkit focus/confirmed-text ordering on a private compositor."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

from gi.repository import Gio, GLib
from application_processes import identity


def main():
    root, toolkit = Path(sys.argv[1]).resolve(), sys.argv[2]
    assert toolkit in ("gtk4", "qt6")
    input_kind = os.environ.get("FLOE_PROBE_INPUT", "native")
    evidence = Path(tempfile.mkdtemp(prefix="focus-" + toolkit + "-" + input_kind + "-", dir=root))
    runtime = Path(tempfile.mkdtemp(prefix="floe-focus-", dir=f"/run/user/{os.getuid()}"))
    receipt = evidence / "received.json"
    left, right = socket.socketpair()
    processes, logs, events = [], [], []
    result = {"passed": False, "toolkit": toolkit, "input": input_kind, "evidence": str(evidence)}
    mainloop = GLib.MainLoop()
    loop_thread = threading.Thread(target=mainloop.run, daemon=True)
    loop_thread.start()

    def wait(predicate, reason):
        deadline = time.monotonic() + 10
        while not predicate():
            if time.monotonic() > deadline:
                raise RuntimeError(reason)
            time.sleep(0.01)

    def start(command, environment, name, **kwargs):
        log = (evidence / (name + ".log")).open("w")
        logs.append(log)
        process = subprocess.Popen(command, env=environment, stdout=log, stderr=log,
                                   start_new_session=True, **kwargs)
        processes.append((process, identity(process.pid)[1]))
        return process

    def controls():
        with left.makefile("r") as stream:
            for line in stream:
                events.append(line.strip())

    try:
        config = evidence / "bus.conf"
        config.write_text('<busconfig><type>session</type><auth>EXTERNAL</auth><listen>unix:tmpdir=/tmp</listen>'
                          '<policy context="default"><allow send_destination="*"/>'
                          '<allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
        bus = subprocess.Popen(["dbus-daemon", "--config-file=" + str(config), "--nofork", "--print-address=1"],
                               stdout=subprocess.PIPE, text=True, start_new_session=True)
        processes.append((bus, identity(bus.pid)[1]))
        address = bus.stdout.readline().strip()
        environment = {**os.environ, "DBUS_SESSION_BUS_ADDRESS": address,
                       "XDG_RUNTIME_DIR": str(runtime), "WAYLAND_DISPLAY": "wayland-0",
                       "GDK_BACKEND": "wayland", "QT_QPA_PLATFORM": "wayland",
                       "GTK_IM_MODULE": "wayland"}
        for key in ("DISPLAY", "XAUTHORITY", "QT_IM_MODULE", "GTK_PATH", "GTK_IM_MODULE_FILE"):
            environment.pop(key, None)
        flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        connection = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
        connection.set_exit_on_close(False)
        ibus = None
        if input_kind == "module":
            assert toolkit == "qt6"
            from native_context_probe import NativeContextProbe
            service = "org.floegence.QtFixture"
            environment.update(QT_IM_MODULE="floe-prototype", QT_PLUGIN_PATH=str(root / "qt-probe"),
                               FLOE_PROBE_NATIVE_SERVICE=service)
            ibus = NativeContextProbe(connection, service, lambda event: events.append(json.dumps(event)))
        if input_kind == "ibus":
            os.environ["IBUS_ADDRESS"] = "unix:abstract=/tmp/ibus/dbus-" + evidence.name
            environment.update(IBUS_ADDRESS=os.environ["IBUS_ADDRESS"], GTK_IM_MODULE="ibus",
                               QT_IM_MODULE="ibus", IBUS_ENABLE_SYNC_MODE=os.environ.get("FLOE_PROBE_IBUS_SYNC", "1"))
            result["ibus_sync_mode"] = environment["IBUS_ENABLE_SYNC_MODE"]
            components = evidence / "ibus-components"
            components.mkdir()
            start(["ibus-daemon", "--single", "--panel=disable", "--config=disable", "--emoji-extension=disable",
                   "--cache=none", "--address=" + os.environ["IBUS_ADDRESS"]],
                  {**environment, "IBUS_COMPONENT_PATH": str(components)}, "ibus")
            def owns_ibus():
                return connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                    "NameHasOwner", GLib.Variant("(s)", ("org.freedesktop.IBus",)), GLib.VariantType.new("(b)"),
                    Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
            wait(owns_ibus, "Private IBus did not start")
            from ibus_probe import IBusProbe
            ibus = IBusProbe(lambda event: events.append(json.dumps(event)))
            ibus.activate()
        start(["weston", "--backend=headless", "--renderer=pixman", "--shell=" + str(root / "probe/probe-shell.so"),
               "--socket=wayland-0", "--width=1000", "--height=700", "--idle-time=0", "--no-config"],
              {**environment, "FLOE_PROBE_CONTROL_FD": str(right.fileno())}, "compositor", pass_fds=(right.fileno(),))
        right.close()
        threading.Thread(target=controls, daemon=True).start()
        wait(lambda: (runtime / "wayland-0").exists(), "Private display unavailable")
        start(["python3", str(root / "input_fixture.py"), toolkit, str(receipt)], environment, "application")
        def context_ready():
            if ibus is None:
                return any(e.startswith("context ") and e.endswith(" 1") for e in events)
            return bool(ibus.clients) if input_kind == "module" else ibus.active is not None
        wait(lambda: receipt.exists() and context_ready(), "No native focused input context")
        captured = evidence / "loaded"
        captured.mkdir()
        capture = start(["python3", "-c", "import os; os.read(0,1); os.execl('/usr/bin/weston-screenshooter','weston-screenshooter')"],
                        {**environment, "XDG_PICTURES_DIR": str(captured)}, "capture", stdin=subprocess.PIPE)
        left.sendall(f"capture-authorize {capture.pid}\n".encode())
        wait(lambda: f"capture-authorized {capture.pid}" in events, "Capture was not authorized")
        capture.stdin.write(b"x")
        capture.stdin.close()
        capture.wait(timeout=10)
        assert capture.returncode == 0 and len(list(captured.glob("*.png"))) == 1
        # One ordered native input stream; no document feedback or time delay
        # is inserted between the text, pointer focus change and next text.
        if ibus is None:
            left.sendall("text 甲🙂\nmotion 750 180\nbutton 272 1\nbutton 272 0\ntext 乙𠮷\n".encode())
        else:
            stale_before = ibus.enqueue("cancelled before replacement")
            ibus.revoke()
            current = ibus.enqueue("甲🙂")
            left.sendall(stale_before + current)
            wait(lambda: ibus.pending is None, "First marker was not processed")
            stale_after = ibus.enqueue("cancelled after replacement")
            ibus.revoke()
            current = ibus.enqueue("乙𠮷")
            left.sendall(b"motion 750 180\nbutton 272 1\nbutton 272 0\n" + current + stale_after)
        expected = ["甲🙂", "乙𠮷"]
        wait(lambda: json.loads(receipt.read_text()) == expected, "Text crossed native input contexts")
        if ibus is not None:
            # Exercise slot reuse and actual toolkit event ordering without
            # waiting for fixture document feedback between each operation.
            for index in range(64):
                if input_kind == "module":
                    ibus.wait_completed(5)
                else:
                    ibus.transactions.wait_drained(5)
                field = index % 2
                command = f"motion {250 if field == 0 else 750} 180\nbutton 272 1\nbutton 272 0\n"
                # Put each field's caret at its end; a click must not make this
                # test dependent on the current text's rendered glyph width.
                command += "key 29 1\nkey 107 1\nkey 107 0\nkey 29 0\n"
                left.sendall(command.encode() + ibus.enqueue("同🙂"))
                if input_kind == "module":
                    ibus.wait_completed(5)
                else:
                    ibus.transactions.wait_drained(5)
                left.sendall(b"key 28 1\nkey 28 0\n")
                expected[field] += "同🙂\n"
            wait(lambda: json.loads(receipt.read_text()) == expected,
                 "Repeated focus changes or subsequent Enter crossed a native transaction")
            wait(lambda: not ibus.transactions.slots, "Native marker releases were not observed")
            result["cancelled_marker_orders"] = ["before replacement", "after replacement"]
            result["focus_changes_with_enter"] = 64
        result["passed"] = True
    except Exception as error:
        result["error"] = str(error)
    finally:
        if receipt.exists():
            result["actual"] = json.loads(receipt.read_text())
        (evidence / "events.json").write_text(json.dumps(events, indent=2))
        for process, ticks in reversed(processes):
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
        mainloop.quit()
        loop_thread.join(timeout=2)
        shutil.rmtree(runtime)
        (evidence / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
