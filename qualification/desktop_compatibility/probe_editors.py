"""Actual Flatpak editor/save receipts on the task's disposable native host."""
import json
import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

from gi.repository import Gio, GLib
from application_processes import ProcessTree, identity
from portal_probe import start_portals
from bus_probe import configuration


def main():
    root, app_id = Path(sys.argv[1]).resolve(), sys.argv[2]
    assert app_id in ("org.gnome.TextEditor", "org.kde.kwrite")
    input_kind = os.environ.get("FLOE_PROBE_INPUT", "native")
    host_address = os.environ["DBUS_SESSION_BUS_ADDRESS"]
    document_bridge = None
    document_authority, document_tree, launcher_tree = None, None, None
    document_probe = None
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
    support, capture_command, authorize = None, None, None
    if os.environ.get('FLOE_PROBE_COMPONENT'):
        from portable_services import PortableServices
        support = PortableServices(os.environ['FLOE_PROBE_COMPONENT'], evidence)
        outcome['support_processes'] = 'private original Alpine component'

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
        nonlocal launcher_tree
        def spawn():
            nonlocal launcher_tree
            log = (evidence / (name + ".log")).open("w")
            logs.append(log)
            process = subprocess.Popen(command, env=environment, stdout=log, stderr=log,
                                       start_new_session=True, **kwargs)
            started = identity(process.pid)[1]
            processes.append((process, started, name))
            if document_authority and name == 'xdg-desktop-portal':
                document_authority.bind_portal(document_tree.admit(process.pid))
            if document_authority and name == 'application':
                launcher_tree = ProcessTree(process.pid, started)
                document_authority.bind_launcher(launcher_tree, shutil.which('flatpak'), [str(document)])
            return process
        if document_authority and name in ('xdg-desktop-portal', 'application'):
            # Publish the exact native identity on the service event loop before
            # it can dispatch the child's first document request.
            finished, result = threading.Event(), []
            def dispatch():
                try:
                    result.append(spawn())
                except Exception as error:
                    result.append(error)
                finished.set()
                return False
            GLib.idle_add(dispatch)
            assert finished.wait(5), 'Document service process binding did not complete'
            if isinstance(result[0], Exception):
                raise result[0]
            return result[0]
        return spawn()

    def owns(name):
        return connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner", GLib.Variant("(s)", (name,)),
            GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]

    def capture(stage):
        destination = evidence / stage
        destination.mkdir()
        if capture_command:
            from capture_probe import capture as portable_capture
            def authorized(pid):
                wait_until(lambda: any(e.get('control') == f'capture-authorized {pid}' for e in events),
                           'Capture process was not authorized')
            outcome.setdefault('frames', []).append(portable_capture(
                capture_command, environment, start, left, authorized, destination))
            return
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
            if document_probe:
                document_probe.assert_unrelated_denied(address)
            if os.environ.get("FLOE_PROBE_TRACE"):
                start(["dbus-monitor", "--address", address], environment, "bus-trace")
            outcome['portal'] = start_portals(evidence, address, display, connection, start, wait_until,
                          host_documents=document_bridge is not None, tools=support, package_runtime=runtime)
            portal_command = support.command('usr/lib/ibus/ibus-portal') if support else [
                os.environ.get("FLOE_PROBE_IBUS_PORTAL", "/usr/libexec/ibus-portal")]
            derived_portal = os.environ.get('FLOE_PROBE_IBUS_PORTAL')
            if derived_portal:
                portal_command = [derived_portal]
            portal_binary = portal_command[-1]
            outcome["ibus_portal"] = {"path": portal_binary,
                                      "sha256": hashlib.sha256(Path(portal_binary).read_bytes()).hexdigest()}
            portal = start(portal_command, support.environment(environment) if support and not derived_portal else environment, "ibus-portal")
            wait_until(lambda: owns("org.freedesktop.portal.IBus") or portal.poll() is not None, "No IBus portal")
            assert portal.poll() is None, "IBus portal exited"
            if native:
                native.enable_ibus(daemon.pid, portal.pid)
            else:
                ibus.activate()
            if authorize:
                log = evidence / 'compositor.log'
                pattern = r'xserver listening on display (:[0-9]+)'
                wait_until(lambda: re.search(pattern, log.read_text()), 'Private Xwayland did not reserve its display')
                xdisplay = re.search(pattern, log.read_text()).group(1)
                authorize(xdisplay)
                environment['DISPLAY'] = xdisplay
                for key in ('GDK_BACKEND', 'QT_QPA_PLATFORM'):
                    environment.pop(key, None)
                outcome['offered_protocols'] = ['wayland', 'x11']
                outcome['graphical_protocol_forced'] = False
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
                overrides += ["QT_IM_MODULE=floe-client-native", "QT_PLUGIN_PATH=" + str(fixture_state / "input-module"),
                              "FLOE_NATIVE_DESKTOP_INPUT=" + app_id + ".FloeClientInput"]
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
            protocols = [e['control'].split()[2] for e in events
                         if e.get('control', '').startswith('window-protocol ')]
            assert protocols, 'No actual application protocol observation'
            outcome['actual_application_protocol'] = protocols[0]
            capture("loaded")
            left.sendall(b"motion 330 300\nbutton 272 1\nbutton 272 0\n")
            if input_kind == "module":
                wait_until(lambda: len(native.clients) == 1, "No sandbox native Qt input context")
            elif input_kind == "ibus":
                if native:
                    wait_until(lambda: native.input_path is not None, 'No native sandbox toolkit context')
                else:
                    wait_until(lambda: ibus.active is not None and any(e.get("ibus") == "context" and e.get("client") != "fake" for e in events),
                               "No sandbox toolkit input context")
                if os.environ.get('FLOE_PROBE_CONTEXT_SOURCE') and not native:
                    from ibus_source_probe import qualify
                    focus = next(e['control'].split() for e in reversed(events)
                                 if e.get('control', '').startswith('focus ') and e['control'].endswith(' 1'))
                    outcome['ibus_context_source'] = qualify(ibus, connection, portal.pid, runtime, int(focus[3]))
            else:
                wait_until(lambda: any(e.get("control", "").startswith("context ") and
                    e["control"].endswith(" 1") for e in events), "No native text-input context")
            def submit_text(value):
                if native:
                    native.commit(value)
                elif input_kind == "ibus":
                    left.sendall(ibus.enqueue(value))
                    ibus.transactions.wait_drained(5)
                else:
                    left.sendall(("text " + value + "\n").encode())
            text = "中文日本語한글🙂👩🏽‍💻e\u0301𠮷"
            submit_text(text)
            # A visible suffix distinguishes Enter delivery from the editors'
            # implicit final newline, without normalizing application bytes.
            suffix = b"key 38 1\nkey 38 0\nkey 30 1\nkey 30 0\nkey 31 1\nkey 31 0\nkey 20 1\nkey 20 0\n"
            submit_text(text)
            left.sendall(b"key 28 1\nkey 28 0\n" + suffix)
            expected = text + text + "\nlast\n"
            if os.environ.get("FLOE_PROBE_STRESS"):
                assert input_kind in ("module", "ibus")
                contents = expected[:-1]
                for value in [text] * 64 + ["界🙂" * 2000]:
                    submit_text(value)
                    left.sendall(b"key 28 1\nkey 28 0\n")
                    contents += value + "\n"
                # GtkSourceView saves an implicit trailing newline even when
                # the visible buffer already ends in one. Finish with a visible
                # suffix so both editors have an unambiguous byte expectation.
                left.sendall(b"key 18 1\nkey 18 0\nkey 49 1\nkey 49 0\nkey 32 1\nkey 32 0\n")
                expected = contents + "end\n"
                outcome["stress"] = {"repeated_commits": 64, "long_commit_bytes": 14000,
                                     "completion": ('synchronous IBus context release' if input_kind == 'ibus'
                                                    else 'toolkit event loop') if native else 'prototype marker receipt'}
            outcome["expected"] = expected
            if native:
                outcome['sandbox_peer'] = next(e['native_context'] for e in events if 'native_context' in e)
                assert outcome['sandbox_peer']['bus_pid'] != outcome['sandbox_peer']['native_pid']
            if os.environ.get("FLOE_PROBE_SAVE_DIALOG"):
                previous_context = native.input_path if native else (
                    ibus.active.input_path if ibus.active else None)
                frames = sum(e.get("control", "").startswith("frame ") for e in events)
                left.sendall(b"key 29 1\nkey 42 1\nkey 31 1\nkey 31 0\nkey 42 0\nkey 29 0\n")
                wait_until(lambda: sum(e.get("control", "").startswith("frame ") for e in events) > frames,
                           "No remote Save As dialog")
                capture("save-dialog")
                destination = evidence / "portal-copy.txt"
                wait_until(lambda: (native.input_path is not None and native.input_path != previous_context)
                           if native else
                           (ibus.active is not None and ibus.active.input_path != previous_context),
                           "No distinct FileChooser IBus context")
                # The official GTK backend owns this context. Its stock chooser
                # preselects the stem and retains '.txt'. Submit exactly once.
                if native:
                    native.commit('portal-copy')
                    left.sendall(b'key 28 1\nkey 28 0\n')
                else:
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
        config.write_text(configuration('unix:abstract=/tmp/dbus-floe-' + secrets.token_hex(12)))
        bus_log = (evidence / "bus.log").open("w")
        logs.append(bus_log)
        bus_command = support.command('usr/bin/dbus-daemon') if support else ['dbus-daemon']
        bus = subprocess.Popen([*bus_command, "--nofork", "--config-file=" + str(config), "--print-address=1"],
            env=support.environment(os.environ) if support else os.environ,
            stdout=subprocess.PIPE, stderr=bus_log, text=True, start_new_session=True)
        processes.append((bus, identity(bus.pid)[1], "bus"))
        address = bus.stdout.readline().strip()
        flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        connection = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
        connection.set_exit_on_close(False)
        if os.environ.get("FLOE_PROBE_DOCUMENT_PORTAL"):
            from desktop_documents import DocumentAuthority
            from desktop_document_service import DocumentService
            from document_probe import DocumentProbe
            document_authority = DocumentAuthority()
            document_tree = ProcessTree(os.getpid(), identity(os.getpid())[1])
            document_probe = DocumentProbe(host_address, app_id, document)
            document_bridge = DocumentService(connection, host_address, app_id, document_authority, record)
        os.environ["IBUS_ADDRESS"] = "unix:abstract=/tmp/ibus/dbus-floe-" + secrets.token_hex(12)
        environment = {**os.environ, "DBUS_SESSION_BUS_ADDRESS": address, "XDG_RUNTIME_DIR": str(runtime),
            "WAYLAND_DISPLAY": display.name, "GDK_BACKEND": "wayland", "QT_QPA_PLATFORM": "wayland",
            "GTK_IM_MODULE": "ibus" if input_kind == "ibus" else "wayland", "IBUS_ENABLE_SYNC_MODE": "1"}
        if input_kind == "module":
            assert app_id == "org.kde.kwrite"
            from context_probe import ToolkitDriver
            shutil.copytree(root / "qt-native/platforminputcontexts", fixture_state / "input-module/platforminputcontexts")
            native = ToolkitDriver(connection, left, runtime, app_id + ".FloeClientInput", record)
        elif input_kind == 'ibus' and os.environ.get('FLOE_PROBE_CONTEXT'):
            from context_probe import ToolkitDriver
            native = ToolkitDriver(connection, left, runtime, 'org.floegence.DesktopInput', record)
        if input_kind == "ibus":
            environment["QT_IM_MODULE"] = "ibus"
        else:
            environment.pop("QT_IM_MODULE", None)
        for key in ("DISPLAY", "XAUTHORITY", "GTK_PATH", "GTK_IM_MODULE_FILE"):
            environment.pop(key, None)
        components = evidence / "ibus-components"
        components.mkdir()
        daemon_command = support.command('usr/bin/ibus-daemon') if support else ['ibus-daemon']
        derived_daemon = os.environ.get('FLOE_PROBE_IBUS_DAEMON')
        if derived_daemon:
            daemon_command = [derived_daemon]
        daemon_environment = {**environment, "IBUS_COMPONENT_PATH": str(components),
            "XDG_CONFIG_HOME": str(evidence / "ibus-config"), "XDG_CACHE_HOME": str(evidence / "ibus-cache")}
        daemon = start([*daemon_command, "--single", "--panel=disable", "--config=disable", "--emoji-extension=disable",
               "--cache=none", "--address=" + os.environ["IBUS_ADDRESS"]],
              support.environment(daemon_environment) if support and not derived_daemon else daemon_environment, "ibus")
        wait_until(lambda: owns("org.freedesktop.IBus"), "No private IBus daemon")
        ibus = None
        if not native:
            from ibus_probe import IBusProbe
            ibus = IBusProbe(record)
        compositor_command = ["weston", "--backend=headless", "--renderer=pixman", "--shell=" + str(root / "probe/probe-shell.so"),
               "--socket=" + display.name, "--width=1000", "--height=700", "--idle-time=0", "--no-config"]
        compositor_environment = dict(environment)
        if support:
            from portable_probe import prepare
            compositor_command, compositor_environment, capture_command, authorize, outcome['portable'] = prepare(
                os.environ['FLOE_PROBE_COMPONENT'], evidence, environment,
                root / 'alpine-wayland-probe/probe-shell.so')
        start(compositor_command, {**compositor_environment, "FLOE_PROBE_CONTROL_FD": str(right.fileno())},
              "compositor", pass_fds=(right.fileno(),))
        right.close()

        def controls():
            with left.makefile("r") as stream:
                for line in stream:
                    record({"control": line.strip()})
                    if native:
                        native.observe(line.strip())

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
            try:
                outcome['document_authority'] = document_probe.preserve_then_clean(document_bridge)
            except Exception as error:
                outcome['document_cleanup_error'] = str(error)
                outcome['passed'] = False
                document_bridge.close()
        if document_authority:
            document_authority.close()
        if launcher_tree:
            launcher_tree.close()
        if document_tree:
            document_tree.close()
        if native:
            native.close()
        left.close()
        right.close()
        outcome["processes"] = [{"pid": p.pid, "start_ticks": ticks, "name": name, "exit": p.returncode}
                                for p, ticks, name in processes]
        outcome["socket_cleaned"] = not display.exists()
        shutil.rmtree(runtime)
        shutil.rmtree(fixture_state)
        (evidence / 'Xauthority').unlink(missing_ok=True)
        (evidence / 'portal-runtime/.flatpak').unlink(missing_ok=True)
        (evidence / "result.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({key: value for key, value in outcome.items()
                          if key not in ("processes", "expected", "actual")}, ensure_ascii=False, indent=2))
    if not outcome["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
