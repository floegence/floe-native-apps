"""A synthetic source for real browser-decoder qualification; never a host desktop."""
import argparse
import os
import sys
import threading
import time
from collections import deque
import gi

parser = argparse.ArgumentParser()
parser.add_argument('--helper', required=True)
parser.add_argument('--state', required=True)
parser.add_argument('--media-fd', type=int, required=True)
parser.add_argument('--profile-stages', action='store_true', help='Enable intrusive Python pad probes for diagnosis, not performance acceptance')
args = parser.parse_args()
sys.path.insert(0, args.helper)
from host_desktop_media import DesktopMedia, select_encoder
from host_desktop_wire import Writer, read_command

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
Gst.init(None)
loop = GLib.MainLoop()
media = None
generation = 0
samples = {'sent': {}, 'encode_ms': [], 'ack_ms': [], 'frames': 0, 'codec_ms': [], 'delivery_ms': [], 'capture_ms': []}
control = Writer(1, lambda _: GLib.idle_add(loop.quit), 64, 16 << 20)
video = Writer(args.media_fd, lambda _: GLib.idle_add(loop.quit), 16, 80 << 20)


class TimedMedia(DesktopMedia):
    def _captured(self, sink):
        started = time.monotonic()
        try:
            return super()._captured(sink)
        finally:
            samples['capture_ms'].append(1000 * (time.monotonic() - started))

    def _ensure_encoder(self, width, height):
        existing = self.encoding
        super()._ensure_encoder(width, height)
        if existing is not None:
            return
        self.codec_starts = deque()
        self.encoded_times = deque()
        def before(_pad, _info):
            self.codec_starts.append(time.monotonic())
            return Gst.PadProbeReturn.OK
        def after(_pad, _info):
            now = time.monotonic()
            if self.codec_starts:
                samples['codec_ms'].append(1000 * (now - self.codec_starts.popleft()))
            self.encoded_times.append(now)
            return Gst.PadProbeReturn.OK
        iterator = self.encoding.iterate_elements()
        while True:
            status, element = iterator.next()
            if status != Gst.IteratorResult.OK:
                break
            if element.get_factory().get_name() == self.encoder_name:
                element.get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, before)
                element.get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER, after)
                break


def emit_media(message, payload):
    if message['type'] == 'frame':
        now = time.monotonic()
        samples['sent'][message['frame_id']] = now
        samples['encode_ms'].append(now * 1000 - message['timestamp'] / 1000)
        if message['codec'] == 'h264' and getattr(media, 'encoded_times', None):
            samples['delivery_ms'].append(1000 * (now - media.encoded_times.popleft()))
        samples['frames'] += 1
    video.send(message, payload)


def report():
    if media:
        def percentile(values):
            return sorted(values)[min(len(values) - 1, int(len(values) * .95))] if values else None
        control.send({'type': 'diagnostics', 'generation': generation, 'frames': samples['frames'],
            'capture_changes': media.sequence, 'submitted_changes': media.encoded_sequence,
            'credit_pending': media.credit.pending, 'encode_p95_ms': percentile(samples['encode_ms']),
            'codec_p95_ms': percentile(samples['codec_ms']), 'delivery_p95_ms': percentile(samples['delivery_ms']),
            'capture_p95_ms': percentile(samples['capture_ms']),
            'paint_return_p95_ms': percentile(samples['ack_ms'])})
        samples['frames'] = 0
        for name in ('encode_ms', 'ack_ms', 'codec_ms', 'delivery_ms', 'capture_ms'):
            samples[name].clear()
    return True


GLib.timeout_add(1000, report)


def dispatch(command):
    global generation, media
    method = command['method']
    if method in ('connect', 'keyframe', 'configure'):
        if media:
            media.close()
        generation += 1
        samples.update(sent={}, encode_ms=[], ack_ms=[], frames=0, codec_ms=[], delivery_ms=[], capture_ms=[])
        picture = command.get('picture', {'mode':'clarity','max_dimension':2560,'frame_rate':60,'audio':False})
        picture['audio'] = False
        width, height = picture['max_dimension'], picture['max_dimension'] * 9 // 16
        engine = TimedMedia if args.profile_stages else DesktopMedia
        media = engine(Gst, GLib, generation, picture, select_encoder(Gst), emit_media,
            lambda code: control.send({'type':'error','code':code}))
        media.capture = media._pipeline(f'videotestsrc is-live=true pattern=smpte ! video/x-raw,format=BGRA,width={width},height={height},framerate=60/1 ! '
            'appsink name=frames max-buffers=1 drop=true emit-signals=true sync=false', 'capture')
        control.send({'type':'state','state':'active','generation':generation,'mode':'view'})
        media._start_capture()
    elif method == 'frame_ack' and media and command['generation'] == generation:
        sent = samples['sent'].get(command['frame_id'])
        if sent is not None:
            samples['ack_ms'].append(1000 * (time.monotonic() - sent))
        samples['sent'] = {key: value for key, value in samples['sent'].items() if key > command['frame_id']}
        media.acknowledge(command['frame_id'])
    elif method == 'freeze' and media:
        media.capture.set_state(Gst.State.PAUSED)
    elif method == 'disconnect':
        if media:
            media.close()
            media = None
        generation += 1
        control.send({'type':'state','state':'disconnected','generation':generation})
    return False


def read():
    try:
        while True:
            command = read_command(sys.stdin.buffer)
            if command is None:
                break
            GLib.idle_add(dispatch, command)
    finally:
        GLib.idle_add(loop.quit)


threading.Thread(target=read, daemon=True).start()
try:
    loop.run()
finally:
    if media:
        media.close()
    video.close()
    control.close()
