"""Observe actual Chromium text-input-v3 state progress before adding an adapter.

This uses unpublished shell commands to investigate native replies and event-loop
ordering. It is not authenticated confirmed-text acceptance or a universal
production completion contract. Actual page values remain the result authority.
"""
import json

from chromium_context_probe import ChromiumPage


def qualify(root, evidence, environment, control, wire, start, wait, paint):
    del root
    receipt = evidence / 'chromium-v3.json'
    browser = ChromiumPage(evidence, receipt, 'wayland')
    first_event = len(wire.events)
    previous = wire.native.target
    try:
        application = start(browser.command, environment, 'chromium-v3')
        wait(lambda: browser.ready and wire.native.target is not None and wire.native.target is not previous,
             'Native Wayland Chromium did not become ready')
        frame = paint('chromium-v3', marker=(59, 117, 159))
        window = frame['window']
        control.send(window, b'motion 200 120\nbutton 272 1\nbutton 272 0\nkey 30 1\nkey 30 0\n')
        wait(lambda: browser.clicked and receipt.exists() and json.loads(receipt.read_text()) == ['a', ''],
             'Native Chromium click and ordinary key did not reach the actual page')
        wait(lambda: any(line.startswith('context-surrounding ') and line.split()[2:] == ['1', '1', '1']
                         for line in wire.events[first_event:]),
             'Chromium could not publish its first actual text-input-v3 surrounding state')
        start_index = len(wire.events)
        wire.send('text wire-proof\n')
        wait(lambda: any(line.startswith('context-surrounding ') and line.split()[2:] == ['11', '11', '11']
                         for line in wire.events[start_index:]),
             'No native text-input-v3 state update after confirmed text')
        control.send(window, b'key 28 1\nkey 28 0\n')
        wait(lambda: json.loads(receipt.read_text()) == ['awire-proof\n', ''],
             'Actual Chromium document differs after protocol state and Enter')
        expected = ['awire-proof\n', '']
        paint('chromium-v3-committed', marker=browser.commit_marker(expected))
        # A surrounding-state acknowledgement must also be evaluated beyond the
        # 4000-byte protocol window. Identical tails may suppress updates even
        # when the document received another real commit.
        # libwayland's standard event buffer also bounds each string message;
        # an operation larger than that must be split at UTF-8 boundaries.
        chunk = 'Z' * 2048
        for index in range(3):
            start_index = len(wire.events)
            wire.send('text ' + chunk + '\n')
            expected[0] += chunk
            wait(lambda: json.loads(receipt.read_text()) == expected,
                 'Repeated ASCII commit was not received')
            committed = paint('chromium-v3-repeated-' + str(index), marker=browser.commit_marker(expected))
        updates = [line for line in wire.events[start_index:] if line.startswith('context-surrounding ')]
        assert not updates, 'Reevaluate the client receipt contract: a repeated-tail state update was observed'
        sequence = 0
        def barrier():
            nonlocal sequence
            sequence += 1
            wire.send('client-barrier ' + str(sequence) + '\n')
            wait(lambda: 'client-barrier-done ' + str(sequence) in wire.events,
                 'Native client event-loop barrier did not complete')
        control.send(window, b'key 29 1\nkey 30 1\nkey 30 0\nkey 29 0\nkey 14 1\nkey 14 0\n')
        barrier()
        expected = ['', '']
        for index, value in enumerate(['中文日本語한글🙂👩🏽‍💻e\u0301𠮷'] * 64 + ['界🙂' * 2000]):
            field = index % 2
            control.send(window, (f'motion {700 if field else 200} 120\nbutton 272 1\nbutton 272 0\n'
                'key 29 1\nkey 107 1\nkey 107 0\nkey 29 0\n').encode())
            barrier()
            wire.send('text-hex ' + value.encode().hex() + '\n')
            barrier()
            control.send(window, b'key 28 1\nkey 28 0\n')
            barrier()
            expected[field] += value + '\n'
        wait(lambda: json.loads(receipt.read_text()) == expected,
             'Actual Chromium Unicode/focus/Enter order differs after native barriers')
        committed = paint('chromium-v3-ordered', marker=browser.commit_marker(expected))
        control.send(window, b'close\n')
        assert application.wait(timeout=10) == 0
        return {'protocol': 'wayland text-input-v3', 'browser': browser.version,
                'actual': json.loads(receipt.read_text()), 'committed_frame': committed['sequence'],
                'repeated_long_text_bytes': 6156,
                'actual_document_changed_without_surrounding_receipt': True,
                'event_loop_barriers': sequence, 'unicode_transactions': 65,
                'limits': 'Native protocol investigation only; not an authenticated confirmed-text adapter or universal client receipt'}
    finally:
        browser.close()
        (evidence / 'chromium-v3-native-events.json').write_text(json.dumps(wire.events[first_event:], indent=2))
