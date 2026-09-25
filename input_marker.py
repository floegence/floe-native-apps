"""Bounded native marker identities beneath the shared input scheduler.

Cancellation leaves a payload-free tombstone until its native key release. A
different generation cannot reuse an unobserved marker. No timer retires slots.
Window/process/context admission belongs to the native context owner.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Condition

FIRST_CODE = 2048
SLOT_COUNT = 32


@dataclass
class Transaction:
    generation: int
    text: str | None
    owner: str | None = None
    pressed: bool = False


class MarkerTransactions:
    def __init__(self):
        self.generation = 1
        self.slots = {}
        self.next_slot = 0
        self.lock = Condition()

    @property
    def pending(self):
        with self.lock:
            return any(t.text is not None for t in self.slots.values())

    def enqueue(self, text, owner=None):
        with self.lock:
            if self.pending:
                raise RuntimeError("A native text transaction is already pending")
            for offset in range(SLOT_COUNT):
                slot = (self.next_slot + offset) % SLOT_COUNT
                code = FIRST_CODE + slot
                if code not in self.slots:
                    self.slots[code] = Transaction(self.generation, text, owner)
                    self.next_slot = (slot + 1) % SLOT_COUNT
                    return code
            raise RuntimeError("Native marker capacity is exhausted")

    def revoke(self):
        with self.lock:
            self.generation += 1
            for transaction in self.slots.values():
                transaction.text = None

    def remove_owner(self, owner):
        # Unique bus names cannot reconnect. Once their connection is gone no
        # late event from that owner can consume or release another's slot.
        with self.lock:
            self.slots = {code: transaction for code, transaction in self.slots.items()
                          if transaction.owner != owner}
            self.lock.notify_all()

    def key(self, code, released, focused, owner=None):
        with self.lock:
            transaction = self.slots.get(code)
            if transaction is None or transaction.owner != owner:
                return None
            if released:
                # An unmatched release is not proof that a pending press was
                # consumed. Keep that slot unavailable instead of guessing.
                if transaction.pressed:
                    del self.slots[code]
                    self.lock.notify_all()
                return None
            if transaction.pressed:
                return None
            transaction.pressed = True
            text, transaction.text = transaction.text, None
            if not focused or transaction.generation != self.generation:
                return None
            return text

    def wait_drained(self, timeout):
        # This is an engine receipt, not a toolkit/application acknowledgement.
        # The actual field contents remain the test's success condition.
        with self.lock:
            if not self.lock.wait_for(lambda: not self.slots, timeout):
                raise RuntimeError("Native marker releases were not observed")


def marker_command(code):
    if not FIRST_CODE <= code < FIRST_CODE + SLOT_COUNT:
        raise ValueError("Unknown native marker")
    return f"key {code} 1\nkey {code} 0\n".encode()
