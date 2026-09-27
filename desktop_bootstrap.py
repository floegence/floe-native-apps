"""Installed entrypoint for the sole private graphical session owner.

The host supplies verified component resources and an immutable launch plan in
an owner-only file. This module assembles DesktopSession; it never chooses a
fallback backend, polls for a window, or infers exit from viewer disconnection.
"""
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

from desktop_control import unique_object, reject_constant
from desktop_graphics import private_directory, owned_identity
from desktop_documents import METHODS, MAX_FILES


def absolute(value):
    return isinstance(value, str) and '\x00' not in value and Path(value).is_absolute()


def read_configuration(path):
    path = Path(path)
    private_directory(path.parent)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 1048576):
            raise ValueError('Private desktop launch configuration is unavailable')
        data = stream.read(1048577)
    if len(data) > 1048576:
        raise ValueError('Desktop launch configuration exceeds limit')
    value = json.loads(data, object_pairs_hook=unique_object, parse_constant=reject_constant)
    fields = {'version', 'instance', 'token', 'directory', 'runtime', 'environment',
              'resources', 'plan', 'host_bus', 'initial_documents'}
    if (type(value) is not dict or set(value) != fields or type(value['version']) is not int or
            value['version'] != 1 or not isinstance(value['instance'], str) or
            not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value['instance']) or
            not isinstance(value['token'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['token']) or
            not absolute(value['directory']) or not absolute(value['runtime']) or
            type(value['plan']) is not dict):
        raise ValueError('Invalid desktop launch configuration')
    private_directory(value['directory'])
    private_directory(value['runtime'])
    environment = value['environment']
    if (type(environment) is not dict or len(environment) > 512 or
            any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or
                not isinstance(item, str) or '\x00' in item for key, item in environment.items())):
        raise ValueError('Invalid application environment')
    resources = value['resources']
    required = {'component', 'shell', 'capture', 'library', 'xwayland', 'ibus_daemon', 'qt_plugins', 'gtk_modules'}
    if (type(resources) is not dict or not required <= resources.keys() or
            resources.keys() - required - {'ibus_portal'} or
            not all(absolute(item) for item in resources.values())):
        raise ValueError('Invalid verified desktop resources')
    documents = value['initial_documents']
    if (type(documents) is not list or len(documents) > MAX_FILES or not all(absolute(item) for item in documents) or
            value['host_bus'] is not None and not isinstance(value['host_bus'], str)):
        raise ValueError('Invalid desktop host service resources')
    return value


class LaunchReceipt:
    """Bounded native process/transition receipts, without application content."""
    def __init__(self, directory, instance, helper):
        self.path = private_directory(directory) / 'desktop-status.json'
        self.value = {'version': 1, 'instance': instance,
            'helper_pid': helper[0], 'helper_start_ticks': helper[1], 'processes': [], 'transitions': [],
            'service_events': []}
        self.identity = None
        self.publish()

    def record(self, event):
        if 'event' in event:
            if event['event'] == 'graphics':
                description = event.get('description')
                expected = {'version', 'derived_weston_sha256', 'shell_sha256', 'capture_sha256',
                            'xwayland_module_sha256', 'original_xwayland_sha256', 'prepared_xwayland_sha256'}
                if (set(event) != {'event', 'description'} or type(description) is not dict or
                        set(description) != expected or description['version'] != 'weston 14.0.2' or
                        any(not re.fullmatch('[0-9a-f]{64}', description[key]) for key in expected - {'version'}) or
                        'graphics' in self.value):
                    raise ValueError('Invalid native graphics receipt')
                self.value['graphics'] = description
                self.publish()
                return
            if event['event'] in ('document-request', 'document-result'):
                key = 'role' if event['event'] == 'document-request' else 'result'
                values = ('launcher', 'portal') if key == 'role' else ('completed', 'failed')
                if (set(event) != {'event', 'method', key} or event['method'] not in METHODS or event[key] not in values):
                    raise ValueError('Invalid native document service receipt')
                # Retain each content-free observation once. Repeated file
                # actions must not grow a persistent per-application history.
                if event not in self.value['service_events']:
                    self.value['service_events'].append(event)
                    self.publish()
                return
            if (event['event'] != 'process' or set(event) != {'event', 'service', 'pid', 'start_ticks'} or
                    not re.fullmatch(r'[a-z_-]{1,64}', event['service']) or type(event['pid']) is not int or
                    event['pid'] <= 0 or event['start_ticks'] is not None and
                    (type(event['start_ticks']) is not int or event['start_ticks'] <= 0) or
                    len(self.value['processes']) >= 16):
                raise ValueError('Invalid native process receipt')
            destination = self.value['processes']
        else:
            allowed = {'state', 'phase', 'error_code', 'helper_pid', 'helper_start_ticks',
                       'socket', 'service', 'exit_code', 'launchers', 'termination_requested'}
            if (set(event) - allowed or event.get('state') not in ('starting', 'prepared', 'failed', 'exited') or
                    not isinstance(event.get('phase'), str) or not re.fullmatch(r'[a-z_]{1,64}', event['phase']) or
                    'error_code' in event and not re.fullmatch(r'[A-Z_]{1,128}', event['error_code']) or
                    len(self.value['transitions']) >= 32):
                raise ValueError('Invalid native transition receipt')
            destination = self.value['transitions']
        destination.append(event)

        self.publish()

    def publish(self):
        if self.identity is not None and owned_identity(self.path) != self.identity:
            raise ValueError('Native launch receipt was replaced')
        data = json.dumps(self.value, ensure_ascii=True, allow_nan=False).encode()
        if len(data) > 65536:
            raise ValueError('Native launch receipt exceeds limit')
        descriptor, temporary = tempfile.mkstemp(prefix='.desktop-status-', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
            if self.identity is None:
                os.link(temporary, self.path)
            else:
                os.replace(temporary, self.path)
            self.identity = owned_identity(self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def assemble(value, record, completed):
    from desktop_services import DesktopServices
    from desktop_graphics import DesktopGraphics
    from desktop_session import DesktopSession
    from desktop_package import DesktopPackageResources
    from launch_plan import revalidate
    resources = value['resources']
    environment = dict(value['environment'])
    plan = revalidate(value['plan'], environment, [value['plan']['backend']])
    if plan['backend']['id'] != 'wayland':
        raise ValueError('Combined desktop helper requires its planned backend')
    services = DesktopServices(resources['component'], value['directory'])
    package = DesktopPackageResources(value['directory'], value['runtime'], plan['observation']['package'],
                                      environment, resources['qt_plugins'], resources['gtk_modules'])
    graphics = None
    def finished():
        graphics.close()
        package.close()
        completed()
    try:
        graphics = DesktopGraphics(services.component, value['directory'], package.environment,
            **{name: resources[name] for name in ('shell', 'capture', 'library', 'xwayland')},
            authentication=package.authentication)
        record({'event': 'graphics', 'description': graphics.description})
        session = DesktopSession(value['directory'], value['runtime'], value['instance'], value['token'],
            services, graphics, ibus_command=[resources['ibus_daemon']], plan=plan,
            application_launcher=[*services.command('usr/bin/python3'), str(Path(__file__).with_name('application.py'))],
            application_environment=package.environment, completed=finished, record=record, host_bus=value['host_bus'],
            ibus_portal_command=[resources['ibus_portal']] if 'ibus_portal' in resources else (),
            initial_documents=value['initial_documents'])
    except BaseException:
        if graphics:
            graphics.close()
        package.close()
        raise
    return session


def main():
    from application_processes import identity
    receipt = None
    try:
        if len(sys.argv) != 2:
            raise ValueError('One private launch configuration is required')
        value = read_configuration(sys.argv[1])
        receipt = LaunchReceipt(value['directory'], value['instance'], (os.getpid(), identity(os.getpid())[1]))
        receipt.record({'state': 'starting', 'phase': 'resources'})
        from gi.repository import GLib
        loop, finished = GLib.MainLoop(), []
        def completed():
            finished.append(True)
            loop.quit()
        session = assemble(value, receipt.record, completed)
        session.start()
        if not finished:
            loop.run()
        return 1 if any(event['state'] == 'failed' for event in receipt.value['transitions']) else 0
    except Exception:
        # Neither configuration contents, native exceptions nor child output
        # belong in production diagnostics. Preparation cannot trigger a retry.
        if receipt:
            try:
                receipt.record({'state': 'failed', 'phase': 'resources', 'error_code': 'DESKTOP_PREPARATION_FAILED'})
            except (OSError, ValueError):
                pass  # A replaced/unwritable receipt is never overwritten or logged.
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
