"""Scoped access to the official host document portal, without a desktop proxy.

Only the admitted Flatpak launcher and the prepared private file portal may
export files. Launch-time files are explicit descriptor identities; chooser
exports come from the trusted official private portal. Existing host grants and
their persistence flags remain authoritative and are never deleted on detach.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat


METHODS = {'GetMountPoint', 'Add', 'AddFull', 'AddNamed', 'AddNamedFull',
           'GrantPermissions', 'RevokePermissions', 'Delete', 'Info'}
MAX_DOCUMENTS, MAX_PENDING, MAX_FILES = 4096, 32, 64
PERMISSIONS = {'read', 'write', 'grant-permissions', 'delete'}


def executable_identity(path):
    info = os.stat(path)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Document launcher executable is unavailable')
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def file_identity(descriptor):
    info = os.fstat(descriptor)
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError('Document descriptor is not a regular file or directory')
    return info.st_dev, info.st_ino


@dataclass
class DocumentCaller:
    role: str
    peer: object
    owned: bool
    files: object = None

    def valid(self):
        return self.peer.valid()

    def close(self):
        if self.owned:
            self.peer.close()


class DocumentAuthority:
    def __init__(self):
        self.portal, self.launcher, self.executable = None, None, None
        self.initial, self.closed = (), False

    def bind_portal(self, peer):
        if self.closed or self.portal is not None or not peer.valid():
            raise ValueError('Private file portal is unavailable')
        self.portal = peer

    def bind_launcher(self, tree, executable, files):
        if self.closed or self.launcher is not None or len(files) > MAX_FILES:
            raise ValueError('Document launcher is unavailable')
        descriptors = []
        try:
            executable = executable_identity(executable)
            for path in files:
                if not Path(path).is_absolute():
                    raise ValueError('Initial documents require absolute paths')
                descriptor = os.open(path, os.O_PATH | os.O_CLOEXEC)
                descriptors.append(descriptor)
                file_identity(descriptor)
            self.executable, self.initial, self.launcher = executable, tuple(descriptors), tree
        except BaseException:
            for descriptor in descriptors:
                os.close(descriptor)
            raise

    def admit(self, credentials):
        if self.closed or credentials.get('UnixUserID') != os.getuid():
            raise ValueError('Document caller is unavailable')
        pid = credentials.get('ProcessID')
        if type(pid) is not int or pid <= 1:
            raise ValueError('Document caller identity is unavailable')
        if self.portal and self.portal.pid == pid and self.portal.valid():
            return DocumentCaller('portal', self.portal, False)
        if self.launcher:
            peer = self.launcher.admit(pid)
            try:
                if executable_identity('/proc/' + str(pid) + '/exe') != self.executable or not peer.valid():
                    raise ValueError('Caller is not the admitted package launcher')
                return DocumentCaller('launcher', peer, True,
                                      {file_identity(descriptor) for descriptor in self.initial})
            except BaseException:
                peer.close()
                raise
        raise ValueError('Document caller is not an admitted service')

    def close(self):
        if self.closed:
            return
        self.closed = True
        for descriptor in self.initial:
            os.close(descriptor)
        self.initial = ()
        # Process references and the application tree belong to preparation.
        self.portal, self.launcher = None, None


@dataclass(eq=False)
class DocumentRequest:
    method: str
    caller: object
    count: int
    unique: bool
    persistent: bool
    document: str = ''


class DocumentGrants:
    def __init__(self, app_id):
        if len(app_id) > 255 or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*){2,}', app_id):
            raise ValueError('Document application identity is invalid')
        self.app_id, self.documents, self.pending = app_id, {}, set()
        self.closed = False

    def admit(self, caller, method, values, descriptors):
        if self.closed or method not in METHODS or not caller.valid() or len(self.pending) >= MAX_PENDING:
            raise ValueError('Document operation is unavailable')
        if caller.role not in ('launcher', 'portal'):
            raise ValueError('Document caller is not a prepared service')
        if len(descriptors) > MAX_FILES:
            raise ValueError('Document descriptor limit exceeded')
        if method in ('GrantPermissions', 'RevokePermissions', 'Delete', 'Info'):
            record = self.documents.get(values[0])
            if record is None:
                raise ValueError('Document does not belong to this instance')
            if method in ('Delete', 'RevokePermissions') and not record['unique']:
                raise ValueError('Existing document grants cannot be removed')
        app_index = {'AddFull': 2, 'AddNamedFull': 3, 'GrantPermissions': 1, 'RevokePermissions': 1}.get(method)
        if app_index is not None:
            if values[app_index] != self.app_id:
                raise ValueError('Document app identity differs from the launch plan')
            permissions = values[app_index + 1]
            if (len(permissions) > len(PERMISSIONS) or len(set(permissions)) != len(permissions) or
                    not set(permissions) <= PERMISSIONS):
                raise ValueError('Document permissions are unsupported')
        count, unique, persistent, handles = 0, False, False, []
        if method.startswith('Add'):
            if method == 'AddFull':
                handles, flags = list(values[0]), values[1]
            elif method == 'AddNamedFull':
                handles, flags = [values[0]], values[2]
            elif method == 'Add':
                handles, flags = [values[0]], int(values[1]) | (int(values[2]) << 1)
            else:
                handles, flags = [values[0]], int(values[2]) | (int(values[3]) << 1)
            if flags & ~15:
                raise ValueError('Document flags are unsupported')
            unique, persistent = not bool(flags & 1), bool(flags & 2)
            count = len(handles)
            if not 1 <= count <= MAX_FILES or set(handles) != set(range(len(descriptors))):
                raise ValueError('Document handles do not match the descriptor list')
            if len(set(handles)) != count:
                raise ValueError('Document handles must be distinct')
            if method.startswith('AddNamed'):
                name = bytes(values[1])
                if (not name.endswith(b'\0') or b'\0' in name[:-1] or b'/' in name or
                        name[:-1] in (b'', b'.', b'..') or len(name) > 256 or caller.role == 'launcher'):
                    raise ValueError('Document save basename is invalid')
            for descriptor in descriptors:
                identity = file_identity(descriptor)
                if caller.role == 'launcher' and identity not in caller.files:
                    raise ValueError('Launcher document was not an authorized input')
                if method.startswith('AddNamed') and not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    raise ValueError('Named document requires a directory descriptor')
        elif descriptors:
            raise ValueError('Document operation does not accept descriptors')
        if len(self.documents) + sum(r.count for r in self.pending) + count > MAX_DOCUMENTS:
            raise ValueError('Document grant limit exceeded')
        request = DocumentRequest(method, caller, count, unique, persistent,
                                  values[0] if method == 'Delete' else '')
        self.pending.add(request)
        return request

    def complete(self, request, reply):
        if request not in self.pending:
            raise ValueError('Document operation is no longer pending')
        self.pending.remove(request)
        if request.method == 'Delete':
            self.documents.pop(request.document, None)
        if request.method.startswith('Add'):
            identifiers = reply[0] if request.method == 'AddFull' else [reply[0]]
            if len(identifiers) != request.count or any(
                    not isinstance(value, str) or (value and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value))
                    for value in identifiers):
                raise ValueError('Official document reply is invalid')
            for value in identifiers:
                if value:
                    previous = self.documents.get(value)
                    # Reuse never grants deletion authority, including after an
                    # earlier unique export of this path in the same instance.
                    self.documents[value] = {'unique': request.unique and previous is None,
                                             'persistent': request.persistent or bool(previous and previous['persistent'])}

    def failed(self, request):
        self.pending.discard(request)

    def close(self):
        self.closed = True
        self.pending.clear()
        self.documents.clear()
        # Do not delete or revoke host grants. They may predate this instance;
        # persistence is decided by the official portal and the original request.
