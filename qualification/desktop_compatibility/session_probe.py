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


def fixture_toolkit():
    value = os.environ.get('FLOE_PROBE_TOOLKIT', 'gtk4')
    if value not in ('gtk', 'gtk4', 'qt5', 'qt6'):
        raise ValueError('Unknown native session fixture toolkit')
    return value


def helper(root, runtime, evidence, token, mode):
    from launch_plan import prepare
    environment = {**os.environ, 'XDG_RUNTIME_DIR': str(runtime), 'WAYLAND_DISPLAY': 'wayland-0',
        'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + str(runtime / 'bus'), 'GDK_BACKEND': 'wayland',
        'FLOE_TEST_WINDOW_COLOR': '13579b', 'PYTHONPATH': '/floe-qualification/host-python'}
    if mode == 'clipboard-x11':
        environment['GDK_BACKEND'] = 'x11'
    if mode == 'bus-loss':
        # This fixture deliberately survives bus loss so the helper's lifetime
        # policy can be tested independently of GApplication's self-termination.
        environment['FLOE_TEST_RETAIN_AFTER_BUS_LOSS'] = '1'
    for name in ('DISPLAY', 'XAUTHORITY', 'FLOE_NATIVE_APPLICATION_ENV', 'FLOE_NATIVE_INPUT_GTK_PATH',
                 'GTK_IM_MODULE', 'GTK_IM_MODULE_FILE', 'GTK_PATH', 'GIO_EXTRA_MODULES',
                 'QT_IM_MODULE', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM'):
        environment.pop(name, None)
    if fixture_toolkit().startswith('qt'):
        environment['QT_QPA_PLATFORM'] = 'xcb' if mode == 'clipboard-x11' else 'wayland'
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
    if 'FLOE_PROBE_DESKTOP_STATE' in os.environ:
        if mode == 'support-failure':
            raise ValueError('Installed components cannot be replaced by a fixture override')
        request = {'state': os.environ['FLOE_PROBE_DESKTOP_STATE'],
            'desktop': str(evidence / 'fixture.desktop'), 'directory': str(evidence / 'session'),
            'runtime': str(runtime), 'instance': runtime.name, 'environment': environment,
            'host_bus': specification.get('host_bus', ''), 'documents': specification.get('initial_documents', [])}
        data = subprocess.check_output([os.environ['FLOE_PROBE_DESKTOP_PREPARER']],
            input=json.dumps(request).encode())
        prepared = json.loads(data)
        descriptor = os.open(evidence / 'endpoint.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(prepared, stream)
        os.execv(prepared['Executable'], [prepared['Executable'], prepared['Configuration']])
    native = Path(os.environ['FLOE_PROBE_NATIVE'])
    library = Path(os.environ['FLOE_PROBE_WESTON_LIBRARY'])
    resources = {'component': os.environ['FLOE_PROBE_COMPONENT'],
        'shell': str(native / 'probe-shell.so'), 'capture': str(native / 'frame-probe'),
        'library': str(library / 'libweston-14.so.0'),
        'xwayland': str(library.parent / 'xwayland/xwayland.so'),
        'ibus_daemon': os.environ['FLOE_PROBE_IBUS_DAEMON'],
        'qt_plugins': os.environ['FLOE_PROBE_QT_PLUGINS'],
        'gtk_modules': os.environ['FLOE_PROBE_GTK_MODULES']}
    if 'FLOE_PROBE_IBUS_PORTAL' in os.environ:
        resources['ibus_portal'] = os.environ['FLOE_PROBE_IBUS_PORTAL']
    if mode == 'support-failure':
        invalid = evidence / 'invalid-shell.so'
        invalid.write_bytes(b'Task-owned invalid compositor module')
        resources['shell'] = str(invalid)
    plan = prepare(str(evidence / 'fixture.desktop'), environment,
        [{'id': 'wayland', 'component': 'unpublished-native-session-fixture', 'protocols': ['wayland', 'x11'],
          'services': ['user-systemd-scope', 'file-portal', 'document-portal', 'ibus-portal']}])
    configuration = evidence / 'launch.json'
    descriptor = os.open(configuration, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump({'version': 1, 'instance': runtime.name, 'token': token,
            'directory': str(evidence), 'runtime': str(runtime), 'resources': resources,
            'environment': environment, 'plan': plan, 'host_bus': specification.get('host_bus'),
            'initial_documents': specification.get('initial_documents', [])}, stream)
    # Execute the installed entrypoint. The viewer has no fixture-only session
    # assembly, diagnostic callback patch, service launcher or direct input path.
    entry = Path(os.environ.get('FLOE_PROBE_DESKTOP_LAUNCHER', root / 'desktop_bootstrap.py'))
    if 'FLOE_PROBE_DESKTOP_LAUNCHER' in os.environ:
        os.execv(str(entry), [str(entry), str(configuration)])
    os.execv(sys.executable, [sys.executable, str(entry), str(configuration)])


def session_file(evidence, name):
    return Path(evidence) / ('session/' if 'FLOE_PROBE_DESKTOP_STATE' in os.environ else '') / name


def session_token(evidence, fallback):
    if 'FLOE_PROBE_DESKTOP_STATE' in os.environ:
        return json.loads((Path(evidence) / 'endpoint.json').read_text())['Endpoint']['Token']
    return fallback


def launch_records(evidence):
    path = session_file(evidence, 'desktop-status.json')
    if not path.exists():
        return []
    receipt = json.loads(path.read_text())
    assert receipt['version'] == 1
    return receipt['processes'] + receipt['transitions'] + receipt['service_events']


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
    assert mode in ('normal', 'chromium', 'clipboard', 'clipboard-x11', 'clipboard-chromium', 'slow-window', 'launcher-failure', 'support-failure', 'capture-loss', 'bus-loss', 'runtime-noexec', 'terminate', 'terminate-windowless')
    from control_probe import ControlClient
    evidence = Path(tempfile.mkdtemp(prefix='persistent-session-', dir=root))
    runtime = Path(tempfile.mkdtemp(prefix='floe-session-', dir=f'/run/user/{os.getuid()}'))
    token = secrets.token_hex(32)
    receipt = evidence / 'document.json'
    desktop = evidence / 'fixture.desktop'
    executable = evidence / 'launch.py'
    browser = None
    if mode in ('chromium', 'clipboard-chromium'):
        from chromium_context_probe import ChromiumPage
        browser = ChromiumPage(evidence, receipt, 'wayland')
    prefix = 'import sys, time, runpy, os, json\n'
    prefix += f'os.dup2(os.open({str(evidence / "application.log")!r}, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 2)\n'
    prefix += f'open({str(evidence / "application-environment.json")!r}, "w").write(json.dumps({{"prefix": sys.prefix, "python_path": os.environ.get("PYTHONPATH"), "support_overrides": "FLOE_NATIVE_APPLICATION_ENV" in os.environ}}))\n'
    if mode == 'slow-window':
        prefix += 'time.sleep(42)\n'
    elif mode == 'terminate-windowless':
        prefix += 'time.sleep(60)\n'
    elif mode == 'launcher-failure':
        prefix += 'sys.exit(46)\n'
    baseline = os.environ.get('FLOE_TEST_GTK4_BASELINE')
    if baseline and (fixture_toolkit() != 'gtk4' or not Path(baseline).is_absolute() or browser):
        raise ValueError('GTK baseline requires its absolute native GTK4 application')
    executable.write_text(('import os\n' + f'command = {browser.command!r}\nos.execv(command[0], command)\n') if browser else
        (prefix + f'os.execv({baseline!r}, {[baseline, str(receipt)]!r})\n') if baseline else
        prefix + f'sys.argv = {[str(root / "input_fixture.py"), fixture_toolkit(), str(receipt)]!r}\n' +
        f'runpy.run_path({str(root / "input_fixture.py")!r}, run_name="__main__")\n')
    desktop.write_text('[Desktop Entry]\nType=Application\nName=Floe persistent test\nExec=' +
        f'"{sys.executable}" "{executable}"\n')
    result = {'passed': False, 'mode': mode, 'evidence': str(evidence), 'sources': record_sources(root),
              'runtime': str(runtime), 'runtime_noexec': bool(os.statvfs(runtime).f_flag & os.ST_NOEXEC)}
    if not browser:
        result['toolkit'] = fixture_toolkit()
    process, client, started = None, None, None
    mounted = None
    logs = (evidence / 'helper.log').open('w')
    def records():
        return launch_records(evidence)
    def wait(predicate, reason, seconds=25):
        deadline = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() >= deadline or process.poll() is not None:
                raise RuntimeError(reason)
            time.sleep(.01)
    def terminate(failed=False):
        # The authenticated request is only intent. The supervisor's receipt
        # and real process disappearance below establish actual completion.
        assert client.response(client.request('terminate_application'))['result'] == 'requested'
        assert process.wait(timeout=15) == (1 if failed else 0)
        exited = next(x for x in records() if x.get('state') == 'exited')
        assert exited['termination_requested'] and exited['exit_code'] < 0
        result['application_exit'] = exited
        assert not (runtime / 'control.sock').exists()
        result['authenticated_termination'], result['passed'] = True, True
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
            assert process.wait(timeout=10) == 1
            assert any(x.get('state') == 'failed' for x in records())
            assert not any(x.get('service') == 'application' for x in records())
            result['support_failure_before_application'] = True
            result['passed'] = True
            return
        if mode == 'launcher-failure':
            assert process.wait(timeout=10) == 1
            exited = next(x for x in records() if x.get('error_code') == 'APPLICATION_LAUNCHER_EXITED')
            assert exited['state'] == 'failed' and exited['exit_code'] == 46 and not exited['termination_requested']
            result['application_exit'], result['passed'] = exited, True
            return
        assert not any(x.get('state') == 'failed' for x in records()), records()
        configuration = session_file(evidence, 'desktop-launch.json') if 'FLOE_PROBE_DESKTOP_STATE' in os.environ else evidence / 'launch.json'
        resources = json.loads(configuration.read_text())['resources']
        if not browser and fixture_toolkit().startswith('qt'):
            result['qt_modules_sha256'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in Path(resources['qt_plugins']).glob('platforminputcontexts/*.so')}
            assert len(result['qt_modules_sha256']) == 2
        result['required_services'] = json.loads(session_file(evidence, 'application-plan.json').read_text())['observation']['services']
        result['fuse_device_available'] = Path('/dev/fuse').exists()
        assert not result['required_services']
        assert not any(x.get('service', '').startswith('xdg-') for x in records()), records()
        client = ControlClient(evidence / 'viewer', runtime / 'control.sock', runtime.name, session_token(evidence, token))
        client.reconnect()
        if mode == 'terminate-windowless':
            wait(lambda: (evidence / 'application-environment.json').exists(), 'Windowless application never started')
            assert client.response(client.request('status'))['result']['state'] == 'waiting'
            assert not receipt.exists()
            client.close()
            assert process.poll() is None
            client.reconnect()
            assert client.state['state'] == 'waiting'
            terminate()
            return
        if mode == 'slow-window':
            assert client.response(client.request('status'))['result']['state'] == 'waiting'
        began = time.monotonic()
        wait((lambda: browser.ready) if browser else receipt.exists,
             'No application readiness receipt', 50 if mode == 'slow-window' else 25)
        result['first_window_wait_seconds'] = time.monotonic() - began
        if not browser:
            actual = json.loads((evidence / 'application-environment.json').read_text())
            assert actual['python_path'] == '/floe-qualification/host-python' and not actual['support_overrides']
            assert actual['prefix'] != str(Path(resources['component']) / 'usr')
            result['application_environment_restored'] = True
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
            if mode in ('clipboard', 'clipboard-x11'):
                # Long pasted text occupies the first editor. The empty second
                # editor retains the same distinctive application background.
                options['sample_point'] = (600, 240)
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
        if mode in ('clipboard', 'clipboard-x11', 'clipboard-chromium'):
            from clipboard_fixture import exercise
            expected = exercise(client, target, receipt, wait, result)
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
        if browser and os.environ.get('FLOE_PROBE_CURSOR') == '1':
            from cursor_fixture import exercise
            exercise(client, target, browser, result)
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
            result[service_name + '_failure_retains_application'] = True
            client.close()
            assert process.poll() is None
            client.reconnect()
            assert client.state['state'] == 'unavailable'
            result['reattached_after_failure'] = True
            terminate(failed=True)
            return
        if mode == 'terminate':
            terminate()
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
        (evidence / 'launch.json').unlink(missing_ok=True)
        (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))
    if not result['passed']:
        sys.exit(1)


if __name__ == '__main__':
    main()
