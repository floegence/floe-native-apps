"""Authorized physical Wayland desktop access through the public desktop portal.

No compositor-private control API, privileged input device or private application
display is substituted when the desktop portal denies an operation.
"""
import json
import fcntl
from contextlib import contextmanager
import os
import secrets
import stat
import tempfile
import select
import threading
import time

from host_desktop_contract import DesktopError


class PortalGrant:
    """A single-use restore token; consuming it is durable before portal Start."""
    def __init__(self, directory):
        info = os.lstat(directory)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise DesktopError('STATE_DIRECTORY_INVALID')
        self.directory = directory
        self.path = os.path.join(directory, 'portal-grant.json')

    @contextmanager
    def _locked(self):
        try:
            fd = os.open(os.path.join(self.directory, 'portal-grant.lock'),
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as error:
            raise DesktopError('STATE_DIRECTORY_INVALID') from error
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise DesktopError('STATE_DIRECTORY_INVALID')
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def consume(self):
        try:
            with self._locked():
                return self._consume()
        except OSError as error:
            raise DesktopError('RESTORE_TOKEN_STORAGE_FAILED') from error

    def _consume(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise DesktopError('RESTORE_TOKEN_INVALID') from error
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 16384:
                raise DesktopError('RESTORE_TOKEN_INVALID')
            try:
                record = json.load(source, object_pairs_hook=_unique_object)
            except (ValueError, UnicodeError) as error:
                raise DesktopError('RESTORE_TOKEN_INVALID') from error
        if (not isinstance(record, dict) or type(record.get('version')) is not int or record.get('version') != 1
                or not self._valid_token(record.get('token'))):
            raise DesktopError('RESTORE_TOKEN_INVALID')
        self._save(None)
        return record['token'] or None

    def save(self, token):
        try:
            with self._locked():
                self._save(token)
        except OSError as error:
            raise DesktopError('RESTORE_TOKEN_STORAGE_FAILED') from error

    def acquire_start(self):
        # Serialize consent and single-use token rotation, not active viewers.
        # The consumer owns the one remote controller across all attachments.
        try:
            fd = os.open(os.path.join(self.directory, 'portal-start.lock'),
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as error:
            raise DesktopError('STATE_DIRECTORY_INVALID') from error
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise DesktopError('STATE_DIRECTORY_INVALID')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DesktopError('AUTHORIZATION_PENDING') from None
            return fd
        except OSError as error:
            os.close(fd)
            raise DesktopError('STATE_DIRECTORY_INVALID') from error
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def _valid_token(token):
        try:
            return isinstance(token, str) and '\0' not in token and len(token.encode()) <= 8192
        except UnicodeError:
            return False

    def _save(self, token):
        if token is not None and not self._valid_token(token):
            raise DesktopError('RESTORE_TOKEN_INVALID')
        fd, path = tempfile.mkstemp(prefix='.portal-', dir=self.directory)
        try:
            with os.fdopen(fd, 'w') as target:
                json.dump({'version': 1, 'token': token or ''}, target)
                target.flush()
                os.fsync(target.fileno())
            os.replace(path, self.path)
            parent = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            if os.path.exists(path):
                os.unlink(path)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


class PortalSession:
    NAME = 'org.freedesktop.portal.Desktop'
    PATH = '/org/freedesktop/portal/desktop'
    REMOTE = 'org.freedesktop.portal.RemoteDesktop'
    SCREEN = 'org.freedesktop.portal.ScreenCast'
    CLIPBOARD = 'org.freedesktop.portal.Clipboard'

    def __init__(self, bus, Gio, GLib, grant, changed, clipboard_changed=lambda _text: None):
        self.bus, self.Gio, self.GLib = bus, Gio, GLib
        self.grant, self.changed = grant, changed
        self.session = None
        self.pending = {}
        self.subscriptions = []
        self.closed = False
        self.streams = []
        self.fd = None
        self.start_lease = None
        self.clipboard = False
        self.devices = 0
        self.clipboard_changed = clipboard_changed
        self.clipboard_enabled = False
        self.clipboard_epoch = 0
        self.clipboard_sync = False
        self.clipboard_types = []
        self.clipboard_text = None
        self.transfer_serial = 0
        self.clipboard_reads = threading.BoundedSemaphore(2)
        self.clipboard_writes = threading.BoundedSemaphore(4)
        self.subscriptions.append(bus.signal_subscribe(
            self.NAME, 'org.freedesktop.portal.Session', 'Closed', None,
            None, Gio.DBusSignalFlags.NONE, self._closed))
        self.subscriptions.append(bus.signal_subscribe(self.NAME, self.CLIPBOARD, 'SelectionOwnerChanged',
            self.PATH, None, Gio.DBusSignalFlags.NONE, self._selection_changed))
        self.subscriptions.append(bus.signal_subscribe(self.NAME, self.CLIPBOARD, 'SelectionTransfer',
            self.PATH, None, Gio.DBusSignalFlags.NONE, self._selection_transfer))

    def version(self, interface):
        try:
            value = self.bus.call_sync(self.NAME, self.PATH, 'org.freedesktop.DBus.Properties',
                'Get', self.GLib.Variant('(ss)', (interface, 'version')),
                self.GLib.VariantType.new('(v)'), self.Gio.DBusCallFlags.NONE, 3000, None)
            return value.unpack()[0]
        except self.GLib.Error:
            return 0

    def _closed(self, _bus, _sender, path, _interface, _signal, _parameters):
        if path == self.session and not self.closed:
            self.close()
            self.changed('permission_revoked')

    def _request(self, interface, method, prefix, options, done):
        if self.closed:
            done(None, 'SESSION_CLOSED')
            return
        token = 'floe_' + secrets.token_hex(12)
        sender = self.bus.get_unique_name().lstrip(':').replace('.', '_')
        path = self.PATH + '/request/' + sender + '/' + token
        values = dict(options, handle_token=self.GLib.Variant('s', token))
        signature, arguments = prefix
        subscription = self.bus.signal_subscribe(self.NAME, 'org.freedesktop.portal.Request',
            'Response', path, None, self.Gio.DBusSignalFlags.NONE,
            lambda *args: response(args[-1]))
        self.pending[path] = subscription
        self.changed('portal_' + method.lower())

        def finish(result, error):
            subscribed = self.pending.pop(path, None)
            if subscribed is None:
                return
            self.bus.signal_unsubscribe(subscribed)
            done(result, error)

        def response(parameters):
            code, result = parameters.unpack()
            finish(result if code == 0 else None,
                None if code == 0 else 'PERMISSION_CANCELLED' if code == 1 else 'PERMISSION_DENIED')

        def called(connection, result):
            try:
                returned = connection.call_finish(result).unpack()[0]
                if returned != path:
                    finish(None, 'PORTAL_PROTOCOL_INVALID')
                else:
                    self.changed('portal_' + method.lower() + '_waiting')
            except self.GLib.Error:
                finish(None, 'PORTAL_UNAVAILABLE')

        self.bus.call(self.NAME, self.PATH, interface, method,
            self.GLib.Variant('(' + signature + 'a{sv})', tuple(arguments) + (values,)),
            self.GLib.VariantType.new('(o)'), self.Gio.DBusCallFlags.NONE, 10000, None, called)

    def start(self, unattended, done):
        if self.session or self.closed:
            done(None, 'SESSION_ALREADY_STARTED')
            return
        version = self.version(self.REMOTE)
        if version < 1 or self.version(self.SCREEN) < 1:
            done(None, 'PORTAL_UNAVAILABLE')
            return
        if unattended and version < 2:
            done(None, 'UNATTENDED_UNAVAILABLE')
            return
        try:
            self.start_lease = self.grant.acquire_start()
        except DesktopError as error:
            done(None, error.code)
            return
        completion = done
        def done(streams, error):
            self.release_start()
            completion(streams, error)
        options = {'session_handle_token': self.GLib.Variant('s', 'floe_' + secrets.token_hex(12))}

        def created(result, error):
            if error:
                done(None, error)
                return
            self.session = result.get('session_handle')
            if not isinstance(self.session, str) or not self.session.startswith(self.PATH + '/session/'):
                done(None, 'PORTAL_PROTOCOL_INVALID')
                return
            devices = {'types': self.GLib.Variant('u', 3)}
            if version >= 2:
                devices['persist_mode'] = self.GLib.Variant('u', 2 if unattended else 0)
                try:
                    token = self.grant.consume() if unattended else None
                except DesktopError as failure:
                    done(None, failure.code)
                    return
                if token:
                    devices['restore_token'] = self.GLib.Variant('s', token)
            self._request(self.REMOTE, 'SelectDevices', ('o', (self.session,)), devices, selected_devices)

        def selected_devices(_result, error):
            if error:
                done(None, error)
                return
            sources = {'types': self.GLib.Variant('u', 1), 'multiple': self.GLib.Variant('b', True),
                'cursor_mode': self.GLib.Variant('u', 2)}
            self._request(self.SCREEN, 'SelectSources', ('o', (self.session,)), sources, selected_sources)

        def selected_sources(_result, error):
            if error:
                done(None, error)
                return
            if self.version(self.CLIPBOARD):
                try:
                    self._call(self.CLIPBOARD, 'RequestClipboard', '(oa{sv})', (self.session, {}))
                except self.GLib.Error:
                    done(None, 'CLIPBOARD_PERMISSION_FAILED')
                    return
            self._request(self.REMOTE, 'Start', ('os', (self.session, '')), {}, started)

        def started(result, error):
            if error:
                done(None, error)
                return
            try:
                self.streams = result.get('streams', [])
                self.devices = result.get('devices', 0)
                self.clipboard = result.get('clipboard_enabled', False)
                if not self.streams:
                    raise DesktopError('DISPLAY_UNAVAILABLE')
                if unattended:
                    self.grant.save(result.get('restore_token'))
                response, descriptors = self.bus.call_with_unix_fd_list_sync(self.NAME, self.PATH,
                    self.SCREEN, 'OpenPipeWireRemote', self.GLib.Variant('(oa{sv})', (self.session, {})),
                    self.GLib.VariantType.new('(h)'), self.Gio.DBusCallFlags.NONE, 5000, None, None)
                self.fd = descriptors.get(response.unpack()[0])
                done(self.streams, None)
            except self.GLib.Error:
                done(None, 'PIPEWIRE_UNAVAILABLE')
            except DesktopError as failure:
                done(None, failure.code)

        self._request(self.REMOTE, 'CreateSession', ('', ()), options, created)

    def _call(self, interface, method, signature, arguments):
        if not self.session or self.closed:
            raise DesktopError('SESSION_CLOSED')
        return self.bus.call_sync(self.NAME, self.PATH, interface, method,
            self.GLib.Variant(signature, arguments), None,
            self.Gio.DBusCallFlags.NONE, 5000, None)

    def pointer(self, stream, x, y):
        self._call(self.REMOTE, 'NotifyPointerMotionAbsolute', '(oa{sv}udd)',
            (self.session, {}, stream, x, y))

    def button(self, button, down):
        self._call(self.REMOTE, 'NotifyPointerButton', '(oa{sv}iu)',
            (self.session, {}, button, int(down)))

    def key(self, keycode, down):
        self._call(self.REMOTE, 'NotifyKeyboardKeycode', '(oa{sv}iu)',
            (self.session, {}, keycode, int(down)))

    def scroll(self, x, y):
        self._call(self.REMOTE, 'NotifyPointerAxis', '(oa{sv}dd)',
            (self.session, {}, x, y))

    def _selection_changed(self, _bus, _sender, _path, _interface, _signal, parameters):
        session, options = parameters.unpack()
        if session != self.session or self.closed:
            return
        types = options.get('mime_types', [])
        # GNOME 46 wraps the advertised string array in a one-element tuple.
        # Preserve the standard `as` value while accepting that observed `(as)`.
        if isinstance(types, tuple) and len(types) == 1 and isinstance(types[0], list):
            types = types[0]
        self.clipboard_types = types if isinstance(types, list) and all(isinstance(item, str) for item in types) else []
        if self.clipboard_enabled and self.clipboard_sync and not options.get('session_is_owner', False):
            self.read_clipboard(lambda text, error: self.clipboard_changed(text) if error is None else None)

    def _clipboard_fd(self, method, signature, arguments):
        value, descriptors = self.bus.call_with_unix_fd_list_sync(self.NAME, self.PATH, self.CLIPBOARD,
            method, self.GLib.Variant(signature, arguments), self.GLib.VariantType.new('(h)'),
            self.Gio.DBusCallFlags.NONE, 3000, None, None)
        return descriptors.get(value.unpack()[0])

    def read_clipboard(self, completed):
        if not self.clipboard or not self.clipboard_enabled or self.closed:
            completed(None, 'CLIPBOARD_UNAVAILABLE')
            return
        epoch = self.clipboard_epoch
        mime = next((name for name in ('text/plain;charset=utf-8', 'text/plain') if name in self.clipboard_types), None)
        if mime is None:
            completed('', None)
            return
        if not self.clipboard_reads.acquire(blocking=False):
            completed(None, 'CLIPBOARD_BUSY')
            return
        try:
            fd = self._clipboard_fd('SelectionRead', '(os)', (self.session, mime))
        except self.GLib.Error:
            self.clipboard_reads.release()
            completed(None, 'CLIPBOARD_UNAVAILABLE')
            return
        def read():
            data, error = bytearray(), None
            deadline = time.monotonic() + 3
            try:
                os.set_blocking(fd, False)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                        raise DesktopError('CLIPBOARD_TIMEOUT')
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > 1 << 20:
                        raise DesktopError('CLIPBOARD_TOO_LARGE')
                text = data.decode('utf-8')
                if '\0' in text:
                    raise DesktopError('CLIPBOARD_INVALID')
            except (OSError, UnicodeError, DesktopError) as failure:
                text, error = None, failure.code if isinstance(failure, DesktopError) else 'CLIPBOARD_UNAVAILABLE'
            finally:
                os.close(fd)
            def finish():
                try:
                    if not self.closed and self.clipboard_enabled and self.clipboard_epoch == epoch:
                        completed(text, error)
                finally:
                    self.clipboard_reads.release()
                return False
            self.GLib.idle_add(finish)
        threading.Thread(target=read, name='floe-clipboard-read', daemon=True).start()

    def set_clipboard(self, text):
        if not self.clipboard or not self.clipboard_enabled or self.closed:
            raise DesktopError('CLIPBOARD_UNAVAILABLE')
        if not isinstance(text, str) or len(text.encode()) > 1 << 20 or '\0' in text:
            raise DesktopError('CLIPBOARD_INVALID')
        self.clipboard_text = text
        self._call(self.CLIPBOARD, 'SetSelection', '(oa{sv})', (self.session,
            {'mime_types': self.GLib.Variant('as', ['text/plain;charset=utf-8', 'text/plain'])}))

    def _selection_transfer(self, _bus, _sender, _path, _interface, _signal, parameters):
        session, mime, serial = parameters.unpack()
        if session != self.session or self.closed or not self.clipboard_enabled or self.clipboard_text is None:
            return
        if mime not in ('text/plain;charset=utf-8', 'text/plain'):
            return
        if not self.clipboard_writes.acquire(blocking=False):
            try:
                self._call(self.CLIPBOARD, 'SelectionWriteDone', '(oub)', (session, serial, False))
            except self.GLib.Error:
                pass
            return
        try:
            fd = self._clipboard_fd('SelectionWrite', '(ou)', (self.session, serial))
        except self.GLib.Error:
            self.clipboard_writes.release()
            return
        data = self.clipboard_text.encode()
        epoch = self.clipboard_epoch
        def write():
            success = False
            try:
                os.set_blocking(fd, False)
                pending, deadline = memoryview(data), time.monotonic() + 3
                while pending and not self.closed and self.clipboard_enabled and self.clipboard_epoch == epoch:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
                        break
                    count = os.write(fd, pending[:65536])
                    if count <= 0:
                        break
                    pending = pending[count:]
                success = not pending
            except OSError:
                pass
            finally:
                os.close(fd)
            def done():
                try:
                    if not self.closed:
                        try:
                            self._call(self.CLIPBOARD, 'SelectionWriteDone', '(oub)', (session, serial, success))
                        except self.GLib.Error:
                            pass
                finally:
                    self.clipboard_writes.release()
                return False
            self.GLib.idle_add(done)
        threading.Thread(target=write, name='floe-clipboard-write', daemon=True).start()

    def release_start(self):
        if self.start_lease is not None:
            os.close(self.start_lease)
            self.start_lease = None

    def close(self):
        if self.closed:
            return
        self.closed = True
        for path, subscription in list(self.pending.items()):
            self.bus.signal_unsubscribe(subscription)
            try:
                self.bus.call_sync(self.NAME, path, 'org.freedesktop.portal.Request', 'Close',
                    None, None, self.Gio.DBusCallFlags.NONE, 1000, None)
            except self.GLib.Error:
                pass
        self.pending.clear()
        for subscription in self.subscriptions:
            self.bus.signal_unsubscribe(subscription)
        self.subscriptions.clear()
        if self.session:
            try:
                self.bus.call_sync(self.NAME, self.session, 'org.freedesktop.portal.Session', 'Close',
                    None, None, self.Gio.DBusCallFlags.NONE, 1000, None)
            except self.GLib.Error:
                pass
            self.session = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.release_start()
