"""Low-delay desktop media with bounded pre-encode and encoded work.

GStreamer owns PipeWire acquisition and codecs; XCB owns authenticated X11
acquisition. Only a latest unencoded sample may be replaced; already encoded
H.264 references are delivered in order until reset.
"""
import base64
import ctypes
from collections import deque
import math
import threading
import time

from host_desktop_contract import DesktopError, FrameCredit, capture_size


class _BufferView(ctypes.Structure):
    # CPython's stable Py_buffer ABI. The pinned interpreter owns both exports;
    # no guessed GstBuffer address or native object layout is accessed.
    _fields_ = [('buf', ctypes.c_void_p), ('obj', ctypes.c_void_p),
                ('len', ctypes.c_ssize_t), ('itemsize', ctypes.c_ssize_t),
                ('readonly', ctypes.c_int), ('ndim', ctypes.c_int),
                ('format', ctypes.c_void_p), ('shape', ctypes.c_void_p),
                ('strides', ctypes.c_void_p), ('suboffsets', ctypes.c_void_p), ('internal', ctypes.c_void_p)]


_get_buffer = ctypes.pythonapi.PyObject_GetBuffer
_get_buffer.argtypes = [ctypes.py_object, ctypes.POINTER(_BufferView), ctypes.c_int]
_get_buffer.restype = ctypes.c_int
_release_buffer = ctypes.pythonapi.PyBuffer_Release
_release_buffer.argtypes = [ctypes.POINTER(_BufferView)]
_release_buffer.restype = None
_memcmp = ctypes.CDLL(None).memcmp
_memcmp.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
_memcmp.restype = ctypes.c_int


def equal_pixels(left, right):
    """Compare mapped bytes without copies and release the GIL during memcmp."""
    a, b = _BufferView(), _BufferView()
    _get_buffer(left, ctypes.byref(a), 0)
    try:
        _get_buffer(right, ctypes.byref(b), 0)
        try:
            if a.len != b.len:
                return False
            # Discover distributed motion before walking a large unchanged
            # prefix. These probes only prove inequality; equality still requires
            # every byte, including changes outside all probe spans.
            if a.len >= 8192:
                for index in range(32):
                    offset = index * (a.len - 128) // 31
                    if _memcmp(a.buf + offset, b.buf + offset, 128):
                        return False
            return not a.len or _memcmp(a.buf, b.buf, a.len) == 0
        finally:
            _release_buffer(ctypes.byref(b))
    finally:
        _release_buffer(ctypes.byref(a))


ENCODERS = (
    ('vah264enc', 'vah264enc rate-control=cbr key-int-max=120 bitrate=16000'),
    ('vaapih264enc', 'vaapih264enc rate-control=cbr keyframe-period=120 bitrate=16000'),
    ('nvh264enc', 'nvh264enc zerolatency=true bframes=0 gop-size=120 bitrate=16000'),
    ('x264enc', 'x264enc tune=zerolatency speed-preset=ultrafast bitrate=16000 key-int-max=120 bframes=0 threads=4'),
    ('openh264enc', 'openh264enc complexity=low rate-control=bitrate bitrate=16000000 multi-thread=4 gop-size=120'),
)


def select_encoder(Gst):
    """Exercise a test pattern before selecting a codec; never read the desktop."""
    for name, specification in ENCODERS:
        if not Gst.ElementFactory.find(name):
            continue
        pipeline = None
        try:
            pipeline = Gst.parse_launch('videotestsrc num-buffers=2 ! video/x-raw,width=64,height=64,framerate=60/1 ! '
                'videoconvert ! video/x-raw,format=I420 ! ' + specification + ' ! h264parse ! fakesink')
            pipeline.set_state(Gst.State.PLAYING)
            message = pipeline.get_bus().timed_pop_filtered(3 * Gst.SECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if message is not None and message.type == Gst.MessageType.EOS:
                return name, specification
        except Exception:
            # A failed capability candidate is never used for real capture.
            continue
        finally:
            if pipeline:
                pipeline.set_state(Gst.State.NULL)
    raise DesktopError('VIDEO_ENCODER_UNAVAILABLE')


class DesktopMedia:
    def __init__(self, Gst, GLib, generation, picture, encoder, emit, failed):
        self.Gst, self.GLib = Gst, GLib
        self.generation, self.picture = generation, picture
        self.encoder_name, self.encoder_spec = encoder
        self.emit, self.failed = emit, failed
        self.credit = FrameCredit()
        self.capture = self.encoding = self.audio = None
        self.x11_capture = None
        self.pixel_format = 'BGRA'
        self.source = None
        self.closed = False
        self.target_valid = True
        self.lock = threading.Lock()
        self.latest = None
        self.sequence = self.encoded_sequence = self.refined_sequence = 0
        self.changed_at = 0
        self.encoding_size = None
        self.submitted = deque()
        self.encoding_busy = False
        self.produce_source = None
        self.refining = False
        self.next_encoded = 0
        self.audio_pending = 0
        self.deadline = GLib.timeout_add(40, self._tick)

    def _pipeline(self, description):
        pipeline = self.Gst.parse_launch(description)
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message::error', lambda *_: self._fail('MEDIA_PIPELINE_FAILED'))
        return pipeline

    def start_pipewire(self, fd, node):
        self.capture = self._pipeline('pipewiresrc name=desktop do-timestamp=true ! videoconvert ! '
            'video/x-raw,format=BGRA ! appsink name=frames max-buffers=1 drop=true emit-signals=true sync=false')
        source = self.capture.get_by_name('desktop')
        source.set_property('fd', fd)
        source.set_property('path', str(node))
        self._start_capture()

    def start_x11(self, display, rectangle):
        from host_desktop_xcapture import X11Capture
        self.pixel_format = 'BGRx'
        self.x11_capture = X11Capture(self.Gst, display, rectangle, self.picture['frame_rate'],
                                      self._changed, self._fail, equal_pixels)
        self._start_audio()

    def _start_capture(self):
        self.capture.get_by_name('frames').connect('new-sample', self._captured)
        if self.capture.set_state(self.Gst.State.PLAYING) == self.Gst.StateChangeReturn.FAILURE:
            raise DesktopError('CAPTURE_UNAVAILABLE')
        self._start_audio()

    def _start_audio(self):
        if self.picture.get('audio'):
            self.audio = self._pipeline('pulsesrc device=@DEFAULT_MONITOR@ do-timestamp=true ! audioconvert ! audioresample ! '
                'audio/x-raw,rate=48000,channels=2 ! opusenc bitrate=128000 frame-size=20 audio-type=restricted-lowdelay ! '
                'appsink name=audio max-buffers=4 drop=true emit-signals=true sync=false')
            self.audio.get_by_name('audio').connect('new-sample', self._audio_sample)
            if self.audio.set_state(self.Gst.State.PLAYING) == self.Gst.StateChangeReturn.FAILURE:
                raise DesktopError('AUDIO_UNAVAILABLE')

    def _captured(self, sink):
        sample = sink.emit('pull-sample')
        if sample is None or self.closed:
            return self.Gst.FlowReturn.OK
        caps = sample.get_caps().get_structure(0)
        width, height = caps.get_value('width'), caps.get_value('height')
        if not 2 <= width <= 32768 or not 2 <= height <= 32768:
            self._fail('DISPLAY_SIZE_UNSUPPORTED')
            return self.Gst.FlowReturn.ERROR
        buffer = sample.get_buffer()
        # Packed BGRA is negotiated explicitly. Reject unexpected padding rather
        # than misinterpreting it as a new coordinate or image surface.
        if buffer.get_size() != width * height * 4:
            self._fail('CAPTURE_LAYOUT_UNSUPPORTED')
            return self.Gst.FlowReturn.ERROR
        with self.lock:
            previous = self.latest
        if previous and previous[1:] != (width, height):
            self.target_valid = False
            self._fail('DISPLAY_GEOMETRY_CHANGED')
            return self.Gst.FlowReturn.ERROR
        if previous and previous[1:] == (width, height):
            ok, current = buffer.map(self.Gst.MapFlags.READ)
            prior_ok, prior = previous[0].map(self.Gst.MapFlags.READ)
            try:
                if ok and prior_ok and equal_pixels(current.data, prior.data):
                    return self.Gst.FlowReturn.OK
            finally:
                if ok:
                    buffer.unmap(current)
                if prior_ok:
                    previous[0].unmap(prior)
        self._changed(buffer, width, height, time.monotonic())
        return self.Gst.FlowReturn.OK

    def _changed(self, buffer, width, height, timestamp):
        with self.lock:
            if self.closed:
                return
            self.latest = (buffer, width, height)
            self.sequence += 1
            self.changed_at = timestamp
        self._schedule_produce()

    def _ensure_encoder(self, width, height):
        output_width, output_height = capture_size(width, height, self.picture['max_dimension'], self.picture.get('native_pixels', False))
        size = (width, height, output_width, output_height)
        if self.encoding_size == size:
            return
        if self.encoding is not None:
            raise DesktopError('DISPLAY_GEOMETRY_CHANGED')
        fps = self.picture['frame_rate']
        bitrate = {'auto': 16000, 'clarity': 24000, 'smooth': 12000, 'data': 6000}[self.picture['mode']]
        specification = self.encoder_spec.replace('bitrate=16000000', 'bitrate=' + str(bitrate * 1000)) if self.encoder_name == 'openh264enc' else self.encoder_spec.replace('bitrate=16000', 'bitrate=' + str(bitrate))
        self.encoding = self._pipeline('appsrc name=source is-live=true format=time do-timestamp=true block=false ! '
            'videoconvertscale n-threads=4 ! video/x-raw,format=I420,width=' + str(output_width) + ',height=' + str(output_height) + ' ! '
            + specification + ' ! h264parse config-interval=-1 ! video/x-h264,stream-format=avc,alignment=au ! '
            'appsink name=encoded max-buffers=4 drop=false emit-signals=true sync=false')
        self.source = self.encoding.get_by_name('source')
        self.source.set_property('caps', self.Gst.Caps.from_string(
            f'video/x-raw,format={self.pixel_format},width={width},height={height},framerate={fps}/1'))
        self.source.set_property('max-bytes', width * height * 4 * 4)
        self.encoding.get_by_name('encoded').connect('new-sample', self._encoded, self.generation)
        if self.encoding.set_state(self.Gst.State.PLAYING) == self.Gst.StateChangeReturn.FAILURE:
            raise DesktopError('VIDEO_ENCODER_UNAVAILABLE')
        self.encoding_size = size

    def _schedule_produce(self, delay=0):
        with self.lock:
            if self.closed or self.produce_source is not None:
                return
            def scheduled():
                with self.lock:
                    self.produce_source = None
                return self.produce()
            self.produce_source = self.GLib.timeout_add(delay, scheduled) if delay else self.GLib.idle_add(scheduled)

    def produce(self):
        if self.closed:
            return False
        with self.lock:
            latest, sequence, captured_at = self.latest, self.sequence, self.changed_at
        if (not latest or self.encoding_busy or sequence == self.encoded_sequence or
                self.credit.pending >= self.credit.capacity):
            return False
        now = time.monotonic()
        if now + 0.0005 < self.next_encoded:
            self._schedule_produce(max(1, math.ceil(1000 * (self.next_encoded - now))))
            return False
        try:
            captured, width, height = latest
            self._ensure_encoder(width, height)
            frame = self.credit.reserve()
            self.encoding_busy = True
            # Keep the cadence anchored across scheduler jitter. Restarting the
            # period at every late callback systematically loses frame rate.
            self.next_encoded = max(now, self.next_encoded + 1 / self.picture['frame_rate'])
            self.encoded_sequence = sequence
            self.submitted.append((frame, int(captured_at * 1_000_000)))
            buffer = self.Gst.Buffer.new()
            if not buffer.copy_into(captured, self.Gst.BufferCopyFlags.MEMORY, 0, captured.get_size()):
                raise DesktopError('CAPTURE_LAYOUT_UNSUPPORTED')
            buffer.duration = self.Gst.SECOND // self.picture['frame_rate']
            if self.source.emit('push-buffer', buffer) != self.Gst.FlowReturn.OK:
                raise DesktopError('VIDEO_ENCODER_FAILED')
        except DesktopError as error:
            self._fail(error.code)
        except Exception:
            self._fail('VIDEO_ENCODER_FAILED')
        return False

    def _encoded(self, sink, generation):
        sample = sink.emit('pull-sample')
        if sample is None or self.closed or generation != self.generation:
            return self.Gst.FlowReturn.OK
        buffer = sample.get_buffer()
        caps = sample.get_caps().get_structure(0)
        description = caps.get_value('codec_data')
        config = description.extract_dup(0, description.get_size()) if description is not None else b''
        data = buffer.extract_dup(0, buffer.get_size())
        key = not buffer.has_flags(self.Gst.BufferFlags.DELTA_UNIT)
        def publish():
            if self.closed or generation != self.generation:
                return False
            if not self.submitted or len(config) < 4:
                self._fail('VIDEO_PROTOCOL_INVALID')
                return False
            frame, timestamp = self.submitted.popleft()
            self.encoding_busy = False
            metadata = {'type': 'frame', 'generation': self.generation, 'frame_id': frame,
                'timestamp': timestamp, 'width': self.encoding_size[2], 'height': self.encoding_size[3],
                'codec': 'h264', 'key': key, 'encoder': self.encoder_name}
            if key:
                metadata.update(description=base64.b64encode(config).decode(), profile='avc1.' + config[1:4].hex())
            self.emit(metadata, data)
            self._schedule_produce()
            return False
        self.GLib.idle_add(publish)
        return self.Gst.FlowReturn.OK

    def _audio_sample(self, sink):
        sample = sink.emit('pull-sample')
        if sample is None or self.closed:
            return self.Gst.FlowReturn.OK
        buffer = sample.get_buffer()
        if buffer.has_flags(self.Gst.BufferFlags.HEADER):
            return self.Gst.FlowReturn.OK
        data = buffer.extract_dup(0, buffer.get_size())
        with self.lock:
            if self.audio_pending >= 4:
                return self.Gst.FlowReturn.OK
            self.audio_pending += 1
        timestamp = time.monotonic_ns() // 1000
        generation = self.generation
        def publish():
            with self.lock:
                self.audio_pending -= 1
            if not self.closed and self.generation == generation:
                self.emit({'type': 'audio', 'generation': self.generation, 'codec': 'opus',
                    'sample_rate': 48000, 'channels': 2, 'timestamp': timestamp}, data)
            return False
        self.GLib.idle_add(publish)
        return self.Gst.FlowReturn.OK

    def acknowledge(self, frame):
        self.credit.acknowledge(frame)
        self._schedule_produce()

    def recover(self, generation):
        # Stop only the encoder before changing generation. Output already
        # queued on GLib keeps its original generation and cannot consume new
        # credits. The next encoder starts with a complete keyframe.
        if self.encoding:
            self.encoding.set_state(self.Gst.State.NULL)
            self.encoding.get_bus().remove_signal_watch()
        self.encoding = self.source = self.encoding_size = None
        self.generation = generation
        self.submitted.clear()
        self.credit = FrameCredit()
        self.encoding_busy = self.refining = False
        self.encoded_sequence = self.refined_sequence = self.next_encoded = 0
        self._schedule_produce()

    def _tick(self):
        if self.closed:
            return False
        with self.lock:
            latest, sequence, captured_at = self.latest, self.sequence, self.changed_at
            settled = time.monotonic() - self.changed_at >= 0.15
        if latest and settled and not self.credit.pending and not self.refining and self.refined_sequence != sequence:
            self.refining = True
            generation = self.generation
            def refine():
                try:
                    captured, width, height = latest
                    size = capture_size(width, height, self.picture['max_dimension'], self.picture.get('native_pixels', False))
                    pipeline = self.Gst.parse_launch('appsrc name=source format=time ! videoscale ! videoconvert ! '
                        f'video/x-raw,format=RGB,width={size[0]},height={size[1]} ! '
                        'pngenc compression-level=1 ! appsink name=png sync=false')
                    try:
                        source = pipeline.get_by_name('source')
                        source.set_property('caps', self.Gst.Caps.from_string(
                            f'video/x-raw,format={self.pixel_format},width={width},height={height},framerate=1/1'))
                        pipeline.set_state(self.Gst.State.PLAYING)
                        source.emit('push-buffer', captured)
                        source.emit('end-of-stream')
                        sample = pipeline.get_by_name('png').emit('try-pull-sample', 2 * self.Gst.SECOND)
                        if sample is None:
                            raise DesktopError('REFINEMENT_FAILED')
                        buffer = sample.get_buffer()
                        pixels = buffer.extract_dup(0, buffer.get_size())
                    finally:
                        pipeline.set_state(self.Gst.State.NULL)
                except Exception:
                    self.GLib.idle_add(lambda: self._fail('REFINEMENT_FAILED') if self.generation == generation else False)
                    return
                def publish():
                    if self.closed or self.generation != generation:
                        return False
                    self.refining = False
                    if self.closed or self.sequence != sequence or self.credit.pending:
                        return False
                    self.refined_sequence = sequence
                    frame = self.credit.reserve()
                    self.emit({'type': 'frame', 'codec': 'png', 'key': True, 'generation': self.generation,
                        'frame_id': frame, 'width': size[0], 'height': size[1], 'timestamp': int(captured_at * 1_000_000)}, pixels)
                    return False
                self.GLib.idle_add(publish)
            threading.Thread(target=refine, name='floe-desktop-refine', daemon=True).start()
        return True

    def _fail(self, code):
        if not self.closed:
            self.GLib.idle_add(self.failed, code)
        return False

    def close(self):
        with self.lock:
            self.closed = True
            if self.produce_source is not None:
                self.GLib.source_remove(self.produce_source)
                self.produce_source = None
        self.GLib.source_remove(self.deadline)
        if self.x11_capture:
            self.x11_capture.close()
            self.x11_capture = None
        for pipeline in (self.capture, self.encoding, self.audio):
            if pipeline:
                pipeline.set_state(self.Gst.State.NULL)
                pipeline.get_bus().remove_signal_watch()
        self.capture = self.encoding = self.audio = None
        self.latest = None
        self.submitted.clear()
