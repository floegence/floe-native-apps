"""Exact native paste/copy/cut receipts through authenticated viewer input."""
import hashlib
import json
import os
from pathlib import Path
import signal
import time


def delayed_xwayland_publication(receipt, publish):
    """Hold only this fixture's X server while the helper queues publication.

    The fixed delay injects native scheduling adversity; it is not a readiness
    wait or a retry. Exact application bytes remain the acceptance condition.
    """
    from application_processes import identity
    status = json.loads((receipt.parent / 'session/desktop-status.json').read_text())
    compositor = next(item for item in status['processes'] if item['service'] == 'compositor')
    assert identity(compositor['pid'])[1] == compositor['start_ticks']
    children = Path(f"/proc/{compositor['pid']}/task/{compositor['pid']}/children").read_text().split()
    servers = [int(pid) for pid in children if any(Path(os.fsdecode(arg)).name == 'Xwayland'
        for arg in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0') if arg)]
    assert len(servers) == 1, 'The fixture must own exactly one Xwayland process'
    started = identity(servers[0])
    descriptor = os.pidfd_open(servers[0])
    try:
        assert identity(servers[0]) == started and started[0] == compositor['pid']
        signal.pidfd_send_signal(descriptor, signal.SIGSTOP)
        try:
            result = publish()
            time.sleep(.2)
        finally:
            signal.pidfd_send_signal(descriptor, signal.SIGCONT)
        return result
    finally:
        os.close(descriptor)


def exercise(client, target, receipt, wait, result):
    def input(operation):
        return client.request('input', **target, operation=operation)

    def shortcut(code):
        input({'kind': 'key', 'code': 29, 'pressed': True})
        input({'kind': 'key', 'code': code, 'pressed': True})
        input({'kind': 'key', 'code': code, 'pressed': False})
        return input({'kind': 'key', 'code': 29, 'pressed': False})

    def copied(text, start):
        while not any(event.get('event') == 'clipboard' and
                      event['clipboard'].get('text') == text for event in client.events[start:]):
            client.record()

    value = '中文 日本語 한국어 😀 𠀀 e\u0301 🧑🏽\u200d💻\n'
    # An application's oversized offer fails explicitly without blocking
    # frames or ordinary input. Only the subsequent valid selection is copied.
    oversized = 'oversized application selection\n' * 600
    shortcut(30)
    input({'kind': 'text', 'text': oversized[:12000]})
    last = input({'kind': 'text', 'text': oversized[12000:]})
    assert 'error' not in client.response(last)
    wait(lambda: json.loads(receipt.read_text()) == [oversized, ''], 'Oversized source document differs')
    start = len(client.events)
    shortcut(30)
    assert 'error' not in client.response(shortcut(46))
    while not any(event.get('event') == 'clipboard' and
                  event['clipboard'].get('error') == 'CLIPBOARD_UNAVAILABLE' for event in client.events[start:]):
        client.record()
    # Publish and enqueue Paste without waiting for the publication reply.
    # Selection publication must precede the shortcut. The toolkit's native
    # asynchronous paste is not a confirmed-text transaction: only document
    # receipt establishes that it has consumed the clipboard stream.
    payload = value * 200
    assert len(payload.encode()) <= 16000
    shortcut(30)  # A
    def publish_and_paste():
        input({'kind': 'clipboard', 'text': payload})
        return shortcut(47)  # V
    last = (delayed_xwayland_publication(receipt, publish_and_paste)
            if os.environ.get('FLOE_PROBE_PAUSE_XWAYLAND') == '1' else publish_and_paste())
    assert 'error' not in client.response(last)
    wait(lambda: json.loads(receipt.read_text()) == [payload, ''], 'Native paste bytes differ')
    input({'kind': 'key', 'code': 28, 'pressed': True})
    last = input({'kind': 'key', 'code': 28, 'pressed': False})
    assert 'error' not in client.response(last)
    expected = payload + '\n'
    wait(lambda: json.loads(receipt.read_text()) == [expected, ''], 'Native paste/Enter bytes differ')
    start = len(client.events)
    shortcut(30)
    assert 'error' not in client.response(shortcut(46))  # C
    copied(expected, start)
    start = len(client.events)
    assert 'error' not in client.response(shortcut(45))  # X
    wait(lambda: json.loads(receipt.read_text()) == ['', ''], 'Native cut did not remove the selection')
    copied(expected, start)
    assert 'error' not in client.response(shortcut(47))
    wait(lambda: json.loads(receipt.read_text()) == [expected, ''], 'Native clipboard did not survive Cut/Paste')
    result['clipboard'] = {'bytes': len(payload.encode()), 'publication_before_paste': True,
        'oversized_offer_rejected': True,
        'copy_cut_exact': True, 'sha256': hashlib.sha256(expected.encode()).hexdigest()}
    return expected
