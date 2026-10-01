import unittest
from unittest.mock import patch
from types import SimpleNamespace

from host_desktop_helper import HostDesktop, portal_displays
from host_desktop_contract import DesktopError
from host_desktop_input import HeldInput


class Loop:
    Error = RuntimeError
    @staticmethod
    def timeout_add(*_):
        return 1


class Backend:
    def __init__(self):
        self.calls = []
    def key(self, code, down):
        self.calls.append(('key', code, down))
    def button(self, code, down):
        self.calls.append(('button', code, down))
    def close(self):
        self.calls.append(('close',))


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.desktop = HostDesktop(None, None, Loop, '', self.messages.append, lambda *_: None)
        self.backend = self.desktop.backend = Backend()
        self.desktop.held = HeldInput(self.backend)
        self.identity_closed = []
        self.desktop.identity = SimpleNamespace(backend='wayland', state=lambda: 'ready', close=lambda: self.identity_closed.append(True))
        self.generation = self.desktop.authority.bind('display-1', 'control')

    def command(self, method, **values):
        self.desktop.command(dict(version=1, id=1, method=method, generation=self.generation, **values))
        return self.messages[-1]

    def test_paint_is_required_and_future_ack_does_not_admit_input(self):
        self.assertEqual(self.command('input', input={'kind': 'key', 'code': 'KeyA', 'pressed': True})['code'], 'STALE_DESKTOP')
        self.desktop.authority.sent(1)
        self.assertEqual(self.command('frame_ack', frame_id=2)['code'], 'STALE_DESKTOP')
        self.assertEqual(self.backend.calls, [])
        self.command('frame_ack', frame_id=1)
        self.command('input', input={'kind': 'key', 'code': 'KeyA', 'pressed': True})
        self.assertEqual(self.backend.calls, [('key', 30, True)])

    def test_decoder_recovery_retires_dependencies_without_restarting_capture(self):
        generations = []
        media = self.desktop.media = SimpleNamespace(target_valid=True, recover=generations.append)
        self.desktop.connected = True
        self.desktop.authority.sent(4)
        self.desktop.authority.paint(self.generation, 4)
        self.desktop.held.key(29, True)
        self.assertEqual(self.command('keyframe')['type'], 'result')
        self.assertIs(self.desktop.media, media)
        self.assertEqual(generations, [self.generation + 1])
        self.assertEqual(self.backend.calls, [('key', 29, True), ('key', 29, False)])
        self.assertFalse(self.backend.clipboard_enabled)
        self.assertEqual(self.desktop.authority.last_painted, 0)
        self.assertEqual(self.command('frame_ack', frame_id=4)['code'], 'STALE_DESKTOP')

    def test_lock_releases_owned_input_and_disables_clipboard(self):
        self.desktop.authority.sent(1)
        self.command('frame_ack', frame_id=1)
        self.desktop.held.key(30, True)
        self.desktop.held.button(272, True)
        self.desktop.identity.state = lambda: 'locked'
        self.assertEqual(self.command('input', input={'kind': 'key', 'code': 'KeyB', 'pressed': True})['code'], 'DESKTOP_NOT_ACTIVE')
        self.assertFalse(self.backend.clipboard_enabled)
        self.assertEqual(set(self.backend.calls), {('key', 30, True), ('button', 272, True), ('key', 30, False), ('button', 272, False)})
        self.assertEqual(self.command('frame_ack', frame_id=1)['code'], 'STALE_DESKTOP')

    def test_view_only_cannot_read_or_change_clipboard(self):
        self.desktop.authority.mode = 'view'
        self.desktop.authority.sent(1)
        self.command('frame_ack', frame_id=1)
        self.assertEqual(self.command('get_clipboard')['code'], 'VIEW_ONLY')
        self.assertEqual(self.command('set_clipboard', text='private')['code'], 'VIEW_ONLY')
        self.assertFalse(self.backend.clipboard_enabled)

    def test_disconnect_keeps_user_processes_outside_native_ownership(self):
        self.desktop.held.key(29, True)
        self.desktop.command(dict(version=1, id=1, method='disconnect'))
        self.assertEqual(self.backend.calls, [('key', 29, True), ('key', 29, False), ('close',)])
        self.assertEqual(self.identity_closed, [True])
        self.assertIsNone(self.desktop.identity)

    def test_lock_signal_stops_media_without_waiting_for_poll_or_input(self):
        stopped = []
        self.desktop.connected = True
        self.desktop.media = SimpleNamespace(close=lambda: stopped.append(True))
        self.desktop.held.key(29, True)
        self.desktop.identity_changed('locked')
        self.assertEqual(stopped, [True])
        self.assertFalse(self.backend.clipboard_enabled)
        self.assertEqual(self.desktop.authority.state, 'locked')
        self.assertEqual(self.backend.calls, [('key', 29, True), ('key', 29, False)])

    def test_lock_request_does_not_claim_the_os_locked_or_resume_when_it_refuses(self):
        self.desktop.connected = True
        self.desktop.identity.lock = lambda: None
        self.desktop.identity.refresh = lambda: 'ready'
        self.desktop.authority.sent(1)
        self.command('frame_ack', frame_id=1)
        with patch('host_desktop_helper.time.monotonic', return_value=100):
            self.command('lock')
        self.assertEqual(self.desktop.authority.state, 'locking')
        with patch('host_desktop_helper.time.monotonic', return_value=103):
            self.desktop.observe()
        self.assertEqual(self.desktop.authority.state, 'LOCK_NOT_CONFIRMED')
        self.assertFalse(self.backend.clipboard_enabled)

    def test_changed_capture_geometry_revokes_input_before_the_media_error_callback(self):
        self.desktop.connected = True
        self.desktop.media = SimpleNamespace(target_valid=False, close=lambda: None)
        self.desktop.authority.sent(1)
        self.desktop.authority.paint(self.generation, 1)
        self.assertEqual(self.command('input', input={'kind': 'key', 'code': 'KeyA', 'pressed': True})['code'], 'DISPLAY_CHANGED')
        self.assertNotIn(('key', 30, True), self.backend.calls)
        self.assertIsNone(self.desktop.identity)

    def test_login_change_cancels_pending_consent_and_releases_identity(self):
        self.desktop.connecting = True
        self.desktop.identity_changed('user_switched')
        self.assertFalse(self.desktop.connecting)
        self.assertIsNone(self.desktop.backend)
        self.assertEqual(self.identity_closed, [True])
        self.assertEqual(self.messages[-1]['state'], 'reconnect_required')

    def test_portal_revoke_during_consent_allows_a_fresh_connection(self):
        self.desktop.connecting = True
        self.desktop.portal_changed('permission_revoked')
        self.assertFalse(self.desktop.connecting)
        self.assertIsNone(self.desktop.backend)
        self.assertIsNone(self.desktop.identity)
        self.assertEqual(self.messages[-1]['code'], 'permission_revoked')

    def test_portal_coordinates_and_display_identity_are_validated(self):
        stream = (12, {'size': (1920, 1080), 'position': (-1920, 0), 'id': 'monitor-2'})
        displays, nodes = portal_displays([stream])
        self.assertEqual(displays[0]['x'], -1920)
        self.assertEqual(nodes[displays[0]['id']], 12)
        for streams in ([], [stream, stream], [(12, {'size': (0, 1080)})],
                        [(12, {'size': (1920, 1080), 'id': 17})],
                        [(12, {'size': (1920, 1080), 'position': (float('nan'), 0)})]):
            with self.assertRaises(DesktopError):
                portal_displays(streams)

    def test_release_never_carries_held_keys_into_a_new_generation(self):
        self.desktop.held.key(29, True)
        self.desktop.suspend('DISPLAY_CHANGED')
        self.desktop.authority.bind('display-2', 'control')
        self.assertEqual(self.command('input', input={'kind':'key', 'code':'KeyA', 'pressed':True})['code'], 'STALE_DESKTOP')
        self.assertEqual(self.backend.calls, [('key', 29, True), ('key', 29, False)])

    def test_remote_release_never_releases_an_unowned_local_key_or_button(self):
        self.desktop.held.key(30, False)
        self.desktop.held.button(272, False)
        self.assertEqual(self.backend.calls, [])

    def test_failed_key_press_retains_release_responsibility(self):
        def failed(_code, down):
            if down:
                raise RuntimeError('unknown native submission outcome')
            self.backend.calls.append(('release', _code))
        self.backend.key = failed
        with self.assertRaises(RuntimeError):
            self.desktop.held.key(29, True)
        self.desktop.held.release()
        self.assertEqual(self.backend.calls, [('release', 29)])


if __name__ == '__main__':
    unittest.main()
