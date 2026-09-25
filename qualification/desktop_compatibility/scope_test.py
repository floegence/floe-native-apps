"""Source-only authorization checks for the unpublished scope prototype."""
import unittest

from scope_bridge import validate_request


class ScopeRequestTest(unittest.TestCase):
    def setUp(self):
        self.unit = "snap.firefox.firefox-77218fe2-ab29-4bc5-a7be-30a9d58bbaf2.scope"
        self.request = (self.unit, "fail", [("PIDs", [121])], [])

    def check(self, request, pid=121):
        return validate_request("snap.firefox.firefox", pid, request)

    def test_exact_caller_scope_is_allowed(self):
        self.assertEqual(self.check(self.request), self.unit)

    def test_other_process_or_multiple_processes_are_rejected(self):
        for pids in ([122], [121, 122], [], [True]):
            with self.subTest(pids=pids), self.assertRaises(ValueError):
                self.check((self.unit, "fail", [("PIDs", pids)], []))

    def test_service_operations_and_other_packages_are_rejected(self):
        for unit in ("snap.other.firefox-77218fe2-ab29-4bc5-a7be-30a9d58bbaf2.scope",
                     "snap.firefox.firefox.service", self.unit + ".scope",
                     "snap.firefox.firefox-existing.scope"):
            with self.subTest(unit=unit), self.assertRaises(ValueError):
                self.check((unit, *self.request[1:]))

    def test_extra_properties_units_and_replace_modes_are_rejected(self):
        for request in (
            (self.unit, "replace", self.request[2], []),
            (self.unit, "isolate", self.request[2], []),
            (self.unit, "fail", self.request[2] + [("Delegate", True)], []),
            (self.unit, "fail", self.request[2] * 2, []),
            (self.unit, "fail", self.request[2], [("other.scope", [])]),
        ):
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.check(request)


if __name__ == "__main__":
    unittest.main()
