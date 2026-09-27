"""Real Flatpak GTK/Qt input and document grants through the persistent helper."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

from gi.repository import GLib
from application_processes import identity
from control_probe import ControlClient
from session_probe import cleanup_helper, launch_records
from source_proof import record_sources


def main():
    root, app_id = Path(sys.argv[1]).resolve(), sys.argv[2]
    assert app_id in ('org.gnome.TextEditor', 'org.kde.kwrite')
    evidence = Path(tempfile.mkdtemp(prefix='flatpak-session-', dir=root))
    runtime = Path(tempfile.mkdtemp(prefix='floe-session-', dir=f'/run/user/{os.getuid()}'))
    state = Path.home() / '.var/app' / app_id / ('floe-session-test-' + secrets.token_hex(8))
    state.mkdir(mode=0o700)
    document, destination = evidence / 'document.txt', evidence / 'portal-copy.txt'
    document.write_bytes(b'')
    result = {'passed': False, 'application': app_id, 'evidence': str(evidence), 'runtime': str(runtime),
              'fixture_state': str(state), 'sources': record_sources(root), 'frames': []}
    process = client = started = None
    token, target = secrets.token_hex(32), None
    log = (evidence / 'helper.log').open('w')
    def records():
        return launch_records(evidence)
    def wait(predicate, message, seconds=30):
        deadline = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() >= deadline or process.poll() is not None:
                raise RuntimeError(message)
            time.sleep(.01)
    def paint(stage):
        frames = client.paint(stage)
        result['frames'] += frames
        return {key: frames[-1][key] for key in ('connection', 'window', 'generation')}
    def send(operation):
        return client.request('input', **target, operation=operation)
    def delivered(request):
        response = client.response(request)
        assert 'error' not in response, response
    def key(code, pressed):
        delivered(send({'kind': 'key', 'code': code, 'pressed': pressed}))
    def click(x, y):
        for op in ({'kind': 'move', 'x': x, 'y': y},
                   {'kind': 'button', 'button': 0, 'pressed': True, 'x': x, 'y': y},
                   {'kind': 'button', 'button': 0, 'pressed': False, 'x': x, 'y': y}):
            delivered(send(op))
    try:
        original = Path.home() / '.local/share/flatpak/exports/share/applications' / (app_id + '.desktop')
        entry = GLib.KeyFile.new()
        entry.load_from_file(str(original), GLib.KeyFileFlags.NONE)
        command = entry.get_string('Desktop Entry', 'Exec')
        assert '%U' in command and '--file-forwarding' in command
        # Tighten this fixture only so real document grants are necessary.
        command = command.replace('flatpak run ', 'flatpak run --nofilesystem=host --nofilesystem=home ', 1)
        overrides = ['GSETTINGS_BACKEND=memory']
        for env_key, leaf in (('XDG_DATA_HOME', 'data'), ('XDG_CONFIG_HOME', 'config'),
                          ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state')):
            path = state / leaf
            path.mkdir(mode=0o700)
            overrides.append(env_key + '=' + str(path))
        executable = 'gnome-text-editor' if app_id == 'org.gnome.TextEditor' else 'kwrite'
        assert '--command=' + executable in command
        command = command.replace('--command=' + executable, '--command=env', 1)
        command = command.replace(' ' + app_id + ' ', ' ' + app_id + ' ' + ' '.join(overrides) + ' ' + executable + ' ', 1)
        if app_id == 'org.gnome.TextEditor':
            command = command.replace('@@u %U', '--standalone @@u %U')
            assert '--standalone' in command
        entry.set_string('Desktop Entry', 'Exec', command.replace('%U', '"' + document.as_uri() + '"'))
        entry.set_boolean('Desktop Entry', 'DBusActivatable', False)
        (evidence / 'fixture.desktop').write_text(entry.to_data()[0])
        result['desktop_sha256'] = hashlib.sha256(original.read_bytes()).hexdigest()
        (evidence / 'fixture-environment.json').write_text(json.dumps({
            # Flatpak's user installation is resolved in the host data home.
            # Fixture application state is overridden inside its sandbox above.
            'environment': {key: os.environ[key] for key in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME') if key in os.environ},
            'unset': ['GDK_BACKEND', 'QT_QPA_PLATFORM',
                *(key for key in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME') if key not in os.environ)],
            'host_bus': os.environ['DBUS_SESSION_BUS_ADDRESS'], 'initial_documents': [str(document)]}))
        process = subprocess.Popen([sys.executable, str(root / 'session_probe.py'), str(root), 'helper',
            str(runtime), str(evidence), 'package'], stdin=subprocess.PIPE, stdout=log, stderr=log, start_new_session=True)
        started = identity(process.pid)[1]
        result['helper'] = {'pid': process.pid, 'start_ticks': started}
        process.stdin.write((token + '\n').encode())
        process.stdin.close()
        wait(lambda: any(x.get('phase') == 'sharing_ready' or x.get('state') == 'failed' for x in records()),
             'Persistent Flatpak helper did not prepare', 45)
        assert not any(x.get('state') == 'failed' for x in records()), records()
        # The production helper now owns sandbox module placement. Qualification
        # only observes this instance's immutable bytes and eventual cleanup.
        prefix = '.floe-native-input-' + hashlib.sha256(str(evidence).encode()).hexdigest()[:16] + '-'
        inputs = list((Path.home() / '.var/app' / app_id).glob(prefix + '*'))
        assert len(inputs) == 1, 'No unique instance-owned Qt module directory'
        result['input_resources'] = {'directory': str(inputs[0]), 'modules': {}}
        for major in (5, 6):
            name = f'platforminputcontexts/libfloe-client-native-qt{major}.so'
            actual = (inputs[0] / name).read_bytes()
            assert actual == (Path(os.environ['FLOE_PROBE_QT_PLUGINS']) / name).read_bytes()
            result['input_resources']['modules'][name] = hashlib.sha256(actual).hexdigest()
        plan = json.loads((evidence / 'application-plan.json').read_text())
        result['package'], result['required_services'] = plan['observation']['package'], plan['observation']['services']
        assert result['package']['kind'] == 'flatpak'
        client = ControlClient(evidence / 'viewer', runtime / 'control.sock', runtime.name, token)
        client.reconnect()
        target = paint('loaded')
        click(330, 300)
        pending, expected = [], ''
        for value in ['中文日本語한글🙂👩🏽‍💻e\u0301𠮷'] * 64 + ['界🙂' * 2000]:
            pending += [send({'kind': 'text', 'text': value}), send({'kind': 'key', 'code': 28, 'pressed': True}),
                        send({'kind': 'key', 'code': 28, 'pressed': False})]
            expected += value + '\n'
        for request in pending:
            delivered(request)
        # Both editors save a final newline; GtkSourceView adds its implicit
        # newline even if the buffer already ends in one. A visible suffix
        # makes Enter ordering and the exact saved bytes unambiguous.
        for code in (18, 49, 32):
            key(code, True)
            key(code, False)
        expected += 'end\n'
        # Save bytes below, not protocol replies, establish actual text delivery.
        target = paint('input-received')
        result['unicode_transactions'] = 65
        old = client.generation
        client.close()
        assert process.poll() is None
        client.reconnect()
        assert client.generation > old
        target = paint('reattached')
        result['detach_retains_application'] = True
        previous = target['window']
        for code, pressed in ((29, True), (42, True), (31, True), (31, False), (42, False), (29, False)):
            key(code, pressed)
        def dialog():
            value = client.response(client.request('status'))['result']
            return value['window'] is not None and value['window'] != previous
        wait(dialog, 'Official remote Save As dialog did not appear')
        target = paint('save-dialog')
        assert target['window'] != previous
        # Activate the visible filename field through the same native pointer
        # and selection keys a user employs; no fixture-forced toolkit focus.
        click(500, 22)
        for code, pressed in ((29, True), (30, True), (30, False), (29, False)):
            key(code, pressed)
        delivered(send({'kind': 'text', 'text': 'portal-copy.txt'}))
        key(28, True)
        key(28, False)
        wait(lambda: destination.exists() and destination.read_bytes() == expected.encode(),
             'Flatpak remote Save As did not produce exact Unicode bytes')
        result['saved_sha256'] = hashlib.sha256(destination.read_bytes()).hexdigest()
        assert document.read_bytes() == b''
        result['document_requests'] = [x for x in records() if x.get('event', '').startswith('document-')]
        assert any(x.get('method', '').startswith('Add') and x.get('role') == 'launcher' for x in result['document_requests'])
        assert any(x.get('method', '').startswith('Add') and x.get('role') == 'portal' for x in result['document_requests'])
        target = paint('saved')
        delivered(client.request('close_window', window=target['window']))
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            assert app_id == 'org.gnome.TextEditor'
            target = paint('close-confirmation')
            click(610, 450)
            process.wait(timeout=15)
            result['close_confirmation'] = 'clicked Save in isolated real close dialog'
        exited = next(x for x in records() if x.get('state') == 'exited')
        assert process.returncode == 0 and exited['exit_code'] == 0 and not exited['termination_requested']
        assert destination.read_bytes() == expected.encode()
        assert not inputs[0].exists(), 'Finished application retained its private input resources'
        result['input_resources']['removed_after_exit'] = True
        result['application_exit'], result['passed'] = exited, True
    except BaseException:
        result['error'] = traceback.format_exc()
    finally:
        if client:
            client.close()
            (evidence / 'control-receipts.json').write_text(json.dumps(client.trace, indent=2))
        processes = [x for x in records() if x.get('event') == 'process']
        remaining = cleanup_helper(process, started, processes)
        result['processes'] = processes
        if remaining:
            result['passed'], result['cleanup_remaining'] = False, remaining
        else:
            try:
                from document_probe import clean_session_documents
                result['fixture_document_grants_removed'] = clean_session_documents(
                    os.environ['DBUS_SESSION_BUS_ADDRESS'], evidence, app_id)
            except BaseException:
                result['passed'], result['document_cleanup_error'] = False, traceback.format_exc()
        log.close()
        (evidence / 'launch.json').unlink(missing_ok=True)
        (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))
    if not result['passed']:
        sys.exit(1)


if __name__ == '__main__':
    main()
