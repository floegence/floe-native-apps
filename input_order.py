"""One bounded native input scheduler, independent of transport and toolkit.

Adapters validate ownership/targets at dequeue and select at most one confirmed
text context. Ordinary input executes at that same boundary. Only that context's
completion can release following input; transport writes never complete text.
"""
from collections import deque


class OrderedInput:
    def __init__(self, available, admit, result, overflow, timeout_add, timeout_remove):
        self.available, self.admit, self.result, self.overflow = available, admit, result, overflow
        self.timeout_add, self.timeout_remove = timeout_add, timeout_remove
        self.queue = deque()
        self.pending, self.timer = None, 0
        self.closed, self.draining = False, False

    def enqueue(self, owner, operation):
        if self.closed or not self.available(owner):
            return
        if len(self.queue) >= 256:
            self.overflow(owner)
            self.invalidate(owner)
            return
        self.queue.append((owner, operation))
        self.drain()

    def drain(self):
        if self.draining or self.closed:
            return
        self.draining = True
        try:
            while self.queue and self.pending is None and not self.closed:
                owner, operation = self.queue.popleft()
                if not self.available(owner):
                    continue
                delivery = self.admit(owner, operation)
                if delivery is None:
                    continue
                sequence, adapter, token, text = delivery
                pending = (owner, sequence, adapter, token)
                self.pending = pending
                def completed(error, pending=pending):
                    if self.pending is not pending:
                        return
                    self.clear_timer()
                    self.pending = None
                    if error:
                        pending[2].cancel(pending[3])
                    if self.available(pending[0]):
                        self.result(pending[0], pending[1], error)
                    self.drain()
                def expired(completed=completed, pending=pending):
                    if self.pending is not pending:
                        return False
                    self.timer = 0
                    completed('INPUT_DELIVERY_TIMEOUT')
                    return False
                self.timer = self.timeout_add(3000, expired)
                try:
                    adapter.commit(token, text, completed)
                except Exception:
                    # Never put text or a toolkit exception in diagnostics.
                    completed('INPUT_DELIVERY_FAILED')
        finally:
            self.draining = False

    def clear_timer(self):
        if self.timer:
            self.timeout_remove(self.timer)
            self.timer = 0

    def cancel_pending(self):
        pending, self.pending = self.pending, None
        if pending:
            self.clear_timer()
            pending[2].cancel(pending[3])

    def invalidate(self, owner):
        self.queue = deque(item for item in self.queue if item[0] is not owner)
        if self.pending and self.pending[0] is owner:
            self.cancel_pending()
        self.drain()

    def close(self):
        self.closed = True
        self.queue.clear()
        self.cancel_pending()
