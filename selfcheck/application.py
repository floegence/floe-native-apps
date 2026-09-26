"""Installed GIO lifetime checks, using only disposable windowless processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

python, launcher = sys.argv[1:]
sys.path.insert(0, str(Path(launcher).parent))
from launch_plan import prepare, restored_environment
from application_processes import LaunchChildren, ProcessTree, identity


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
        body = 'test -z "$FLOE_NATIVE_APPLICATION_ENV" && test -z "$FLOE_NATIVE_ROOT" && test -z "$FLOE_NATIVE_INPUT_GTK_PATH" && test -z "$FLOE_NATIVE_HOST_BUS" || exit 2\n'
        body += f'test "$GTK_PATH" = "{root}/gtk" || exit 3\n'
        body += f'touch "{root}/started"\nwhile test ! -f "{root}/exit"; do sleep 0.05; done\n'
        for _ in range(adopted):
            body = "(\n" + body + ") &\nexit 0\n"
        script.write_text(body)
        desktop = root / "fixture.desktop"
        desktop.write_text(f'[Desktop Entry]\nType=Application\nName=Lifetime fixture\nExec=/bin/sh "{script}"\nDBusActivatable=true\n')
        receipt = root / "launch.json"
        child = subprocess.Popen([python, launcher, str(desktop), str(receipt)],
                                 env={**os.environ, "FLOE_NATIVE_INPUT_GTK_PATH": str(root / "gtk"),
                                      "FLOE_NATIVE_HOST_BUS": "fixture-host-address-must-not-reach-child"})
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

with tempfile.TemporaryDirectory(prefix="floe-application-exit-status-") as directory:
    root = Path(directory)
    script = root / "app.sh"
    script.write_text('exit 46\n')
    desktop = root / "fixture.desktop"
    desktop.write_text(f'[Desktop Entry]\nType=Application\nName=Exit status fixture\nExec=/bin/sh "{script}"\n')
    receipt = root / "launch.json"
    result = subprocess.run([python, launcher, str(desktop), str(receipt)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    status = json.loads(receipt.read_text())
    assert result.returncode == 46, "supervisor discarded the launcher's failure status"
    assert status["exit_code"] == 46 and status["phase"] == "process_exit"
    assert status["launchers"][0]["exit_code"] == 46
    assert not status["termination_requested"]

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
        status = json.loads((root / "launch.json").read_text())
        assert status["termination_requested"], "explicit force quit lost its observation"
        try:
            os.kill(descendant, 0)
            raise AssertionError("explicit termination left an application descendant")
        except ProcessLookupError:
            pass
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
print("application lifetime: windowless, adopted descendant, host environment, exit status, failure and termination passed")

with tempfile.TemporaryDirectory(prefix="floe-application-plan-") as directory:
    root = Path(directory)
    script = root / 'arguments.sh'
    received = root / 'arguments.txt'
    script.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > ' + str(received) + '\n')
    script.chmod(0o700)
    desktop = root / 'fixture.desktop'
    source = ('[Desktop Entry]\nType=Application\nName=Floe planned fixture\n'
              f'Exec="{script}" "literal spaces" %% %c %k %F\n')
    desktop.write_text(source)
    # This checks pre-execution identity, not availability of a graphics backend.
    backend = {'id': 'wayland', 'component': 'identity-selfcheck-only',
               'protocols': ['wayland', 'x11']}
    plan = prepare(str(desktop), restored_environment(os.environ), [backend])
    plan_path = root / 'plan.json'
    plan_path.write_text(json.dumps(plan))
    receipt = root / 'receipt.json'
    command = [python, launcher, '--plan', str(plan_path), str(receipt)]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    assert result.returncode == 0, 'validated plan failed to launch'
    assert received.read_text().splitlines() == ['literal spaces', '%', 'Floe planned fixture', str(desktop)]
    received.unlink()
    desktop.write_text(source.replace('Floe planned fixture', 'Changed fixture'))
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    assert result.returncode != 0 and not received.exists(), 'stale plan executed a child'
    assert json.loads(receipt.read_text())['error_code'] == 'APPLICATION_PLAN_STALE'
print('application planning: GIO arguments preserved; stale plan rejected before execution')

# The process remains a zombie, retaining its numeric PID, until the real
# host-service operation releases its lease. pidfd liveness alone cannot prove
# this property. Exercise the kernel's wait boundary on each native runner.
received = []
children = LaunchChildren(lambda pid, status: received.append((pid, status)))
process = subprocess.Popen([python, '-c', 'import os; os.read(0,1); os._exit(46)'], stdin=subprocess.PIPE)
pid = process.pid
children.register(pid)
started = identity(pid)[1]
lease = children.pin(pid)
waiter = threading.Thread(target=children.wait)
waiter.start()
try:
    process.stdin.write(b'x')
    process.stdin.close()
    observation = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
    assert observation.si_pid == pid and observation.si_status == 46
    assert identity(pid)[1] == started and received == [], 'scope lease allowed premature PID reuse'
    lease.close()
    waiter.join(5)
    assert not waiter.is_alive() and received == [(pid, 46 << 8)]
    process.returncode = 46  # LaunchChildren already performed the sole waitpid.
finally:
    lease.close()
    waiter.join(5)
print('application ownership: actual kernel exit observed without reaping until scope lease release')

# Native window and D-Bus peer admission shares this observer, while the
# supervisor alone keeps launch/termination/wait ownership.
tree = ProcessTree(os.getpid(), identity(os.getpid())[1])
peer = subprocess.Popen([python, '-c', 'import os; os.read(0,1)'], stdin=subprocess.PIPE)
try:
    reference = tree.admit(peer.pid)
    assert reference.valid(), 'live owned native peer was rejected'
    assert not tree.owns(os.getppid()), 'unrelated parent inherited application authority'
    peer.stdin.write(b'x')
    peer.stdin.close()
    assert peer.wait(timeout=5) == 0
    assert not reference.valid(), 'exited native peer retained input authority'
    reference.close()
    assert not tree.references
finally:
    tree.close()
    if peer.poll() is None:
        peer.kill()
        peer.wait(timeout=5)
print('native peer ownership: exact live descendant, unrelated process rejection and exit revocation passed')
