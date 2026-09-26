"""Actual browser/terminal documents through XIM and authenticated ordered input."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch

from application_processes import ProcessTree, identity
from desktop_context import NativeContexts
from desktop_x11 import X11Resources
from desktop_xim import NativeXIMContexts


def qualify(root, evidence, environment, control, wire, start, wait, paint, display):
    from Xlib.display import Display
    tree = ProcessTree(os.getpid(), identity(os.getpid())[1])
    local = {**environment, 'DISPLAY': display, 'XMODIFIERS': '@im=floe-client', 'LC_ALL': 'C.UTF-8'}
    def serve():
        with patch.dict(os.environ, local):
            resources = X11Resources(Display(display))
            contexts = NativeContexts(wire.native, tree, environment['XDG_RUNTIME_DIR'])
            contexts.x11 = resources
            bridge = NativeXIMContexts(contexts)
        wire.native.contexts = contexts
        return contexts, resources, bridge
    contexts, resources, bridge = wire.invoke(serve)
    http = None
    toolkit = os.environ.get('FLOE_PROBE_CONTEXT_TOOLKIT', 'terminal')
    try:
        receipt = evidence / (toolkit + '-context.json')
        previous = wire.native.target
        if toolkit == 'chromium':
            http = browser_server(receipt)
            command = [os.environ['FLOE_TEST_CHROMIUM_BIN'], '--user-data-dir=' + str(evidence / 'browser-profile'),
                '--gtk-version=3', '--no-first-run', '--no-default-browser-check', '--ozone-platform=x11',
                '--disable-gpu', '--window-size=1000,700', '--app=http://127.0.0.1:' + str(http.server_port) + '/']
        else:
            command = ['xterm', '-u8', '-bg', '#3b759f', '-fa', 'monospace', '-fs', '12',
                '-xrm', 'XTerm*inputMethod:floe-client',
                '-e', 'python3', str(root / 'input_fixture.py'), 'terminal', str(receipt)]
        app = start(command, local, toolkit + '-context')
        wait(lambda: receipt.with_suffix('.ready').exists() and wire.native.target is not None and
             wire.native.target is not previous and (toolkit == 'chromium' or bridge.focused is not None),
             'No actual XIM input context')
        frame = paint(toolkit + '-context', marker=(59, 117, 159))
        window = frame['window']
        if toolkit == 'chromium':
            click = control.send(window, b'motion 220 240\nbutton 272 1\nbutton 272 0\n')
            assert control.response(click).get('result') == 'submitted'
            wait(lambda: bridge.focused is not None,
                 'Chromium did not establish an XIM context after click: clients=%r origins=%r supported=%r' %
                 (bridge.clients, bridge.origins, bridge.supported_contexts))
            from context_probe import exercise_fields
            # Readiness comes from the real page, native context and rendered
            # pixels. Never force DOM focus or set field contents from a driver.
            result = exercise_fields(control, window, receipt, wait)
        else:
            result = exercise_terminal(control, window, receipt, wait)
        paint(toolkit + '-context-committed')
        control.send(window, b'close\n')
        app.wait(timeout=10)
        return {**result, 'toolkit':toolkit, 'protocol':'x11', 'pid':app.pid,
                'completion':'native XIM marker, protocol sync, native release and exact application bytes'}
    finally:
        def stop():
            bridge.close()
            contexts.close()
            resources.close()
            wire.native.contexts = None
        wire.invoke(stop)
        tree.close()
        if http:
            http.shutdown()
            http.server_close()


def exercise_terminal(control, window, receipt, wait):
        expected = ''
        for batch in range(8):
            responses = []
            for _ in range(8):
                text = '同中文日本語한글🙂👩🏽‍💻e\u0301𠮷'
                responses.append((control.request('input', connection=control.generation, window=window,
                    generation=control.native.generation, operation={'kind': 'text', 'text': text}), 'completed'))
                responses.append((control.send(window, b'key 30 1\nkey 30 0\nkey 28 1\nkey 28 0\n'), 'submitted'))
                expected += text + 'a\r'
            for request, status in responses:
                response = control.response(request)
                assert response.get('result') == status, response
        long_text = '界🙂' * 2000
        response = control.response(control.request('input', connection=control.generation, window=window,
            generation=control.native.generation, operation={'kind': 'text', 'text': long_text}))
        assert response.get('result') == 'completed', response
        expected += long_text
        wait(lambda: receipt.exists() and json.loads(receipt.read_text()) == expected,
             'Actual terminal lost Unicode or reordered ordinary keys')
        return {'commits':65, 'long_bytes':len(long_text.encode()), 'actual':json.loads(receipt.read_text())}


def browser_server(receipt):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'''<!doctype html><style>
html,body{margin:0;background:#3b759f}body{display:flex;width:100vw;height:100vh}
textarea{box-sizing:border-box;resize:none;width:50%;height:100%;background:#3b759f}
</style><textarea autofocus></textarea><textarea></textarea><script>
const fields=[...document.querySelectorAll('textarea')];let pending=Promise.resolve();
function report(path,body=''){pending=pending.then(()=>fetch(path,{method:'POST',body}));}
window.addEventListener('load',()=>requestAnimationFrame(()=>report('/ready')));
for(const field of fields)field.addEventListener('input',()=>report('/received',JSON.stringify(fields.map(f=>f.value))));
</script>''')

        def do_POST(self):
            data = self.rfile.read(min(65536, int(self.headers.get('Content-Length', '0'))))
            if self.path == '/ready':
                receipt.with_suffix('.ready').touch()
            elif self.path == '/received':
                pending = receipt.with_suffix('.pending')
                pending.write_bytes(data)
                pending.replace(receipt)
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server
