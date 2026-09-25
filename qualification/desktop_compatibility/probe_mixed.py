"""Actual Wayland/Xwayland window and seat coexistence on a disposable host.

The two child fixtures represent a single owned application process tree. This
does not qualify production X11 authorization, Unicode or sandbox admission.
"""
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

from scope_bridge import identity


def main():
    root = Path(sys.argv[1]).resolve()
    evidence = Path(tempfile.mkdtemp(prefix="mixed-", dir=root))
    runtime = Path(tempfile.mkdtemp(prefix="floe-mixed-", dir=f"/run/user/{os.getuid()}"))
    left, right = socket.socketpair()
    processes, logs, events = [], [], []
    result = {"passed": False, "evidence": str(evidence)}
    frame_socket, frame_process, frame_sequence = None, None, 0

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
        return process

    def capture(stage):
        nonlocal frame_socket, frame_process, frame_sequence
        if os.environ.get("FLOE_PROBE_FRAMES"):
            if frame_socket is None:
                frame_socket, child_socket = socket.socketpair()
                frame_socket.settimeout(10)
                frame_process = start(["python3", "-c", "import os,sys; os.read(0,1); os.execv(sys.argv[1],sys.argv[1:])",
                                       str(root / "frame-probe/frame-probe")],
                    {**environment, "FLOE_PROBE_FRAME_FD": str(child_socket.fileno())},
                    "frame-capture", stdin=subprocess.PIPE, pass_fds=(child_socket.fileno(),))
                child_socket.close()
                left.sendall(f"capture-authorize {frame_process.pid}\n".encode())
                wait(lambda: f"capture-authorized {frame_process.pid}" in events, "Frame capture was not authorized")
                frame_process.stdin.write(b"x")
                frame_process.stdin.close()
            def read_bytes(length):
                chunks, total = [], 0
                while total < length:
                    chunk = frame_socket.recv(length - total)
                    if not chunk:
                        raise RuntimeError("Frame capture disconnected")
                    chunks.append(chunk)
                    total += len(chunk)
                return b"".join(chunks)
            frame_sequence += 1
            frame_socket.sendall(struct.pack("=I", frame_sequence))
            sequence, status, width, height, fmt, length = struct.unpack("=6I", read_bytes(24))
            assert sequence == frame_sequence and status == 1
            assert 0 < width <= 4096 and 0 < height <= 4096 and length == width * height * 4
            pixels = read_bytes(length)
            assert len(set(pixels)) > 16, "Captured frame does not contain real painted content"
            result.setdefault("frames", []).append({"stage": stage, "sequence": sequence, "width": width,
                "height": height, "format": fmt, "sha256": hashlib.sha256(pixels).hexdigest()})
            from PIL import Image
            Image.frombytes("RGBA", (width, height), pixels, "raw", "BGRA").convert("RGB").save(evidence / (stage + ".png"))
            return
        directory = evidence / stage
        directory.mkdir()
        process = start(["python3", "-c", "import os; os.read(0,1); os.execl('/usr/bin/weston-screenshooter','weston-screenshooter')"],
                        {**environment, "XDG_PICTURES_DIR": str(directory)}, "capture-" + stage, stdin=subprocess.PIPE)
        left.sendall(f"capture-authorize {process.pid}\n".encode())
        wait(lambda: f"capture-authorized {process.pid}" in events, "Capture was not authorized")
        process.stdin.write(b"x")
        process.stdin.close()
        process.wait(timeout=10)
        assert process.returncode == 0 and len(list(directory.glob("*.png"))) == 1

    def controls():
        with left.makefile("r") as stream:
            for line in stream:
                events.append(line.strip())

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
            command, server_environment, authorize, result['portable'] = prepare(
                os.environ['FLOE_PROBE_COMPONENT'], evidence, environment,
                root / 'alpine-wayland-probe/probe-shell.so')
        compositor = start(command,
                           {**server_environment, "FLOE_PROBE_CONTROL_FD": str(right.fileno())},
                           "compositor", pass_fds=(right.fileno(),))
        right.close()
        threading.Thread(target=controls, daemon=True).start()
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
                        {**environment, "GDK_BACKEND": "wayland"}, "wayland")
        wait(lambda: receipts[0].exists() and any(e.startswith("frame ") for e in events),
             "No mapped Wayland fixture frame")
        capture("wayland")
        left.sendall(b"motion 250 180\nbutton 272 1\nbutton 272 0\nkey 30 1\nkey 30 0\n")
        wait(lambda: json.loads(receipts[0].read_text()) == ["a", ""], "No actual Wayland seat input")
        xwayland = start(["python3", str(root / "input_fixture.py"), "gtk4", str(receipts[1])],
                         {**environment, "GDK_BACKEND": "x11", "DISPLAY": display}, "xwayland")
        wait(lambda: receipts[1].exists() and events.count("window-added") == 2 and
             sum(e.startswith("frame ") for e in events) == 2, "No distinct mapped Xwayland fixture")
        capture("mixed")
        left.sendall(b"motion 250 180\nbutton 272 1\nbutton 272 0\nkey 48 1\nkey 48 0\n")
        wait(lambda: json.loads(receipts[1].read_text()) == ["b", ""], "No actual Xwayland seat input")
        if frame_socket:
            for index in range(8):
                capture("frame-" + str(index))
            # Withhold the complete frame from the consumer. The separate
            # bounded capture process may block, but the application must not.
            frame_sequence += 1
            frame_socket.sendall(struct.pack("=I", frame_sequence))
            left.sendall(b"key 32 1\nkey 32 0\n")
            wait(lambda: json.loads(receipts[1].read_text()) == ["bd", ""],
                 "Frame backpressure blocked application input")
            # Closing in the middle of that response must free the one pending
            # frame, without requiring the compositor or application to exit.
            result["backpressure_input"] = ["bd", ""]
            # Detaching the capture consumer must preserve both applications
            # and the compositor. A new helper receives a fresh authorization.
            frame_socket.close()
            frame_socket = None
            frame_process.wait(timeout=5)
            assert frame_process.returncode == 0 and wayland.poll() is None and xwayland.poll() is None
            capture("reattached")
        from Xlib import Xatom, display as xdisplay
        from unittest.mock import patch
        with patch.dict(os.environ, {'XAUTHORITY': environment.get('XAUTHORITY', '')}):
            xconnection = xdisplay.Display(display)
        pids = []
        pending = list(xconnection.screen().root.query_tree().children)
        inspected = 0
        while pending:
            window = pending.pop()
            inspected += 1
            assert inspected <= 512, "Unexpectedly large private X11 window tree"
            prop = window.get_full_property(xconnection.intern_atom("_NET_WM_PID"), Xatom.CARDINAL)
            if prop:
                pids.extend(int(value) for value in prop.value)
            pending.extend(window.query_tree().children)
        xconnection.close()
        assert xwayland.pid in pids, "X11 window does not identify the owned fixture"
        result["x11_window_pids"] = pids
        left.sendall(b"close\n")
        xwayland.wait(timeout=10)
        assert xwayland.returncode == 0 and wayland.poll() is None
        wait(lambda: "window-restored" in events, "Closing Xwayland did not restore Wayland")
        capture("restored")
        identities = [int(e.split()[1]) for e in events if e.startswith("window-instance ")]
        assert len(identities) == 2 and identities[0] != identities[1]
        left.sendall(b"connection 1\nconnection 2\n")
        wait(lambda: "connection-ready 2" in events, "Connection replacement was not admitted")
        # A retired native window and an old viewer generation must both reject
        # their late input. The following live key proves the stream progressed.
        stale = (f"input 1 {identities[0]} key 45 1\ninput 1 {identities[0]} key 45 0\n"
                 f"input 2 {identities[1]} key 45 1\ninput 2 {identities[1]} key 45 0\n")
        left.sendall(stale.encode())
        left.sendall(b"key 46 1\nkey 46 0\n")
        wait(lambda: json.loads(receipts[0].read_text()) == ["ac", ""], "Restored Wayland window did not receive input")
        assert events.count(f"input-rejected 1 {identities[0]}") == 2
        assert events.count(f"input-rejected 2 {identities[1]}") == 2
        result["rejected_stale_input"] = {"old_connection": 2, "retired_window": 2}
        left.sendall(b"close\n")
        wayland.wait(timeout=10)
        assert wayland.returncode == 0
        result["actual"] = [json.loads(path.read_text()) for path in receipts]
        result["passed"] = True
    except Exception as error:
        result["error"] = str(error)
    finally:
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
