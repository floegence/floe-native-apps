"""Decoded-frame authority and ordered input for one native graphical backend.

The native backend owns the window registry, process identities and capture. Its
target objects are immutable instances replaced whenever native geometry or
input permission changes. This class owns attachment/paint acknowledgement only;
it never discovers, launches, retries, terminates or infers application lifetime.
"""
from collections import deque
from input_order import OrderedInput, valid_text


class DesktopAttachment:
    def __init__(self, native, timeout_add, timeout_remove, *, terminate_application=None,
                 can_control=None):
        self.native = native
        self.terminate_application = terminate_application
        self.owner, self.epoch, self.last_request = None, 0, 0
        self.native_epoch = 0
        self.can_control = can_control
        self.ready, self.capture, self.awaiting = None, None, deque()
        self.mode, self.capacity = None, 1
        self.timeout_add, self.timeout_remove = timeout_add, timeout_remove
        self.refinement = None
        self.cadence = None
        self.lossy = False
        self.frame_sequence, self.dirty, self.failed = 0, False, False
        self.cursor_sent = None
        self.order = OrderedInput(self.available, self.admit, self.input_result,
                                  lambda owner: owner.close(), timeout_add, timeout_remove)

    def available(self, owner):
        return self.owner is owner and not self.failed

    def controllable(self, owner):
        return self.available(owner) and (self.can_control is None or self.can_control(owner))

    def authorized(self, owner):
        """Whether this viewer is the current controller, including failed streams."""
        return self.owner is owner and (self.can_control is None or self.can_control(owner))

    def attach(self, owner):
        if self.owner:
            self.detach(self.owner)
        self.owner, self.last_request = owner, 0
        self.mode, self.capacity = None, 1
        self.lossy = False
        self.cursor_sent = None
        self.epoch += 1
        self.failed = False
        if hasattr(self.native, 'register_attachment'):
            self.native_epoch = self.native.register_attachment(self, self.epoch)
        else:
            self.native.bind(self.epoch)
            self.native_epoch = getattr(self.native, 'epoch', self.epoch)
        owner.send({'event': 'attached', 'version': 1, 'connection': self.epoch,
                    'state': self.native.snapshot(), 'stream_version': 2})
        self.cursor_changed()
        self.damage()

    def detach(self, owner):
        if self.owner is not owner:
            return
        self.owner, self.ready = None, None
        self.awaiting.clear()
        self.cancel_refinement()
        self.cancel_cadence()
        self.order.invalidate(owner)
        if hasattr(self.native, 'unregister_attachment'):
            self.native.unregister_attachment(self)
        else:
            self.native.unbind(self.native_epoch or self.epoch)
        self.native_epoch = 0

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
        if method == 'terminate_application':
            if set(message) != {'id', 'method'}:
                self.reply(owner, request, error='REQUEST_INVALID')
            elif not self.controllable(owner):
                self.reply(owner, request, error='INPUT_NOT_AUTHORIZED')
            elif self.terminate_application is None:
                self.reply(owner, request, error='METHOD_UNSUPPORTED')
            else:
                # Only the installed session owner can signal its supervisor.
                # Force quit discards pending input; it never waits behind text
                # completion or accepts a caller-selected PID/signal.
                self.retire_input()
                try:
                    self.terminate_application()
                    self.reply(owner, request, 'requested')
                except (ValueError, OSError):
                    self.reply(owner, request, error='APPLICATION_UNAVAILABLE')
        elif method == 'status':
            self.reply(owner, request, self.native.snapshot())
        elif method == 'frame_ack':
            frame = self.awaiting[0] if self.awaiting else None
            if (frame is None or type(message.get('frame')) is not int or message['frame'] != frame[0] or
                    self.native.target is not frame[1]):
                self.reply(owner, request, error='FRAME_TARGET_UNAVAILABLE')
                return
            first = self.ready is not frame[1]
            self.ready = frame[1]
            self.awaiting.popleft()
            self.reply(owner, request, 'painted')
            if first:
                self.native.sync_clipboard(self.native_epoch, self.ready)
            self.pump()
        elif method == 'configure_stream':
            mode = message.get('mode')
            if set(message) != {'id', 'method', 'mode'} or mode not in ('auto', 'clarity', 'smooth', 'data'):
                self.reply(owner, request, error='REQUEST_INVALID')
                return
            if mode != self.mode:
                self.cancel_cadence()
                self.native.configure_stream(mode)
                self.mode, self.capacity = mode, 2
                self.damage()
            self.reply(owner, request, {'mode': self.mode})
        elif method == 'refresh':
            if self.mode:
                self.native.refine()
            self.damage()
            self.reply(owner, request, 'requested')
        elif method == 'input':
            self.enqueue_input(owner, request, message)
        elif method == 'release_input':
            if set(message) != {'id', 'method', 'connection', 'window', 'generation'}:
                self.reply(owner, request, error='REQUEST_INVALID')
            elif not self.controllable(owner) or not self.matches_target(owner, message):
                self.reply(owner, request, error='INPUT_TARGET_UNAVAILABLE')
            else:
                self.cancel_input()
                self.reply(owner, request, 'released')
        elif method in ('select_window', 'close_window'):
            window = message.get('window')
            if type(window) is not int or not 1 <= window <= 9007199254740991:
                self.reply(owner, request, error='WINDOW_UNAVAILABLE')
                return
            if not self.authorized(owner):
                self.reply(owner, request, error='INPUT_NOT_AUTHORIZED')
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

    def matches_target(self, owner, message):
        target = self.ready
        return (self.available(owner) and target is not None and self.native.target is target and
                all(type(message.get(key)) is int for key in ('connection', 'window', 'generation')) and
                message.get('connection') == self.epoch and message.get('window') == target.window and
                message.get('generation') == target.generation)

    def enqueue_input(self, owner, request, message):
        if not self.controllable(owner):
            self.reply(owner, request, error='INPUT_NOT_AUTHORIZED')
            return
        if not self.matches_target(owner, message):
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
        self.order.enqueue(owner, (self.ready, request, operation))

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
            if value['kind'] == 'clipboard':
                return request, self.native.clipboard, self.native.clipboard.token(target), value['text']
            self.native.deliver(self.native_epoch, target, value)
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
        self.ready = None
        self.awaiting.clear()
        self.cancel_refinement()
        self.cancel_cadence()
        self.cancel_input()

    def cancel_input(self):
        if self.owner:
            owner = self.owner
            pending, queued = self.order.invalidate(owner)
            requests = ([pending] if pending is not None else []) + [item[1] for item in queued]
            for request in requests:
                self.reply(owner, request, error='INPUT_TARGET_UNAVAILABLE')
            self.native.release(self.native_epoch)

    def scene_changed(self):
        self.retire_input()
        self.metadata_changed()
        self.damage()

    def metadata_changed(self):
        # Display metadata cannot revoke a painted target or an in-flight
        # confirmed-text transaction. Only native scene changes do that.
        if self.owner:
            self.owner.send({'event': 'state', 'connection': self.epoch, 'state': self.native.snapshot()})

    def damage(self):
        self.dirty = True
        self.pump()

    def cursor_changed(self):
        if not self.owner or self.owner.cursor_pending:
            return
        current = self.native.cursor.current
        if current is self.cursor_sent:
            return
        if current:
            description, pixels = current
        else:
            description, pixels = {'mode': 'default', 'sequence': self.native.cursor.revision,
                                   'window': 0, 'generation': 0}, None
        if self.owner.send_cursor({**description, 'connection': self.epoch}, pixels):
            self.cursor_sent = current

    def writable(self, owner):
        if self.owner is owner:
            self.cursor_changed()
            self.pump()

    def clipboard_changed(self, selection):
        target, epoch, text = selection
        if (not self.owner or self.failed or epoch != self.epoch or self.ready is not target or
                self.native.target is not target):
            return
        value = {'connection': epoch, 'window': target.window, 'generation': target.generation}
        if text is None:
            value['error'] = 'CLIPBOARD_UNAVAILABLE'
        else:
            value['text'] = text
        self.owner.send({'event': 'clipboard', 'clipboard': value})

    def pump(self):
        if (not self.owner or self.native.target is None or not self.dirty or self.capture or
                len(self.awaiting) >= self.capacity or self.owner.frame_pending or self.cadence is not None):
            return
        ticket = (self.owner, self.native.target)
        self.capture, self.dirty = ticket, False
        def completed(description, data, error):
            self.captured(ticket, description, data, error)
        try:
            try:
                self.native.capture(ticket[1], completed, self)
            except TypeError:
                # Small embedders and unit fixtures may still expose the
                # pre-broker two-argument capture contract.
                self.native.capture(ticket[1], completed)
        except Exception:
            completed(None, None, 'CAPTURE_UNAVAILABLE')

    def captured(self, ticket, description, data, error):
        if self.capture is not ticket:
            return
        self.capture = None
        owner, target = ticket
        if self.owner is not owner or self.native.target is not target:
            if self.mode:
                self.native.refine()
            self.damage()
            return
        if error:
            # The native boundary returns a stable code, never input, pixels or
            # library diagnostics. Recovery requires native damage or refresh.
            self.retire_input()
            if self.mode:
                self.native.refine()
            owner.send({'event': 'capture_unavailable', 'code': 'CAPTURE_UNAVAILABLE'})
            return
        if description is None:
            # The compositor can commit without changing pixels. No network
            # frame or paint receipt is needed for an identical source raster.
            self.schedule_refinement(owner, target)
            self.pump()
            return
        previous = self.frame_sequence
        self.frame_sequence += 1
        frame = {**description, 'sequence': self.frame_sequence, 'connection': self.epoch,
                 'window': target.window, 'generation': target.generation}
        full = (description.get('x', 0) == 0 and description.get('y', 0) == 0 and
                description.get('region_width', description['width']) == description['width'] and
                description.get('region_height', description['height']) == description['height'])
        frame['base'] = 0 if full else previous
        if owner.send_frame(frame, data):
            self.awaiting.append((self.frame_sequence, target))
            if description.get('encoding') == 'jpeg':
                self.lossy = True
            elif full:
                self.lossy = False
            self.schedule_refinement(owner, target, changed=True)
            if self.mode == 'data':
                def next_frame():
                    self.cadence = None
                    self.pump()
                self.cadence = self.timeout_add(67, next_frame)
            self.pump()
        else:
            if self.mode:
                self.native.refine()
            self.dirty = True

    def schedule_refinement(self, owner, target, changed=False):
        if self.refinement is not None and not changed:
            return
        self.cancel_refinement()
        if not self.lossy:
            return
        def refine():
            self.refinement = None
            if self.owner is owner and self.native.target is target:
                self.native.refine()
                self.dirty = True
                self.pump()
        self.refinement = self.timeout_add(250, refine)

    def cancel_refinement(self):
        if self.refinement is not None:
            self.timeout_remove(self.refinement)
            self.refinement = None

    def cancel_cadence(self):
        if self.cadence is not None:
            self.timeout_remove(self.cadence)
            self.cadence = None

    def close(self):
        self.cancel_cadence()
        self.cancel_refinement()
        if self.owner:
            self.detach(self.owner)
        self.order.close()
        self.capture = None
