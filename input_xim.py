"""Commit-only XIM adapter using xcb-imdkit's public ABI."""
import ctypes as c
import locale
from gi.repository import GLib

P = c.c_void_p


def function(lib, name, result, arguments):
    value = getattr(lib, name)
    value.restype, value.argtypes = result, arguments
    return value


class Styles(c.Structure):
    _fields_ = [('count', c.c_uint32), ('values', c.POINTER(c.c_uint32))]


class Encodings(c.Structure):
    _fields_ = [('count', c.c_uint16), ('values', c.POINTER(c.c_char_p))]


class Header(c.Structure):
    _fields_ = [('major', c.c_uint8), ('minor', c.c_uint8), ('length', c.c_uint16)]


class Open(c.Structure):
    # xcb-imdkit's public xcb_im_open_fr_t contains one xcb_im_str_fr_t.
    _fields_ = [('length', c.c_uint8), ('locale', P)]


class Key(c.Structure):
    _fields_ = [('type', c.c_uint8), ('detail', c.c_uint8), ('serial', c.c_uint16),
               ('time', c.c_uint32), ('root', c.c_uint32), ('window', c.c_uint32),
               ('child', c.c_uint32), ('root_x', c.c_int16), ('root_y', c.c_int16),
               ('x', c.c_int16), ('y', c.c_int16), ('state', c.c_uint16),
               ('same_screen', c.c_uint8), ('pad', c.c_uint8)]


Callback = c.CFUNCTYPE(None, P, P, P, c.POINTER(Header), P, P, P)


def text_fragments(text):
    # XIM consumers such as xterm have fixed lookup buffers. One transport
    # operation stays ordered while bounded UTF-8 fragments enter the same IC.
    fragment = []
    size = 0
    for scalar in text:
        width = len(scalar.encode('utf-8'))
        if size + width > 256:
            yield ''.join(fragment)
            fragment, size = [], 0
        fragment.append(scalar)
        size += width
    if fragment:
        yield ''.join(fragment)


class XCBSource(GLib.Source):
    # A synchronous XCB reply can leave events in XCB's userspace queue while
    # its fd is no longer readable. Check that queue before the loop sleeps;
    # a fd-only watch loses acknowledgements. No polling timer is involved.
    def __init__(self, owner, fd):
        super().__init__()
        self.owner = owner
        self.fd = GLib.PollFD(fd, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR)
        self.add_poll(self.fd)

    def prepare(self):
        if not self.owner.queued:
            self.owner.queued = self.owner.poll_queued(self.owner.connection)
        return bool(self.owner.queued), -1

    def check(self):
        return bool(self.owner.queued or self.fd.revents)

    def dispatch(self, _callback, _args):
        return self.owner._read(None, self.fd.revents)


class XIM:
    event_mask = 1 << 21

    def __init__(self):
        xcb = c.CDLL('libxcb.so.1')
        im = c.CDLL('libxcb-imdkit.so.1')
        aux = c.CDLL('libxcb-util.so.1')
        self.free = function(c.CDLL(None), 'free', None, [P])
        connect = function(xcb, 'xcb_connect', P, [c.c_char_p, c.POINTER(c.c_int)])
        number = c.c_int()
        self.connection = connect(None, c.byref(number))
        if function(xcb, 'xcb_connection_has_error', c.c_int, [P])(self.connection):
            raise RuntimeError('Private input display unavailable')
        screen = function(aux, 'xcb_aux_get_screen', c.POINTER(c.c_uint32), [P, c.c_int])(self.connection, number.value)
        self.root = screen[0]
        wid = function(xcb, 'xcb_generate_id', c.c_uint32, [P])(self.connection)
        function(xcb, 'xcb_create_window', c.c_uint32, [P, c.c_uint8, c.c_uint32, c.c_uint32,
            c.c_int16, c.c_int16, c.c_uint16, c.c_uint16, c.c_uint16, c.c_uint16,
            c.c_uint32, c.c_uint32, P])(self.connection, 0, wid, screen[0], 0, 0, 1, 1, 0, 1, 0, 0, None)
        self.flush = function(xcb, 'xcb_flush', c.c_int, [P])
        self.poll = function(xcb, 'xcb_poll_for_event', P, [P])
        self.poll_queued = function(xcb, 'xcb_poll_for_queued_event', P, [P])
        self.queued = None
        self.disconnect = function(xcb, 'xcb_disconnect', None, [P])
        self.get_focus = function(xcb, 'xcb_get_input_focus', c.c_uint32, [P])
        self.focus_reply = function(xcb, 'xcb_get_input_focus_reply', c.POINTER(c.c_uint32), [P, c.c_uint32, P])
        self.query_tree = function(xcb, 'xcb_query_tree', c.c_uint32, [P, c.c_uint32])
        self.tree_reply = function(xcb, 'xcb_query_tree_reply', c.POINTER(c.c_uint32), [P, c.c_uint32, P])
        self.send_event = function(xcb, 'xcb_send_event', c.c_uint32, [P, c.c_uint8, c.c_uint32, c.c_uint32, P])
        self.keymap = function(xcb, 'xcb_get_keyboard_mapping', c.c_uint32, [P, c.c_uint8, c.c_uint8])
        self.keymap_reply = function(xcb, 'xcb_get_keyboard_mapping_reply', P, [P, c.c_uint32, P])
        self.keysyms = function(xcb, 'xcb_get_keyboard_mapping_keysyms', c.POINTER(c.c_uint32), [P])
        self.keysyms_count = function(xcb, 'xcb_get_keyboard_mapping_keysyms_length', c.c_int, [P])
        self.filter = function(im, 'xcb_im_filter_event', c.c_bool, [P, P])
        self.forward = function(im, 'xcb_im_forward_event', None, [P, P, P])
        self.send = function(im, 'xcb_im_commit_string', None, [P, P, c.c_uint32, P, c.c_uint32, c.c_uint32])
        self.sync = function(im, 'xcb_im_sync_xlib', None, [P, P])
        self.client_window = function(im, 'xcb_im_input_context_get_client_window', c.c_uint32, [P])
        self.focus_window = function(im, 'xcb_im_input_context_get_focus_window', c.c_uint32, [P])
        self.encode = function(im, 'xcb_utf8_to_compound_text', P, [P, c.c_size_t, c.POINTER(c.c_size_t)])
        self.close_im = function(im, 'xcb_im_close_im', None, [P])
        self.destroy = function(im, 'xcb_im_destroy', None, [P])
        im.xcb_compound_text_init()
        self.focused = None
        self.pending = None
        self.fragments = []
        self.cancelled = set()
        self.client_locales = {}
        self.supported_contexts = set()
        self.callback = Callback(self._callback)
        # Request focus notifications only. Passing zero to xcb-imdkit selects
        # its default KeyPress filter, which would asynchronously forward an
        # ordinary key behind a subsequent text commit. This bridge owns no keys.
        styles = Styles(3, (c.c_uint32 * 3)(0x408, 0x808, 0x810))
        encodings = Encodings(1, (c.c_char_p * 1)(b'COMPOUND_TEXT'))
        languages = {'C', 'POSIX'} | {name.split('_')[0].split('.')[0] for name in locale.locale_alias.values()}
        # Xlib sends the matching advertised locale, not necessarily its full
        # process locale. Advertise only UTF-8 names so a language-only match
        # cannot conceal a non-Unicode client conversion context.
        locales = ','.join(name + '.UTF-8' for name in sorted(languages)).encode('ascii')
        self.server = function(im, 'xcb_im_create', P, [P, c.c_int, c.c_uint32, c.c_char_p,
            c.c_char_p, c.POINTER(Styles), P, P, c.POINTER(Encodings), c.c_uint32, Callback, P])(
            self.connection, number.value, wid, b'floe-client', locales,
            c.byref(styles), None, None, c.byref(encodings), self.event_mask, self.callback, None)
        if not self.server or not function(im, 'xcb_im_open_im', c.c_bool, [P])(self.server):
            raise RuntimeError('Private XIM input unavailable')
        self.sync_mode = function(im, 'xcb_im_set_use_sync_mode', None, [P, c.c_bool])
        self.sync_mode(self.server, False)
        self.flush(self.connection)
        fd = function(xcb, 'xcb_get_file_descriptor', c.c_int, [P])(self.connection)
        self.source = XCBSource(self, fd)
        self.watch = self.source.attach(None)

    def _callback(self, server, client, context, header, frame, argument, _data):
        code = header.contents.major
        if code == 30:
            opened = c.cast(frame, c.POINTER(Open)).contents
            name = c.string_at(opened.locale, opened.length).decode('ascii', 'replace')
            self.client_locales[client] = name.partition('.')[2].partition('@')[0].lower().replace('-', '') == 'utf8'
        elif code == 50 and self.client_locales.get(client, False):
            self.supported_contexts.add(context)
        elif code in (3, 32):
            self.client_locales.pop(client, None)
        if code == 58:
            # XIM conversion in a non-Unicode locale can acknowledge while
            # discarding characters. Reject that context before sending text.
            self.focused = context if context in self.supported_contexts else None
        elif code in (52, 59) and self.focused == context:
            self.focused = None
        elif code == 60:
            self._forward_event(server, context, argument)
        elif code == 62 and self.pending and self.pending[0] == context:
            _, completed = self.pending
            if self.focused == context and self.fragments:
                self._send_fragment(context)
            else:
                self.pending = None
                self.fragments = []
                completed(None if self.focused == context else 'INPUT_CONTEXT_UNAVAILABLE')
        if code == 52:
            self.supported_contexts.discard(context)
            self.cancelled.discard(context)
            if self.pending and self.pending[0] == context:
                _, completed = self.pending
                self.pending = None
                self.fragments = []
                completed('INPUT_CONTEXT_UNAVAILABLE')

    def _forward_event(self, server, context, event):
        self.forward(server, context, event)

    def _read(self, _fd, condition):
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            self.focused = None
            self.watch = 0
            if self.pending:
                _, completed = self.pending
                self.pending = None
                self.fragments = []
                completed('INPUT_CONTEXT_UNAVAILABLE')
            return False
        while True:
            event, self.queued = self.queued or self.poll(self.connection), None
            if not event:
                break
            self.filter(self.server, event)
            self.free(event)
        self.flush(self.connection)
        return True

    def descendant(self, child, parent):
        # A bounded tree walk rejects foreign, vanished or cyclic window targets.
        for _ in range(64):
            if child == parent:
                return True
            if child <= 1:
                return False
            reply = self.tree_reply(self.connection, self.query_tree(self.connection, child), None)
            if not reply:
                return False
            child = reply[3]
            self.free(reply)
        return False

    def focused_within(self, xid):
        reply = self.focus_reply(self.connection, self.get_focus(self.connection), None)
        if not reply:
            return False
        focus = reply[2]
        self.free(reply)
        return self.descendant(focus, xid)

    def context_for(self, xid):
        self._read(None, GLib.IO_IN)
        context = self.focused
        if context and self.descendant(self.focus_window(context) or self.client_window(context), xid):
            return context
        return None

    def commit(self, token, text, completed):
        if not token or token != self.focused or self.pending or token in self.cancelled:
            completed('INPUT_CONTEXT_UNAVAILABLE')
            return
        self.pending = (token, completed)
        self.fragments = list(text_fragments(text))
        self._send_fragment(token)

    def _send_fragment(self, token):
        data = self.fragments.pop(0).encode('utf-8')
        size = c.c_size_t()
        encoded = self.encode(data, len(data), c.byref(size))
        if not encoded or size.value > 65535:
            if encoded:
                self.free(encoded)
            _, completed = self.pending
            self.pending = None
            self.fragments = []
            completed('INPUT_TEXT_TOO_LONG')
            return
        self.send(self.server, token, 2, encoded, size.value, 0)
        self.free(encoded)
        # Disable the library's implicit commit acknowledgement and use exactly
        # one explicit sync. Its callback reports only explicitly requested sync.
        self.sync(self.server, token)
        self.flush(self.connection)

    def marker(self, sequence, xid):
        # Core events preserve an operation ID without changing keyboard state.
        # Event mask zero addresses the window's owning client even when GTK4
        # selects XI2 only. Its input module consumes this via GDK's xevent signal;
        # GTK3 and Qt consume the same marker in their ordinary key filter.
        # Never steal a keycode that an application has explicitly mapped.
        reply = self.keymap_reply(self.connection, self.keymap(self.connection, 8, 1), None)
        if not reply:
            return False
        values = self.keysyms(reply)
        available = all(values[i] == 0 for i in range(self.keysyms_count(reply)))
        self.free(reply)
        if not available:
            return False
        reply = self.focus_reply(self.connection, self.get_focus(self.connection), None)
        if not reply:
            return False
        focus = reply[2]
        self.free(reply)
        if not self.descendant(focus, xid):
            return False
        event = Key(type=2, detail=8, time=sequence, root=self.root,
                    window=focus, same_screen=1)
        self.send_event(self.connection, 0, focus, 0, c.byref(event))
        return self.flush(self.connection) > 0

    def cancel(self, token):
        if self.pending and self.pending[0] == token:
            # XIM has no commit revocation operation. Do not reuse this context
            # after losing its acknowledgement; late sync is never a new commit.
            self.pending = None
            self.fragments = []
            self.cancelled.add(token)

    def close(self):
        self.pending = None
        self.fragments = []
        if self.watch:
            self.source.destroy()
            self.watch = 0
        self.source.owner = None
        if self.queued:
            self.free(self.queued)
            self.queued = None
        self.close_im(self.server)
        self.destroy(self.server)
        self.disconnect(self.connection)
