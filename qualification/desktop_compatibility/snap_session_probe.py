"""Strict Snap acceptance through the sole persistent session and authenticated IPC.

The only application/profile/files here belong to this fixture. No compositor,
input, portal or application process is assembled on the viewer side.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from threading import Thread
import time
import traceback

from gi.repository import GLib
from application_processes import identity
from control_probe import ControlClient
from session_probe import cleanup_helper, launch_records
from source_proof import record_sources


def main():
    root = Path(sys.argv[1]).resolve()
    evidence = Path(tempfile.mkdtemp(prefix='snap-session-', dir=root))
    runtime = Path(tempfile.mkdtemp(prefix='floe-session-', dir=f'/run/user/{os.getuid()}'))
    package_runtime = Path(f'/run/user/{os.getuid()}/snap.firefox')
    assert package_runtime.is_dir() and package_runtime.stat().st_uid == os.getuid()
    profile = Path(tempfile.mkdtemp(prefix='floe-session-test-', dir=Path.home() / 'snap/firefox/common'))
    downloads = profile / 'downloads'
    downloads.mkdir()
    (profile / 'user.js').write_text('user_pref("browser.download.folderList", 2);\n'
        'user_pref("browser.download.useDownloadDir", false);\n'
        'user_pref("browser.download.dir", ' + json.dumps(str(downloads)) + ');\n')
    receipts = []
    result = {'passed': False, 'evidence': str(evidence), 'runtime': str(runtime), 'profile': str(profile),
              'sources': record_sources(root), 'frames': []}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body = b'''<!doctype html><meta charset="utf-8"><title>Floe persistent Snap input</title>
<style>body{font:20px sans-serif;margin:30px;background:#b8d9ed}textarea{width:85%;height:350px;font:24px sans-serif}</style>
<h1>Floe persistent Snap input</h1><textarea autofocus></textarea><p><button id="save">Save test text</button></p>
<div id="checkpoint" style="position:fixed;bottom:0;left:0;right:0;height:16px"></div><script>
const field=document.querySelector('textarea'); let sequence=0,pending=Promise.resolve();
const report=event=>{const body=JSON.stringify({sequence:++sequence,event,value:field.value});
 pending=pending.then(()=>fetch('/receipt',{method:'POST',body}));};
field.addEventListener('input',()=>{let hash=2166136261;
 for(const byte of new TextEncoder().encode(field.value))hash=Math.imul(hash^byte,16777619)>>>0;
 document.querySelector('#checkpoint').style.backgroundColor=`rgb(${hash>>>16&255},${hash>>>8&255},${hash&255})`;
 report('input');});
field.addEventListener('pointerdown',()=>report('pointerdown'));
document.querySelector('#save').addEventListener('click',()=>{report('save-click');
 const link=document.createElement('a');link.href=URL.createObjectURL(new Blob([field.value],{type:'text/plain;charset=utf-8'}));
 link.download='floe-confirmed-text.txt';link.click();});
report('loaded');</script>'''
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            count = int(self.headers.get('Content-Length', '0'))
            if self.path != '/receipt' or not 0 <= count <= 100000:
                self.send_error(400)
                return
            value = json.loads(self.rfile.read(count))
            receipts.append(value)
            with (evidence / 'browser-receipts.jsonl').open('a') as stream:
                stream.write(json.dumps(value, ensure_ascii=False) + '\n')
            self.send_response(204)
            self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token = secrets.token_hex(32)
    process = client = started = None
    log = (evidence / 'helper.log').open('w')
    def records():
        return launch_records(evidence)
    def wait(predicate, message, seconds=30):
        deadline = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() >= deadline or process.poll() is not None:
                raise RuntimeError(message)
            time.sleep(.01)
    def paint(stage, **options):
        frames = client.paint(stage, **options)
        result['frames'] += frames
        return {key: frames[-1][key] for key in ('connection', 'window', 'generation')}
    target = None
    def send(operation):
        return client.request('input', **target, operation=operation)
    def delivered(request):
        response = client.response(request)
        assert 'error' not in response, response
    def click(x, y):
        for operation in ({'kind': 'move', 'x': x, 'y': y},
                          {'kind': 'button', 'button': 0, 'pressed': True, 'x': x, 'y': y},
                          {'kind': 'button', 'button': 0, 'pressed': False, 'x': x, 'y': y}):
            delivered(send(operation))
    def key(code):
        delivered(send({'kind': 'key', 'code': code, 'pressed': True}))
        delivered(send({'kind': 'key', 'code': code, 'pressed': False}))
    try:
        original = Path('/var/lib/snapd/desktop/applications/firefox_firefox.desktop')
        entry = GLib.KeyFile.new()
        entry.load_from_file(str(original), GLib.KeyFileFlags.NONE)
        assert entry.get_string('Desktop Entry', 'Exec') == '/snap/bin/firefox %u'
        entry.set_string('Desktop Entry', 'Exec', f'/snap/bin/firefox -no-remote -profile "{profile}" http://127.0.0.1:{server.server_port}/')
        entry.set_boolean('Desktop Entry', 'DBusActivatable', False)
        (evidence / 'fixture.desktop').write_text(entry.to_data()[0])
        result['desktop_sha256'] = hashlib.sha256(original.read_bytes()).hexdigest()
        (evidence / 'fixture-environment.json').write_text(json.dumps({
            'environment': {'XDG_RUNTIME_DIR': str(package_runtime), 'WAYLAND_DISPLAY': 'wayland-' + secrets.token_hex(8),
                'DBUS_SESSION_BUS_ADDRESS': 'unix:abstract=/tmp/dbus-floe-' + secrets.token_hex(12), 'GTK_USE_PORTAL': '1'},
            'unset': ['GDK_BACKEND', 'QT_QPA_PLATFORM', 'MOZ_ENABLE_WAYLAND'],
            'host_bus': os.environ['DBUS_SESSION_BUS_ADDRESS']}))
        process = subprocess.Popen([sys.executable, str(root / 'session_probe.py'), str(root), 'helper',
            str(runtime), str(evidence), 'package'], stdin=subprocess.PIPE, stdout=log, stderr=log, start_new_session=True)
        started = identity(process.pid)[1]
        result['helper'] = {'pid': process.pid, 'start_ticks': started}
        process.stdin.write((token + '\n').encode())
        process.stdin.close()
        wait(lambda: any(x.get('phase') == 'sharing_ready' or x.get('state') == 'failed' for x in records()),
             'Persistent Snap helper did not prepare', 45)
        assert not any(x.get('state') == 'failed' for x in records()), records()
        plan = json.loads((evidence / 'application-plan.json').read_text())
        result['package'], result['required_services'] = plan['observation']['package'], plan['observation']['services']
        assert result['package']['kind'] == 'snap' and result['package']['confinement'] == 'strict'
        client = ControlClient(evidence / 'viewer', runtime / 'control.sock', runtime.name, token)
        client.reconnect()
        wait(lambda: any(x['event'] == 'loaded' for x in receipts), 'Real Snap Firefox page did not load', 45)
        launched = json.loads((evidence / 'application.json').read_text())
        assert launched['state'] == 'running' and len(launched['launcher_pids']) == 1
        pid = launched['launcher_pids'][0]
        cgroup = Path(f'/proc/{pid}/cgroup').read_text().strip()
        assert re.search(r'/snap\.firefox\.firefox-[a-f0-9-]+\.scope$', cgroup)
        result['scope'] = {'pid': pid, 'start_ticks': identity(pid)[1], 'cgroup': cgroup}
        target = paint('loaded', marker=(184, 217, 237))
        click(300, 280)
        wait(lambda: any(x['event'] == 'pointerdown' for x in receipts), 'Actual field click was not received')
        expected, pending = '', []
        for value in ['中文日本語한글🙂👩🏽‍💻e\u0301𠮷'] * 64 + ['界🙂' * 2000]:
            pending += [send({'kind': 'text', 'text': value}), send({'kind': 'key', 'code': 28, 'pressed': True}),
                        send({'kind': 'key', 'code': 28, 'pressed': False})]
            expected += value + '\n'
        for request in pending:
            delivered(request)
        wait(lambda: receipts[-1]['value'] == expected, 'Actual Snap document differs after ordered Unicode/Enter')
        checksum = 2166136261
        for byte in expected.encode():
            checksum = ((checksum ^ byte) * 16777619) & 0xffffffff
        marker = (checksum >> 16 & 255, checksum >> 8 & 255, checksum & 255)
        target = paint('input-received', marker=marker)
        result['input_sha256'], result['unicode_transactions'] = hashlib.sha256(expected.encode()).hexdigest(), 65
        old_generation = client.generation
        client.close()
        assert process.poll() is None
        client.reconnect()
        assert client.generation > old_generation
        target = paint('reattached', marker=marker)
        result['detach_retains_application'] = True
        click(105, 585)
        wait(lambda: any(x['event'] == 'save-click' for x in receipts), 'Save was not clicked')
        previous = target['window']
        def dialog():
            state = client.response(client.request('status'))['result']
            return state['window'] is not None and state['window'] != previous
        wait(dialog, 'Official remote file dialog did not appear')
        target = paint('save-dialog', absent=(marker,))
        assert target['window'] != previous
        key(28)
        saved = downloads / 'floe-confirmed-text.txt'
        wait(lambda: saved.exists() and saved.read_bytes() == expected.encode(), 'Actual save did not produce exact bytes')
        shutil.copyfile(saved, evidence / 'saved-text.txt')
        result['saved_sha256'] = hashlib.sha256(saved.read_bytes()).hexdigest()
        target = paint('saved', marker=marker)
        delivered(client.request('close_window', window=target['window']))
        assert process.wait(timeout=20) == 0
        exited = next(x for x in records() if x.get('state') == 'exited')
        assert exited['exit_code'] == 0 and not exited['termination_requested']
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
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        log.close()
        (evidence / 'launch.json').unlink(missing_ok=True)
        (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))
    if not result['passed']:
        sys.exit(1)


if __name__ == '__main__':
    main()
