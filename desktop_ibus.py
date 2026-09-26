"""Synchronous IBus contexts beneath the existing native text transaction owner.

The derived official daemon and portal describe live connection credentials.
Client descriptions never establish identity. A toolkit in synchronous IBus
post-processing mode handles the press's commit before dispatching its release;
only that same context's release can complete the existing ordered transaction.
Preparation must select this qualified mode; this is not a generic IBus engine.
"""
from dataclasses import dataclass
import os

from application_peer import ApplicationPeer
from input_marker import FIRST_CODE, SLOT_COUNT


@dataclass(frozen=True)
class IBusSource:
    path: str
    connection: str
    pid: int
    portal_owner: str = ''
    portal_context: str = ''

    @property
    def sender(self):
        # Separate the private IBus bus from native module session-bus names.
        return 'ibus/' + self.connection + '/' + self.portal_owner


class IBusSources:
    def __init__(self, tree, runtime, daemon, portal, describe, describe_portal, credentials):
        self.tree, self.runtime = tree, runtime
        self.daemon, self.portal = daemon, portal
        self.describe, self.describe_portal, self.credentials = describe, describe_portal, credentials

    def read(self, path):
        if not self.daemon.valid():
            raise ValueError('Private IBus daemon is unavailable')
        version, owner, pid, uid, focused, post_process = self.describe(path)
        if (version != 1 or not owner.startswith(':') or uid != os.getuid() or
                pid <= 1 or not focused or not post_process):
            raise ValueError('Synchronous native input context is unavailable')
        portal_owner, portal_context = '', ''
        if self.portal and pid == self.portal.pid:
            if not self.portal.valid():
                raise ValueError('Private IBus portal is unavailable')
            service = self.credentials('org.freedesktop.portal.IBus')
            if service.get('ProcessID') != pid or service.get('UnixUserID') != os.getuid():
                raise ValueError('Private IBus portal owner changed')
            version, portal_owner, portal_context = self.describe_portal(path)
            if version != 1 or not portal_owner.startswith(':') or not portal_context.startswith('/'):
                raise ValueError('Private IBus portal context is unavailable')
            source = self.credentials(portal_owner)
            if source.get('UnixUserID') != os.getuid() or source.get('ProcessID', 0) <= 1:
                raise ValueError('Private IBus source user differs')
            pid = source['ProcessID']
        return IBusSource(path, owner, pid, portal_owner, portal_context)

    def lease(self, path):
        source = self.read(path)
        peer = ApplicationPeer(self.tree, source.pid, self.runtime)
        try:
            if not peer.valid() or self.read(path) != source:
                raise ValueError('Private IBus source changed during admission')
            return source, peer
        except BaseException:
            peer.close()
            raise


class IBusContexts:
    """One adapter; queue, pending payload and completion belong to NativeContexts."""
    def __init__(self, contexts, sources):
        if contexts.ibus is not None:
            raise ValueError('An IBus context adapter already exists')
        self.contexts, self.sources = contexts, sources
        self.active, self.closed = None, False
        contexts.ibus = self

    def focus(self, engine, path):
        active = (engine, path) if path else None
        previous = self.active
        if active is None and previous and previous[0] is not engine:
            return
        self.active = active
        operation = self.contexts.pending
        if (active != previous and operation and operation['token'].adapter is self and
                operation['taken']):
            self.contexts.finish('INPUT_TARGET_UNAVAILABLE')

    def select(self, surface):
        if self.closed or not self.active:
            return None
        try:
            source, peer = self.sources.lease(self.active[1])
            if peer.matches(surface):
                return source.sender, peer
            peer.close()
        except (OSError, ValueError):
            pass
        return None

    def valid(self, _token):
        return not self.closed and self.sources.daemon.valid()

    def key(self, engine, code, released, commit):
        if not FIRST_CODE <= code < FIRST_CODE + SLOT_COUNT:
            return False
        operation = self.contexts.pending
        owned = bool(operation and operation['code'] == code and operation['token'].adapter is self)
        peer = None
        try:
            if self.closed or not self.active or self.active[0] is not engine:
                raise ValueError('Private IBus focus is unavailable')
            source, peer = self.sources.lease(self.active[1])
            if not owned:
                # Cancelled markers have no payload. Retire only their original
                # connection's press/release; never reuse an unobserved slot.
                self.contexts.markers.key(code, released, False, owner=source.sender)
                return True
            token = operation['token']
            if source.sender != token.sender or not peer.matches(token.surface_peer):
                raise ValueError('Private IBus marker belongs to another application')
            if released:
                self.contexts.released(token.sender, code)
                if operation.get('ibus_source') != source or not operation['taken']:
                    raise ValueError('Private IBus completion belongs to another context')
                self.contexts.done(token.sender, operation['sequence'])
            else:
                taken = self.contexts.take(token.sender, code, token.focus.surface)
                if taken is not None:
                    operation['ibus_source'] = source
                    commit(taken[1])
        except (OSError, ValueError):
            if owned:
                self.contexts.finish('INPUT_TARGET_UNAVAILABLE')
        finally:
            if peer:
                peer.close()
        return True

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.contexts.pending and self.contexts.pending['token'].adapter is self:
            self.contexts.finish('INPUT_CONTEXT_UNAVAILABLE')
        if self.contexts.bound and self.contexts.bound.adapter is self:
            self.contexts.unbind()
        self.contexts.ibus = None
        self.active = None
