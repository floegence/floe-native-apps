"""Actual packaged editor input/save/exit through the installed session API.

Only explicit disposable documents and configuration directories are used.
AppImages execute their original mounted image; no extraction or host install.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import traceback

from gi.repository import GLib
from application_processes import identity
from control_probe import ControlClient
from session_probe import cleanup_helper, launch_records, session_file, session_token
from source_proof import record_sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--desktop', type=Path)
    target.add_argument('--executable', type=Path)
    parser.add_argument('--kind', choices=('deb', 'rpm', 'appimage', 'snap'), required=True)
    parser.add_argument('--standalone', action='store_true')
    parser.add_argument('--emacs', action='store_true', help='Use the documented Emacs isolated-start and save commands')
    parser.add_argument('--save-adds-newline', action='store_true', help='Assert the selected editor appends one additional newline when serializing a document')
    parser.add_argument('--inspect-failure', action='store_true', help='Keep only the failed task-owned fixture alive for 60 seconds for native inspection')
    parser.add_argument('--notepadnext', action='store_true', help='Use a disposable Notepad Next plain-text configuration without word completion')
    args = parser.parse_args()
    root = args.source.resolve()
    evidence = Path(tempfile.mkdtemp(prefix='native-editor-', dir=root))
    runtime = Path(tempfile.mkdtemp(prefix='floe-session-', dir=f'/run/user/{os.getuid()}'))
    document = evidence / 'document.txt'
    document.write_bytes(b'')
    result = {'passed': False, 'evidence': str(evidence), 'runtime': str(runtime),
              'sources': record_sources(root), 'frames': []}
    process = client = started = None
    token, current = secrets.token_hex(32), None
    log = (evidence / 'helper.log').open('w')

    def records():
        return launch_records(evidence)

    def wait(predicate, message):
        deadline = time.monotonic() + 45
        while not predicate():
            if time.monotonic() >= deadline or process.poll() is not None:
                raise RuntimeError(message)
            time.sleep(.01)

    def paint(stage):
        frames = client.paint(stage)
        result['frames'] += frames
        return {key: frames[-1][key] for key in ('connection', 'window', 'generation')}

    def delivered(request):
        response = client.response(request)
        assert 'error' not in response, response

    def send(operation):
        return client.request('input', **current, operation=operation)

    def key(code, pressed):
        delivered(send({'kind': 'key', 'code': code, 'pressed': pressed}))

    def saved_exactly(expected):
        try:
            return document.read_bytes() == expected.encode()
        except FileNotFoundError:
            # Editors may rename the old document before publishing the new
            # file. Observe completion without changing or retrying input.
            return False

    try:
        entry = GLib.KeyFile.new()
        if args.desktop:
            original = args.desktop.resolve(strict=True)
            entry.load_from_file(str(original), GLib.KeyFileFlags.NONE)
            command = entry.get_string('Desktop Entry', 'Exec')
            fields = [field for field in ('%U', '%F') if command.count(field) == 1]
            assert len(fields) == 1
            field = fields[0]
            # Emacs otherwise resizes its character-cell frame when fallback
            # fonts first appear. A maximized fixture keeps this text-ordering
            # case separate from intentional geometry-change cancellation.
            switches = '--standalone ' if args.standalone else '--no-init-file --no-site-file --maximized ' if args.emacs else ''
            command = command.replace(field, switches + '"' + (document.as_uri() if field == '%U' else str(document)) + '"')
            result['desktop_sha256'] = hashlib.sha256(original.read_bytes()).hexdigest()
        else:
            executable = args.executable.resolve(strict=True)
            assert args.kind == 'appimage' and not args.standalone
            entry.set_string('Desktop Entry', 'Type', 'Application')
            entry.set_string('Desktop Entry', 'Name', 'Floe original AppImage qualification')
            command = '"' + str(executable) + '" "' + str(document) + '"'
            result['executable_sha256'] = hashlib.sha256(executable.read_bytes()).hexdigest()
        entry.set_string('Desktop Entry', 'Exec', command)
        entry.set_boolean('Desktop Entry', 'DBusActivatable', False)
        (evidence / 'fixture.desktop').write_text(entry.to_data()[0])
        environment = {'GSETTINGS_BACKEND': 'memory'}
        if args.notepadnext:
            # This fixture tests literal text/Enter; editor word suggestions
            # deliberately consume Enter and are a different application action.
            config = evidence / 'test-config'
            directory = config / 'NotepadNext'
            directory.mkdir(parents=True, mode=0o700)
            (directory / 'NotepadNext.ini').write_text('[Editor]\nAutoCompletion=false\nURLHighlighting=false\n')
            environment['XDG_CONFIG_HOME'] = str(config)
            result['application_settings'] = {'Editor/AutoCompletion': False, 'Editor/URLHighlighting': False}
        (evidence / 'fixture-environment.json').write_text(json.dumps({
            'environment': environment,
            'unset': ['GDK_BACKEND', 'QT_QPA_PLATFORM', 'APPIMAGE_EXTRACT_AND_RUN'],
            'host_bus': os.environ['DBUS_SESSION_BUS_ADDRESS']}))
        process = subprocess.Popen([sys.executable, str(root / 'session_probe.py'), str(root), 'helper',
            str(runtime), str(evidence), 'package'], stdin=subprocess.PIPE, stdout=log, stderr=log, start_new_session=True)
        started = identity(process.pid)[1]
        result['helper'] = {'pid': process.pid, 'start_ticks': started}
        process.stdin.write((token + '\n').encode())
        process.stdin.close()
        wait(lambda: any(x.get('phase') == 'sharing_ready' or x.get('state') == 'failed' for x in records()),
             'Installed native editor session did not prepare')
        assert not any(x.get('state') == 'failed' for x in records()), records()
        plan = json.loads(session_file(evidence, 'application-plan.json').read_text())
        result['package'] = plan['observation']['package']
        assert result['package']['kind'] == args.kind
        if args.kind == 'snap':
            assert result['package']['confinement'] == 'classic'
        client = ControlClient(evidence / 'viewer', runtime / 'control.sock', runtime.name, session_token(evidence, token))
        client.reconnect()
        current = paint('loaded')
        for operation in ({'kind': 'move', 'x': 330, 'y': 300},
                          {'kind': 'button', 'button': 0, 'pressed': True, 'x': 330, 'y': 300},
                          {'kind': 'button', 'button': 0, 'pressed': False, 'x': 330, 'y': 300}):
            delivered(send(operation))
        expected, pending = '', []
        for value in ['中文日本語한글🙂👩🏽‍💻e\u0301𠮷'] * 64 + ['界🙂' * 2000]:
            pending += [send({'kind': 'text', 'text': value}), send({'kind': 'key', 'code': 28, 'pressed': True}),
                        send({'kind': 'key', 'code': 28, 'pressed': False})]
            expected += value + '\n'
        for request in pending:
            delivered(request)
        for code in (18, 49, 32, 28):
            key(code, True)
            key(code, False)
        expected += 'end\n'
        if args.save_adds_newline:
            expected += '\n'
        previous = client.generation
        client.close()
        assert process.poll() is None
        client.reconnect()
        assert client.generation > previous
        current = paint('reattached')
        shortcut = [(29, True)]
        if args.emacs:
            shortcut += [(45, True), (45, False)]
        shortcut += [(31, True), (31, False), (29, False)]
        for code, pressed in shortcut:
            key(code, pressed)
        wait(lambda: saved_exactly(expected), 'Packaged editor did not save the exact document')
        result['saved_sha256'] = hashlib.sha256(document.read_bytes()).hexdigest()
        result.setdefault('unicode_transactions', 65)
        current = paint('saved')
        delivered(client.request('close_window', window=current['window']))
        process.wait(timeout=20)
        exited = next(x for x in records() if x.get('state') == 'exited')
        assert process.returncode == 0 and exited['exit_code'] == 0 and not exited['termination_requested']
        result['application_exit'], result['passed'] = exited, True
    except BaseException:
        result['error'] = traceback.format_exc()
        if args.inspect_failure:
            (evidence / 'inspection.json').write_text(json.dumps({'helper': result.get('helper'), 'error': result['error']}))
            time.sleep(60)
        if client and client.client:
            try:
                paint('failure')
            except BaseException:
                result['failure_frame_error'] = traceback.format_exc()
    finally:
        if client:
            client.close()
            (evidence / 'control-receipts.json').write_text(json.dumps(client.trace, indent=2))
        processes = [x for x in records() if x.get('event') == 'process']
        remaining = cleanup_helper(process, started, processes)
        result['processes'] = processes
        if remaining:
            result['passed'], result['cleanup_remaining'] = False, remaining
        log.close()
        (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))
    if not result['passed']:
        sys.exit(1)


if __name__ == '__main__':
    main()
