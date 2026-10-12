import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from host_desktop_media import DesktopMedia, equal_pixels
from host_desktop_contract import DesktopError


class PixelComparisonTests(unittest.TestCase):
    def test_identical_pixels_and_every_tail_byte_are_observed(self):
        for size in (0, 4, 8, 12, 32):
            source = bytearray(range(size))
            self.assertTrue(equal_pixels(memoryview(source), memoryview(bytes(source))))
            for index in range(size):
                changed = bytearray(source)
                changed[index] ^= 0xff
                self.assertFalse(equal_pixels(memoryview(source), memoryview(changed)))
        self.assertFalse(equal_pixels(memoryview(b'1234'), memoryview(b'12345678')))

    def test_motion_outside_probe_spans_is_never_declared_equal(self):
        source = bytes(1 << 20)
        for index in (129, 4001, 65535, 500123, len(source) - 129):
            changed = bytearray(source)
            changed[index] = 1
            self.assertFalse(equal_pixels(memoryview(source), memoryview(changed)))


class SchedulerTests(unittest.TestCase):
    def test_software_codec_selection_is_explicit_and_hardware_h264_stays_preferred(self):
        loop = SimpleNamespace(timeout_add=lambda *_: 1)
        for encoder, offer, selected in (
                ('x264enc', ['h264'], 'x264enc'),
                ('x264enc', ['vp9', 'h264'], 'vp9enc'),
                ('nvidia-nvenc', ['vp9', 'h264'], 'nvidia-nvenc'),
                ('nvidia-nvenc', ['vp9'], 'vp9enc')):
            with self.subTest(encoder=encoder, offer=offer), patch('host_desktop_media.select_encoder', return_value=('vp9enc', '')) as choose:
                media = DesktopMedia(None, loop, 1, {'frame_rate': 60, 'codecs': offer}, (encoder, ''), lambda *_: None, lambda *_: None)
                self.assertEqual(media.encoder_name, selected)
                self.assertEqual(choose.call_count, int(selected != encoder))

    def test_vp9_reference_frames_keep_identity_without_avc_configuration(self):
        callbacks, outputs, failures = [], [], []
        loop = SimpleNamespace(timeout_add=lambda *_: 1, idle_add=lambda callback: callbacks.append(callback) or 1)
        gst = SimpleNamespace(FlowReturn=SimpleNamespace(OK='ok'), BufferFlags=SimpleNamespace(DELTA_UNIT=1))
        media = DesktopMedia(gst, loop, 1, {'frame_rate': 60, 'codecs': ['vp9']}, ('vp9enc', ''),
            lambda *args: outputs.append(args), failures.append)
        media.encoding_size = (1920, 1280, 1920, 1280)
        buffer = Mock()
        buffer.get_size.return_value = 3
        buffer.extract_dup.return_value = b'vp9'
        sample, sink = Mock(), Mock()
        sink.emit.return_value = sample
        sample.get_buffer.return_value = buffer
        for key in (True, False):
            media.submitted.append((media.credit.reserve(), 100))
            buffer.has_flags.return_value = not key
            media._encoded(sink, 1)
            while callbacks:
                callbacks.pop(0)()
        self.assertEqual(failures, [])
        self.assertEqual([m['frame_id'] for m, _ in outputs], [1, 2])
        self.assertEqual(outputs[0][0]['profile'], 'vp09.00.51.08')
        self.assertNotIn('profile', outputs[1][0])
        self.assertNotIn('description', outputs[0][0])
        self.assertEqual(outputs[0][0]['codec'], 'vp9')

    def test_input_dimensions_require_live_capture_and_ignore_encoder_resize(self):
        loop = SimpleNamespace(timeout_add=lambda *_: 1)
        media = DesktopMedia(None, loop, 1, {'frame_rate': 60}, ('fixture', ''), lambda *_: None, lambda *_: None)
        with self.assertRaisesRegex(DesktopError, 'DESKTOP_NOT_ACTIVE'):
            media.input_size()
        media.latest = (object(), 1920, 1280)
        media.encoding_size = (1920, 1280, 1600, 1066)
        self.assertEqual(media.input_size(), (1920, 1280))
        media.target_valid = False
        with self.assertRaisesRegex(DesktopError, 'DESKTOP_NOT_ACTIVE'):
            media.input_size()
        media.target_valid = True
        media.closed = True
        with self.assertRaisesRegex(DesktopError, 'DESKTOP_NOT_ACTIVE'):
            media.input_size()

    def test_interaction_defers_refinement_until_pixels_and_input_are_idle(self):
        loop = SimpleNamespace(timeout_add=lambda *_: 1)
        media = DesktopMedia(None, loop, 1, {'frame_rate': 60}, ('fixture', ''), lambda *_: None, lambda *_: None)
        media.changed_at = 10
        with patch('host_desktop_media.time.monotonic', return_value=10.2):
            self.assertTrue(media._settled())
            media.interacted()
            self.assertFalse(media._settled())
        with patch('host_desktop_media.time.monotonic', return_value=10.99):
            self.assertFalse(media._settled())
        with patch('host_desktop_media.time.monotonic', return_value=11.21):
            self.assertTrue(media._settled())
        media.changed_at = 11.2
        with patch('host_desktop_media.time.monotonic', return_value=11.21):
            self.assertFalse(media._settled())

    def test_lost_pipewire_source_invalidates_target_and_requests_display_reconnect(self):
        callbacks, failures = [], []
        loop = SimpleNamespace(timeout_add=lambda *_: 1, idle_add=lambda callback, *args: callbacks.append(lambda: callback(*args)))
        gst = SimpleNamespace(ResourceError=SimpleNamespace(quark=lambda: 5, FAILED=1))
        media = DesktopMedia(gst, loop, 1, {'frame_rate': 60}, ('fixture', ''), lambda *_: None, failures.append)
        source, pipeline, error = Mock(), Mock(), Mock()
        media.capture = pipeline
        pipeline.get_by_name.return_value = source
        error.matches.side_effect = lambda domain, code: (domain, code) == (5, 1)
        message = SimpleNamespace(src=source, parse_error=lambda: (error, 'fixture diagnostics'))
        media._pipeline_error(pipeline, message, 'capture')
        self.assertFalse(media.target_valid)
        for callback in callbacks:
            callback()
        self.assertEqual(failures, ['DISPLAY_STREAM_LOST'])

    def test_other_media_errors_do_not_request_display_reconnect(self):
        for role, source_matches, resource_matches in (('encoding', True, True), ('audio', True, True),
                                                      ('capture', False, True), ('capture', True, False)):
            with self.subTest(role=role, source_matches=source_matches, resource_matches=resource_matches):
                callbacks, failures = [], []
                loop = SimpleNamespace(timeout_add=lambda *_: 1, idle_add=lambda callback, *args: callbacks.append(lambda: callback(*args)))
                gst = SimpleNamespace(ResourceError=SimpleNamespace(quark=lambda: 5, FAILED=1))
                media = DesktopMedia(gst, loop, 1, {'frame_rate': 60}, ('fixture', ''), lambda *_: None, failures.append)
                pipeline, source, error = Mock(), Mock(), Mock()
                setattr(media, role, pipeline)
                pipeline.get_by_name.return_value = source if source_matches else Mock()
                error.matches.return_value = resource_matches
                media._pipeline_error(pipeline, SimpleNamespace(src=source, parse_error=lambda: (error, '')), role)
                for callback in callbacks:
                    callback()
                self.assertEqual(failures, ['MEDIA_PIPELINE_FAILED'])

    def test_retired_pipeline_error_does_not_revoke_current_capture(self):
        loop = SimpleNamespace(timeout_add=lambda *_: 1)
        media = DesktopMedia(None, loop, 2, {'frame_rate': 60}, ('fixture', ''), lambda *_: None, lambda *_: None)
        media.encoding = Mock()
        media._pipeline_error(Mock(), Mock(), 'encoding')
        self.assertTrue(media.target_valid)

    def test_recovery_keeps_capture_and_rejects_queued_old_encoder_output(self):
        callbacks, outputs = [], []
        loop = SimpleNamespace(timeout_add=lambda *_: 1, idle_add=lambda callback: callbacks.append(callback) or len(callbacks))
        gst = SimpleNamespace(State=SimpleNamespace(NULL='null'), FlowReturn=SimpleNamespace(OK='ok'), BufferFlags=SimpleNamespace(DELTA_UNIT=1))
        media = DesktopMedia(gst, loop, 1, {'frame_rate': 60}, ('fixture', ''), lambda *args: outputs.append(args), lambda *_: None)
        capture, encoding = Mock(), Mock()
        media.capture, media.encoding = capture, encoding
        media.encoding_size = (2, 2, 2, 2)
        media.latest = ('captured', 2, 2)
        for _ in range(4):
            media.submitted.append((media.credit.reserve(), 100))
        media.encoding_busy = True
        buffer = Mock()
        buffer.get_size.return_value = 4
        buffer.extract_dup.return_value = b'\x01\x42\x00\x1e'
        buffer.has_flags.return_value = False
        sample, sink = Mock(), Mock()
        sink.emit.return_value = sample
        sample.get_buffer.return_value = buffer
        sample.get_caps.return_value.get_structure.return_value.get_value.return_value = buffer
        media._encoded(sink, 1)
        media.recover(2)
        self.assertEqual(encoding.set_state.call_args.args, ('null',))
        capture.set_state.assert_not_called()
        self.assertIs(media.capture, capture)
        self.assertEqual(media.latest, ('captured', 2, 2))
        self.assertEqual(media.credit.pending, 0)
        self.assertFalse(media.encoding_busy)
        media.submitted.append((media.credit.reserve(), 200))
        callbacks[0]()
        self.assertEqual(list(media.submitted), [(1, 200)])
        self.assertEqual(outputs, [])

    def test_repeated_receipts_keep_one_pending_producer_and_close_cancels_it(self):
        class Loop:
            def __init__(self):
                self.next = 0
                self.pending = {}
            def timeout_add(self, delay, callback):
                self.next += 1
                self.pending[self.next] = (delay, callback)
                return self.next
            def idle_add(self, callback):
                return self.timeout_add(0, callback)
            def source_remove(self, source):
                del self.pending[source]
        loop = Loop()
        media = DesktopMedia(None, loop, 1, {'frame_rate': 60}, ('fixture', ''), lambda *_: None, lambda *_: None)
        frame = media.credit.reserve()
        media._schedule_produce(12)
        for _ in range(100):
            media.acknowledge(frame)
            media._schedule_produce()
        self.assertEqual(len(loop.pending), 2)  # Refinement observer and one producer.
        self.assertEqual(loop.pending[media.produce_source][0], 12)
        media.close()
        self.assertEqual(loop.pending, {})


if __name__ == '__main__':
    unittest.main()
