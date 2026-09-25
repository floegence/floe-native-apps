"""Native window instances and typed seat operations at the helper boundary."""
import socket
import unittest

from desktop_native import NativeChannel, NativeDesktop


class Loop:
    def __init__(self):
        self.sources = {}

    def watch(self, descriptor, read=None, write=None):
        if read or write:
            self.sources[descriptor] = (read, write)
        else:
            self.sources.pop(descriptor, None)


class Frames:
    def __init__(self):
        self.cancelled = 0

    def cancel(self):
        self.cancelled += 1

    def close(self):
        pass


class Attachment:
    def __init__(self):
        self.changes, self.damages = 0, 0

    def scene_changed(self):
        self.changes += 1

    def damage(self):
        self.damages += 1


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.frames = Frames()
        self.native = NativeDesktop(self.sent.append, self.frames)
        self.attachment = Attachment()
        self.native.attachment = self.attachment
        self.native.observe('native-version 1')
        self.native.observe('window-instance 1')
        self.native.observe('window-state 1 0 wayland 120 1000 700')
        self.native.observe('surface-instance 1')
        self.native.observe('focus 1 1 120 19 1')
        self.native.observe('scene 1 1')
        self.native.bind(1)

    def test_registry_is_native_and_ids_cannot_be_reused(self):
        previous = self.native.target
        self.native.observe('window-instance 2')
        self.native.observe('window-state 2 1 wayland 120 350 280')
        self.native.observe('surface-instance 2')
        self.native.observe('focus 2 2 120 20 1')
        self.native.observe('scene 2 2')
        self.assertIsNot(previous, self.native.target)
        snapshot = self.native.snapshot()
        self.assertEqual([w['window'] for w in snapshot['windows']], [1, 2])
        self.assertEqual(snapshot['windows'][1]['parent'], 1)
        self.native.observe('window-retired 2')
        self.assertIsNone(self.native.target)
        self.native.observe('focus 1 1 120 19 1')
        self.native.observe('scene 3 1')
        self.assertEqual(self.native.target.window, 1)
        with self.assertRaises(ValueError):
            self.native.observe('window-state 2 0 wayland 120 350 280')
        with self.assertRaises(ValueError):
            self.native.observe('window-instance 2')

    def test_surface_map_order_does_not_change_instance_creation_order(self):
        self.native.observe('window-instance 2')
        self.native.observe('window-instance 3')
        self.native.observe('window-state 3 0 wayland 121 1000 700')
        self.native.observe('window-state 2 0 wayland 121 1000 700')
        self.native.observe('surface-instance 2')
        self.native.observe('focus 2 2 121 25 1')
        self.native.observe('scene 2 2')
        self.assertEqual(self.native.target.window, 2)
        with self.assertRaises(ValueError):
            self.native.observe('window-instance 2')

    def test_geometry_change_revokes_old_target_before_scene_barrier(self):
        self.native.observe('window-state 1 0 wayland 120 600 400')
        self.assertIsNone(self.native.target)
        self.native.observe('scene 2 1')
        self.assertEqual(self.native.target.width, 600)
        with self.assertRaises(ValueError):
            self.native.observe('scene 2 1')

    def test_popup_focus_cannot_keep_parent_input_or_reuse_a_destroyed_surface(self):
        previous = self.native.target
        self.native.observe('surface-instance 9')
        self.native.observe('focus 9 1 120 37 0')
        self.assertIsNone(self.native.target)
        self.assertFalse(self.native.focus.available)
        with self.assertRaises(ValueError):
            self.native.observe('scene 2 1')
        self.native.observe('scene 2 0')
        self.native.observe('focus 9 1 120 37 1')
        self.native.observe('scene 3 1')
        self.assertIsNot(self.native.target, previous)
        self.assertEqual(self.native.focus.surface, 37)
        self.native.observe('surface-retired 9')
        self.assertIsNone(self.native.target)
        self.assertIsNone(self.native.focus)
        for event in ('surface-instance 9', 'focus 9 1 120 37 1', 'scene 4 1'):
            with self.subTest(event=event), self.assertRaises(ValueError):
                self.native.observe(event)

    def test_surface_and_focus_are_private_to_each_native_connection(self):
        other = NativeDesktop(lambda _: None, Frames())
        other.observe('native-version 1')
        other.observe('window-instance 1')
        with self.assertRaises(ValueError):
            other.observe('focus 1 1 120 19 1')
        self.native.lost()
        self.native.observe('focus 1 1 120 19 1')
        self.assertIsNone(self.native.focus)
        self.assertIsNone(self.native.target)

    def test_pointer_key_and_scroll_are_typed_without_marker_or_command_access(self):
        target = self.native.target
        values = [
            {'kind': 'move', 'x': 42.5, 'y': 66},
            {'kind': 'button', 'x': 42.5, 'y': 66, 'button': 0, 'pressed': True},
            {'kind': 'key', 'code': 30, 'pressed': True},
            {'kind': 'scroll', 'x': 42.5, 'y': 66, 'dx': 0.25, 'dy': -7.5},
        ]
        for value in values:
            self.native.deliver(1, target, self.native.validate_input(value))
        self.assertEqual(self.sent[1:], [
            'input 1 1 1 motion 42.5 66\n',
            'input 1 1 1 motion 42.5 66\ninput 1 1 1 button 272 1\n',
            'input 1 1 1 key 30 1\n',
            'input 1 1 1 motion 42.5 66\ninput 1 1 1 scroll 0.25 -7.5\n',
        ])
        for value in [
            {'kind': 'fixture', 'commands': 'capture-authorize 123'},
            {'kind': 'key', 'code': 2048, 'pressed': True},
            {'kind': 'key', 'code': True, 'pressed': True},
            {'kind': 'key', 'code': 30, 'pressed': 1},
            {'kind': 'move', 'x': float('nan'), 'y': 0},
            {'kind': 'move', 'x': 0, 'y': 0, 'commands': 'close'},
            {'kind': 'scroll', 'x': 0, 'y': 0, 'dx': 1, 'dy': float('inf')},
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.native.validate_input(value)

    def test_unknown_context_is_unavailable_without_a_fallback(self):
        self.assertIsNone(self.native.context_for(self.native.target))
        self.assertEqual(self.native.validate_input({'kind': 'text', 'text': 'test'}),
                         {'kind': 'text', 'text': 'test'})

    def test_middle_and_right_buttons_preserve_controller_mapping(self):
        for button in (1, 2):
            self.native.deliver(1, self.native.target, {
                'kind': 'button', 'x': 5, 'y': 8, 'button': button, 'pressed': True})
        self.assertTrue(self.sent[-2].endswith('button 274 1\n'))
        self.assertTrue(self.sent[-1].endswith('button 273 1\n'))

    def test_barriers_belong_to_the_current_query_and_disconnect_invalidates_target(self):
        results = []
        self.native.query_scene(lambda *args: results.append(args))
        with self.assertRaises(ValueError):
            self.native.observe('scene-at 2 1 1')
        self.native.observe('scene-at 1 1 1')
        self.assertEqual(results, [(1, 1)])
        self.native.query_scene(lambda *args: results.append(args))
        self.native.lost()
        self.assertIsNone(self.native.target)
        self.assertEqual(results[-1], (0, 0))
        self.assertEqual(self.native.snapshot()['state'], 'unavailable')
        with self.assertRaises(ValueError):
            self.native.bind(2)

    def test_connection_and_native_instance_are_checked_at_delivery(self):
        target = self.native.target
        operation = self.native.validate_input({'kind': 'key', 'code': 30, 'pressed': True})
        self.native.unbind(1)
        with self.assertRaises(ValueError):
            self.native.deliver(1, target, operation)
        self.native.bind(2)
        with self.assertRaises(ValueError):
            self.native.deliver(1, target, operation)
        self.native.observe('scene 2 1')
        with self.assertRaises(ValueError):
            self.native.deliver(2, target, operation)


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.left, self.right = socket.socketpair()
        self.right.settimeout(1)
        self.lines, self.loss = [], []
        self.channel = NativeChannel(self.left, Loop(), self.lines.append, lambda: self.loss.append(True))

    def tearDown(self):
        self.channel.close()
        self.right.close()

    def test_split_records_and_ordered_commands(self):
        self.right.sendall(b'scene 1')
        self.channel.read()
        self.assertEqual(self.lines, [])
        self.right.sendall(b' 1\ndamage 1 1\n')
        self.channel.read()
        self.assertEqual(self.lines, ['scene 1 1', 'damage 1 1'])
        self.channel.send('connection 1\n')
        self.channel.send('release 1\n')
        self.channel.write()
        self.assertEqual(self.right.recv(1024), b'connection 1\nrelease 1\n')

    def test_read_eof_releases_once(self):
        self.right.close()
        self.channel.read()
        self.channel.close()
        self.assertEqual(self.loss, [True])

    def test_overflow_fails_instead_of_replaying_after_backpressure(self):
        self.channel.send('x' * (128 * 1024 - 1) + '\n')
        with self.assertRaises(OSError):
            self.channel.send('key 30 1\n')
        self.assertEqual(self.loss, [True])
        self.assertEqual(self.channel.output, b'')

    def test_malformed_native_records_close_without_unbounded_storage(self):
        self.right.sendall(b'x' * 8192)
        self.channel.read()
        self.assertEqual(self.loss, [True])


if __name__ == '__main__':
    unittest.main()
