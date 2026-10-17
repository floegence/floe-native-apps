"""Check bounded readiness of the disposable runner's real host manager."""
import unittest
from qualification.host_manager import await_owner


class HostManagerTests(unittest.TestCase):
    def test_manager_may_register_after_systemctl_returns(self):
        observations = iter([None, None, (':1.4', 1001)])
        elapsed = [0.0]
        def sleep(duration):
            elapsed[0] += duration
        self.assertEqual(await_owner(lambda: next(observations), 1001,
            clock=lambda: elapsed[0], sleep=sleep), ':1.4')
        self.assertAlmostEqual(elapsed[0], 0.2)

    def test_wrong_user_fails_without_retry(self):
        with self.assertRaisesRegex(RuntimeError, 'host user'):
            await_owner(lambda: (':1.4', 0), 1001,
                sleep=lambda _duration: self.fail('Wrong owner must not be retried'))

    def test_absent_manager_has_a_deadline(self):
        elapsed = [0.0]
        def sleep(duration):
            elapsed[0] += duration
        with self.assertRaisesRegex(TimeoutError, 'did not register'):
            await_owner(lambda: None, 1001, timeout=0.3,
                clock=lambda: elapsed[0], sleep=sleep)
        self.assertAlmostEqual(elapsed[0], 0.3)


if __name__ == '__main__':
    unittest.main()
