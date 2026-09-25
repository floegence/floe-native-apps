"""Decoded-frame authority and ordered input for one native graphical backend.

The native backend owns the window registry, process identities and capture. Its
target objects are immutable instances replaced whenever native geometry or
input permission changes. This class owns attachment/paint acknowledgement only;
it never discovers, launches, retries, terminates or infers application lifetime.
"""
from input_order import OrderedInput, valid_text


class DesktopAttachment:
    def __init__(self, native, timeout_add, timeout_remove):
        self.native = native
        self.owner, self.epoch, self.last_request = None, 0, 0
        self.ready, self.capture, self.awaiting = None, None, None
        self.frame_sequence, self.dirty, self.failed = 0, False, False
        self.order = OrderedInput(self.available, self.admit, self.input_result,
                                  lambda owner: owner.close(), timeout_add, timeout_remove)

    def available(self, owner):
        return self.owner is owner and not self.failed

    def attach(self, owner):
        if self.owner:
            self.detach(self.owner)
        self.owner, self.last_request = owner, 0
        self.epoch += 1
        self.failed = False
        self.native.bind(self.epoch)
        owner.send({'event': 'attached', 'version': 1, 'connection': self.epoch,
                    'state': self.native.snapshot()})
        self.damage()

    def detach(self, owner):
        if self.owner is not owner:
            return
        self.owner, self.ready, self.awaiting = None, None, None
        self.order.invalidate(owner)
        self.native.unbind(self.epoch)

    @staticmethod
    def reply(owner, request, result=None, error=None):
        owner.send({'id': request, 'error': error} if error else {'id': request, 'result': result})

    def request(self, owner, message):
        if self.owner is not owner:
            return
        request, method = message['id'], message['method']
        if request <= self.last_request:
            self.reply(owner, request, error='REQUEST_SEQUENCE_INVALID')
            return
        self.last_request = request
        if method == 'status':
            self.reply(owner, request, self.native.snapshot())
        elif method == 'frame_ack':
            frame = self.awaiting
            if (frame is None or type(message.get('frame')) is not int or message['frame'] != frame[0] or
                    self.native.target is not frame[1]):
                self.reply(owner, request, error='FRAME_TARGET_UNAVAILABLE')
                return
            self.ready, self.awaiting = frame[1], None
            self.reply(owner, request, 'painted')
            self.pump()
        elif method == 'refresh':
            self.damage()
            self.reply(owner, request, 'requested')
        elif method == 'input':
            self.enqueue_input(owner, request, message)
        elif method in ('select_window', 'close_window'):
            window = message.get('window')
            if type(window) is not int or not 1 <= window <= 9007199254740991:
                self.reply(owner, request, error='WINDOW_UNAVAILABLE')
                return
            if method == 'close_window' and not self.failed:
                self.order.enqueue(owner, (None, request, {'kind': 'close_window', 'window': window}))
                return
            try:
                if method == 'select_window':
                    self.retire_input()
                    self.native.select(window)
                else:
                    self.native.close_window(window)
                self.reply(owner, request, 'requested')
            except (ValueError, OSError):
                self.reply(owner, request, error='WINDOW_UNAVAILABLE')
        else:
            self.reply(owner, request, error='METHOD_UNSUPPORTED')

    def enqueue_input(self, owner, request, message):
        target = self.ready
        if (not self.available(owner) or target is None or self.native.target is not target or
                any(type(message.get(key)) is not int for key in ('connection', 'window', 'generation')) or
                message.get('connection') != self.epoch or message.get('window') != target.window or
                message.get('generation') != target.generation):
            self.reply(owner, request, error='INPUT_TARGET_UNAVAILABLE')
            return
        try:
            operation = self.native.validate_input(message.get('operation'))
            if operation['kind'] == 'text' and not valid_text(operation.get('text')):
                self.input_result(owner, request, 'INPUT_TEXT_INVALID')
                return
        except (TypeError, KeyError, ValueError):
            self.input_result(owner, request, 'INPUT_OPERATION_INVALID')
            return
        self.order.enqueue(owner, (target, request, operation))

    def admit(self, owner, operation):
        target, request, value = operation
        try:
            if target is None:
                self.native.close_window(value['window'])
                self.reply(owner, request, 'requested')
                return None
            if self.native.target is not target or self.ready is not target:
                self.reply(owner, request, error='INPUT_TARGET_UNAVAILABLE')
                return None
            if value['kind'] == 'text':
                context = self.native.context_for(target)
                if context is None:
                    self.input_result(owner, request, 'INPUT_CONTEXT_UNAVAILABLE')
                    return None
                adapter, token = context
                return request, adapter, token, value['text']
            self.native.deliver(self.epoch, target, value)
            # A native transport submission is not an application text receipt.
            self.reply(owner, request, 'submitted')
        except Exception:
            self.input_result(owner, request, 'INPUT_DELIVERY_FAILED')
        return None

    def input_result(self, owner, request, error):
        if self.owner is not owner:
            return
        if error:
            self.failed = True
            self.retire_input()
        self.reply(owner, request, 'completed', error)

    def retire_input(self):
        self.ready, self.awaiting = None, None
        if self.owner:
            self.order.invalidate(self.owner)
            self.native.release(self.epoch)

    def scene_changed(self):
        self.retire_input()
        if self.owner:
            self.owner.send({'event': 'state', 'connection': self.epoch, 'state': self.native.snapshot()})
        self.damage()

    def damage(self):
        self.dirty = True
        self.pump()

    def writable(self, owner):
        if self.owner is owner:
            self.pump()

    def pump(self):
        if (not self.owner or self.native.target is None or not self.dirty or self.capture or
                self.awaiting or self.owner.frame_pending):
            return
        ticket = (self.owner, self.native.target)
        self.capture, self.dirty = ticket, False
        def completed(description, data, error):
            self.captured(ticket, description, data, error)
        try:
            self.native.capture(ticket[1], completed)
        except Exception:
            completed(None, None, 'CAPTURE_UNAVAILABLE')

    def captured(self, ticket, description, data, error):
        if self.capture is not ticket:
            return
        self.capture = None
        owner, target = ticket
        if self.owner is not owner or self.native.target is not target:
            self.damage()
            return
        if error:
            # The native boundary returns a stable code, never input, pixels or
            # library diagnostics. Recovery requires native damage or refresh.
            owner.send({'event': 'capture_unavailable', 'code': 'CAPTURE_UNAVAILABLE'})
            return
        self.frame_sequence += 1
        frame = {**description, 'sequence': self.frame_sequence, 'connection': self.epoch,
                 'window': target.window, 'generation': target.generation}
        if owner.send_frame(frame, data):
            self.awaiting = (self.frame_sequence, target)
        else:
            self.dirty = True

    def close(self):
        if self.owner:
            self.detach(self.owner)
        self.order.close()
        self.capture = None
