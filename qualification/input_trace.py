"""Record qualification transaction phases without recording application text.

Only the disposable fixture launcher uses these observers. Production modules,
deadlines, dispatch order and acknowledgement behavior remain unchanged.
"""
import json
from pathlib import Path
import runpy
import sys
from time import monotonic

launcher, trace_path = sys.argv[1:3]
sys.argv = [launcher, *sys.argv[3:]]
sys.path.insert(0, str(Path(launcher).parent))
from input_context import Contexts
from input_dispatch import InputDispatch

trace = open(trace_path, 'w', buffering=1)


def record(context, phase, **details):
    operation = context.pending
    trace.write(json.dumps({'time': monotonic(), 'phase': phase,
                           'sequence': operation['sequence'] if operation else None,
                           'taken': operation is not None and 'text' not in operation,
                           **details}) + '\n')


original_init, original_commit = Contexts.__init__, Contexts.commit
original_take, original_done, original_cancel = Contexts.take, Contexts.done, Contexts.cancel
original_enqueue = InputDispatch.enqueue


def enqueue(self, protocol, packet, handler=None):
    if handler is not None:
        # Packet type only: keys, coordinates, clipboard and text bodies never
        # become diagnostic output. This proves the actual registered Xpra
        # handler entered the shared scheduler, including legacy aliases.
        trace.write(json.dumps({'time':monotonic(), 'phase':'ordered-event', 'kind':packet[0]}) + '\n')
    return original_enqueue(self, protocol, packet, handler)


def initialize(self, address, display, marker):
    def observed_marker(sequence, xid):
        result = marker(sequence, xid)
        record(self, 'marker', delivered=result)
        return result
    original_init(self, address, display, observed_marker)


def commit(self, token, text, completed):
    record(self, 'commit', toolkit=self.contexts.get(token[0], {}).get('toolkit'))
    def observed_completion(error):
        record(self, 'complete', error=error)
        completed(error)
    return original_commit(self, token, text, observed_completion)


def take(self, sender, sequence, xid):
    result = original_take(self, sender, sequence, xid)
    record(self, 'take', accepted=result is not None, requested_sequence=sequence)
    return result


def done(self, sender, sequence):
    record(self, 'done', requested_sequence=sequence)
    return original_done(self, sender, sequence)


def cancel(self, token):
    record(self, 'cancel')
    return original_cancel(self, token)


Contexts.__init__, Contexts.commit = initialize, commit
Contexts.take, Contexts.done, Contexts.cancel = take, done, cancel
InputDispatch.enqueue = enqueue
runpy.run_path(launcher, run_name='__main__')
