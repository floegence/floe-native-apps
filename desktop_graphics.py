"""Prepare one private combined display from caller-verified native artifacts.

The catalog owner supplies the original component and exact derived shell,
libweston, XWM and capture artifacts. This boundary neither discovers system
fallbacks nor activates components. The launch owner starts the returned commands
and retains the directory for the entire application's lifetime.
"""
import hashlib
import os
from pathlib import Path
import platform
import re
import secrets
import shlex
import shutil
import stat
import subprocess


def private_directory(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('Absolute private graphics directory is required')
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('Private graphics directory is required')
    return path


def native_path(path, *, directory=False):
    path = Path(path)
    if not path.is_absolute() or any(c in str(path) for c in (':', ';', '\n', '\r', '\0')):
        raise ValueError('Native graphics path cannot be represented unambiguously')
    resolved = path.resolve(strict=True)
    if any(c in str(resolved) for c in (':', ';', '\n', '\r', '\0')):
        raise ValueError('Native graphics resource target is ambiguous')
    if not (resolved.is_dir() if directory else resolved.is_file()):
        raise ValueError('Verified native graphics resource is unavailable')
    return path


def owned_identity(path):
    info = path.lstat()
    return info.st_dev, info.st_ino


def remove_owned(path, identity, *, directory=False):
    try:
        if owned_identity(path) == identity:
            if directory:
                shutil.rmtree(path)
            else:
                path.unlink()
    except FileNotFoundError:
        pass


def prepare_x11_socket_directory(path=Path('/tmp/.X11-unix')):
    """Xwayland needs the standard socket namespace even without a desktop.

    Create only an absent directory, without privilege or package installation.
    Existing directories and other sessions' sockets are never repaired/removed.
    """
    created = False
    try:
        path.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        pass
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if info.st_uid not in (0, os.getuid()):
            raise ValueError('Standard X11 socket directory has an unknown owner')
        if created:
            os.fchmod(descriptor, 0o1777)
            info = os.fstat(descriptor)
        if stat.S_IMODE(info.st_mode) != 0o1777:
            raise ValueError('Standard X11 socket directory is unsafe')
    finally:
        os.close(descriptor)


class DesktopGraphics:
    def __init__(self, component, instance, environment, *, shell, capture, library, xwayland, authentication=None):
        self.component = native_path(component, directory=True).resolve(strict=True)
        instance = private_directory(instance)
        # These paths appear in Weston's explicit INI/module map, not in user
        # configuration. Refuse delimiter ambiguity before creating resources.
        native_path(instance, directory=True)
        shell, capture, library, xwayland = (native_path(value) for value in (shell, capture, library, xwayland))
        architecture = {'aarch64': 'aarch64', 'arm64': 'aarch64', 'x86_64': 'x86_64'}.get(platform.machine())
        if architecture is None:
            raise ValueError('Unsupported native graphics architecture')
        loader = self.resource('lib/ld-musl-' + architecture + '.so.1')
        self.libraries = ':'.join(str(path) for path in (library.parent, self.component / 'lib',
            self.component / 'usr/lib', self.component / 'usr/lib/weston', self.component / 'usr/lib/libweston-14'))
        self.loader = loader
        self.display, self.authority_identity = None, None
        self.application_environment = dict(environment)
        runtime = private_directory(environment.get('XDG_RUNTIME_DIR', ''))
        socket_name = environment.get('WAYLAND_DISPLAY', '')
        if (not isinstance(socket_name, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,80}', socket_name) or
                socket_name in ('.', '..')):
            raise ValueError('Private Wayland socket name is required')
        if os.path.lexists(runtime / socket_name):
            raise FileExistsError('Private Wayland endpoint already exists')
        self.private = instance / 'desktop-graphics'
        self.authentication = Path(authentication) if authentication is not None else self.private / 'Xauthority'
        if authentication is not None:
            private_directory(self.authentication.parent)
        if not self.authentication.is_absolute() or os.path.lexists(self.authentication):
            raise FileExistsError('New private X11 authority is required')
        self.private.mkdir(mode=0o700)
        created = owned_identity(self.private)
        try:
            prepare_x11_socket_directory()
            self.environment = {key: value for key, value in environment.items()
                if key in ('PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'DBUS_SESSION_BUS_ADDRESS')}
            self.environment.update(XDG_RUNTIME_DIR=str(runtime), WAYLAND_DISPLAY=socket_name)
            for key, name in (('XDG_CACHE_HOME', 'cache'), ('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data')):
                path = self.private / name
                path.mkdir(mode=0o700)
                self.environment[key] = str(path)
            version = subprocess.check_output(self.tool('usr/bin/weston') + ['--version'], text=True,
                env=self.environment, stderr=subprocess.PIPE, timeout=10).strip()
            if version != 'weston 14.0.2':
                raise ValueError('Unreviewed native compositor version')
            assets = self.resource('usr/share/weston', directory=True)
            for name in ('icon_window.png', 'sign_close.png', 'sign_maximize.png', 'sign_minimize.png'):
                self.resource('usr/share/weston/' + name)
            backend = self.resource('usr/lib/libweston-14/headless-backend.so')
            keyboard = self.resource('usr/share/X11/xkb', directory=True)
            original = self.resource('usr/bin/Xwayland').read_bytes()
            if original.count(b'/usr/bin\0') != 1:
                raise ValueError('Unreviewed native Xwayland compiler path')
            relocated = original.replace(b'/usr/bin\0', b'.\0\0\0\0\0\0\0\0')
            binary = self.private / 'Xwayland'
            self.write(binary, relocated, 0o700)
            self.write(self.authentication, b'', 0o600)
            self.authority_identity = owned_identity(self.authentication)
            self.write(self.private / 'xkbcomp', ('#!/bin/sh\nexec ' +
                shlex.join(self.tool('usr/bin/xkbcomp')) + ' "$@"\n').encode(), 0o700)
            wrapper = self.private / 'xwayland-launch'
            self.write(wrapper, ('#!/bin/sh\ncd ' + shlex.quote(str(self.private)) + ' || exit 1\nexec ' +
                shlex.join([str(loader), '--library-path', self.libraries, str(binary)]) +
                ' -shm -auth ' + shlex.quote(str(self.authentication)) + ' -xkbdir ' +
                shlex.quote(str(keyboard)) + ' "$@"\n').encode(), 0o700)
            config = self.private / 'weston.ini'
            self.write(config, ('[xwayland]\npath=' + str(wrapper) + '\n').encode(), 0o600)
            self.environment.update(WESTON_DATA_DIR=str(assets), XKB_CONFIG_ROOT=str(keyboard),
                WESTON_MODULE_MAP='headless-backend.so=' + str(backend) + ';xwayland.so=' + str(xwayland))
            self.command = self.tool('usr/bin/weston') + ['--backend=headless', '--renderer=pixman', '--xwayland',
                '--shell=' + str(shell), '--socket=' + socket_name, '--width=1000', '--height=700',
                '--idle-time=0', '--config=' + str(config)]
            self.capture_command = [str(loader), '--library-path', self.libraries, str(capture)]
            self.description = {'version': version, 'derived_weston_sha256': self.digest(library.read_bytes()),
                'shell_sha256': self.digest(shell.read_bytes()), 'capture_sha256': self.digest(capture.read_bytes()),
                'xwayland_module_sha256': self.digest(xwayland.read_bytes()),
                'original_xwayland_sha256': self.digest(original), 'prepared_xwayland_sha256': self.digest(relocated)}
        except BaseException:
            if self.authority_identity is not None:
                remove_owned(self.authentication, self.authority_identity)
            remove_owned(self.private, created, directory=True)
            raise

    @staticmethod
    def digest(data):
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def write(path, data, mode):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)

    def resource(self, relative, *, directory=False):
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Native graphics resource must remain in the verified component')
        result = native_path(self.component / path, directory=directory)
        if not result.resolve(strict=True).is_relative_to(self.component):
            raise ValueError('Native graphics resource is outside the verified component')
        return result

    def tool(self, relative):
        return [str(self.loader), '--library-path', self.libraries, str(self.resource(relative))]

    def authorize(self, display):
        if not isinstance(display, str) or not re.fullmatch(r':[0-9]{1,5}', display):
            raise ValueError('Native X11 display is invalid')
        if self.display is not None:
            raise ValueError('Native X11 authorization already consumed')
        if owned_identity(self.authentication) != self.authority_identity:
            raise ValueError('Native X11 authority was replaced')
        # Authorization is a one-shot launch prerequisite. A failure must not
        # rotate credentials or retry behind an already executing application.
        self.display = display
        subprocess.run(self.tool('usr/bin/xauth') + ['-f', str(self.authentication), 'source', '-'],
            input='add ' + display + ' MIT-MAGIC-COOKIE-1 ' + secrets.token_hex(16) + '\n',
            text=True, check=True, env=self.environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
        info = self.authentication.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('Native X11 authority is unavailable')
        self.authority_identity = owned_identity(self.authentication)
        return {**self.application_environment, 'DISPLAY': display, 'XAUTHORITY': str(self.authentication)}

    def close(self):
        # The session calls this only after its application and services exit.
        # Shared package runtimes must not retain instance authorization files.
        if self.authority_identity is not None:
            remove_owned(self.authentication, self.authority_identity)
            self.authority_identity = None
