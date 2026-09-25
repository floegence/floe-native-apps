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
