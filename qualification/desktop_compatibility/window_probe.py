"""Actual GDK native move/resize with observed pixels and application geometry."""
import json


def qualify(control, parent, receipt, wait, paint, parent_color=(19, 87, 155)):
    actions = receipt.with_suffix('.windows.json')
    control.send(parent, b'key 87 1\nkey 87 0\n')
    frame = paint('manipulation', marker=(25, 166, 200))
    window = frame['window']
    assert window != parent
    observed = []
    for operation, color, dx, dy in (('move', (25, 166, 200), 80, 44),
                                      ('resize', (216, 162, 75), 100, 64)):
        if operation == 'resize':
            assert control.response(control.request('refresh'))['result'] == 'requested'
        frame = paint(operation + '-before', marker=color) if operation == 'resize' else frame
        before = frame['marker_bounds']
        x, y = (before[0] + before[2]) / 2, (before[1] + before[3]) / 2
        state = next(w for w in control.native.snapshot()['windows'] if w['window'] == window)
        wait(lambda: 'manipulated_size' in json.loads(actions.read_text()), 'Application surface size is unavailable')
        original_size = json.loads(actions.read_text())['manipulated_size']
        control.send(window, f'motion {x} {y}\nbutton 272 1\n'.encode())
        wait(lambda: json.loads(actions.read_text()).get('window_operation') == operation,
             'Application did not issue the native decoration operation')
        wait(lambda: next(w for w in control.native.snapshot()['windows']
                          if w['window'] == window).get('interacting', False),
             'Native shell did not begin the requested decoration grab')
        generation = control.native.generation
        # Distinct movements exercise a continuous native grab, not only a
        # final resize. No new DOM event owner or synthesized application event.
        for step in (0.5, 1):
            control.send(window, f'motion {x + dx * step} {y + dy * step}\n'.encode())
            def moved(bounds):
                return (bounds[2] - before[2], bounds[3] - before[3]) == (int(dx * step), int(dy * step))
            frame = paint(operation + '-during', marker=color, accept_bounds=moved)
            assert frame['generation'] == generation, 'A native grab revoked its own remaining movement'
        control.send(window, f'button 272 0\n'.encode())
        wait(lambda: control.native.generation > generation, 'Ending the native grab retained old coordinates')
        frame = paint(operation + '-after', marker=color)
        after = frame['marker_bounds']
        if operation == 'move':
            assert [after[i] - before[i] for i in range(4)] == [dx, dy, dx, dy]
        else:
            size = [original_size[0] + dx, original_size[1] + dy]
            wait(lambda: json.loads(actions.read_text()).get('manipulated_size') == size,
                 'Actual application surface did not accept the native resize')
            native = control.native.windows[window]
            assert (native.width, native.height) == (state['width'] + dx, state['height'] + dy)
            assert after[2] - before[2] == dx
            assert after[3] - before[3] == dy
        observed.append({'operation': operation, 'before': before, 'after': after,
                         'generation': generation, 'completed_generation': frame['generation']})
    # Disconnect in an active native grab. The same window must survive and
    # require a fresh painted frame; old held buttons must not resume dragging.
    assert control.response(control.request('refresh'))['result'] == 'requested'
    frame = paint('grab-before-reattach', marker=(25, 166, 200))
    x0, y0, x1, y1 = frame['marker_bounds']
    control.send(window, f'motion {(x0 + x1) / 2} {(y0 + y1) / 2}\nbutton 272 1\n'.encode())
    wait(lambda: control.native.windows[window].mode == 8, 'Native move did not begin before reconnect')
    previous = control.generation
    assert control.reconnect() == previous + 1
    wait(lambda: control.native.windows[window].mode == 0, 'Reconnection kept the native grab alive')
    request = control.send(window, b'motion 700 550\n')
    assert control.response(request)['error'] == 'INPUT_TARGET_UNAVAILABLE'
    paint('grab-after-reattach', marker=(25, 166, 200))
    # Closing the window during another native grab also releases its seat.
    control.send(window, f'motion {(x0 + x1) / 2} {(y0 + y1) / 2}\nbutton 272 1\n'.encode())
    wait(lambda: control.native.windows[window].mode == 8, 'Native move did not begin before close')
    control.send(window, b'close\n')
    wait(lambda: window not in control.native.windows, 'Manipulation fixture did not close normally')
    paint('manipulation-restored', parent_color)
    observed.append({'reattach_cancels_grab': True, 'close_cancels_grab': True})
    return observed
