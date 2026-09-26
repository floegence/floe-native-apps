"""Actual GDK native move/resize with observed pixels and application geometry."""
import json


def qualify(control, parent, receipt, wait, paint):
    actions = receipt.with_suffix('.windows.json')
    control.send(parent, b'key 87 1\nkey 87 0\n')
    frame = paint('manipulation', marker=(25, 166, 200))
    window = frame['window']
    assert window != parent
    observed = []
    for operation, color, dx, dy in (('move', (25, 166, 200), 80, 45),
                                      ('resize', (216, 162, 75), 100, 65)):
        frame = paint(operation + '-before', marker=color) if operation == 'resize' else frame
        before = frame['marker_bounds']
        x, y = (before[0] + before[2]) / 2, (before[1] + before[3]) / 2
        state = next(w for w in control.native.snapshot()['windows'] if w['window'] == window)
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
            frame = paint(operation + '-during', marker=color)
            assert frame['generation'] == generation, 'A native grab revoked its own remaining movement'
        control.send(window, f'button 272 0\n'.encode())
        wait(lambda: control.native.generation > generation, 'Ending the native grab retained old coordinates')
        frame = paint(operation + '-after', marker=color)
        after = frame['marker_bounds']
        if operation == 'move':
            assert [after[i] - before[i] for i in range(4)] == [dx, dy, dx, dy]
        else:
            size = [state['width'] + dx, state['height'] + dy]
            wait(lambda: json.loads(actions.read_text()).get('manipulated_size') == size,
                 'Actual application surface did not accept the native resize')
            assert after[2] - before[2] == dx
            assert after[3] - before[3] == dy
        observed.append({'operation': operation, 'before': before, 'after': after,
                         'generation': generation, 'completed_generation': frame['generation']})
    control.send(window, b'close\n')
    wait(lambda: window not in control.native.windows, 'Manipulation fixture did not close normally')
    paint('manipulation-restored', (19, 87, 155))
    return observed
