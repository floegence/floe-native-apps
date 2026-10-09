"""Client text for the administrator-authorized GNOME desktop service.

The root attachment admits each operation. This fixed child drops all privileges,
binds the real user session, and uses Mutter's selection transfer protocol. No
Portal, desktop consent, keymap modification, clipboard history or content logs.
"""
import os
import select
import socket
import sys
import threading
import time

from host_desktop_contract import DesktopError, text_value
from host_desktop_wire import packet, read_command


class LoginText:
    NAME = 'org.gnome.Mutter.RemoteDesktop'
    PATH = '/org/gnome/Mutter/RemoteDesktop'
    MIME = 'text/plain;charset=utf-8'

    def __init__(self, Gio, GLib, identity, completed, prepared, session, compositor):
        self.Gio, self.GLib, self.identity = Gio, GLib, identity
        self.completed = completed
        self.prepared = prepared
        self.bus = identity.bus
        self.session, self.compositor = session, compositor
        self.closed = False
        self.active()
        owner = self.bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', (self.NAME,)),
            None, Gio.DBusCallFlags.NONE, 500, None).unpack()[0]
        pid = self.bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetConnectionUnixProcessID', GLib.Variant('(s)', (owner,)),
            None, Gio.DBusCallFlags.NONE, 500, None).unpack()[0]
        if str(pid) != compositor.split(':')[0]:
            raise DesktopError('CLIPBOARD_SESSION_RETIRED')
        self.owner = owner
        self.path = None
        self.text = None
        self.request = None
        self.timer = None
        # A clipboard manager and the focused application can request the same
        # selection concurrently. Rejecting the second reader loses user text.
        self.writes = threading.BoundedSemaphore(4)
        self.subscription = self.bus.signal_subscribe(self.NAME, self.NAME + '.Session',
            'SelectionTransfer', None, None, Gio.DBusSignalFlags.NONE, self.transfer)
        self.path = self.call('CreateSession', root=True).unpack()[0]
        self.call('Start')
        self.call('EnableClipboard', '(a{sv})', ({},))

    def active(self):
        if self.closed or self.identity.refresh() != 'ready' or self.identity.selected.get('id') != self.session:
            raise DesktopError('CLIPBOARD_SESSION_RETIRED')
        with open('/sys/class/tty/tty0/active', encoding='ascii') as terminal:
            if terminal.read().strip() != 'tty' + str(self.identity.observed['VTNr']):
                raise DesktopError('CLIPBOARD_SESSION_RETIRED')
        pid, started = self.compositor.split(':')
        with open('/proc/' + pid + '/stat', encoding='ascii') as source:
            current = source.read().rsplit(')', 1)[1].split()[19]
        if current != started or os.readlink('/proc/' + pid + '/exe') != '/usr/bin/gnome-shell':
            raise DesktopError('CLIPBOARD_SESSION_RETIRED')

    def call(self, method, signature=None, values=(), root=False):
        self.active()
        return self.bus.call_sync(self.owner, self.PATH if root else self.path,
            self.NAME if root else self.NAME + '.Session', method,
            self.GLib.Variant(signature, values) if signature else None,
            None, self.Gio.DBusCallFlags.NONE, 500, None)

    def paste(self, request):
        if self.request is not None or not text_value(request.get('text'), 16000) or not request.get('text'):
            raise DesktopError('CLIPBOARD_INPUT_REJECTED')
        self.active()
        self.text, self.request = request['text'], request['id']
        self.call('SetSelection', '(a{sv})', ({'mime-types': self.GLib.Variant('as', [self.MIME])},))
        self.timer = self.GLib.timeout_add(800, lambda: self.finish('CLIPBOARD_TARGET_UNAVAILABLE'))
        self.prepared(self.request)

    def finish(self, code=None):
        if self.timer is not None:
            self.GLib.source_remove(self.timer)
            self.timer = None
        request, self.request = self.request, None
        if request is not None:
            self.completed(request, code)
        return False

    def transfer(self, _bus, _sender, path, _iface, _signal, parameters):
        if path != self.path or self.closed:
            return
        mime, serial = parameters.unpack()
        if mime != self.MIME or self.text is None or not self.writes.acquire(blocking=False):
            self.call('SelectionWriteDone', '(ub)', (serial, False))
            return
        try:
            self.active()
            result, files = self.bus.call_with_unix_fd_list_sync(self.owner, self.path,
                self.NAME + '.Session', 'SelectionWrite', self.GLib.Variant('(u)', (serial,)),
                None, self.Gio.DBusCallFlags.NONE, 500, None, None)
            fd = files.get(result.unpack()[0])
            data = self.text.encode()
            request = self.request
            os.set_blocking(fd, False)
        except Exception:
            self.writes.release()
            self.finish('CLIPBOARD_TRANSFER_FAILED')
            return
        def write():
            success = False
            try:
                pending = memoryview(data)
                deadline = time.monotonic() + .5
                while pending and not self.closed and time.monotonic() < deadline:
                    if not select.select([], [fd], [], .05)[1]:
                        continue
                    try:
                        pending = pending[os.write(fd, pending):]
                    except BlockingIOError:
                        continue
                success = not pending and not self.closed
            except OSError:
                pass
            finally:
                os.close(fd)
                def done():
                    try:
                        self.call('SelectionWriteDone', '(ub)', (serial, success))
                        if request == self.request:
                            if not success:
                                self.finish('CLIPBOARD_TRANSFER_FAILED')
                            else:
                                # SelectionWriteDone admits bytes to Mutter; it
                                # does not acknowledge document insertion. Keep
                                # this selection stable for a 34 ms quiet period
                                # before admitting another paste, extending it if
                                # another reader requests the same selection.
                                if self.timer is not None:
                                    self.GLib.source_remove(self.timer)
                                self.timer = self.GLib.timeout_add(34, lambda: self.finish())
                    except Exception:
                        self.finish('CLIPBOARD_SESSION_RETIRED')
                    finally:
                        self.writes.release()
                    return False
                self.GLib.idle_add(done)
        threading.Thread(target=write, name='floe-login-text-transfer', daemon=True).start()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.text = None
        self.finish('CLIPBOARD_SESSION_RETIRED')
        self.bus.signal_unsubscribe(self.subscription)
        if self.path:
            try:
                self.bus.call_sync(self.owner, self.path, self.NAME + '.Session', 'Stop',
                    None, None, self.Gio.DBusCallFlags.NONE, 500, None)
            except Exception:
                pass
            self.path = None


def main():
    if os.geteuid() == 0:
        raise SystemExit(77)
    with open('/proc/self/status', encoding='ascii') as status:
        if any(line.startswith(('CapEff:', 'CapPrm:', 'CapInh:', 'CapAmb:')) and int(line.split()[1], 16) for line in status):
            raise SystemExit(77)
    import gi
    from gi.repository import Gio, GLib
    from host_desktop_identity import HostIdentity
    loop = GLib.MainLoop()
    connection = socket.socket(fileno=3)
    writer_lock = threading.Lock()
    def completed(request, code):
        with writer_lock:
            connection.sendall(packet({'type': 'error' if code else 'result', 'id': request,
                **({'code': code} if code else {})}))
    def prepared(request):
        with writer_lock:
            connection.sendall(packet({'type': 'result', 'code': 'TEXT_SELECTION_READY', 'id': request}))
    identity = HostIdentity(Gio, GLib, lambda state: loop.quit() if state != 'ready' else None)
    backend = LoginText(Gio, GLib, identity, completed, prepared, *sys.argv[1:])
    def command(request):
        try:
            if request.get('type') != 'clipboard' or request.get('bytes', 0) != 0 or set(request) - {'version', 'id', 'type', 'text', 'bytes'}:
                raise DesktopError('CLIPBOARD_INPUT_REJECTED')
            backend.paste(request)
        except DesktopError as error:
            completed(request['id'], error.code)
        except Exception:
            completed(request['id'], 'CLIPBOARD_UNAVAILABLE')
            loop.quit()
        return False
    def read():
        try:
            with connection.makefile('rb') as source:
                while True:
                    request = read_command(source)
                    if request is None:
                        break
                    GLib.idle_add(command, request)
        except Exception:
            pass
        GLib.idle_add(loop.quit)
    threading.Thread(target=read, name='floe-login-text-control', daemon=True).start()
    try:
        loop.run()
    finally:
        backend.close()
        identity.close()
        connection.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit(1) from None
