"""View-only real-host start/stop qualification; never saves desktop pixels."""
import argparse
import json
import queue
import subprocess
import threading
import time


def qualify(binary, during_capture):
    child = subprocess.Popen([binary, 'session'], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    messages = queue.Queue()
    request = 0
    states = []
    errors = []

    def read():
        for line in child.stdout:
            messages.put(json.loads(line))
        messages.put(None)

    def send(method, **values):
        nonlocal request
        request += 1
        child.stdin.write((json.dumps(dict(version=1, id=request, method=method, **values)) + '\n').encode())
        child.stdin.flush()

    threading.Thread(target=read, daemon=True).start()
    picture = dict(mode='clarity', max_dimension=1920, frame_rate=60, audio=False)
    try:
        send('connect', mode='view', picture=picture)
        if during_capture:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                message = messages.get(timeout=max(.01, deadline - time.monotonic()))
                if message is None:
                    raise RuntimeError('helper ended before capture')
                if message['type'] == 'error':
                    raise RuntimeError(message['code'])
                if message['type'] == 'state':
                    states.append(message['state'])
                if message['type'] == 'frame':
                    send('configure', generation=message['generation'], picture=dict(picture, max_dimension=2560))
                    break
            else:
                raise RuntimeError('capture did not produce pixels')
        send('disconnect')
        child.stdin.close()
        child.wait(timeout=10)
        while True:
            message = messages.get(timeout=2)
            if message is None:
                break
            if message['type'] == 'state':
                states.append(message['state'])
            elif message['type'] == 'error':
                errors.append(message['code'])
        if child.returncode != 0 or errors or states[-1:] != ['disconnected']:
            raise RuntimeError('native lifecycle failed: ' + repr((child.returncode, states, errors)))
        retired = states.index('disconnected')
        if any(state != 'disconnected' for state in states[retired:]):
            raise RuntimeError('retired capture restarted')
        return {'scenario': 'close_during_reconfigure' if during_capture else 'close_during_start',
                'exit': child.returncode, 'states': states}
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    args = parser.parse_args()
    print(json.dumps({'measurement': 'real view-only capture lifecycle',
                      'results': [qualify(args.binary, active) for active in (False, True)]}))
