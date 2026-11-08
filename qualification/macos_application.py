"""Explicit signed application qualification; never requests or resets TCC grants."""
import json
import os
from pathlib import Path
import plistlib
import select
import shutil
import signal
import subprocess
import tempfile
import time


def run():
    identity = os.environ["NATIVE_APPLICATION_SIGNING_IDENTITY"]
    subprocess.run(["swift", "build", "--product", "NativeApplicationQualification"], check=True)
    executable = Path(".build/debug/NativeApplicationQualification").resolve()
    with tempfile.TemporaryDirectory(prefix="native-app-qualification-") as temporary:
        app = Path(temporary) / "Native Application Fixture.app"
        (app / "Contents/MacOS").mkdir(parents=True)
        shutil.copy2(executable, app / "Contents/MacOS/fixture")
        with (app / "Contents/Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleIdentifier": "com.floegence.native.application.fixture",
                          "CFBundleName": "Native Application Fixture", "CFBundleExecutable": "fixture",
                          "CFBundlePackageType": "APPL", "LSUIElement": True, "CFBundleVersion": "1"}, stream)
        subprocess.run(["codesign", "--force", "--sign", identity, str(app)], check=True)
        pids = []
        for mode in ["eof", "signal", "media", "killed"]:
            read_fd, write_fd = os.pipe()
            def media_descriptor():
                os.dup2(write_fd, 3)
            command = [str(executable), str(app)] + (["--media"] if mode == "media" else [])
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, close_fds=False, preexec_fn=media_descriptor if mode == "media" else None)
            try:
                assert select.select([process.stdout], [], [], 30)[0], "application attachment timed out"
                line = process.stdout.readline()
                assert line, process.stderr.read()
                hello = json.loads(line)
                assert hello["parent"] == 1 and hello["pid"] != process.pid
                assert hello["name"] == "Native Application Fixture"
                assert hello["identifier"] == "com.floegence.native.application.fixture"
                pids.append(hello["pid"])
                assert not Path(hello["endpoint"]).exists(), "attached application left endpoint directory"
                process.stdin.write("fresh-channel-" + mode + "\n"); process.stdin.flush()
                assert process.stdout.readline().strip() == "fresh-channel-" + mode
                if mode == "media":
                    assert select.select([read_fd], [], [], 5)[0]
                    assert os.read(read_fd, 13) == b"media-fixture"
                if mode == "signal": process.send_signal(signal.SIGTERM)
                elif mode == "killed": process.kill()
                else: process.stdin.close()
                status = process.wait(timeout=10)
                assert status == ({"signal": 143, "killed": -9}.get(mode, 0)), status
                deadline = time.monotonic() + 5
                while True:
                    try: os.kill(hello["pid"], 0)
                    except ProcessLookupError: break
                    assert time.monotonic() < deadline, "application survived relay exit"
                    time.sleep(0.05)
            finally:
                if process.poll() is None: process.terminate(); process.wait(timeout=10)
                os.close(read_fd); os.close(write_fd)
        assert len(set(pids)) == len(pids), "restart reused an existing process"
        executable_bytes = (app / "Contents/MacOS/fixture").read_bytes()
        with (app / "Contents/MacOS/fixture").open("ab") as stream:
            stream.write(b"invalid-signature")
        tampered = subprocess.run([str(executable), str(app)], input="", text=True, capture_output=True, timeout=10)
        assert tampered.returncode != 0 and "invalidBundle" in tampered.stderr
        (app / "Contents/MacOS/fixture").write_bytes(executable_bytes)
        subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
        denied = subprocess.run([str(executable), str(app)], input="", text=True, capture_output=True, timeout=10)
        assert denied.returncode != 0 and "invalidBundle" in denied.stderr
        print(json.dumps({"signed_application": True, "independent_parent": True, "duplex": True,
                          "media": True, "fresh_processes": len(pids), "eof_signal_crash_cleanup": True,
                          "endpoint_cleanup": True, "tampering_rejected": True, "adhoc_rejected": True, "system_permissions_changed": False}))


if __name__ == "__main__":
    run()
