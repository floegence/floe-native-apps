import unittest
from host_desktop_contract import DesktopError
from host_desktop_identity import select_session, HostIdentity


class IdentityTests(unittest.TestCase):
    def record(self, **changes):
        return dict({'User': [1000, '/user'], 'Class': 'user', 'Type': 'wayland',
                     'Active': True, 'Remote': False, 'Seat': ['seat0', '/seat']}, **changes)

    def test_private_or_remote_desktops_never_become_host_desktop(self):
        valid = self.record()
        for changes in ({'Seat': ['', '/']}, {'Type': 'tty'}, {'Class': 'greeter'},
                        {'Active': False}, {'Remote': True}, {'User': [1001, '/other']}):
            with self.assertRaises(DesktopError):
                select_session([self.record(**changes)], 1000)
            self.assertEqual(select_session([self.record(**changes), valid], 1000), valid)

    def test_ambiguous_desktops_require_explicit_resolution(self):
        with self.assertRaisesRegex(DesktopError, 'DESKTOP_SESSION_AMBIGUOUS'):
            select_session([self.record(), self.record(Type='x11')], 1000)

    def test_lock_and_deactivation_signals_revoke_cached_input_immediately(self):
        identity = HostIdentity.__new__(HostIdentity)
        identity.selected = dict(self.record(), id='real-desktop', LockedHint=False)
        identity.observed = dict(identity.selected)
        identity.backend = 'wayland'
        changes = []
        identity.changed = changes.append
        class Signal:
            def unpack(self):
                return 'org.freedesktop.login1.Session', {'LockedHint': True}, []
        self.assertEqual(identity.state(), 'ready')
        identity._properties_changed(None, None, None, None, None, Signal())
        self.assertEqual(identity.state(), 'locked')
        self.assertEqual(changes, ['locked'])
        identity.observed.update(Active=False, LockedHint=False)
        self.assertEqual(identity.state(), 'session_unavailable')


if __name__ == '__main__':
    unittest.main()
