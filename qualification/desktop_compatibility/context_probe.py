"""Actual toolkit documents through the production authenticated input boundary."""
import json
import os
import secrets
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
        self.connection, self.ibus = connection, None
        self.sequence = 0

    @property
    def clients(self):
        return self.contexts.clients

    def enable_ibus(self, daemon_pid, portal_pid=None):
        from gi.repository import GLib
        from desktop_ibus_service import NativeIBusService
        finished, result = Event(), []
        def completed(error):
            result.append(error)
            finished.set()
        def start():
            try:
                daemon = self.tree.admit(daemon_pid)
                portal = self.tree.admit(portal_pid) if portal_pid else None
                self.ibus = NativeIBusService(self.contexts, self.connection, daemon, portal)
                self.ibus.activate(completed)
            except Exception as error:
                completed(type(error).__name__)
            return False
        GLib.idle_add(start)
        if not finished.wait(5) or result != [None]:
            raise RuntimeError('Native IBus activation did not complete: ' + str(result))

    @property
    def input_path(self):
        active = self.ibus.adapter.active if self.ibus else None
        return active[1] if active else None

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
        if self.ibus:
            self.ibus.close()
        self.service.close()
        self.tree.close()


def qualify(root, evidence, environment, control, wire, start, wait, paint, display=None):
    from gi.repository import Gio, GLib
    toolkit = os.environ.get('FLOE_PROBE_CONTEXT_TOOLKIT', 'qt6')
    if toolkit == 'terminal':
        from xim_context_probe import qualify as qualify_xim
        return qualify_xim(root, evidence, environment, control, wire, start, wait, paint, display)
    assert toolkit in ('qt5', 'qt6', 'gtk', 'gtk4')
    protocol = os.environ.get('FLOE_PROBE_CONTEXT_PROTOCOL', 'wayland')
    assert protocol in ('wayland', 'x11') and (protocol != 'x11' or display)
    gtk = toolkit in ('gtk', 'gtk4')
    tree = ProcessTree(os.getpid(), identity(os.getpid())[1])
    connection = Gio.DBusConnection.new_for_address_sync(environment['DBUS_SESSION_BUS_ADDRESS'],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    connection.set_exit_on_close(False)
    input_service, daemon, daemon_peer, resources = None, None, None, None
    app_environment = {**environment, 'QT_QPA_PLATFORM': 'xcb' if protocol == 'x11' else 'wayland', 'QT_IM_MODULE': 'floe-client-native',
                       'QT_PLUGIN_PATH': str(root / ('qt5-native' if toolkit == 'qt5' else 'qt-native')),
                       'FLOE_TEST_WINDOW_COLOR': '3b759f'}
    if protocol == 'x11':
        app_environment.update(DISPLAY=display, QT_XCB_NO_XI2='1', FLOE_TEST_NATIVE_KEYS='1')
    if gtk:
        os.environ['IBUS_ADDRESS'] = 'unix:abstract=/tmp/ibus/dbus-floe-' + secrets.token_hex(12)
        components = evidence / 'ibus-components'
        components.mkdir()
        app_environment.update(IBUS_ADDRESS=os.environ['IBUS_ADDRESS'], GTK_IM_MODULE='ibus',
            IBUS_ENABLE_SYNC_MODE='1', GDK_BACKEND=protocol)
        daemon = start([os.environ['FLOE_PROBE_IBUS_DAEMON'], '--single', '--panel=disable',
            '--config=disable', '--emoji-extension=disable', '--cache=none',
            '--address=' + os.environ['IBUS_ADDRESS']],
            {**app_environment, 'IBUS_COMPONENT_PATH': str(components),
             'XDG_CONFIG_HOME': str(evidence / 'ibus-config'), 'XDG_CACHE_HOME': str(evidence / 'ibus-cache')}, 'ibus')
        wait(lambda: connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'NameHasOwner', GLib.Variant('(s)', ('org.freedesktop.IBus',)),
            GLib.VariantType.new('(b)'), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0],
            'Private derived IBus is not ready')
        daemon_peer = tree.admit(daemon.pid)
    activated = []
    def serve():
        nonlocal input_service, resources
        contexts = NativeContexts(wire.native, tree, environment['XDG_RUNTIME_DIR'])
        original_take = contexts.take
        def observed_take(sender, code, surface):
            operation = contexts.pending
            if operation:
                token = operation['token']
                diagnostic = {'code': code, 'surface': surface, 'expected_surface': token.surface,
                    'native_valid': contexts.valid(token), 'target': vars(token.target),
                    'same_focus': token.focus is wire.native.focus,
                    'x11_focus': resources.focused_within(token.xid) if token.xid else None}
                if token.xid:
                    focus = resources.connection.get_input_focus().focus
                    diagnostic['x11_focus_resource'] = focus.id if hasattr(focus, 'id') else focus
                    tree = resources.connection.create_resource_object('window', token.xid).query_tree()
                    diagnostic['x11_parent'] = tree.parent.id
            else:
                diagnostic = {'code': code, 'pending': False}
            value = original_take(sender, code, surface)
            diagnostic['accepted'] = value is not None
            with (evidence / 'context-admission.jsonl').open('a') as file:
                file.write(json.dumps(diagnostic) + '\n')
            return value
        contexts.take = observed_take
        original_context = contexts.context_for
        def observed_context(target):
            token = original_context(target)
            if token is None:
                diagnostic = {'context_unavailable': True, 'target': vars(target) if target else None,
                    'focus': vars(contexts.native.focus) if contexts.native.focus else None}
                if target and resources:
                    xid = contexts.native.x11_windows.get(target.window)
                    diagnostic.update(xid=xid, pid=resources.owner_pid(xid) if xid else None)
                if contexts.ibus:
                    active = contexts.ibus.active
                    diagnostic['ibus_context'] = active[1] if active else None
                    if active:
                        try:
                            diagnostic['ibus_source'] = vars(contexts.ibus.sources.read(active[1]))
                        except ValueError as error:
                            diagnostic['ibus_source_error'] = str(error)
                with (evidence / 'context-admission.jsonl').open('a') as file:
                    file.write(json.dumps(diagnostic) + '\n')
            return token
        contexts.context_for = observed_context
        wire.native.contexts = contexts
        if protocol == 'x11':
            from Xlib.display import Display
            from unittest.mock import patch
            from desktop_x11 import X11Resources
            with patch.dict(os.environ, {'XAUTHORITY': environment.get('XAUTHORITY', '')}):
                resources = X11Resources(Display(display))
            contexts.x11 = resources
        if gtk:
            from desktop_ibus_service import NativeIBusService
            input_service = NativeIBusService(contexts, connection, daemon_peer)
            input_service.activate(activated.append)
        return NativeContextService(connection, contexts)
    service = wire.invoke(serve)
    try:
        if input_service:
            wait(lambda: bool(activated), 'Native IBus engine selection did not complete')
            assert activated == [None], activated
        receipt = evidence / (toolkit + '-context.json')
        previous = wire.native.target
        app = start(['python3', str(root / 'input_fixture.py'), toolkit, str(receipt)],
            app_environment, toolkit + '-context')
        def registered():
            if input_service is None:
                return bool(wire.native.contexts.clients)
            active = input_service.adapter.active
            if not active:
                return False
            try:
                return input_service.adapter.sources.read(active[1]).pid == app.pid
            except (OSError, ValueError):
                return False
        wait(lambda: receipt.exists() and wire.native.target is not None and wire.native.target is not previous and
             wire.invoke(registered),
             'No ready native toolkit context')
        frame = paint(toolkit + '-context', marker=(59, 117, 159))
        window = frame['window']
        result = exercise_fields(control, window, receipt, wait)
        paint(toolkit + '-context-committed')
        control.send(window, b'close\n')
        app.wait(timeout=10)
        assert app.returncode == 0
        return {**result, 'toolkit': toolkit, 'protocol': protocol, 'pid': app.pid,
                'surface_binding': ('native XWM resource binding and XRes' if protocol == 'x11' else 'native surface ID') + ' and live process identity',
                'completion': ('synchronous IBus context release' if input_service else 'toolkit event loop') +
                              ', shared scheduler and authenticated attachment'}
    finally:
        def stop():
            if input_service:
                input_service.close()
            service.close()
            if resources:
                resources.close()
            wire.native.contexts = None
        wire.invoke(stop)
        connection.close_sync(None)
        if daemon_peer:
            daemon_peer.close()
        tree.close()


def exercise_fields(control, window, receipt, wait):
    width = control.native.windows[window].width
    expected = ['', '']
    completed = 0
    for batch in range(8):
        responses = []
        for index in range(batch * 8, batch * 8 + 8):
            field = index % 2
            command = f'motion {width / 4 if field == 0 else width * 3 / 4} 120\nbutton 272 1\nbutton 272 0\n'
            command += 'key 29 1\nkey 107 1\nkey 107 0\nkey 29 0\n'
            responses.append((control.send(window, command.encode()), 'submitted'))
            text = '同中文日本語한글🙂👩🏽‍💻e\u0301𠮷'
            responses.append((control.request('input', connection=control.generation, window=window,
                generation=control.native.generation, operation={'kind': 'text', 'text': text}), 'completed'))
            responses.append((control.send(window, b'key 28 1\nkey 28 0\n'), 'submitted'))
            expected[field] += text + '\n'
        for request, status in responses:
            response = control.response(request)
            assert response.get('result') == status, response
            completed += status == 'completed'
    long_text = '界🙂' * 2000
    request = control.request('input', connection=control.generation, window=window,
        generation=control.native.generation, operation={'kind': 'text', 'text': long_text})
    assert control.response(request).get('result') == 'completed'
    expected[1] += long_text
    wait(lambda: json.loads(receipt.read_text()) == expected,
         'Native context sequence crossed actual toolkit fields or lost Unicode')
    return {'commits': completed + 1, 'long_bytes': len(long_text.encode()), 'actual': json.loads(receipt.read_text())}
