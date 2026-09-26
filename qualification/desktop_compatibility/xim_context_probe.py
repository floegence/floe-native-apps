"""Qualify the terminal-only XIM adapter with an actual xterm receipt."""
import json
import os
from unittest.mock import patch

from application_processes import ProcessTree, identity
from desktop_context import NativeContexts
from desktop_x11 import X11Resources
from desktop_xim import NativeXIMContexts


def qualify(root, evidence, environment, control, wire, start, wait, paint, display):
    """Exercise the production XIM boundary for applications that expose XIM.

    Chromium is intentionally not part of this probe.  The supported Chromium
    path is the private GTK3 module launched by the Xpra qualification in
    ``qualification/input.py``; Chromium does not create an XIM context merely
    because XMODIFIERS is set.
    """
    from Xlib.display import Display

    tree = ProcessTree(os.getpid(), identity(os.getpid())[1])
    local = {
        **environment,
        'DISPLAY': display,
        'XMODIFIERS': '@im=floe-client',
        'LC_ALL': 'C.UTF-8',
    }

    def serve():
        with patch.dict(os.environ, local):
            resources = X11Resources(Display(display))
            contexts = NativeContexts(wire.native, tree, environment['XDG_RUNTIME_DIR'])
            contexts.x11 = resources
            bridge = NativeXIMContexts(contexts)
        wire.native.contexts = contexts
        return contexts, resources, bridge

    contexts, resources, bridge = wire.invoke(serve)
    try:
        receipt = evidence / 'terminal-context.json'
        previous = wire.native.target
        command = [
            'xterm', '-u8', '-bg', '#3b759f', '-fa', 'monospace', '-fs', '12',
            '-xrm', 'XTerm*inputMethod:floe-client',
            '-e', 'python3', str(root / 'input_fixture.py'), 'terminal', str(receipt),
        ]
        app = start(command, local, 'terminal-context')
        wait(
            lambda: receipt.with_suffix('.ready').exists()
            and wire.native.target is not None
            and wire.native.target is not previous
            and bridge.focused is not None,
            'No actual XIM input context',
        )
        frame = paint('terminal-context', marker=(59, 117, 159))
        window = frame['window']
        result = exercise_terminal(control, window, receipt, wait)
        paint('terminal-context-committed')
        control.send(window, b'close\n')
        app.wait(timeout=10)
        return {
            **result,
            'toolkit': 'terminal',
            'protocol': 'x11',
            'pid': app.pid,
            'completion': 'native XIM marker, protocol sync, native release and exact application bytes',
        }
    finally:
        def stop():
            bridge.close()
            contexts.close()
            resources.close()
            wire.native.contexts = None

        wire.invoke(stop)
        tree.close()


def exercise_terminal(control, window, receipt, wait):
    expected = ''
    for _ in range(8):
        responses = []
        for _ in range(8):
            text = '同中文日本語한글🙂👩🏽‍💻e\u0301𠮷'
            responses.append((
                control.request(
                    'input',
                    connection=control.generation,
                    window=window,
                    generation=control.native.generation,
                    operation={'kind': 'text', 'text': text},
                ),
                'completed',
            ))
            responses.append((
                control.send(window, b'key 30 1\nkey 30 0\nkey 28 1\nkey 28 0\n'),
                'submitted',
            ))
            expected += text + 'a\r'
        for request, status in responses:
            response = control.response(request)
            assert response.get('result') == status, response

    long_text = '界🙂' * 2000
    response = control.response(control.request(
        'input',
        connection=control.generation,
        window=window,
        generation=control.native.generation,
        operation={'kind': 'text', 'text': long_text},
    ))
    assert response.get('result') == 'completed', response
    expected += long_text
    wait(
        lambda: receipt.exists() and json.loads(receipt.read_text()) == expected,
        'Actual terminal lost Unicode or reordered ordinary keys',
    )
    return {
        'commits': 65,
        'long_bytes': len(long_text.encode()),
        'actual': json.loads(receipt.read_text()),
    }
