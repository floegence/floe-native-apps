"""Exercise the production media scheduler without a real desktop or input.

Acknowledgements are synthetic sink receipts, not client paint measurements.
This fixture cannot satisfy remote-desktop performance acceptance.
"""
import argparse
import json
import time
import sys
sys.path.insert(0, sys.argv.pop(1))
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
from host_desktop_media import DesktopMedia, select_encoder

parser = argparse.ArgumentParser()
parser.add_argument('--width', type=int, default=1920)
parser.add_argument('--height', type=int, default=1080)
parser.add_argument('--seconds', type=int, default=5)
parser.add_argument('--pattern', choices=('ball', 'smpte'), default='smpte')
args = parser.parse_args()
Gst.init(None)
loop = GLib.MainLoop()
started = time.monotonic()
frames, count, sizes, failure, refinements = [], 0, [], [], []


def output(metadata, payload):
    global count
    now = time.monotonic()
    count += 1
    if metadata['codec'] == 'h264':
        frames.append(now)
        sizes.append(len(payload))
    else:
        refinements.append(now)
    GLib.idle_add(lambda: (media.acknowledge(metadata['frame_id']), False)[1])


def failed(code):
    failure.append(code)
    loop.quit()
    return False


media = DesktopMedia(Gst, GLib, 1, {'mode':'clarity', 'max_dimension':args.width, 'frame_rate':60, 'audio':False},
                     select_encoder(Gst), output, failed)
media.capture = media._pipeline(f'videotestsrc is-live=true pattern={args.pattern} ! video/x-raw,format=BGRA,width={args.width},height={args.height},framerate=60/1 ! '
    'appsink name=frames max-buffers=1 drop=true emit-signals=true sync=false', 'capture')
media._start_capture()
GLib.timeout_add_seconds(args.seconds, lambda: (loop.quit(), False)[1])
try:
    loop.run()
finally:
    media.close()
intervals = sorted(1000 * (right - left) for left, right in zip(frames, frames[1:]))
print(json.dumps({'measurement':'synthetic encode receipts, not remote painted FPS', 'encoder':media.encoder_name, 'source_pattern':args.pattern,
    'width':args.width, 'height':args.height, 'frames':len(frames), 'fps':(len(frames)-1)/(frames[-1]-frames[0]) if len(frames)>1 else 0,
    'interval_p95_ms':intervals[int(len(intervals)*.95)] if intervals else None,
    'captured_changes':media.sequence, 'submitted_changes':media.encoded_sequence, 'bytes':sum(sizes), 'errors':failure}))
raise SystemExit(1 if failure or len(frames) < 2 else 0)
