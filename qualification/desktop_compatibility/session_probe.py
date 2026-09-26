"""Actual persistent helper, GIO supervisor and toolkit receipts in a private session.

The viewer side knows only authenticated IPC and decoded frames. It has no local
native registry or direct compositor input path. All application data is owned
by this test; no user's desktop application is launched or reused.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import traceback

from application_processes import identity
from source_proof import record_sources


def helper(root, runtime, evidence, token, mode):
    from gi.repository import Gio, GLib
    from desktop_graphics import DesktopGraphics
    from desktop_services import DesktopServices
    from desktop_session import DesktopSession
    from launch_plan import prepare
    loop = GLib.MainLoop()
    environment = {**os.environ, 'XDG_RUNTIME_DIR': str(runtime), 'WAYLAND_DISPLAY': 'wayland-0',
        'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + str(runtime / 'bus'), 'GDK_BACKEND': 'wayland',
        'FLOE_TEST_WINDOW_COLOR': '13579b'}
    for name in ('DISPLAY', 'XAUTHORITY', 'FLOE_NATIVE_APPLICATION_ENV', 'FLOE_NATIVE_INPUT_GTK_PATH',
                 'GTK_IM_MODULE', 'GTK_IM_MODULE_FILE', 'GTK_PATH', 'GIO_EXTRA_MODULES'):
        environment.pop(name, None)
    for key, name in (('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'), ('XDG_DATA_HOME', 'data')):
        path = runtime / name
        path.mkdir(mode=0o700)
        environment[key] = str(path)
    specification = {}
    if mode == 'package':
        specification = json.loads((evidence / 'fixture-environment.json').read_text())
        environment.update(specification['environment'])
        for key in specification['unset']:
            environment.pop(key, None)
    services = DesktopServices(os.environ['FLOE_PROBE_COMPONENT'], evidence)
    native = Path(os.environ['FLOE_PROBE_NATIVE'])
    library = Path(os.environ['FLOE_PROBE_WESTON_LIBRARY'])
    graphics = DesktopGraphics(services.component, evidence, environment, shell=native / 'probe-shell.so',
        capture=native / 'frame-probe', library=library / 'libweston-14.so.0',
        xwayland=library.parent / 'xwayland/xwayland.so')
    if mode == 'support-failure':
        graphics.command.append('--floe-deliberately-invalid-option')
    (evidence / 'graphics.json').write_text(json.dumps(graphics.description, indent=2))
    def record(value):
        with (evidence / 'helper.jsonl').open('a') as stream:
            stream.write(json.dumps(value) + '\n')
    plan = prepare(str(evidence / 'fixture.desktop'), environment,
        [{'id': 'wayland', 'component': 'unpublished-native-session-fixture', 'protocols': ['wayland', 'x11'],
          'services': ['user-systemd-scope', 'file-portal', 'document-portal', 'ibus-portal']}])
    session = DesktopSession(evidence, runtime, runtime.name, token, services, graphics,
        ibus_command=[os.environ['FLOE_PROBE_IBUS_DAEMON']], plan=plan,
        application_launcher=[sys.executable, str(root / 'application.py')], application_environment=environment,
        completed=loop.quit, record=record, host_bus=specification.get('host_bus'),
        ibus_portal_command=[os.environ['FLOE_PROBE_IBUS_PORTAL']] if 'FLOE_PROBE_IBUS_PORTAL' in os.environ else (),
        initial_documents=specification.get('initial_documents', ()))
    original_failure = session.fail
    def failure(code, **details):
        # Test-only traceback observation: the isolated fixture has no user data.
        # Production diagnostics still contain only the classified error fields.
        if sys.exc_info()[0] is not None:
            with (evidence / 'failure.log').open('a') as stream:
                traceback.print_exc(file=stream)
        original_failure(code, **details)
    session.fail = failure
    from unittest.mock import patch
    create_launcher = Gio.SubprocessLauncher.new
    def launcher(flags):
        # Preserve real subprocess creation, retaining support stderr only in
        # this isolated fixture. Native diagnostics never redirect application data.
        return create_launcher(Gio.SubprocessFlags(flags & ~Gio.SubprocessFlags.STDERR_SILENCE))
    with patch.object(Gio.SubprocessLauncher, 'new', side_effect=launcher):
        session.start()
        loop.run()


def cleanup_helper(process, started, processes):
    if process and process.poll() is None and identity(process.pid)[1] == started:
        # The fixture owns this exact supervisor. Its explicit SIGTERM is
        # test cleanup only; no viewer or production disconnect does this.
        for item in processes:
            if item['service'] != 'application':
                continue
            try:
                descriptor = os.pidfd_open(item['pid'])
                if identity(item['pid'])[1] == item['start_ticks']:
                    signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                os.close(descriptor)
            except (FileNotFoundError, ProcessLookupError):
                pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            descriptor = os.pidfd_open(process.pid)
            if identity(process.pid)[1] == started:
                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
            os.close(descriptor)
            process.wait(timeout=5)
    # A test cannot pass merely because its helper was killed after cleanup
    # stalled. Every recorded support process must have actually exited.
    remaining = []
    for item in processes:
        try:
            if identity(item['pid'])[1] == item['start_ticks']:
                remaining.append(item)
        except (FileNotFoundError, ProcessLookupError):
            pass
    return remaining


def main():
    root = Path(sys.argv[1]).resolve()
    if len(sys.argv) > 2 and sys.argv[2] == 'helper':
        helper(root, Path(sys.argv[3]), Path(sys.argv[4]), sys.stdin.readline().strip(), sys.argv[5])
        return
    mode = sys.argv[2] if len(sys.argv) > 2 else 'normal'
    assert mode in ('normal', 'chromium', 'slow-window', 'launcher-failure', 'support-failure', 'capture-loss', 'bus-loss', 'runtime-noexec')
    from control_probe import ControlClient
    evidence = Path(tempfile.mkdtemp(prefix='persistent-session-', dir=root))
    runtime = Path(tempfile.mkdtemp(prefix='floe-session-', dir=f'/run/user/{os.getuid()}'))
    token = secrets.token_hex(32)
    receipt = evidence / 'document.json'
    desktop = evidence / 'fixture.desktop'
    executable = evidence / 'launch.py'
    browser = None
    if mode == 'chromium':
        from chromium_context_probe import ChromiumPage
        browser = ChromiumPage(evidence, receipt, 'wayland')
    prefix = 'import sys, time, runpy\n'
    if mode == 'slow-window':
        prefix += 'time.sleep(42)\n'
    elif mode == 'launcher-failure':
        prefix += 'sys.exit(46)\n'
    executable.write_text(('import os\n' + f'command = {browser.command!r}\nos.execv(command[0], command)\n') if browser else
        prefix + f'sys.argv = {[str(root / "input_fixture.py"), "gtk4", str(receipt)]!r}\n' +
        f'runpy.run_path({str(root / "input_fixture.py")!r}, run_name="__main__")\n')
    desktop.write_text('[Desktop Entry]\nType=Application\nName=Floe persistent test\nExec=' +
        f'"{sys.executable}" "{executable}"\n')
    result = {'passed': False, 'mode': mode, 'evidence': str(evidence), 'sources': record_sources(root),
              'runtime': str(runtime), 'runtime_noexec': bool(os.statvfs(runtime).f_flag & os.ST_NOEXEC)}
    process, client, started = None, None, None
    mounted = None
    logs = (evidence / 'helper.log').open('w')
    def records():
        path = evidence / 'helper.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    def wait(predicate, reason, seconds=25):
        deadline = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() >= deadline or process.poll() is not None:
                raise RuntimeError(reason)
            time.sleep(.01)
    try:
        if mode == 'runtime-noexec':
            # A restrictive mount belongs only to this newly created fixture
            # directory. No existing desktop mount or security policy is changed.
            assert not os.path.ismount(runtime)
            subprocess.run(['sudo', '-n', 'mount', '-t', 'tmpfs', '-o',
                f'size=16m,mode=700,uid={os.getuid()},gid={os.getgid()},noexec,nodev,nosuid',
                'tmpfs', str(runtime)], check=True, timeout=10)
            info = runtime.stat()
            mounted = (info.st_dev, info.st_ino)
            result['runtime_noexec'] = bool(os.statvfs(runtime).f_flag & os.ST_NOEXEC)
            assert result['runtime_noexec']
        process = subprocess.Popen([sys.executable, str(root / 'session_probe.py'), str(root), 'helper',
            str(runtime), str(evidence), mode], stdin=subprocess.PIPE, stdout=logs, stderr=logs, start_new_session=True)
        started = identity(process.pid)[1]
        result['helper'] = {'pid': process.pid, 'start_ticks': started}
        process.stdin.write((token + '\n').encode())
        process.stdin.close()
        wait(lambda: any(x.get('phase') == 'sharing_ready' or x.get('state') == 'failed' for x in records()),
            'Persistent helper did not prepare', 45)
        if mode == 'support-failure':
            assert process.wait(timeout=10) == 0
            assert any(x.get('state') == 'failed' for x in records())
            assert not any(x.get('service') == 'application' for x in records())
            result['support_failure_before_application'] = True
            result['passed'] = True
            return
        if mode == 'launcher-failure':
            assert process.wait(timeout=10) == 0
            exited = next(x for x in records() if x.get('error_code') == 'APPLICATION_LAUNCHER_EXITED')
            assert exited['state'] == 'failed' and exited['exit_code'] == 46 and not exited['termination_requested']
            result['application_exit'], result['passed'] = exited, True
            return
        assert not any(x.get('state') == 'failed' for x in records()), records()
        result['required_services'] = json.loads((evidence / 'application-plan.json').read_text())['observation']['services']
        result['fuse_device_available'] = Path('/dev/fuse').exists()
        assert not result['required_services']
        assert not any(x.get('service', '').startswith('xdg-') for x in records()), records()
        client = ControlClient(evidence / 'viewer', runtime / 'control.sock', runtime.name, token)
        client.reconnect()
        if mode == 'slow-window':
            assert client.response(client.request('status'))['result']['state'] == 'waiting'
        began = time.monotonic()
        wait((lambda: browser.ready) if browser else receipt.exists,
             'No application readiness receipt', 50 if mode == 'slow-window' else 25)
        result['first_window_wait_seconds'] = time.monotonic() - began
        if mode == 'slow-window':
            assert result['first_window_wait_seconds'] > 40
        status = client.response(client.request('status'))['result']
        while status['window'] is None:
            client.record()
            status = client.state
        target = {'connection': client.generation, 'window': status['window'], 'generation': status['generation']}
        denied = client.request('input', **target, operation={'kind': 'key', 'code': 45, 'pressed': True})
        assert client.response(denied)['error'] == 'INPUT_TARGET_UNAVAILABLE'
        def paint(stage, values=None):
            options = {'marker': browser.commit_marker(values) if values else (59, 117, 159)} if browser else {'expected': (19, 87, 155)}
            return client.paint(stage, **options)
        frame = paint('first-frame')[-1]
        result['frames'] = [frame]
        target = {'connection': client.generation, 'window': frame['window'], 'generation': frame['generation']}
        def input(operation):
            return client.request('input', **target, operation=operation)
        for op in ({'kind': 'move', 'x': 250, 'y': 180},
                   {'kind': 'button', 'button': 0, 'pressed': True, 'x': 250, 'y': 180},
                   {'kind': 'button', 'button': 0, 'pressed': False, 'x': 250, 'y': 180}):
            request = input(op)
            assert 'error' not in client.response(request)
        expected = ''
        for index in range(32):
            text = '相同中文 日本語 한국어 😀 𠀀 e\u0301 🧑🏽\u200d💻'
            input({'kind': 'text', 'text': text})
            input({'kind': 'key', 'code': 28, 'pressed': True})
            last = input({'kind': 'key', 'code': 28, 'pressed': False})
            assert 'error' not in client.response(last)
            expected += text + '\n'
        wait(lambda: json.loads(receipt.read_text()) == [expected, ''], 'Actual Unicode/Enter order differs')
        result['document_sha256'] = hashlib.sha256(expected.encode()).hexdigest()
        result['unicode_transactions'] = 32
        input({'kind': 'key', 'code': 29, 'pressed': True})
        old_connection = client.generation
        client.close()
        assert process.poll() is None and identity(process.pid)[1] == started
        client.reconnect()
        assert client.generation > old_connection
        frame = paint('after-reconnect', [expected, ''])[-1]
        result['frames'].append(frame)
        target = {'connection': client.generation, 'window': frame['window'], 'generation': frame['generation']}
        input({'kind': 'key', 'code': 48, 'pressed': True})
        last = input({'kind': 'key', 'code': 48, 'pressed': False})
        assert 'error' not in client.response(last)
        expected += 'b'
        wait(lambda: json.loads(receipt.read_text()) == [expected, ''], 'Reconnect did not preserve document/release modifiers')
        result['detach_retains_application'] = True
        result['frames'] += paint('final-document', [expected, ''])
        if browser:
            result['browser'] = browser.version
            result['protocol'] = 'wayland'
        if mode in ('capture-loss', 'bus-loss'):
            service_name = 'capture' if mode == 'capture-loss' else 'bus'
            service = next(x for x in records() if x.get('service') == service_name)
            descriptor = os.pidfd_open(service['pid'])
            try:
                assert identity(service['pid'])[1] == service['start_ticks']
                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
            finally:
                os.close(descriptor)
            wait(lambda: any(x.get('state') == 'failed' for x in records()), 'Capture failure not reported')
            assert client.response(client.request('status'))['result']['state'] == 'unavailable'
            assert client.response(input({'kind': 'key', 'code': 48, 'pressed': True}))['error'] == 'INPUT_TARGET_UNAVAILABLE'
            app = next(x for x in records() if x.get('service') == 'application')
            assert identity(app['pid'])[1] == app['start_ticks'] and process.poll() is None
            assert json.loads(receipt.read_text()) == [expected, '']
            result[service_name + '_failure_retains_application'], result['passed'] = True, True
            return
        close = client.request('close_window', window=target['window'])
        assert 'error' not in client.response(close)
        assert process.wait(timeout=15) == 0
        exited = next(x for x in records() if x.get('state') == 'exited')
        assert exited['exit_code'] == 0 and not exited['termination_requested']
        result['application_exit'] = exited
        assert not (runtime / 'control.sock').exists()
        result['passed'] = True
    except BaseException:
        result['error'] = traceback.format_exc()
    finally:
        if browser:
            browser.close()
        if client:
            client.close()
            (evidence / 'control-receipts.json').write_text(json.dumps(client.trace, indent=2))
        result['processes'] = [x for x in records() if x.get('event') == 'process']
        remaining = cleanup_helper(process, started, result['processes'])
        if remaining:
            result['passed'] = False
            result['cleanup_remaining'] = remaining
        if mounted is not None:
            info = runtime.stat()
            assert (info.st_dev, info.st_ino) == mounted and os.path.ismount(runtime)
            unmounted = subprocess.run(['sudo', '-n', 'umount', str(runtime)], timeout=10)
            result['restrictive_runtime_mount_removed'] = unmounted.returncode == 0
            if unmounted.returncode:
                result['passed'] = False
            else:
                runtime.rmdir()
        logs.close()
        (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))
    if not result['passed']:
        sys.exit(1)


if __name__ == '__main__':
    main()
