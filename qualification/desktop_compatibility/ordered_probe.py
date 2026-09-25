"""Actual toolkit completion through the one production input scheduler.

The private fixture has one native window and one admitted module. This does
not substitute for production surface/process admission or sandbox ABI support.
"""
from threading import Event

from gi.repository import GLib
from input_order import OrderedInput


class OrderedProbe:
    def __init__(self, channel, native):
        self.channel, self.native = channel, native
        self.owner, self.sequence = object(), 0
        self.errors, self.completed = [], []
        self.order = OrderedInput(lambda owner: owner is self.owner and not self.errors,
                                  self.admit, self.result, self.overflow,
                                  GLib.timeout_add, GLib.source_remove)

    def admit(self, owner, operation):
        sequence, kind, value = operation
        if kind == 'text':
            # Bind the registered native module, not a later registration with
            # the same PID. The module independently checks native focus.
            if len(self.native.clients) != 1:
                self.result(owner, sequence, 'INPUT_CONTEXT_UNAVAILABLE')
                return None
            sender, identity = next(iter(self.native.clients.items()))
            return sequence, self, (sender, identity), value
        if kind == 'barrier':
            value.set()
        else:
            self.channel.sendall(value)
        return None

    def commit(self, token, text, completed):
        sender, identity = token
        if self.native.clients.get(sender) != identity:
            completed('INPUT_CONTEXT_UNAVAILABLE')
            return
        self.channel.sendall(self.native.enqueue(text, completed))

    def cancel(self, _token):
        self.native.revoke()

    def result(self, owner, sequence, error):
        if error:
            self.errors.append(error)
            self.order.invalidate(owner)
        else:
            self.completed.append(sequence)

    def overflow(self, _owner):
        self.errors.append('INPUT_QUEUE_FULL')

    def enqueue(self, kind, value):
        self.sequence += 1
        operation = (self.sequence, kind, value)
        def submit():
            if kind == 'barrier' and self.errors:
                value.set()
            else:
                self.order.enqueue(self.owner, operation)
            return False
        GLib.idle_add(submit)

    def flush(self):
        finished = Event()
        self.enqueue('barrier', finished)
        if not finished.wait(15) or self.errors:
            raise RuntimeError('Ordered native fixture did not finish: ' + ','.join(self.errors))

    def close(self):
        finished = Event()
        def stop():
            self.order.close()
            finished.set()
            return False
        GLib.idle_add(stop)
        assert finished.wait(5)
