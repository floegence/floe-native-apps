"""Identity and cancellation properties of the unpublished marker experiment."""
import unittest

from marker_probe import FIRST_CODE, SLOT_COUNT, MarkerTransactions


class MarkerTests(unittest.TestCase):
    def test_cancelled_marker_cannot_take_new_text(self):
        queue = MarkerTransactions()
        stale = queue.enqueue("cancelled")
        queue.revoke()
        current = queue.enqueue("current")
        self.assertNotEqual(stale, current)
        self.assertIsNone(queue.key(stale, False, True))
        self.assertTrue(queue.pending)
        self.assertEqual(queue.key(current, False, True), "current")
        self.assertFalse(queue.pending)

    def test_duplicate_down_does_not_commit_twice(self):
        queue = MarkerTransactions()
        code = queue.enqueue("same")
        self.assertEqual(queue.key(code, False, True), "same")
        self.assertIsNone(queue.key(code, False, True))
        self.assertIsNone(queue.key(code, True, True))
        code = queue.enqueue("same")
        self.assertEqual(queue.key(code, False, True), "same")

    def test_revocation_after_press_keeps_release_identity(self):
        queue = MarkerTransactions()
        old = queue.enqueue("delivered")
        self.assertEqual(queue.key(old, False, True), "delivered")
        queue.revoke()
        new = queue.enqueue("new")
        self.assertIsNone(queue.key(old, True, True))
        self.assertEqual(queue.key(new, False, True), "new")

    def test_unobserved_cancelled_slots_never_expire(self):
        queue = MarkerTransactions()
        codes = []
        for _ in range(SLOT_COUNT):
            codes.append(queue.enqueue("discarded"))
            queue.revoke()
        self.assertEqual(len(set(codes)), SLOT_COUNT)
        with self.assertRaisesRegex(RuntimeError, "capacity"):
            queue.enqueue("cannot be admitted")
        self.assertIsNone(queue.key(codes[0], False, True))
        self.assertIsNone(queue.key(codes[0], True, True))
        self.assertEqual(queue.enqueue("safe reuse"), codes[0])

    def test_lost_focus_consumes_without_committing(self):
        queue = MarkerTransactions()
        code = queue.enqueue("discarded")
        self.assertIsNone(queue.key(code, False, False))
        self.assertFalse(queue.pending)
        self.assertIsNone(queue.key(code, False, True))

    def test_release_without_press_cannot_free_slot(self):
        queue = MarkerTransactions()
        code = queue.enqueue("still pending")
        queue.key(code, True, True)
        self.assertIn(code, queue.slots)
        self.assertIsNone(queue.key(FIRST_CODE + SLOT_COUNT, False, True))
        self.assertEqual(queue.key(code, False, True), "still pending")


if __name__ == "__main__":
    unittest.main()
