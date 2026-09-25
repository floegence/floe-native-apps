"""Application child ownership and the supervisor's sole wait boundary.

A pidfd identifies a process but does not reserve its numeric PID after waitpid.
An admitted host-service request therefore also pins the unreaped direct child.
Only this supervisor may release its children; viewers never own this boundary.
"""
import os
from pathlib import Path
import select
from threading import Condition


def identity(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return int(fields[1]), int(fields[19])


def descriptor_exited(descriptor):
    poll = select.poll()
    poll.register(descriptor, select.POLLIN)
    return bool(poll.poll(0))


class ProcessReference:
    """A native peer identity, never process termination or wait authority."""
    def __init__(self, tree, pid, started, descriptor):
        self.tree, self.pid, self.started, self.descriptor = tree, pid, started, descriptor

    def valid(self):
        if self.descriptor is None:
            return False
        try:
            return (not descriptor_exited(self.descriptor) and identity(self.pid)[1] == self.started and
                    self.tree.owns(self.pid))
        except (OSError, ValueError):
            return False

    def close(self):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None
            self.tree.references.discard(self)


class ProcessTree:
    """Verify native window/bus callers against the existing launch supervisor.

    This observes ownership only. It never adopts, waits for, signals or moves a
    process. The application supervisor remains the sole lifecycle owner. Use
    the root identity recorded at launch, not a PID rediscovered by app name.
    """
    def __init__(self, pid, started):
        self.pid, self.started = pid, started
        self.references = set()
        self.descriptor = os.pidfd_open(pid)
        try:
            if descriptor_exited(self.descriptor) or identity(pid)[1] != started:
                raise ValueError('Application supervisor identity is unavailable')
        except BaseException:
            os.close(self.descriptor)
            self.descriptor = None
            raise

    def owns(self, pid):
        if self.descriptor is None or pid == self.pid:
            return False
        ancestors = []
        try:
            if descriptor_exited(self.descriptor) or identity(self.pid)[1] != self.started:
                return False
            seen = set()
            for _ in range(128):
                if pid <= 1 or pid in seen:
                    return False
                seen.add(pid)
                observed = identity(pid)
                descriptor = os.pidfd_open(pid)
                ancestors.append((pid, observed, descriptor))
                if descriptor_exited(descriptor) or identity(pid) != observed:
                    return False
                parent, _started = observed
                if parent == self.pid:
                    # A pidfd does not freeze ancestry or reserve a reaped PID.
                    # Validate the entire observed chain before granting a peer.
                    return (not descriptor_exited(self.descriptor) and identity(self.pid)[1] == self.started and
                            all(not descriptor_exited(fd) and identity(child) == previous
                                for child, previous, fd in ancestors))
                pid = parent
            return False
        except (OSError, ValueError):
            return False
        finally:
            for _pid, _observed, descriptor in ancestors:
                os.close(descriptor)

    def admit(self, pid):
        if type(pid) is not int or pid <= 1 or len(self.references) >= 256:
            raise ValueError('Native application peer is unavailable')
        before = identity(pid)
        descriptor = os.pidfd_open(pid)
        try:
            if descriptor_exited(descriptor) or identity(pid) != before or not self.owns(pid):
                raise ValueError('Native peer does not belong to the application')
            reference = ProcessReference(self, pid, before[1], descriptor)
            self.references.add(reference)
            return reference
        except BaseException:
            os.close(descriptor)
            raise

    def close(self):
        for reference in tuple(self.references):
            reference.close()
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


class ChildLease:
    def __init__(self, owner, pid, started, descriptor):
        self.owner, self.pid, self.started, self.descriptor = owner, pid, started, descriptor

    def close(self):
        with self.owner.condition:
            if self.descriptor is None:
                return
            os.close(self.descriptor)
            self.descriptor = None
            self.owner.pins[self.pid] -= 1
            if not self.owner.pins[self.pid]:
                del self.owner.pins[self.pid]
            self.owner.condition.notify_all()


class LaunchChildren:
    def __init__(self, observed):
        self.observed = observed
        self.condition = Condition()
        self.roots, self.pins = {}, {}
        self.waiting = False

    def register(self, pid):
        with self.condition:
            parent, started = identity(pid)
            if parent != os.getpid() or pid in self.roots:
                raise ValueError('Invalid direct application launcher')
            self.roots[pid] = started

    def pin(self, pid):
        with self.condition:
            started = self.roots.get(pid)
            if started is None or identity(pid) != (os.getpid(), started):
                raise ValueError('Caller is not the registered application launcher')
            descriptor = os.pidfd_open(pid)
            try:
                if descriptor_exited(descriptor) or identity(pid) != (os.getpid(), started):
                    raise ValueError('Application launcher identity changed')
                self.pins[pid] = self.pins.get(pid, 0) + 1
                return ChildLease(self, pid, started, descriptor)
            except BaseException:
                os.close(descriptor)
                raise

    def wait(self, before_wait=lambda: None):
        with self.condition:
            if self.waiting:
                raise RuntimeError('Application children already have a wait owner')
            self.waiting = True
        try:
            while True:
                before_wait()
                try:
                    # WNOWAIT leaves zombies allocated until a real host-service
                    # operation has finished using their numeric PID.
                    result = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOWAIT)
                except InterruptedError:
                    continue
                except ChildProcessError:
                    return
                with self.condition:
                    self.condition.wait_for(lambda: result.si_pid not in self.pins)
                    pid, status = os.waitpid(result.si_pid, 0)
                    self.roots.pop(pid, None)
                    self.observed(pid, status)
        finally:
            with self.condition:
                self.waiting = False
