"""Pre-execution application identity; GIO remains the only Exec interpreter.

The host supplies an admitted desktop entry and prepared backend capabilities.
This module never executes that application or modifies host package state.
Plans are private, short-lived inputs to the supervisor, not authorization tokens.
"""
import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import socket
import stat
import subprocess
import time


class Unavailable(ValueError):
    def __init__(self, code, stage='planning'):
        self.code, self.stage = code, stage
        super().__init__(code)


class StalePlan(Unavailable):
    def __init__(self):
        super().__init__('APPLICATION_PLAN_STALE', 'revalidation')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def file_identity(path, limit=4 << 30):
    """Bind symlink destination, inode generation metadata and actual file bytes."""
    try:
        if not Path(path).is_absolute():
            raise Unavailable('APPLICATION_TARGET_INVALID')
        resolved = str(Path(path).resolve(strict=True))
        descriptor = os.open(resolved, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(descriptor, 'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise Unavailable('APPLICATION_TARGET_INVALID')
            checksum = hashlib.sha256()
            count = 0
            while True:
                block = source.read(1 << 20)
                if not block:
                    break
                count += len(block)
                if count > limit:
                    raise Unavailable('APPLICATION_TARGET_INVALID')
                checksum.update(block)
            after = os.fstat(source.fileno())
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode')
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise StalePlan()
        if str(Path(path).resolve(strict=True)) != resolved or os.stat(path) != after:
            raise StalePlan()
        return {'path': path, 'resolved': resolved, 'sha256': checksum.hexdigest(),
                **{field[3:]: getattr(after, field) for field in fields}}
    except (OSError, ValueError) as error:
        if isinstance(error, Unavailable):
            raise
        raise Unavailable('APPLICATION_TARGET_UNAVAILABLE') from None


def restored_environment(environment):
    """Undo support-tool library overrides before resolving host executables."""
    result = dict(environment)
    for key, value in json.loads(result.pop('FLOE_NATIVE_APPLICATION_ENV', '{}')).items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    result.pop('FLOE_NATIVE_ROOT', None)
    return result


def resolution_environment(environment):
    # Session display, bus and input variables are assigned after planning. Bind
    # the variables used by executable and package deployment resolution instead.
    keys = ('PATH', 'HOME', 'XDG_DATA_HOME', 'XDG_DATA_DIRS', 'FLATPAK_USER_DIR',
            'FLATPAK_SYSTEM_DIR', 'FLATPAK_SYSTEM_CACHE_DIR', 'FLATPAK_CONFIG_DIR')
    return {key: environment.get(key) for key in keys}


def command_output(command, environment, missing=False):
    try:
        with subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            chunks, size, deadline = [], 0, time.monotonic() + 10
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise Unavailable('PACKAGE_PROBE_TIMEOUT')
                        block = os.read(process.stdout.fileno(), 65536)
                        if not block:
                            break
                        size += len(block)
                        if size > 1 << 20:
                            raise Unavailable('PACKAGE_METADATA_INVALID')
                        chunks.append(block)
                code = process.wait(timeout=max(0, deadline - time.monotonic()))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        if missing and code == 1:
            return None
        if code:
            raise Unavailable('PACKAGE_RUNTIME_UNAVAILABLE')
        return b''.join(chunks).decode('utf-8').strip()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise Unavailable('PACKAGE_RUNTIME_UNAVAILABLE') from None


class SnapConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/run/snapd.socket')


def snap_information(name):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', name):
        raise Unavailable('PACKAGE_METADATA_INVALID')
    connection = SnapConnection('localhost', timeout=5)
    try:
        connection.request('GET', '/v2/snaps/' + name)
        response = connection.getresponse()
        raw = response.read((1 << 20) + 1)
        if response.status != 200 or len(raw) > 1 << 20:
            raise Unavailable('PACKAGE_RUNTIME_UNAVAILABLE')
        document = json.loads(raw)
        package = document['result']
        if document['type'] != 'sync' or package['name'] != name or package['status'] != 'active':
            raise Unavailable('PACKAGE_METADATA_INVALID')
        if package['confinement'] not in ('strict', 'classic'):
            raise Unavailable('PACKAGE_CONFINEMENT_UNSUPPORTED')
        return package
    except (OSError, http.client.HTTPException):
        raise Unavailable('PACKAGE_RUNTIME_UNAVAILABLE') from None
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, Unavailable):
            raise
        raise Unavailable('PACKAGE_METADATA_INVALID') from None
    finally:
        connection.close()


def launch_tokens(app, environment, GLib):
    # GIO still owns field-code expansion and execution. Parsing here identifies
    # a reviewed launcher only; no field codes are expanded or rewritten.
    valid, tokens = GLib.shell_parse_argv(app.get_string('Exec') or '')
    if not valid or not tokens:
        raise Unavailable('APPLICATION_TARGET_INVALID')
    directory = app.get_string('Path') or os.getcwd()
    if not Path(directory).is_absolute():
        raise Unavailable('APPLICATION_TARGET_INVALID')

    def find(command):
        search = os.pathsep.join(os.path.abspath(os.path.join(directory, item))
                                 for item in environment.get('PATH', os.defpath).split(os.pathsep))
        if '/' in command and not Path(command).is_absolute():
            command = os.path.join(directory, command)
        return shutil.which(command, path=search)

    executable = find(tokens[0])
    if not executable:
        raise Unavailable('APPLICATION_TARGET_UNAVAILABLE')
    executable = os.path.abspath(executable)
    # Exported Snap desktop files may put a desktop hint before their launcher.
    # Other wrappers remain native executables; never inspect or execute shell code.
    if os.path.realpath(executable) == os.path.realpath('/usr/bin/env'):
        tokens = tokens[1:]
        while tokens and re.match(r'^[A-Za-z_][A-Za-z_0-9]*=', tokens[0]):
            key, value = tokens.pop(0).split('=', 1)
            environment = {**environment, key: value}
        if tokens and tokens[0] == '--':
            tokens.pop(0)
        if not tokens or tokens[0].startswith('-'):
            raise Unavailable('APPLICATION_LAUNCHER_UNSUPPORTED')
        executable = find(tokens[0])
        if not executable:
            raise Unavailable('APPLICATION_TARGET_UNAVAILABLE')
    return os.path.abspath(executable), tokens, environment


def native_package(target, environment):
    """Use installed package databases; extensions or app names prove nothing."""
    search = environment.get('PATH', os.defpath)
    dpkg = shutil.which('dpkg-query', path=search)
    if dpkg:
        for path in dict.fromkeys((target['path'], target['resolved'])):
            answer = command_output([dpkg, '--search', path], environment, missing=True)
            if answer is None:
                continue
            owners = {line[:-len(': ' + path)] for line in answer.splitlines() if line.endswith(': ' + path)}
            if len(owners) != 1:
                raise Unavailable('PACKAGE_METADATA_INVALID')
            owner = owners.pop()
            if not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?::[a-z0-9-]+)?', owner):
                raise Unavailable('PACKAGE_METADATA_INVALID')
            fields = command_output([dpkg, '--show', '--showformat=${binary:Package}\t${Version}\t${db:Status-Status}', owner], environment).split('\t')
            if len(fields) != 3 or fields[2] != 'installed':
                raise Unavailable('PACKAGE_METADATA_INVALID')
            return {'kind': 'deb', 'id': fields[0], 'revision': fields[1]}
    rpm = shutil.which('rpm', path=search)
    if rpm:
        answer = command_output([rpm, '-qf', '--qf', '%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}',
                                 target['resolved']], environment, missing=True)
        if answer is not None:
            fields = answer.split('\t')
            if len(fields) != 2 or not all(fields):
                raise Unavailable('PACKAGE_METADATA_INVALID')
            return {'kind': 'rpm', 'id': fields[0], 'revision': fields[1]}
    return {'kind': 'native', 'id': '', 'revision': ''}


def inspect_application(path, environment):
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    desktop = file_identity(path, 1 << 20)
    app = Gio.DesktopAppInfo.new_from_filename(path)
    if not app or app.get_is_hidden() or app.get_boolean('Terminal'):
        raise Unavailable('APPLICATION_TARGET_INVALID')
    executable, tokens, environment = launch_tokens(app, environment, GLib)
    target = file_identity(executable)
    package = {'kind': 'native', 'id': '', 'revision': ''}
    services = []
    snap_name = app.get_string('X-SnapInstanceName')
    flatpak_name = app.get_string('X-Flatpak')
    if snap_name and flatpak_name:
        raise Unavailable('PACKAGE_METADATA_INVALID')
    snap_command = None
    if executable.startswith('/snap/bin/'):
        snap_command = Path(executable).name
    elif os.path.realpath(executable) == os.path.realpath('/usr/bin/snap'):
        if len(tokens) < 3 or tokens[1] != 'run' or tokens[2].startswith('-'):
            raise Unavailable('PACKAGE_LAUNCHER_UNSUPPORTED')
        snap_command = tokens[2]
    if snap_command:
        inferred_name, _, inferred_app = snap_command.partition('.')
        if snap_name and snap_name != inferred_name:
            raise Unavailable('PACKAGE_LAUNCHER_MISMATCH')
        snap_name = inferred_name
        inferred_app = inferred_app or inferred_name.partition('_')[0]
    flatpak = shutil.which('flatpak', path=environment.get('PATH', os.defpath))
    is_flatpak = flatpak and os.path.realpath(executable) == os.path.realpath(flatpak)
    if snap_name and (flatpak_name or is_flatpak):
        raise Unavailable('PACKAGE_LAUNCHER_MISMATCH')
    if snap_name:
        metadata = snap_information(snap_name)
        app_name = app.get_string('X-SnapAppName') or (inferred_app if snap_command else None)
        member = next((item for item in metadata['apps'] if item['name'] == app_name), None)
        command = snap_name if app_name == snap_name.partition('_')[0] else snap_name + '.' + (app_name or '')
        if not member or snap_command != command:
            raise Unavailable('PACKAGE_LAUNCHER_MISMATCH')
        package = {'kind': 'snap', 'id': snap_name, 'application': app_name,
                   'revision': str(metadata['revision']), 'version': metadata['version'],
                   'confinement': metadata['confinement'],
                   'security_tag': 'snap.' + snap_name + '.' + app_name}
        services = ['user-systemd-scope']
        if metadata['confinement'] == 'strict':
            services += ['file-portal', 'document-portal']
    elif flatpak_name or is_flatpak:
        if not is_flatpak:
            raise Unavailable('PACKAGE_LAUNCHER_MISMATCH')
        if len(tokens) < 3 or tokens[1] != 'run':
            raise Unavailable('PACKAGE_LAUNCHER_MISMATCH')
        qualifiers, seen = [], set()
        index = 2
        while index < len(tokens) and tokens[index].startswith('--'):
            token = tokens[index]
            key, separator, value = token.partition('=')
            if key in ('--branch', '--arch', '--installation'):
                if not separator or not value or key in seen:
                    raise Unavailable('PACKAGE_LAUNCHER_UNSUPPORTED')
                seen.add(key)
                qualifiers.append(token)
            elif key in ('--user', '--system'):
                if separator or key in seen or ({'--user', '--system'} & seen):
                    raise Unavailable('PACKAGE_LAUNCHER_UNSUPPORTED')
                seen.add(key)
                qualifiers.append(token)
            elif key == '--command':
                if not separator or not value or key in seen:
                    raise Unavailable('PACKAGE_LAUNCHER_UNSUPPORTED')
                seen.add(key)
            elif token != '--file-forwarding':
                raise Unavailable('PACKAGE_LAUNCHER_UNSUPPORTED')
            index += 1
        if index == len(tokens) or (flatpak_name and tokens[index] != flatpak_name):
            raise Unavailable('PACKAGE_LAUNCHER_MISMATCH')
        flatpak_name = tokens[index]
        if not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){2,}', flatpak_name):
            raise Unavailable('PACKAGE_METADATA_INVALID')
        # info has a positional branch, unlike run's --branch option.
        branch = [arg.split('=', 1)[1] for arg in qualifiers if arg.startswith('--branch=')]
        qualifiers = [arg for arg in qualifiers if not arg.startswith('--branch=')]
        values = {}
        for field in ('ref', 'commit', 'location'):
            values[field] = command_output([executable, 'info', '--show-' + field,
                                           *qualifiers, flatpak_name, *branch], environment)
        if (not values['ref'].startswith('app/' + flatpak_name + '/') or
                not re.fullmatch('[0-9a-f]{64}', values['commit']) or
                not Path(values['location']).is_absolute()):
            raise Unavailable('PACKAGE_METADATA_INVALID')
        metadata = file_identity(str(Path(values['location']) / 'metadata'), 1 << 20)
        package = {'kind': 'flatpak', 'id': flatpak_name, 'revision': values['commit'],
                   'ref': values['ref'], 'deployment': values['location'], 'metadata': metadata}
        services = ['file-portal', 'document-portal', 'ibus-portal']
    else:
        with open(target['resolved'], 'rb') as source:
            header = source.read(11)
        if header[:4] == b'\x7fELF' and header[8:11] in (b'AI\x01', b'AI\x02'):
            package = {'kind': 'appimage', 'id': target['resolved'], 'revision': target['sha256'],
                       'format': header[10]}
        elif executable.startswith('/snap/bin/') or Path(executable).name in ('snap', 'flatpak'):
            # Missing package metadata cannot silently receive the native launch
            # environment. A custom entry must resolve through a verified contract.
            raise Unavailable('PACKAGE_METADATA_REQUIRED')
        else:
            package = native_package(target, environment)
    if file_identity(path, 1 << 20) != desktop:
        raise StalePlan()
    return {'desktop': desktop, 'executable': target, 'package': package, 'services': services}


def prepare(path, environment, backends, *, inspect=inspect_application):
    observed = inspect(path, environment)
    matches = [backend for backend in backends if backend.get('id') == 'wayland' and
               backend.get('component') and set(backend.get('protocols', [])) == {'wayland', 'x11'}]
    if len(matches) != 1:
        raise Unavailable('GRAPHICAL_BACKEND_UNAVAILABLE')
    backend = matches[0]
    if any(service not in backend.get('services', []) for service in observed['services']):
        raise Unavailable('HOST_SERVICE_UNAVAILABLE')
    value = {'version': 1, 'observation': observed, 'backend': copy.deepcopy(backend),
             'environment_sha256': digest(resolution_environment(environment))}
    return {**value, 'sha256': digest(value)}


def revalidate(plan, environment, backends, *, inspect=inspect_application):
    # Reject corruption/version/environment drift before reading another target.
    if (plan.get('version') != 1 or plan.get('environment_sha256') != digest(resolution_environment(environment)) or
            plan.get('sha256') != digest({key: value for key, value in plan.items() if key != 'sha256'})):
        raise StalePlan()
    try:
        current = prepare(plan['observation']['desktop']['path'], environment, backends, inspect=inspect)
    except (KeyError, Unavailable):
        raise StalePlan() from None
    if current != plan:
        raise StalePlan()
    return current


def main():
    import sys
    try:
        request = json.loads(sys.stdin.buffer.read((2 << 20) + 1))
        environment = restored_environment(os.environ)
        if request['operation'] == 'prepare':
            result = prepare(request['desktop'], environment, request['backends'])
        elif request['operation'] == 'revalidate':
            result = revalidate(request['plan'], environment, request['backends'])
        else:
            raise Unavailable('APPLICATION_PLAN_INVALID')
        print(json.dumps({'plan': result}))
    except Unavailable as error:
        print(json.dumps({'error': {'code': error.code, 'stage': error.stage}}))
        sys.exit(1)


if __name__ == '__main__':
    main()
