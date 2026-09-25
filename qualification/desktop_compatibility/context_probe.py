"""Actual Qt documents through the production authenticated text/input boundary."""
import json
import os
from threading import Event

from application_processes import ProcessTree, identity
from desktop_context import NativeContexts, NativeContextService
from desktop_native import NativeDesktop


class ToolkitDriver:
    """Application fixture driver; the context protocol and ownership are real.

    This fixture waits for each toolkit receipt. The separate authenticated
    qualification below exercises scheduling, batching and the decoded-frame gate.
    """
    def __init__(self, connection, channel, runtime, destination, record):
        self.record = record
        self.tree = ProcessTree(os.getpid(), identity(os.getpid())[1])
        self.native = NativeDesktop(lambda value: channel.sendall(value.encode()), None)
        self.contexts = NativeContexts(self.native, self.tree, runtime)
        self.service = NativeContextService(connection, self.contexts, destination)
        self.sequence = 0

    @property
    def clients(self):
        return self.contexts.clients

    def observe(self, line):
        from gi.repository import GLib
        def apply():
            self.native.observe(line)
            if line == 'native-version 1':
                self.native.bind(1)
            return False
        GLib.idle_add(apply)

    def commit(self, text):
        from gi.repository import GLib
        finished, result = Event(), []
        def completed(error):
            result.append(error)
            finished.set()
        def submit():
            token = self.contexts.context_for(self.native.target)
            if token is None:
                completed('INPUT_CONTEXT_UNAVAILABLE')
            else:
                self.sequence += 1
                if self.sequence == 1:
                    package = token.client.package
                    self.record({'native_context': {'bus_pid': token.client.process.pid,
                        'native_pid': token.surface_peer.process.pid, 'surface': token.focus.surface,
                        'instance': package.instance if package else None,
                        'revision': package.revision if package else None,
                        'matched': token.client.matches(token.surface_peer)}})
                self.contexts.commit(token, text, completed)
            return False
        GLib.idle_add(submit)
        if not finished.wait(5) or result != [None]:
            raise RuntimeError('Native context did not acknowledge: ' + str(result))

    def close(self):
        self.service.close()
        self.tree.close()


def qualify(root, evidence, environment, control, wire, start, wait, paint):
    from gi.repository import Gio
    tree = ProcessTree(os.getpid(), identity(os.getpid())[1])
    connection = Gio.DBusConnection.new_for_address_sync(environment['DBUS_SESSION_BUS_ADDRESS'],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    connection.set_exit_on_close(False)
    def serve():
        contexts = NativeContexts(wire.native, tree, environment['XDG_RUNTIME_DIR'])
        wire.native.contexts = contexts
        return NativeContextService(connection, contexts)
    service = wire.invoke(serve)
    try:
        receipt = evidence / 'qt-context.json'
        previous = wire.native.target
        app = start(['python3', str(root / 'input_fixture.py'), 'qt6', str(receipt)],
            {**environment, 'QT_QPA_PLATFORM': 'wayland', 'QT_IM_MODULE': 'floe-client-wayland',
             'QT_PLUGIN_PATH': str(root / 'qt-native')}, 'qt-context')
        wait(lambda: receipt.exists() and wire.native.target is not None and wire.native.target is not previous and
             bool(wire.native.contexts.clients), 'No ready native Qt context')
        frame = paint('qt-context')
        window = frame['window']
        expected = ['', '']
        completed = 0
        for batch in range(8):
            responses = []
            for index in range(batch * 8, batch * 8 + 8):
                field = index % 2
                command = f'motion {250 if field == 0 else 750} 180\nbutton 272 1\nbutton 272 0\n'
                command += 'key 29 1\nkey 107 1\nkey 107 0\nkey 29 0\n'
                responses.append((control.send(window, command.encode()), 'submitted'))
                text = '同中文日本語한글🙂👩🏽‍💻e\u0301𠮷'
                responses.append((control.request('input', connection=control.generation, window=window,
                    generation=wire.native.generation, operation={'kind': 'text', 'text': text}), 'completed'))
                responses.append((control.send(window, b'key 28 1\nkey 28 0\n'), 'submitted'))
                expected[field] += text + '\n'
            for request, status in responses:
                response = control.response(request)
                assert response.get('result') == status, response
                completed += status == 'completed'
        long_text = '界🙂' * 2000
        request = control.request('input', connection=control.generation, window=window,
            generation=wire.native.generation, operation={'kind': 'text', 'text': long_text})
        assert control.response(request).get('result') == 'completed'
        expected[1] += long_text
        wait(lambda: json.loads(receipt.read_text()) == expected,
             'Native context sequence crossed actual Qt fields or lost Unicode')
        paint('qt-context-committed')
        control.send(window, b'close\n')
        app.wait(timeout=10)
        assert app.returncode == 0
        return {'toolkit': 'qt6', 'commits': completed + 1, 'long_bytes': len(long_text.encode()),
                'actual': json.loads(receipt.read_text()), 'pid': app.pid,
                'surface_binding': 'native surface ID and live process identity',
                'completion': 'toolkit event loop, shared scheduler and authenticated attachment'}
    finally:
        def stop():
            service.close()
            wire.native.contexts = None
        wire.invoke(stop)
        connection.close_sync(None)
        tree.close()
