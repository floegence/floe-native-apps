"""Real public PipeWire ABI qualification on a private synthetic daemon.

No portal requests, desktop pixels, host input or user runtime are accessed.
Run with system Python; --helper selects the freshly installed component.
"""
import argparse
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import time


def capture(args):
    sys.path.insert(0, str(Path(args.helper).resolve()))
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    from host_desktop_pipewire import PipeWireCapture
    from host_desktop_media import equal_pixels
    Gst.init(None)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(os.environ['PIPEWIRE_RUNTIME_DIR'] + '/pipewire-0')
    try:
        for mode in ('control', 'view', 'control'):
            frames, shapes, errors = [], [], []
            def frame(buffer, width, height, timestamp):
                raw = buffer.extract_dup(0, buffer.get_size())
                baseline = bytes([64, 64, 64, 255]) * width * height
                frames.append({'size': [width, height], 'background': raw == baseline})
            stream = PipeWireCapture(Gst, connection.fileno(), args.node, 60,
                frame, errors.append, equal_pixels,
                (lambda *shape: shapes.append(shape[:4])) if mode == 'control' else None)
            try:
                print(json.dumps({'ready': mode}), flush=True)
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline and not errors:
                    time.sleep(0.02)
                if errors or not frames:
                    raise RuntimeError({'errors': errors, 'frames': len(frames)})
                if mode == 'control':
                    assert len(frames) == 1 and frames[0]['background'] and len(shapes) > 1
                else:
                    assert len(frames) > 1 and not any(f['background'] for f in frames) and not shapes
                print(json.dumps({'mode': mode, 'frames': len(frames), 'shapes': len(shapes),
                    'size': frames[0]['size'], 'errors': errors}), flush=True)
            finally:
                stream.close()
    finally:
        connection.close()


def run(args):
    helper = str(Path(args.helper).resolve())
    with tempfile.TemporaryDirectory(prefix='floe-pipewire-') as directory:
        # The private socket is the only possible graph; never invoke pw-link
        # with the login session's environment or touch its existing daemon.
        env = dict(os.environ, XDG_RUNTIME_DIR=directory, PIPEWIRE_RUNTIME_DIR=directory,
            PIPEWIRE_REMOTE='pipewire-0')
        processes = []
        try:
            with open(Path(directory, 'daemon.log'), 'w') as log:
                daemon = subprocess.Popen(['pipewire'], env=env, stdout=log, stderr=log)
            processes.append(daemon)
            deadline = time.monotonic() + 10
            while not Path(directory, 'pipewire-0').exists():
                if daemon.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(Path(directory, 'daemon.log').read_text())
                time.sleep(0.05)
            source = subprocess.Popen([str(Path(args.source).resolve())], env=env,
                stdout=subprocess.PIPE, text=True, bufsize=1)
            processes.append(source)
            if not select.select([source.stdout], [], [], 10)[0]:
                raise RuntimeError('Synthetic source did not register')
            node = int(source.stdout.readline())
            client = subprocess.Popen([helper + '/python3', str(Path(__file__).resolve()),
                '--helper', helper, '--node', str(node)], env=env, stdout=subprocess.PIPE,
                text=True, bufsize=1)
            processes.append(client)
            results = []
            deadline = time.monotonic() + 30
            while client.poll() is None:
                if time.monotonic() > deadline:
                    raise RuntimeError('Capture timed out')
                if not select.select([client.stdout], [], [], 0.1)[0]:
                    continue
                line = client.stdout.readline()
                if not line:
                    break
                event = json.loads(line)
                if 'ready' in event:
                    linked = False
                    for _ in range(100):
                        graph = json.loads(subprocess.check_output(['pw-dump'], env=env, text=True))
                        ports = [item for item in graph if item['type'] == 'PipeWire:Interface:Port']
                        outputs = [p['id'] for p in ports if str(p['info']['props'].get('node.id')) == str(node)]
                        inputs = [p['id'] for p in ports if p['info']['props'].get('port.direction') == 'in']
                        if len(outputs) == len(inputs) == 1:
                            subprocess.run(['pw-link', str(outputs[0]), str(inputs[0])], env=env, check=True)
                            linked = True
                            break
                        time.sleep(0.02)
                    if not linked:
                        raise RuntimeError('Isolated capture port not found')
                else:
                    results.append(event)
                    print(json.dumps(event), flush=True)
            assert client.wait(timeout=5) == 0 and [r['mode'] for r in results] == ['control', 'view', 'control']
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--helper', required=True)
    parser.add_argument('--source')
    parser.add_argument('--node', type=int)
    arguments = parser.parse_args()
    capture(arguments) if arguments.node is not None else run(arguments)
