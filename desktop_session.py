"""One persistent event-loop owner for prepared native service processes.

This internal launch boundary accepts already verified resources and an immutable
supervisor command. Viewers only attach to DesktopHelper; they cannot spawn,
restart or terminate these processes. The existing application supervisor remains
the only owner of application descendants and application exit receipts.
"""
import json
import os
import re
import stat
from pathlib import Path
import secrets
import signal
import socket

from gi.repository import Gio, GLib
from application_processes import ProcessTree, identity
from desktop_control import GLibLoop
from desktop_graphics import private_directory
from desktop_helper import DesktopHelper
from desktop_portals import DesktopPortals, bus_configuration


def read_application_result(path, process_code):
    """Read only the private supervisor's completed, bounded process receipt.

    Missing or stale running receipts are helper failures, never proof of normal
    application exit. No application output or arbitrary fields become diagnostics.
    """
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536):
            raise ValueError('Private application receipt is unavailable')
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError('Private application receipt is oversized')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('Application receipt is invalid')
    if value.get('state') == 'failed':
        if (set(value) != {'state', 'phase', 'error_code'} or process_code != 1 or
                not isinstance(value['phase'], str) or not re.fullmatch(r'[a-z_]{1,64}', value['phase']) or
                not isinstance(value['error_code'], str) or
                not re.fullmatch(r'APPLICATION_[A-Z_]{1,96}', value['error_code'])):
            raise ValueError('Application failure receipt is invalid')
    elif value.get('state') == 'exited':
        if (set(value) != {'state', 'phase', 'exit_code', 'launchers', 'termination_requested'} or
                value['phase'] != 'process_exit' or type(value['exit_code']) is not int or
                not -64 <= value['exit_code'] <= 255 or type(value['termination_requested']) is not bool or
                not isinstance(value['launchers'], list) or not 1 <= len(value['launchers']) <= 128):
            raise ValueError('Application exit receipt is invalid')
        code = value['exit_code']
        if process_code != (128 - code if code < 0 else code):
            raise ValueError('Supervisor process and receipt differ')
        pids = set()
        for launcher in value['launchers']:
            if (not isinstance(launcher, dict) or set(launcher) != {'pid', 'exit_code'} or
                    type(launcher['pid']) is not int or not 0 < launcher['pid'] <= 2147483647 or
                    launcher['pid'] in pids or type(launcher['exit_code']) is not int or
                    not -64 <= launcher['exit_code'] <= 255):
                raise ValueError('Application launcher receipt is invalid')
            pids.add(launcher['pid'])
        if next((item['exit_code'] for item in value['launchers'] if item['exit_code']), 0) != code:
            raise ValueError('Application launcher status differs')
    else:
        raise ValueError('Supervisor did not finish its application receipt')
    return value


class DesktopSession:
    def __init__(self, directory, instance, token, services, graphics, *, ibus_command,
                 application_command, application_environment, application_receipt, completed, record):
        self.directory = private_directory(directory)
        self.instance, self.token = instance, token
        self.services, self.graphics = services, graphics
        self.ibus_command = tuple(ibus_command)
        self.application_command = tuple(application_command)
        self.application_environment = dict(application_environment)
        self.application_receipt = Path(application_receipt)
        if (self.application_receipt.parent != self.directory or os.path.lexists(self.application_receipt)):
            raise ValueError('New instance-private application receipt is required')
        self.completed, self.record = completed, record
        self.loop = GLibLoop()
        self.processes, self.watches, self.peers = {}, {}, []
        self.connection = self.helper = self.capture = None
        self.application = None
        self.closed, self.failed, self.ready = False, False, False
        self.timeout = self.kill_timeout = None
        self.stage = 'created'
        self.ibus_address = 'unix:abstract=/tmp/ibus/dbus-floe-' + secrets.token_hex(16)
        self.bus_address = graphics.application_environment['DBUS_SESSION_BUS_ADDRESS']
        self.bus_config = self.directory / 'desktop-bus.conf'
        with self.bus_config.open('x') as file:
            file.write(bus_configuration(self.bus_address))
        self.bus_config.chmod(0o600)
        self.tree = ProcessTree(os.getpid(), identity(os.getpid())[1])

    def transition(self, stage):
        self.stage = stage
        self.record({'state': 'starting', 'phase': stage})

    def guard(self, callback):
        def call(*args):
            if self.closed or self.failed:
                return
            try:
                return callback(*args)
            except Exception:
                # Native errors are classified at the stage boundary. Neither
                # exception text nor application output belongs in diagnostics.
                self.fail('DESKTOP_PREPARATION_FAILED')
        return call

    def start(self):
        if self.stage != 'created' or self.closed:
            raise ValueError('Native session already started')
        self.guard(self.start_bus)()

    def start_bus(self):
        self.transition('private_bus')
        self.timeout = self.loop.later(30000, lambda: self.fail('DESKTOP_PREPARATION_TIMEOUT'))
        process = self.spawn('bus', self.services.command('usr/bin/dbus-daemon') + [
            '--nofork', '--config-file=' + str(self.bus_config), '--print-address=1'],
            self.services.environment({'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}), output=True)
        stream = process.get_stdout_pipe()
        # The configured address is known, but the actual daemon's newline is
        # readiness. Do not race a connection against socket creation or poll.
        chunks = bytearray()
        def received(source, result, _data):
            data = source.read_bytes_finish(result).get_data()
            if not data or len(chunks) + len(data) > 1024:
                raise ValueError('Private bus startup reply is unavailable')
            chunks.extend(data)
            if b'\n' not in chunks:
                source.read_bytes_async(1024 - len(chunks), GLib.PRIORITY_DEFAULT, None, self.guard(received), None)
                return
            address = chunks.decode('ascii').strip()
            if address.split(',guid=')[0] != self.bus_address:
                raise ValueError('Private bus address differs')
            source.close(None)
            Gio.DBusConnection.new_for_address(address,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None, None, self.bus_connected, None)
        stream.read_bytes_async(1024, GLib.PRIORITY_DEFAULT, None, self.guard(received), None)

    def spawn(self, name, command, environment, *, descriptors=(), output=False, input_pipe=False):
        if self.closed or name in self.processes or len(self.processes) >= 16:
            raise ValueError('Native process admission is unavailable')
        flags = Gio.SubprocessFlags.STDERR_SILENCE
        flags |= Gio.SubprocessFlags.STDOUT_PIPE if output else Gio.SubprocessFlags.STDOUT_SILENCE
        if input_pipe:
            flags |= Gio.SubprocessFlags.STDIN_PIPE
        launcher = Gio.SubprocessLauncher.new(flags)
        launcher.set_environ([key + '=' + value for key, value in environment.items()])
        for descriptor in descriptors:
            launcher.take_fd(os.dup(descriptor), descriptor)
        try:
            process = launcher.spawnv(list(command))
        finally:
            launcher.close()
        self.processes[name] = process
        pid = int(process.get_identifier())
        def exited(child, result, _data):
            child.wait_finish(result)
            code = child.get_exit_status() if child.get_if_exited() else -child.get_term_sig()
            if self.processes.get(name) is child:
                self.processes.pop(name)
                if not self.closed:
                    if name == 'application':
                        self.application_exited(code)
                        self.close()
                    else:
                        self.fail('DESKTOP_SERVICE_EXITED', service=name, exit_code=code)
            if self.closed and not self.processes:
                self.finish()
        process.wait_async(None, exited, None)
        try:
            started = identity(pid)[1]
        except FileNotFoundError:
            # A failed executable may already have been reaped by GSubprocess.
            # Its registered wait callback still owns the authoritative result.
            started = None
        self.record({'event': 'process', 'service': name, 'pid': pid, 'start_ticks': started})
        return process

    def application_exited(self, code):
        try:
            result = read_application_result(self.application_receipt, code)
            if (result['state'] == 'exited' and result['exit_code'] != 0 and
                    not result['termination_requested'] and self.helper.native.last_window == 0):
                result = {**result, 'state': 'failed', 'phase': 'application_start',
                          'error_code': 'APPLICATION_LAUNCHER_EXITED'}
            self.record(result)
        except (OSError, ValueError):
            self.record({'state': 'failed', 'phase': 'application',
                         'error_code': 'APPLICATION_RECEIPT_UNAVAILABLE', 'exit_code': code})

    def bus_connected(self, _source, result, _data):
        try:
            connection = Gio.DBusConnection.new_for_address_finish(result)
        except GLib.Error:
            self.fail('DESKTOP_BUS_UNAVAILABLE')
            return
        if self.closed or self.failed:
            connection.close_sync(None)
            return
        self.connection = connection
        self.guard(self.start_graphics)()

    def start_graphics(self):
        self.connection.set_exit_on_close(False)
        self.connection.connect('closed', lambda *_args: self.fail('DESKTOP_BUS_UNAVAILABLE'))
        self.transition('graphics')
        left, right = socket.socketpair()
        self.helper = DesktopHelper(left, self.loop)
        self.helper.channel.lost = self.graphics_lost
        def observed(line):
            self.helper.native.observe(line)
            if line == 'native-version 1':
                self.helper.native.query_display(self.guard(self.display_ready))
            elif line.startswith('capture-authorized '):
                self.capture_authorized(line)
        self.helper.channel.observed = self.guard(observed)
        try:
            self.spawn('compositor', self.graphics.command,
                {**self.graphics.environment, 'FLOE_PROBE_CONTROL_FD': str(right.fileno())}, descriptors=(right.fileno(),))
        finally:
            right.close()

    def display_ready(self, display):
        if display is None:
            raise ValueError('Combined native display is unavailable')
        authorized = self.graphics.authorize(display)
        self.application_environment.update(DISPLAY=display, XAUTHORITY=authorized['XAUTHORITY'])
        self.application_environment.update(GTK_IM_MODULE='ibus', IBUS_ENABLE_SYNC_MODE='1',
            IBUS_ADDRESS=self.ibus_address, XMODIFIERS='@im=floe-client')
        # This process owns one graphical instance. These are helper-local
        # native library connections, never mutations of the user's environment.
        os.environ.update(DISPLAY=display, XAUTHORITY=self.application_environment['XAUTHORITY'],
                          IBUS_ADDRESS=self.ibus_address, DBUS_SESSION_BUS_ADDRESS=self.bus_address)
        self.transition('capture')
        self.capture, child = socket.socketpair()
        self.capture_process = None
        try:
            command = self.services.command('usr/bin/python3') + ['-c',
                'import os,sys; os.read(0,1); os.execv(sys.argv[1],sys.argv[1:])', *self.graphics.capture_command]
            self.capture_process = self.spawn('capture', command,
                self.services.environment({**self.graphics.environment, 'FLOE_PROBE_FRAME_FD': str(child.fileno())}),
                descriptors=(child.fileno(),), input_pipe=True)
        finally:
            child.close()
        self.capture_pid = int(self.capture_process.get_identifier())
        self.helper.channel.send('capture-authorize ' + str(self.capture_pid) + '\n')

    def capture_authorized(self, line):
        if self.stage != 'capture' or line != 'capture-authorized ' + str(self.capture_pid):
            raise ValueError('Unexpected capture identity')
        stream = self.capture_process.get_stdin_pipe()
        stream.write_all(b'x', None)
        stream.close(None)
        self.transition('input')
        from Xlib.display import Display
        from desktop_x11 import X11Resources
        resources = X11Resources(Display(self.application_environment['DISPLAY']))
        self.helper.configure_input(self.connection, self.tree,
            self.application_environment['XDG_RUNTIME_DIR'], x11=resources)
        self.helper.enable_xim()
        components = self.directory / 'ibus-components'
        components.mkdir(mode=0o700)
        environment = {key: value for key, value in self.application_environment.items()
            if key in ('PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'DISPLAY', 'XAUTHORITY',
                       'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'IBUS_ADDRESS')}
        environment.update(IBUS_COMPONENT_PATH=str(components),
            XDG_CONFIG_HOME=str(self.directory / 'ibus-config'), XDG_CACHE_HOME=str(self.directory / 'ibus-cache'))
        process = self.spawn('ibus', [*self.ibus_command, '--single', '--panel=disable', '--config=disable',
            '--emoji-extension=disable', '--cache=none', '--address=' + self.ibus_address], environment)
        self.service('org.freedesktop.IBus', process, self.input_ready)

    def service(self, name, process, ready):
        expected = int(process.get_identifier())
        current = [None]
        def appeared(connection, _name, owner):
            values = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'GetConnectionCredentials', GLib.Variant('(s)', (owner,)),
                GLib.VariantType.new('(a{sv})'), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
            if values.get('ProcessID') != expected or values.get('UnixUserID') != os.getuid() or current[0] is not None:
                raise ValueError('Prepared service owner differs')
            peer = self.tree.admit(expected)
            self.peers.append(peer)
            current[0] = owner
            ready(peer)
        def vanished(_connection, _name):
            if current[0] is not None:
                self.fail('DESKTOP_SERVICE_UNAVAILABLE')
        watch = Gio.bus_watch_name_on_connection(self.connection, name, Gio.BusNameWatcherFlags.NONE,
            self.guard(appeared), vanished)
        self.watches[name] = watch

    def input_ready(self, daemon):
        def activated(error):
            if error:
                raise ValueError('Native input activation failed')
            self.start_portals()
        self.helper.enable_ibus(daemon, self.guard(activated))

    def start_portals(self):
        self.transition('portals')
        self.portals = DesktopPortals(self.services, self.bus_address,
            Path(self.application_environment['XDG_RUNTIME_DIR']) / self.application_environment['WAYLAND_DISPLAY'],
            self.application_environment)
        pending = iter(self.portals.commands)
        def next_service(_peer=None):
            item = next(pending, None)
            if item is None:
                self.launch_application()
                return
            name, bus_name, command = item
            process = self.spawn(name, command, self.portals.environment)
            self.service(bus_name, process, next_service)
        next_service()

    def launch_application(self):
        self.transition('application')
        # The one preparation deadline ends before application execution. There
        # is deliberately no deadline for the application's first native window.
        self.loop.cancel(self.timeout)
        self.timeout = None
        self.helper.listen(self.directory, self.instance, self.token, self.capture)
        self.capture = None
        self.application = self.spawn('application', self.application_command, self.application_environment)
        self.ready = True
        self.record({'state': 'prepared', 'phase': 'sharing_ready', 'helper_pid': os.getpid(),
                     'helper_start_ticks': identity(os.getpid())[1], 'socket': self.helper.server.path})

    def graphics_lost(self):
        self.helper.native.lost()
        self.fail('DESKTOP_GRAPHICS_UNAVAILABLE')

    def fail(self, code, **details):
        if self.closed or self.failed:
            return
        self.failed = True
        self.record({'state': 'failed', 'phase': self.stage, 'error_code': code, **details})
        if self.helper:
            self.helper.native.lost()
            self.helper.close_input()
        # Once an application exists, retain its processes and remaining
        # services. Failure is never implicit force quit or launch retry.
        if self.application is None:
            self.close()

    def close(self):
        if self.closed:
            return
        if 'application' in self.processes:
            raise ValueError('An application must exit before its graphical services are disposed')
        self.closed = True
        if self.timeout:
            self.loop.cancel(self.timeout)
            self.timeout = None
        for watch in self.watches.values():
            Gio.bus_unwatch_name(watch)
        self.watches.clear()
        if self.helper:
            self.helper.close()
        if self.capture:
            self.capture.close()
            self.capture = None
        if self.connection:
            self.connection.close_sync(None)
        for peer in self.peers:
            peer.close()
        self.peers.clear()
        self.tree.close()
        # Callers may dispose before launch or after the application supervisor
        # exits. Viewer detach never reaches this method.
        for process in tuple(self.processes.values()):
            process.send_signal(signal.SIGTERM)
        if self.processes:
            self.kill_timeout = self.loop.later(1000, self.kill_support)
        else:
            self.finish()

    def kill_support(self):
        self.kill_timeout = None
        for name, process in tuple(self.processes.items()):
            if name != 'application':
                process.force_exit()

    def finish(self):
        if self.kill_timeout:
            self.loop.cancel(self.kill_timeout)
            self.kill_timeout = None
        completed, self.completed = self.completed, None
        if completed:
            completed()
