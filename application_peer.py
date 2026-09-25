"""Bind native/bus peers to the supervisor and official Flatpak instance identity.

Flatpak's bus peer is xdg-dbus-proxy, not the application's Wayland peer. The
official /.flatpak-info and runtime bwrapinfo.json identify its sandbox child.
Both processes must still belong to the launch supervisor; metadata never grants
process ownership. No application name, command line or bus description is a
substitute for these live identities. This module never manages process lifetime.
"""
import configparser
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat


PROC = Path('/proc')
MAX_METADATA = 256 * 1024


def read_regular(path, directory=None):
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW,
                         dir_fd=directory)
    with os.fdopen(descriptor, 'rb') as source:
        before = os.fstat(source.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or
                before.st_mode & 0o022 or before.st_size > MAX_METADATA):
            raise ValueError('Unsafe application instance metadata')
        value = source.read(MAX_METADATA + 1)
        after = os.fstat(source.fileno())
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode')
    if len(value) > MAX_METADATA or any(getattr(before, key) != getattr(after, key) for key in fields):
        raise ValueError('Application instance metadata changed')
    return value.decode('utf-8')


@dataclass(frozen=True)
class FlatpakIdentity:
    app: str
    instance: str
    revision: str
    runtime: str


def flatpak_identity(pid):
    try:
        value = read_regular(PROC / str(pid) / 'root/.flatpak-info')
    except FileNotFoundError:
        return None
    try:
        metadata = configparser.ConfigParser(interpolation=None)
        metadata.read_string(value)
        identity = FlatpakIdentity(metadata['Application']['name'], metadata['Instance']['instance-id'],
                                   metadata['Instance']['app-commit'], metadata['Instance']['runtime-commit'])
        if (not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*){2,}', identity.app) or
                len(identity.app) > 255 or not re.fullmatch(r'[0-9]{1,20}', identity.instance) or
                any(not re.fullmatch(r'[0-9a-f]{64}', value) for value in (identity.revision, identity.runtime))):
            raise ValueError('Invalid Flatpak identity')
        return identity
    except (KeyError, configparser.Error):
        raise ValueError('Invalid Flatpak identity') from None


def sandbox_child(runtime, instance):
    """Read only the official, private instance record without following links."""
    descriptors = []
    try:
        for path in (os.fspath(runtime), '.flatpak', instance):
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=descriptors[-1] if descriptors else None)
            descriptors.append(descriptor)
            info = os.fstat(descriptor)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise ValueError('Unsafe Flatpak instance directory')
        document = json.loads(read_regular('bwrapinfo.json', descriptors[-1]))
        if not isinstance(document, dict):
            raise ValueError('Invalid Flatpak child record')
        pid = document.get('child-pid')
        if type(pid) is not int or not 1 < pid <= 0x7fffffff:
            raise ValueError('Invalid Flatpak child identity')
        return pid
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


class ApplicationPeer:
    def __init__(self, tree, pid, runtime):
        self.process, self.child, self.runtime = tree.admit(pid), None, runtime
        try:
            self.package = flatpak_identity(pid)
            self.namespace = os.readlink(PROC / str(pid) / 'ns/pid')
            self.child_namespace = None
            if self.package:
                self.child = tree.admit(sandbox_child(runtime, self.package.instance))
                self.child_namespace = os.readlink(PROC / str(self.child.pid) / 'ns/pid')
                if flatpak_identity(self.child.pid) != self.package:
                    raise ValueError('Flatpak sandbox identity differs from bus peer')
            if not self.valid():
                raise ValueError('Application peer changed during admission')
        except BaseException:
            self.close()
            raise

    def valid(self):
        try:
            if (not self.process.valid() or flatpak_identity(self.process.pid) != self.package or
                    os.readlink(PROC / str(self.process.pid) / 'ns/pid') != self.namespace):
                return False
            if self.child and (not self.child.valid() or flatpak_identity(self.child.pid) != self.package or
                               sandbox_child(self.runtime, self.package.instance) != self.child.pid or
                               os.readlink(PROC / str(self.child.pid) / 'ns/pid') != self.child_namespace):
                return False
            return self.process.valid() and (self.child is None or self.child.valid())
        except (OSError, ValueError):
            return False

    def matches(self, surface_peer):
        """Match a bus peer to an independently admitted native surface peer."""
        if self.process.tree is not surface_peer.process.tree or not self.valid() or not surface_peer.valid():
            return False
        if (self.process.pid, self.process.started) == (surface_peer.process.pid, surface_peer.process.started):
            return True
        return bool(self.package and self.package == surface_peer.package and self.child and surface_peer.child and
                    (self.child.pid, self.child.started) == (surface_peer.child.pid, surface_peer.child.started) and
                    surface_peer.namespace == self.child_namespace)

    def close(self):
        self.process.close()
        if self.child:
            self.child.close()
