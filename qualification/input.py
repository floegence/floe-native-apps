"""Native application qualification with isolated ports, bus, display and state."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import secrets
import socket
import subprocess
import sys
import threading
import time

config = json.loads(Path(sys.argv[1]).read_text())
source = Path(__file__).resolve().parent
state = Path(config['state'])
native_env = dict(item.split('=', 1) for item in config['environment'] if '=' in item)
for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'DBUS_SESSION_BUS_ADDRESS',
            'IBUS_ADDRESS', 'IBUS_ADDRESS_FILE'):
    native_env.pop(key, None)
native_env.update(XPRA_PRIVATE_XAUTH='1', XPRA_SHARED_XAUTHORITY='0', XPRA_DEFAULT_CONF_DIRS='',
                  XPRA_SYSTEM_CONF_DIRS='', XPRA_USER_CONF_DIRS='')


def stop(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def qualify(kind):
    directory = state / kind
    directory.mkdir()
    receipt = directory / 'received.json'
    password = receipt.with_suffix('.password')
    password.write_text(secrets.token_urlsafe(32))
    password.chmod(0o600)
    environment = {**native_env, 'XDG_RUNTIME_DIR': str(directory), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
    bus = server = http = None
    log = (directory / 'server.log').open('w')
    try:
        bus = subprocess.Popen([config['dbus'], '--session', '--nofork', '--print-address=1'],
                               stdout=subprocess.PIPE, stderr=log, text=True, start_new_session=True)
        environment['DBUS_SESSION_BUS_ADDRESS'] = bus.stdout.readline().strip()
        if not environment['DBUS_SESSION_BUS_ADDRESS'].startswith('unix:'):
            raise RuntimeError('Private fixture bus did not start')
        # Restore only the support wrapper's overrides for these system fixtures.
        # User applications in production use WriteApplicationLauncher instead.
        application = ['env']
        for key in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONNOUSERSITE', 'GI_TYPELIB_PATH',
                    'GIO_MODULE_DIR', 'GIO_EXTRA_MODULES', 'GDK_PIXBUF_MODULE_FILE',
                    'GDK_PIXBUF_MODULEDIR', 'FONTCONFIG_PATH', 'FONTCONFIG_FILE',
                    'GIO_LAUNCH_DESKTOP'):
            application += ['-u', key]
        if kind == 'terminal':
            application += ['xterm', '-u8', '-geometry', '80x20', '-xrm', 'XTerm*inputMethod:floe-client',
                            '-e', '/usr/bin/python3', str(source / 'input_fixture.py'), kind, str(receipt)]
        elif kind == 'chromium':
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def do_GET(self):
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(b'''<!doctype html><style>html,body{background:#13579b;min-height:100vh}</style>
<textarea autofocus rows="10" style="width:45%"></textarea><textarea rows="10" style="width:45%"></textarea><script>
const editors=[...document.querySelectorAll('textarea')],t=editors[0];let pending=Promise.resolve();
// Allow Chromium's initial window/layout click protection to settle in this fixture.
window.addEventListener('load',()=>setTimeout(()=>fetch('/ready',{method:'POST'}),1000));
t.addEventListener('pointermove',()=>fetch('/hover',{method:'POST'}),{once:true});
t.addEventListener('pointerup',()=>requestAnimationFrame(()=>{if(document.hasFocus()&&document.activeElement===t)fetch('/clicked',{method:'POST'});}));
for(const editor of editors)editor.addEventListener('input',()=>{const body=JSON.stringify(editors.map(t=>t.value));pending=pending.then(()=>fetch('/received',{method:'POST',body}));});
</script>''')

                def do_POST(self):
                    data = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                    if self.path in ('/ready', '/hover', '/clicked'):
                        receipt.with_suffix('.' + self.path[1:]).touch()
                    elif self.path == '/received':
                        pending = receipt.with_suffix('.pending')
                        pending.write_bytes(data)
                        pending.replace(receipt)
                    else:
                        self.send_error(404)
                        return
                    self.send_response(200)
                    self.end_headers()

            http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            threading.Thread(target=http.serve_forever, daemon=True).start()
            chromium = os.environ.get('FLOE_TEST_CHROMIUM_BIN') or shutil.which('chromium') or shutil.which('google-chrome')
            if not chromium:
                raise RuntimeError('A native Chromium binary is required')
            application += [chromium, '--user-data-dir=' + str(directory / 'profile'), '--gtk-version=3',
                            '--no-first-run', '--no-default-browser-check', '--ozone-platform=x11',
                            '--app=http://127.0.0.1:' + str(http.server_port) + '/']
        else:
            application += ['/usr/bin/python3', str(source / 'input_fixture.py'), kind, str(receipt)]
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        listener.close()
        args = [config['python'], config['launcher'], 'start', '--daemon=no', '--systemd-run=no',
                '--attach=no', '--use-display=no', '--html=no', '--source=', '--source-start=',
                '--socket-dir=' + str(directory), '--socket-dirs=' + str(directory),
                '--sessions-dir=' + str(directory / 'sessions'), '--bind-ws=127.0.0.1:' + str(port),
                '--ws-auth=file:filename=' + str(password),
                '--exit-with-client=no', '--exit-with-children=yes', '--start-child=' + shlex.join(application),
                '--xvfb=' + shlex.quote(config['xvfb']) + ' -screen 0 1024x768x24 -nolisten tcp -noreset +extension Composite -auth $XAUTHORITY',
                '--audio=no', '--pulseaudio=no', '--speaker=disabled', '--microphone=disabled',
                '--notifications=no', '--mdns=no', '--webcam=no', '--printing=no', '--dbus-launch=',
                '--dbus=no', '--dbus-control=no', '--start-new-commands=no', '--opengl=no',
                '--start-env=DBUS_SESSION_BUS_ADDRESS=' + environment['DBUS_SESSION_BUS_ADDRESS'], *config['args']]
        server = subprocess.Popen(args, env=environment, stdout=log, stderr=log, start_new_session=True)
        (directory / 'process.json').write_text(json.dumps({'pid': server.pid, 'bus_pid': bus.pid, 'port': port,
                                                          'state': str(directory), 'capability': config['capability']}))
        deadline = time.monotonic() + 15
        while True:
            if server.poll() is not None:
                raise RuntimeError('Private Xpra server exited before readiness')
            try:
                with socket.create_connection(('127.0.0.1', port), .2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Private Xpra server did not become ready') from None
                time.sleep(.05)
        client = subprocess.run([config['python'], str(source / 'input_client.py'), str(receipt),
                                 'ws://127.0.0.1:' + str(port), kind], env=environment,
                                capture_output=True, text=True, timeout=40)
        if client.returncode or 'PASS ' + kind + ' ' not in client.stdout:
            raise RuntimeError('Application receipt assertion failed: ' + client.stdout + client.stderr)
        print(client.stdout.strip(), flush=True)
    except Exception:
        log.flush()
        # Fixture logs never contain real user input or documents.
        print((directory / 'server.log').read_text()[-12000:], file=sys.stderr)
        raise
    finally:
        stop(server)
        stop(bus)
        log.close()
        if http:
            http.shutdown()
            http.server_close()
        evidence = os.environ.get('FLOE_TEST_INPUT_EVIDENCE')
        if evidence:
            target = Path(evidence) / kind
            target.mkdir(parents=True, exist_ok=True)
            for name in ('process.json', 'received.json', 'received.unicode.json', 'received.json.png', 'received.ready', 'received.hover', 'received.clicked', 'server.log'):
                if (directory / name).exists():
                    shutil.copy2(directory / name, target / name)
                elif (target / name).exists():
                    (target / name).unlink()


for fixture in os.environ.get('FLOE_TEST_INPUT_FIXTURES', 'gtk,qt5,qt6,chromium,terminal').split(','):
    if fixture not in ('gtk', 'qt5', 'qt6', 'chromium', 'terminal'):
        raise RuntimeError('Unknown native fixture')
    qualify(fixture)
