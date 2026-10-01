"""Native input receipts from a task-owned window; no desktop pixels are saved.

Receipt ACKs exercise native authority only, not end-to-end paint or latency.
The fixture denies control as soon as another application becomes active.
"""
import argparse
import json
import queue
import subprocess
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    args = parser.parse_args()
    child = subprocess.Popen([args.binary, 'fixture'], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    messages = queue.Queue()
    request, generation, frames = 0, 0, 0
    acknowledgements = set()

    def read():
        for line in child.stdout:
            messages.put(json.loads(line))
        messages.put(None)

    def send(method, **values):
        nonlocal request
        request += 1
        child.stdin.write((json.dumps(dict(version=1, id=request, method=method, **values)) + '\n').encode())
        child.stdin.flush()
        return request

    def until(predicate, timeout=10):
        nonlocal generation, frames
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = messages.get(timeout=max(.01, deadline - time.monotonic()))
            if message is None:
                raise RuntimeError('native fixture exited')
            if message['type'] == 'state':
                generation = message['generation']
            if message['type'] == 'frame':
                frames += 1
                if message['generation'] == generation:
                    acknowledgements.add(send('frame_ack', generation=message['generation'], frame_id=message['frame_id']))
            if predicate(message):
                return message
            if message['type'] == 'error':
                if message.get('id') in acknowledgements and message['code'] == 'STALE_DESKTOP':
                    acknowledgements.discard(message['id'])
                    continue
                raise RuntimeError(message['code'])
            if message['type'] == 'result':
                acknowledgements.discard(message.get('id'))
        raise RuntimeError('native receipt timeout')

    def state():
        send('fixture_status')
        return until(lambda value: value['type'] == 'fixture')

    def observe(predicate):
        deadline = time.monotonic() + 3
        while True:
            result = state()
            if predicate(result) or not result['active'] or time.monotonic() >= deadline:
                return result
            time.sleep(.05)

    threading.Thread(target=read, daemon=True).start()
    try:
        until(lambda value: value['type'] == 'fixture')
        send('fixture_focus')
        until(lambda value: value['type'] == 'fixture')
        time.sleep(1)
        if not state()['active']:
            raise RuntimeError('task fixture is not focused')
        send('connect', mode='control', picture=dict(mode='clarity', max_dimension=1920, frame_rate=60, audio=False))
        until(lambda value: value['type'] == 'frame')
        expected = 'Native 中文輸入 😀'
        send('input', generation=generation, input=dict(kind='text', text=expected))
        result = observe(lambda value: value['text'] == expected)
        if result['text'] != expected:
            raise RuntimeError(f"Unicode receipt invalid: received_length={len(result['text'])}, fixture_active={result['active']}")
        for pressed in (True, False):
            send('input', generation=generation, input=dict(kind='key', code='ArrowLeft', key='ArrowLeft', pressed=pressed))
        send('input', generation=generation, input=dict(kind='text', text='X'))
        edited = expected[:-1] + 'X' + expected[-1]
        if observe(lambda value: value['text'] == edited)['text'] != edited:
            raise RuntimeError('physical navigation did not move within the Unicode document')
        for kind in ('move', 'down', 'up'):
            send('input', generation=generation, input=dict(kind=kind, x=result['x'], y=result['y'], button=0))
        pointer_result = observe(lambda value: value['clicks'] == 1)
        if pointer_result['clicks'] != 1:
            raise RuntimeError(f"pointer receipt invalid: clicks={pointer_result['clicks']}, fixture_active={pointer_result['active']}")
        host_text = 'Floe task host clipboard 中文'
        client_text = 'Floe task client clipboard 😀'
        send('fixture_clipboard', text=host_text, expected=host_text)
        if not until(lambda value: value['type'] == 'fixture_clipboard')['matches']:
            raise RuntimeError('fixture clipboard write failed')
        clipboard_id = send('get_clipboard', generation=generation)
        if until(lambda value: value['type'] == 'clipboard' and value.get('id') == clipboard_id)['text'] != host_text:
            raise RuntimeError('host clipboard was not returned')
        send('set_clipboard', generation=generation, text=client_text)
        send('fixture_clipboard', expected=client_text)
        if not until(lambda value: value['type'] == 'fixture_clipboard')['matches']:
            raise RuntimeError('client clipboard did not reach the host')
        send('set_clipboard_sync', generation=generation, enabled=True)
        send('fixture_clipboard', text=host_text, expected=host_text)
        until(lambda value: value['type'] == 'clipboard' and value.get('text') == host_text and 'id' not in value)
        send('set_clipboard_sync', generation=generation, enabled=False)
        old_generation = generation
        send('set_mode', generation=generation, mode='view')
        until(lambda value: value['type'] == 'frame' and value['generation'] != old_generation)
        denied_id = send('input', generation=generation, input=dict(kind='text', text='must-not-arrive'))
        denied = until(lambda value: value['type'] == 'error' and value.get('id') == denied_id)
        if denied['code'] != 'VIEW_ONLY':
            raise RuntimeError('view-only authority did not reject input')
        print(json.dumps({'unicode': True, 'physical_navigation': True, 'pointer': True, 'clipboard': True,
                          'view_only': True, 'frames': frames, 'measurement': 'native fixture receipts, not painted latency'}))
    finally:
        if child.poll() is None:
            send('disconnect')
            child.stdin.close()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=5)


if __name__ == '__main__':
    main()
