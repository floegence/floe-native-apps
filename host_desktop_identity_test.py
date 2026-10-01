import unittest
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from host_desktop_contract import DesktopError
from host_desktop_identity import select_session, HostIdentity, x11_credentials


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

    def test_disappearing_seatless_login_does_not_interrupt_the_graphical_desktop(self):
        identity = HostIdentity.__new__(HostIdentity)
        requested = []
        def call(path, _interface, method, *_args):
            requested.append((path, method))
            if method == 'ListSessions':
                return ([('desktop', 1000, 'fixture', 'seat0', '/desktop'),
                         ('ssh', 1000, 'fixture', '', '/removed-ssh')],)
            if path == '/removed-ssh':
                raise RuntimeError('The unrelated SSH session disappeared')
            return (self.record(Type='x11'),)
        identity._call = call
        with patch('host_desktop_identity.os.getuid', return_value=1000):
            selected = identity.current()
        self.assertEqual(selected['id'], 'desktop')
        self.assertNotIn(('/removed-ssh', 'GetAll'), requested)

    def test_missing_graphical_session_properties_fail_instead_of_using_cached_authority(self):
        identity = HostIdentity.__new__(HostIdentity)
        identity.selected = self.record(id='desktop')
        def call(_path, _interface, method, *_args):
            if method == 'ListSessions':
                return ([('desktop', 1000, 'fixture', 'seat0', '/removed-desktop')],)
            raise RuntimeError('The selected graphical session disappeared')
        identity._call = call
        with patch('host_desktop_identity.os.getuid', return_value=1000):
            with self.assertRaisesRegex(RuntimeError, 'graphical session disappeared'):
                identity.current()

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


class X11IdentityTests(unittest.TestCase):
    def test_empty_logind_display_resolves_only_the_selected_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority = root / 'Xauthority'
            authority.write_bytes(b'task-only cookie fixture')
            authority.chmod(0o600)
            session = {'Type': 'x11', 'Scope': 'session-1.scope', 'Display': ''}
            def process(pid, scope, display):
                entry = root / str(pid)
                entry.mkdir()
                (entry / 'cgroup').write_text('0::/user.slice/user-1000.slice/' + scope)
                (entry / 'environ').write_bytes(('DISPLAY=' + display + '\0XAUTHORITY=' + str(authority) + '\0').encode())
            process(100, 'session-private.scope', ':99')
            with self.assertRaisesRegex(DesktopError, 'X11_SESSION_UNAVAILABLE'):
                x11_credentials(session, os.getuid(), directory)
            process(101, 'session-1.scope', ':0')
            self.assertEqual(x11_credentials(session, os.getuid(), directory), (':0', str(authority)))
            session['Display'] = ':1'
            with self.assertRaisesRegex(DesktopError, 'X11_SESSION_UNAVAILABLE'):
                x11_credentials(session, os.getuid(), directory)
            session['Display'] = ''
            process(102, 'session-1.scope', ':2')
            with self.assertRaisesRegex(DesktopError, 'X11_SESSION_AMBIGUOUS'):
                x11_credentials(session, os.getuid(), directory)

    def test_authority_must_be_private_owned_and_regular(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority = root / 'Xauthority'
            authority.write_bytes(b'task-only cookie fixture')
            authority.chmod(0o600)
            process = root / '101'
            process.mkdir()
            (process / 'cgroup').write_text('0::/session-1.scope')
            (process / 'environ').write_bytes(('DISPLAY=:0\0XAUTHORITY=' + str(authority) + '\0').encode())
            session = {'Type': 'x11', 'Scope': 'session-1.scope'}
            self.assertEqual(x11_credentials(session, os.getuid(), directory)[0], ':0')
            authority.chmod(0o644)
            with self.assertRaisesRegex(DesktopError, 'X11_SESSION_UNAVAILABLE'):
                x11_credentials(session, os.getuid(), directory)
            authority.unlink()
            authority.symlink_to(process / 'environ')
            with self.assertRaisesRegex(DesktopError, 'X11_SESSION_UNAVAILABLE'):
                x11_credentials(session, os.getuid(), directory)
            session['Type'] = 'wayland'
            with self.assertRaisesRegex(DesktopError, 'X11_SESSION_UNAVAILABLE'):
                x11_credentials(session, os.getuid(), directory)


if __name__ == '__main__':
    unittest.main()
