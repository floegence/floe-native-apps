"""Installed GIO lifetime checks, using only disposable windowless processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

python, launcher = sys.argv[1:]


def await_file(path, process):
    deadline = time.monotonic() + 5
    while not path.exists():
        if process.poll() is not None or time.monotonic() > deadline:
            raise AssertionError("application did not reach its owned lifetime checkpoint")
        time.sleep(0.02)


for adopted in (0, 1, 2):
    with tempfile.TemporaryDirectory(prefix="floe-application-check-") as directory:
        root = Path(directory)
        script = root / "app.sh"
        body = 'test -z "$FLOE_NATIVE_APPLICATION_ENV" && test -z "$FLOE_NATIVE_ROOT" && test -z "$FLOE_NATIVE_INPUT_GTK_PATH" || exit 2\n'
        body += f'test "$GTK_PATH" = "{root}/gtk" || exit 3\n'
        body += f'touch "{root}/started"\nwhile test ! -f "{root}/exit"; do sleep 0.05; done\n'
        for _ in range(adopted):
            body = "(\n" + body + ") &\nexit 0\n"
        script.write_text(body)
        desktop = root / "fixture.desktop"
        desktop.write_text(f'[Desktop Entry]\nType=Application\nName=Lifetime fixture\nExec=/bin/sh "{script}"\nDBusActivatable=true\n')
        receipt = root / "launch.json"
        child = subprocess.Popen([python, launcher, str(desktop), str(receipt)],
                                 env={**os.environ, "FLOE_NATIVE_INPUT_GTK_PATH": str(root / "gtk")})
        try:
            await_file(root / "started", child)
            await_file(receipt, child)
            assert json.loads(receipt.read_text())["state"] == "running"
            time.sleep(0.1)
            assert child.poll() is None, "windowless or adopted application was released early"
        finally:
            (root / "exit").touch()
            child.wait(timeout=5)
        assert child.returncode == 0
        assert json.loads(receipt.read_text())["state"] == "exited"

with tempfile.TemporaryDirectory(prefix="floe-application-failed-") as directory:
    receipt = Path(directory) / "launch.json"
    result = subprocess.run([python, launcher, str(Path(directory) / "missing.desktop"), str(receipt)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    assert result.returncode != 0
    assert json.loads(receipt.read_text())["state"] == "failed"

with tempfile.TemporaryDirectory(prefix="floe-application-terminate-") as directory:
    root = Path(directory)
    script = root / "app.sh"
    script.write_text(f'sleep 30 &\necho $! > "{root}/child"\nwait\n')
    desktop = root / "fixture.desktop"
    desktop.write_text(f'[Desktop Entry]\nType=Application\nName=Termination fixture\nExec=/bin/sh "{script}"\n')
    child = subprocess.Popen([python, launcher, str(desktop), str(root / "launch.json")])
    try:
        await_file(root / "child", child)
        descendant = int((root / "child").read_text())
        child.terminate()
        child.wait(timeout=5)
        try:
            os.kill(descendant, 0)
            raise AssertionError("explicit termination left an application descendant")
        except ProcessLookupError:
            pass
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
print("application lifetime: windowless, adopted descendant, host environment, exit and failure passed")
