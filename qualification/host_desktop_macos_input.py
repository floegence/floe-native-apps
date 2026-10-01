"""Native input receipts from a task-owned window; no desktop pixels are saved.

Receipt ACKs exercise native authority only, not end-to-end paint or latency.
The fixture denies control as soon as another application becomes active.
"""
import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    parser.add_argument('--compose-first', action='store_true', help='Complete a real host IME composition before switching to client text input')
    parser.add_argument('--separate-session', action='store_true', help='Keep the input receiver in a different process from native input delivery')
    parser.add_argument('--text-mode', choices=('text', 'paste'), default='text', help='Explicit native text or clipboard paste contract')
    parser.add_argument('--fixture-command', nargs='+', help='Task-owned external fixture executable and arguments (requires --separate-session)')
    args = parser.parse_args()
    if args.fixture_command and not args.separate_session:
        parser.error('--fixture-command requires --separate-session')
    child = subprocess.Popen(args.fixture_command or [args.binary, 'fixture'], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    native = subprocess.Popen([args.binary, 'session'], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=sys.stderr,
                              env=dict(os.environ, FLOE_QUALIFY_TARGET_PID=str(child.pid))) if args.separate_session else child
    messages = queue.Queue()
    request, generation, frames = 0, 0, 0
    acknowledgements = set()

    def read(process):
        for line in process.stdout:
            messages.put(json.loads(line))
        messages.put(None)

    def send(method, **values):
        nonlocal request
        request += 1
        target = child if method.startswith('fixture_') else native
        target.stdin.write((json.dumps(dict(version=1, id=request, method=method, **values)) + '\n').encode())
        target.stdin.flush()
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

    for process in dict.fromkeys((child, native)):
        threading.Thread(target=read, args=(process,), daemon=True).start()
    try:
        until(lambda value: value['type'] == 'fixture')
        send('fixture_focus')
        until(lambda value: value['type'] == 'fixture')
        time.sleep(1)
        if not state()['active']:
            raise RuntimeError('task fixture is not focused')
        send('connect', mode='control', picture=dict(mode='clarity', max_dimension=1920, frame_rate=60, audio=False))
        until(lambda value: value['type'] == 'result' and value.get('id') in acknowledgements)
        prefix = ''
        if args.compose_first:
            for pressed in (True, False):
                send('input', generation=generation, input=dict(kind='key', code='KeyX', key='x', pressed=pressed))
            composing = observe(lambda value: value.get('marked_text') is True)
            if not composing.get('marked_text'):
                raise RuntimeError('host IME did not create marked text; composition coverage is unavailable')
            for pressed in (True, False):
                send('input', generation=generation, input=dict(kind='key', code='Enter', key='Enter', pressed=pressed))
            completed = observe(lambda value: not value.get('marked_text'))
            if completed.get('marked_text'):
                raise RuntimeError('the host IME did not finish its composition')
            prefix = completed['text']
        committed = 'Native 中文輸入 😀'
        expected = prefix + committed
        if args.text_mode == 'paste':
            send('fixture_clipboard', text='Task clipboard before paste', expected='Task clipboard before paste')
            until(lambda value: value['type'] == 'fixture_clipboard')
            send('input', generation=generation, input=dict(kind='key', code='ShiftLeft', key='Shift', pressed=True))
            denied_id = send('input', generation=generation, input=dict(kind='paste', text='must-not-replace-clipboard'))
            denied = until(lambda value: value['type'] == 'error' and value.get('id') == denied_id)
            if denied['code'] != 'INPUT_KEYS_HELD':
                raise RuntimeError('paste did not reject a held modifier')
            send('fixture_clipboard', expected='Task clipboard before paste')
            if not until(lambda value: value['type'] == 'fixture_clipboard')['matches']:
                raise RuntimeError('rejected paste changed the clipboard')
            retired_generation = generation
            send('keyframe', generation=generation)
            until(lambda value: value['type'] == 'result' and value.get('id') in acknowledgements
                  and value.get('generation') != retired_generation)
            denied_id = send('input', generation=retired_generation, input=dict(kind='paste', text='must-not-arrive'))
            denied = until(lambda value: value['type'] == 'error' and value.get('id') == denied_id)
            if denied['code'] != 'STALE_DESKTOP':
                raise RuntimeError('retired generation retained paste authority')
        send('input', generation=generation, input=dict(kind=args.text_mode, text=committed))
        result = observe(lambda value: value['text'] == expected and (not args.compose_first or not value.get('marked_text')))
        if result['text'] != expected:
            raise RuntimeError(f"Unicode receipt invalid in task-owned document: {result!r}")
        if args.compose_first and result.get('marked_text'):
            raise RuntimeError(f'client commit left stale host marked text: {result!r}')
        for pressed in (True, False):
            send('input', generation=generation, input=dict(kind='key', code='ArrowLeft', key='ArrowLeft', pressed=pressed))
        send('input', generation=generation, input=dict(kind=args.text_mode, text='X'))
        edited = expected[:-1] + 'X' + expected[-1]
        navigation_result = observe(lambda value: value['text'] == edited)
        if navigation_result['text'] != edited:
            raise RuntimeError(f"physical navigation did not move within the Unicode document: {navigation_result!r}")
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
        clipboard_id = send('set_clipboard', generation=generation, text=client_text)
        until(lambda value: value['type'] == 'result' and value.get('id') == clipboard_id)
        send('fixture_clipboard', expected=client_text)
        if not until(lambda value: value['type'] == 'fixture_clipboard')['matches']:
            raise RuntimeError('client clipboard did not reach the host')
        sync_id = send('set_clipboard_sync', generation=generation, enabled=True)
        until(lambda value: value['type'] == 'result' and value.get('id') == sync_id)
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
        print(json.dumps({'text_mode': args.text_mode, 'unicode': True, 'host_composition_then_client_text': args.compose_first, 'physical_navigation': True, 'pointer': True, 'clipboard': True,
                          'view_only': True, 'frames': frames, 'measurement': 'native fixture receipts, not painted latency'}))
    finally:
        for process in dict.fromkeys((native, child)):
            if process.poll() is not None:
                continue
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)


if __name__ == '__main__':
    main()
