"""Synthetic installed-stack proof. Never opens a display, microphone or portal."""
import json
import ctypes
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import tempfile
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
from host_desktop_media import DesktopMedia, select_encoder

# The client-node module depends on protocol-native by soname. Finding the
# GStreamer element alone does not prove that the private loader can resolve
# this mandatory dependency. Loading it needs no desktop or portal connection.
ctypes.CDLL('libpipewire-module-client-node.so')
for library in ('libxcb.so.1', 'libxcb-shm.so.0', 'libxcb-xfixes.so.0', 'libcairo.so.2'):
    ctypes.CDLL(library)

Gst.init(None)
for name in ('pipewiresrc', 'pulsesrc', 'h264parse', 'pngenc', 'opusenc', 'opusdec', 'openh264dec'):
    if not Gst.ElementFactory.find(name):
        raise RuntimeError('required media element missing: ' + name)
encoder, specification = select_encoder(Gst)
for label, description, minimum in (
    ('video', 'videotestsrc num-buffers=8 pattern=smpte ! video/x-raw,width=320,height=180,framerate=60/1 ! '
        'videoconvert ! video/x-raw,format=I420 ! ' + specification + ' ! h264parse ! openh264dec ! '
        'videoconvert ! video/x-raw,format=BGRA ! appsink name=decoded sync=false', 320 * 180 * 4),
    ('audio', 'audiotestsrc num-buffers=8 wave=sine ! audioconvert ! audioresample ! '
        'audio/x-raw,rate=48000,channels=2 ! opusenc frame-size=20 ! opusdec ! '
        'appsink name=decoded sync=false', 1),
):
    pipeline = Gst.parse_launch(description)
    try:
        pipeline.set_state(Gst.State.PLAYING)
        sink = pipeline.get_by_name('decoded')
        sample = sink.emit('try-pull-sample', 10 * Gst.SECOND)
        if sample is None or sample.get_buffer().get_size() < minimum:
            message = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
            raise RuntimeError(label + ' round trip failed: ' + str(message.parse_error() if message else 'no pixels'))
    finally:
        pipeline.set_state(Gst.State.NULL)

# Exercise the actual bounded scheduler and static-first-frame refinement, not
# only independent encoder elements. This remains a synthetic installation test.
loop = GLib.MainLoop()
frames, errors = [], []


def output(message, payload):
    if message['type'] != 'frame':
        return
    if not payload or message['width'] != 320 or message['height'] != 180:
        errors.append('invalid synthetic frame')
        loop.quit()
        return
    frames.append((message['frame_id'], message['codec']))
    GLib.idle_add(lambda: (media.acknowledge(message['frame_id']), False)[1])
    if message['codec'] == 'png':
        if payload[:8] != b'\x89PNG\r\n\x1a\n':
            errors.append('invalid refinement')
        loop.quit()


def failed(code):
    errors.append(code)
    loop.quit()
    return False


media = DesktopMedia(Gst, GLib, 1, {'mode': 'clarity', 'max_dimension': 1920, 'frame_rate': 60, 'audio': False},
                     (encoder, specification), output, failed)
media.capture = media._pipeline('videotestsrc num-buffers=1 pattern=smpte ! '
    'video/x-raw,format=BGRA,width=320,height=180,framerate=60/1 ! '
    'appsink name=frames max-buffers=1 drop=true emit-signals=true sync=false')
timeout = GLib.timeout_add_seconds(8, lambda: failed('SYNTHETIC_FRAME_TIMEOUT'))
try:
    media._start_capture()
    loop.run()
finally:
    media.close()
    if not errors:
        GLib.source_remove(timeout)
if errors or [codec for _, codec in frames] != ['h264', 'png'] or frames[0][0] >= frames[1][0]:
    raise RuntimeError('production scheduler failed: ' + repr((errors, frames)))
# A signal must revoke and exit even if a Runtime still holds stdin open.
# An invalid command confirms startup without querying the user's desktop.
with tempfile.TemporaryDirectory(prefix='floe-desktop-selfcheck-') as state:
    reader, writer = os.pipe()
    root = Path(__file__).resolve().parent
    child = subprocess.Popen([str(root / 'python3'), str(root / 'host_desktop_helper.py'),
        '--state', state, '--media-fd', str(writer)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(writer,))
    os.close(writer)
    try:
        data = json.dumps({'version': 1, 'id': 1, 'method': 'selfcheck_readiness'}).encode()
        child.stdin.write(struct.pack('!I', len(data)) + data)
        child.stdin.flush()
        if not select.select([child.stdout], [], [], 10)[0]:
            raise RuntimeError('helper startup timed out')
        size = struct.unpack('!I', child.stdout.read(4))[0]
        if not 0 < size < 4096:
            raise RuntimeError('helper startup protocol failed')
        reply = json.loads(child.stdout.read(size))
        if reply.get('code') != 'INVALID_ARGUMENT':
            raise RuntimeError('helper startup response failed')
        child.send_signal(signal.SIGTERM)
        child.wait(timeout=4)
        if child.returncode != 0:
            raise RuntimeError('helper signal shutdown failed')
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=4)
        os.close(reader)
print(json.dumps({'video' : 'decoded', 'audio': 'decoded', 'encoder': encoder,
                  'static_frame': 'h264_then_lossless_refinement', 'signal_shutdown': 'clean'}))
