"""One wait owner must retain admitted launch PIDs during host-service calls."""
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

import application_processes as processes


class ProcessOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.parent = 100
        self.identities = {101: (100, 41), 102: (100, 42), 103: (101, 43)}
        self.closed, self.observed = [], []
        self.owner = processes.LaunchChildren(lambda pid, status: self.observed.append((pid, status)))
        for item in (
            patch.object(processes.os, 'getpid', return_value=self.parent),
            patch.object(processes, 'identity', side_effect=lambda pid: self.identities[pid]),
            patch.object(processes.os, 'pidfd_open', side_effect=lambda pid: pid + 1000, create=True),
            patch.object(processes.os, 'close', side_effect=self.closed.append),
            patch.object(processes, 'descriptor_exited', return_value=False),
            patch.object(processes.os, 'P_ALL', 0, create=True),
            patch.object(processes.os, 'WEXITED', 4, create=True),
            patch.object(processes.os, 'WNOWAIT', 0x1000000, create=True),
        ):
            item.start()
            self.addCleanup(item.stop)
        self.owner.register(101)

    def test_only_registered_live_direct_launchers_can_be_pinned(self):
        for pid in (102, 103):
            with self.subTest(pid=pid), self.assertRaises(ValueError):
                self.owner.pin(pid)
        self.identities[101] = (100, 99)
        with self.assertRaises(ValueError):
            self.owner.pin(101)

    def test_pid_reuse_between_observation_and_open_is_rejected(self):
        with patch.object(processes.os, 'pidfd_open', create=True) as opened:
            def reused(_pid):
                self.identities[101] = (100, 99)
                return 1101
            opened.side_effect = reused
            with self.assertRaises(ValueError):
                self.owner.pin(101)
        self.assertEqual(self.closed, [1101])

    def test_exited_caller_cannot_acquire_new_authority(self):
        with patch.object(processes, 'descriptor_exited', return_value=True):
            with self.assertRaises(ValueError):
                self.owner.pin(101)
        self.assertEqual(self.closed, [1101])

    def test_wait_observes_without_reaping_until_host_call_releases_pid(self):
        lease = self.owner.pin(101)
        seen = threading.Event()
        calls = iter([SimpleNamespace(si_pid=101), ChildProcessError()])
        def observe(*_args):
            value = next(calls)
            if isinstance(value, Exception):
                raise value
            seen.set()
            return value
        with patch.object(processes.os, 'waitid', side_effect=observe, create=True), \
             patch.object(processes.os, 'waitpid', return_value=(101, 46 << 8)) as reap:
            waiter = threading.Thread(target=self.owner.wait)
            waiter.start()
            try:
                self.assertTrue(seen.wait(1))
                # Both methods use the same condition lock. Reaching this
                # assertion after observation cannot race a protected reap.
                with self.owner.condition:
                    reap.assert_not_called()
                lease.close()
                waiter.join(1)
                self.assertFalse(waiter.is_alive())
                reap.assert_called_once_with(101, 0)
            finally:
                lease.close()
                waiter.join(1)
        self.assertEqual(self.observed, [(101, 46 << 8)])
        self.assertEqual(self.closed, [1101])
        with self.assertRaises(ValueError):
            self.owner.pin(101)

    def test_adopted_descendants_are_reaped_but_not_added_as_launchers(self):
        observations = [SimpleNamespace(si_pid=101), SimpleNamespace(si_pid=103), ChildProcessError()]
        with patch.object(processes.os, 'waitid', side_effect=observations, create=True), \
             patch.object(processes.os, 'waitpid', side_effect=[(101, 0), (103, 0)]):
            self.owner.wait()
        self.assertEqual(self.observed, [(101, 0), (103, 0)])
        self.assertEqual(self.owner.roots, {})


if __name__ == '__main__':
    unittest.main()
