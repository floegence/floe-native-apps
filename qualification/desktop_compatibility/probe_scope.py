"""Run the installed Snap's version query on a task-private bus, with no display."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading

from gi.repository import GLib
from scope_bridge import ScopeBridge


def main():
    host_address = os.environ["DBUS_SESSION_BUS_ADDRESS"]
    events = []
    with tempfile.TemporaryDirectory(prefix="floe-scope-probe-") as directory:
        root = Path(directory)
        config = root / "bus.conf"
        config.write_text('''<busconfig><type>session</type><auth>EXTERNAL</auth>
        <listen>unix:tmpdir=/tmp</listen><policy context="default">
        <allow send_destination="*"/><allow receive_sender="*"/>
        <allow own="*"/></policy></busconfig>''')
        bus = subprocess.Popen(["dbus-daemon", "--nofork", "--config-file=" + str(config),
                                "--print-address=1"], stdout=subprocess.PIPE, text=True)
        bridge = None
        try:
            private_address = bus.stdout.readline().strip()
            bridge = ScopeBridge(private_address, host_address, "snap.firefox.firefox", os.getpid(), events.append)
            environment = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=private_address)
            for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"):
                environment.pop(key, None)
            loop = GLib.MainLoop()
            result = {}

            def run():
                child = subprocess.Popen(["/snap/bin/firefox", "--version"], env=environment,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
                try:
                    stdout, stderr = child.communicate(timeout=20)
                    result.update(code=child.returncode, stdout=stdout, stderr=stderr)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGTERM)
                    stdout, stderr = child.communicate(timeout=5)
                    result.update(timeout=True, stdout=stdout, stderr=stderr)
                finally:
                    GLib.idle_add(loop.quit)

            thread = threading.Thread(target=run)
            thread.start()
            loop.run()
            thread.join()
            print(json.dumps({"result": result, "events": events}, indent=2))
            assert result.get("code") == 0, "Snap still cannot launch on the private bus"
            jobs = [event for event in events if event["event"] == "real_systemd_job"]
            assert len(jobs) == 1 and jobs[0]["result"] == "done"
            assert jobs[0]["cgroup"].endswith(jobs[0]["unit"])
        finally:
            if bridge:
                bridge.close()
            bus.terminate()
            bus.wait(timeout=5)


if __name__ == "__main__":
    main()
