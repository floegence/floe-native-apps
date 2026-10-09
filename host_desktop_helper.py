"""One authorized current-user desktop. Product session ownership stays in Go."""
import argparse
import hashlib
import math
import os
import signal
import sys
import threading
import time

from host_desktop_contract import DesktopAuthority, DesktopError, integer, text_value
from host_desktop_identity import HostIdentity, x11_credentials
from host_desktop_input import HeldInput, physical_key
from host_desktop_media import DesktopMedia, select_encoder
from host_desktop_portal import PortalGrant, PortalSession
from host_desktop_wire import Writer, read_command


def picture_value(value):
    if (not isinstance(value, dict) or set(value) - {'mode', 'max_dimension', 'frame_rate', 'audio', 'native_pixels'} or
            not {'mode', 'max_dimension', 'frame_rate', 'audio'}.issubset(value) or
            type(value.get('native_pixels', False)) is not bool or
            value['mode'] not in ('auto', 'clarity', 'smooth', 'data') or
            type(value['max_dimension']) is not int or value['max_dimension'] not in (1600, 1920, 2560, 3840, 4096) or
            type(value['frame_rate']) is not int or value['frame_rate'] not in (15, 30, 60) or
            type(value['audio']) is not bool):
        raise DesktopError('INVALID_ARGUMENT')
    return dict(value)


def portal_displays(streams):
    if not isinstance(streams, (list, tuple)) or not 1 <= len(streams) <= 64:
        raise DesktopError('DISPLAY_UNAVAILABLE')
    displays, nodes = [], {}
    for stream in streams:
        if not isinstance(stream, (list, tuple)) or len(stream) != 2:
            raise DesktopError('PORTAL_PROTOCOL_INVALID')
        node, properties = stream
        if not integer(node, 1, (1 << 32) - 1) or not isinstance(properties, dict):
            raise DesktopError('PORTAL_PROTOCOL_INVALID')
        size, position = properties.get('size'), properties.get('position', (0, 0))
        if (not isinstance(size, (list, tuple)) or len(size) != 2 or
                any(not integer(value, 2, 32768) for value in size)):
            raise DesktopError('DISPLAY_SIZE_UNAVAILABLE')
        if (not isinstance(position, (list, tuple)) or len(position) != 2 or
                any(not integer(value, -65536, 65536) for value in position)):
            raise DesktopError('PORTAL_PROTOCOL_INVALID')
        width, height = size
        x, y = position
        identity = properties.get('id') or str((x, y, width, height))
        if not text_value(identity, 4096):
            raise DesktopError('PORTAL_PROTOCOL_INVALID')
        display_id = 'portal-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
        if display_id in nodes or node in nodes.values():
            raise DesktopError('DISPLAY_IDENTITY_AMBIGUOUS')
        nodes[display_id] = node
        displays.append({'id': display_id, 'name': '', 'x': x, 'y': y, 'width': width,
                         'height': height, 'scale': 1, 'primary': x == 0 and y == 0})
    return displays, nodes


class HostDesktop:
    def __init__(self, Gst, Gio, GLib, state, control, media):
        self.Gst, self.Gio, self.GLib = Gst, Gio, GLib
        self.state_directory, self.output, self.media_output = state, control, media
        self.authority = DesktopAuthority()
        self.identity = self.backend = self.held = self.media = None
        self.encoder = None
        self.displays, self.streams = [], {}
        self.picture = None
        self.unattended = False
        self.authorization = 'unsupported'
        self.clipboard_sync = False
        self.lock_requested_at = None
        self.service_state = 'not_installed' if sys.platform.startswith(('linux', 'darwin')) else 'unsupported'
        self.login_service = None
        self.connected = self.connecting = False
        self.observer = GLib.timeout_add(500, self.observe)

    def emit(self, message):
        if message.get('type') == 'error':
            message = dict(message, authorization=self.authorization)
        self.output(dict(message, generation=self.authority.generation))

    def status(self, state, reason=None):
        self.emit({'type': 'state', 'state': state, 'code': reason or '',
                   'mode': self.authority.mode, 'display_id': self.authority.display or '',
                   'authorization': self.authorization})

    def capabilities(self):
        if self.identity is None:
            self.identity = HostIdentity(self.Gio, self.GLib, self.identity_changed)
        state = self.identity.refresh()
        portal = None
        if self.identity.backend == 'wayland':
            portal = PortalSession(self.identity.bus, self.Gio, self.GLib, PortalGrant(self.state_directory), lambda _: None)
            try:
                available = portal.version(portal.REMOTE) >= 1 and portal.version(portal.SCREEN) >= 1
                clipboard = portal.version(portal.CLIPBOARD) >= 1
                unattended = portal.version(portal.REMOTE) >= 2
                self.authorization = portal.grant.inspect() if unattended else 'unsupported'
            finally:
                portal.close()
        else:
            available, clipboard, unattended = True, True, True
            self.authorization = 'unsupported'
        audio = bool(self.Gst.ElementFactory.find('pulsesrc') and self.Gst.ElementFactory.find('opusenc'))
        return {'backend': self.identity.backend, 'state': state, 'screen': available, 'input': available,
                'clipboard': clipboard, 'audio': audio, 'unattended': unattended,
                'unlock': bool(self.login_service and self.service_state == 'active'),
                'locked_screen': bool(self.login_service and self.service_state == 'active'),
                'service': self.service_state,
                'encoder': self.encoder[0] if self.encoder else '', 'displays': self.displays,
                'authorization': self.authorization}

    def command(self, command):
        request = command['id']
        try:
            method = command.get('method')
            common = {'version', 'id', 'method'}
            fields = {'probe': set(), 'disconnect': set(), 'forget_authorization': set(),
                'connect': {'display_id', 'mode', 'picture', 'unattended'},
                'configure': {'generation', 'picture'}, 'set_mode': {'generation', 'mode'},
                'select_display': {'generation', 'display_id'}, 'frame_ack': {'generation', 'frame_id'},
                'input': {'generation', 'input'}, 'set_clipboard': {'generation', 'text'},
                'get_clipboard': {'generation'}, 'release_input': {'generation'},
                'set_clipboard_sync': {'generation', 'enabled'},
                'keyframe': {'generation'}, 'lock': {'generation'},
                'unlock_input': {'generation', 'frame_id', 'input'}, 'unlock_cancel': {'generation'},
                'service_status': {'service'}, 'service_install': {'service'},
                'service_uninstall': {'service'}, 'login_session': {'service'}}
            if not isinstance(method, str) or method not in fields or set(command) - common - fields[method]:
                raise DesktopError('INVALID_ARGUMENT')
            if method == 'probe':
                self.emit({'type': 'capabilities', 'id': request, 'capabilities': self.capabilities()})
                return False
            if method == 'service_status':
                self.emit({'type': 'service_status', 'id': request, 'service': 'login-screen',
                           'service_status': {'state': self.service_state, 'backend': sys.platform}})
                return False
            if method in ('service_install', 'service_uninstall', 'login_session'):
                if command.get('service') != 'login-screen':
                    raise DesktopError('INVALID_ARGUMENT')
                if method == 'service_install':
                    # Elevation is deliberately owned by the caller's explicit
                    # UI flow; the unprivileged helper never invokes it.
                    self.service_state = 'authorization_required'
                    self.emit({'type': 'service_status', 'id': request, 'service': 'login-screen',
                               'service_status': {'state': self.service_state, 'backend': sys.platform},
                               'code': 'ADMIN_AUTHORIZATION_REQUIRED'})
                elif method == 'service_uninstall':
                    self.service_state = 'not_installed'
                    self.emit({'type': 'service_status', 'id': request, 'service': 'login-screen',
                               'service_status': {'state': self.service_state, 'backend': sys.platform}})
                else:
                    if self.service_state != 'active':
                        raise DesktopError('LOGIN_SERVICE_UNAVAILABLE')
                    self.emit({'type': 'result', 'id': request})
                return False
            if method == 'forget_authorization':
                if self.connected or self.connecting:
                    raise DesktopError('AUTHORIZATION_PENDING')
                if self.capabilities()['backend'] != 'wayland':
                    raise DesktopError('UNATTENDED_UNAVAILABLE')
                PortalGrant(self.state_directory).forget()
                self.authorization = 'needs_consent'
            elif method == 'disconnect':
                self.disconnect()
            elif method == 'connect':
                self.connect(command)
                return False
            else:
                generation = command.get('generation')
                if not integer(generation, 1, (1 << 53) - 1) or generation != self.authority.generation:
                    raise DesktopError('STALE_DESKTOP')
                if method == 'set_mode':
                    mode = command.get('mode')
                    if mode not in ('view', 'control'):
                        raise DesktopError('INVALID_ARGUMENT')
                    self.transition(self.authority.display, mode)
                elif method == 'release_input':
                    if self.held:
                        self.held.release()
                elif method == 'set_clipboard_sync':
                    enabled = command.get('enabled')
                    if type(enabled) is not bool:
                        raise DesktopError('INVALID_ARGUMENT')
                    if enabled:
                        self.authority.input(generation)
                        self.require_active()
                    self.clipboard_sync = enabled
                    self.clipboard_enabled()
                elif method == 'configure':
                    picture = picture_value(command.get('picture'))
                    self.picture = picture
                    self.transition(self.authority.display, self.authority.mode)
                elif method == 'select_display':
                    self.transition(command.get('display_id'), self.authority.mode)
                elif method == 'frame_ack':
                    self.authority.paint(generation, command.get('frame_id'))
                    if self.media:
                        self.media.acknowledge(command['frame_id'])
                    self.clipboard_enabled()
                elif method == 'keyframe':
                    self.recover_decoder()
                elif method == 'unlock_input':
                    if self.authority.state != 'locked' or not self.login_service:
                        raise DesktopError('LOGIN_SERVICE_UNAVAILABLE')
                    self.authority.unlock_input(generation, command.get('frame_id'))
                    self.login_service.input(command.get('input'))
                elif method == 'unlock_cancel':
                    if self.authority.state != 'locked':
                        raise DesktopError('DESKTOP_NOT_LOCKED')
                    if self.login_service:
                        self.login_service.cancel()
                else:
                    self.authority.input(generation)
                    self.require_active()
                    if method == 'input':
                        self.input(command.get('input'))
                        if self.media:
                            self.media.interacted()
                    elif method == 'set_clipboard':
                        value = command.get('text')
                        if not text_value(value, 1 << 20):
                            raise DesktopError('INVALID_ARGUMENT')
                        self.backend.set_clipboard(value)
                    elif method == 'get_clipboard':
                        def received(value, error):
                            if generation != self.authority.generation:
                                return
                            self.emit({'type': 'error', 'id': request, 'code': error} if error else
                                      {'type': 'clipboard', 'id': request, 'text': value})
                        self.backend.read_clipboard(received)
                        return False
                    elif method == 'lock':
                        self.identity.lock()
                        self.lock_requested_at = time.monotonic()
                        self.suspend('locking')
            self.emit({'type': 'result', 'id': request})
        except DesktopError as error:
            self.emit({'type': 'error', 'id': request, 'code': error.code})
        except self.GLib.Error:
            self.suspend('permission_revoked')
            self.emit({'type': 'error', 'id': request, 'code': 'DESKTOP_OS_FAILED'})
        return False

    def require_active(self):
        state = self.identity.state() if self.identity else 'session_unavailable'
        if state != 'ready':
            self.suspend(state)
            raise DesktopError('DESKTOP_NOT_ACTIVE')
        if self.media and not self.media.target_valid:
            self.media_failed(self.authority.generation, 'DISPLAY_GEOMETRY_CHANGED')
            raise DesktopError('DISPLAY_CHANGED')
        if self.connected and self.identity.backend == 'x11' and self.refresh_displays():
            raise DesktopError('DISPLAY_CHANGED')

    def refresh_displays(self):
        displays = self.backend.displays()
        if displays == self.displays:
            return False
        self.displays = displays
        self.suspend('DISPLAY_CHANGED')
        self.emit({'type': 'displays', 'displays': displays})
        return True

    def connect(self, command):
        if self.connected or self.connecting:
            raise DesktopError('SESSION_ALREADY_STARTED')
        picture = picture_value(command.get('picture'))
        mode = command.get('mode')
        if mode not in ('control', 'view') or type(command.get('unattended', False)) is not bool:
            raise DesktopError('INVALID_ARGUMENT')
        # A prior read-only probe must not pin a login that has since ended.
        self.close_identity()
        self.capabilities()
        self.require_active()
        if self.encoder is None:
            self.encoder = select_encoder(self.Gst)
        self.picture, self.unattended = picture, command.get('unattended', False)
        self.connecting = True
        self.status('authorizing')
        request = command['id']
        if self.identity.backend == 'wayland':
            portal = PortalSession(self.identity.bus, self.Gio, self.GLib, PortalGrant(self.state_directory),
                self.portal_changed, self.clipboard_changed)
            self.backend = portal
            def started(streams, error):
                if self.backend is not portal:
                    return
                if error:
                    self.disconnect()
                    self.emit({'type': 'error', 'id': request, 'code': error})
                    return
                try:
                    self.displays, self.streams = portal_displays(streams)
                    if command['mode'] == 'control' and portal.devices & 3 != 3:
                        raise DesktopError('INPUT_PERMISSION_REQUIRED')
                except DesktopError as failure:
                    self.disconnect()
                    self.emit({'type': 'error', 'id': request, 'code': failure.code})
                    return
                self.finish_connect(command)
            portal.start(self.unattended, started)
        else:
            from host_desktop_x11 import X11Desktop
            try:
                name, authority = x11_credentials(self.identity.selected, os.getuid())
                os.environ['XAUTHORITY'] = authority
                self.backend = X11Desktop(name, self.GLib, self.clipboard_changed)
                self.displays = self.backend.displays()
                self.finish_connect(command)
            except Exception:
                self.disconnect()
                raise DesktopError('X11_DESKTOP_UNAVAILABLE') from None

    def finish_connect(self, command):
        try:
            self.require_active()
            self.held = HeldInput(self.backend)
            self.connected, self.connecting = True, False
            display_id = command.get('display_id')
            if not any(display['id'] == display_id for display in self.displays):
                display_id = next((display['id'] for display in self.displays if display['primary']),
                                  self.displays[0]['id'] if self.displays else None)
            self.emit({'type': 'displays', 'displays': self.displays})
            self.transition(display_id, command['mode'])
            self.emit({'type': 'result', 'id': command['id']})
        except DesktopError as error:
            self.disconnect()
            self.emit({'type': 'error', 'id': command['id'], 'code': error.code})

    def transition(self, display_id, mode):
        selected = next((display for display in self.displays if display['id'] == display_id), None)
        if not selected or not self.connected:
            raise DesktopError('DISPLAY_UNAVAILABLE')
        self.require_active()
        if mode == 'control' and self.identity.backend == 'wayland' and self.backend.devices & 3 != 3:
            raise DesktopError('INPUT_PERMISSION_REQUIRED')
        self.stop_media()
        generation = self.authority.bind(display_id, mode)
        self.clipboard_enabled()
        media = DesktopMedia(self.Gst, self.GLib, generation, self.picture, self.encoder,
            self.pixels, lambda code: self.media_failed(generation, code))
        self.media = media
        try:
            if self.identity.backend == 'wayland':
                media.start_pipewire(self.backend.fd, self.streams[display_id],
                    metadata_cursor=self.backend.cursor_mode == 4, local_cursor=mode == 'control')
            else:
                media.start_x11(self.backend.name, (selected['x'], selected['y'], selected['width'], selected['height']), local_cursor=mode == 'control')
            self.status('active')
        except DesktopError:
            self.suspend('capture_failed')
            raise

    def pixels(self, message, payload):
        if message['generation'] != self.authority.generation or self.authority.state != 'active':
            return
        if message['type'] == 'frame':
            self.authority.sent(message['frame_id'])
        self.media_output(message, payload)

    def recover_decoder(self):
        self.require_active()
        if self.authority.state != 'active' or not self.media:
            raise DesktopError('DESKTOP_NOT_ACTIVE')
        self.release_input()
        generation = self.authority.bind(self.authority.display, self.authority.mode)
        # A decoder reset retires encoded dependencies and paint authority, but
        # leaves the already authorized capture/portal session running.
        self.media.failed = lambda code: self.media_failed(generation, code)
        self.media.recover(generation)
        self.status('active')

    def clipboard_enabled(self):
        if self.backend:
            self.backend.clipboard_sync = self.clipboard_sync
            self.backend.clipboard_enabled = (self.authority.state == 'active' and
                self.authority.mode == 'control' and bool(self.authority.last_painted))

    def clipboard_changed(self, value):
        if self.clipboard_sync and self.authority.mode == 'control' and self.authority.last_painted and self.authority.state == 'active':
            self.emit({'type': 'clipboard', 'text': value})

    def input(self, value):
        if not isinstance(value, dict):
            raise DesktopError('INVALID_ARGUMENT')
        kind = value.get('kind')
        if kind == 'key':
            if type(value.get('pressed', False)) is not bool:
                raise DesktopError('INVALID_ARGUMENT')
            self.held.key(physical_key(value.get('code')), value.get('pressed', False))
        elif kind in ('text', 'paste'):
            text = value.get('text')
            if not text_value(text, 16000) or not text:
                raise DesktopError('INVALID_ARGUMENT')
            if self.held.keys or self.held.buttons:
                raise DesktopError('INPUT_KEYS_HELD')
            self.backend.set_clipboard(text)
            self.held.paste()
        elif kind in ('move', 'down', 'up', 'scroll'):
            x, y, dx, dy = (value.get(key, 0) for key in ('x', 'y', 'dx', 'dy'))
            if (any(type(number) not in (int, float) or not math.isfinite(number) for number in (x, y, dx, dy))
                    or not 0 <= x <= 1 or not 0 <= y <= 1 or abs(dx) > 10000 or abs(dy) > 10000):
                raise DesktopError('INVALID_ARGUMENT')
            button = value.get('button', 0)
            if not integer(button, 0, 4):
                raise DesktopError('INVALID_ARGUMENT')
            display = next(item for item in self.displays if item['id'] == self.authority.display)
            if self.identity.backend == 'wayland':
                # Portal monitor geometry may be scaled; absolute input targets
                # the negotiated stream, before any viewer encoding resize.
                if self.media is None:
                    raise DesktopError('DESKTOP_NOT_ACTIVE')
                width, height = self.media.input_size()
            else:
                width, height = display['width'], display['height']
            x, y = min(x * width, width - 1), min(y * height, height - 1)
            if self.identity.backend == 'x11':
                x, y = x + display['x'], y + display['y']
            self.backend.pointer(self.streams.get(self.authority.display, 0), x, y)
            if kind in ('down', 'up'):
                self.held.button((272, 274, 273, 275, 276)[button], kind == 'down')
            elif kind == 'scroll':
                self.backend.scroll(dx, dy)
        else:
            raise DesktopError('INPUT_UNSUPPORTED')

    def media_failed(self, generation, code):
        if generation == self.authority.generation:
            if code in ('DISPLAY_GEOMETRY_CHANGED', 'DISPLAY_SIZE_UNSUPPORTED', 'DISPLAY_STREAM_LOST'):
                self.disconnect()
                self.displays, self.streams = [], {}
                self.status('reconnect_required', code)
            else:
                self.suspend(code)
        return False

    def portal_changed(self, state):
        if state in ('INPUT_DELIVERY_FAILED', 'INPUT_BACKPRESSURE'):
            self.disconnect()
            self.status('reconnect_required', state)
        elif state.startswith('authorization_'):
            self.authorization = state.removeprefix('authorization_')
            if self.connecting:
                self.status('authorizing', state)
        elif state == 'permission_revoked':
            if self.connecting:
                self.disconnect()
                self.status('reconnect_required', state)
            else:
                self.suspend(state)
        elif self.connecting:
            self.status('authorizing', state)

    def release_input(self):
        if self.held:
            self.held.release()
        if self.backend:
            self.backend.clipboard_enabled = False
            self.backend.clipboard_epoch = getattr(self.backend, 'clipboard_epoch', 0) + 1
            self.backend.clipboard_text = None

    def stop_media(self):
        self.release_input()
        if self.media:
            self.media.close()
            self.media = None

    def suspend(self, reason):
        self.stop_media()
        self.authority.revoke(reason)
        self.status('suspended', reason)
        return False

    def observe(self):
        if not self.connected and not self.connecting:
            return True
        state = self.identity.refresh()
        self.identity_changed(state)
        if not self.connected or state != 'ready':
            return True
        if self.authority.state == 'locking':
            if time.monotonic() - self.lock_requested_at >= 2:
                self.suspend('LOCK_NOT_CONFIRMED')
        elif self.authority.state == 'locked':
            try:
                self.transition(self.authority.display, self.authority.mode)
            except DesktopError:
                self.suspend('RECONNECT_REQUIRED')
        elif self.identity.backend == 'x11' and self.authority.state in ('active', 'DISPLAY_CHANGED'):
            self.refresh_displays()
        return True

    def identity_changed(self, state):
        if state == 'ready' or not (self.connected or self.connecting):
            return
        if self.connecting:
            # Closing the pending portal request prevents late consent from
            # admitting a different or locked login.
            self.disconnect()
            self.status('reconnect_required', state)
        elif self.authority.state != state:
            self.suspend(state)

    def close_identity(self):
        if self.identity:
            identity, self.identity = self.identity, None
            identity.close()

    def disconnect(self):
        self.stop_media()
        if self.backend:
            self.backend.close()
            self.backend = None
        self.connected = self.connecting = False
        self.clipboard_sync = False
        self.lock_requested_at = None
        self.held = None
        self.authority.revoke('disconnected')
        self.close_identity()
        self.displays, self.streams = [], {}
        self.status('disconnected')

    def close(self):
        if self.observer:
            self.GLib.source_remove(self.observer)
            self.observer = None
        self.disconnect()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', required=True)
    parser.add_argument('--media-fd', type=int, required=True)
    args = parser.parse_args()
    PortalGrant(args.state)  # Require a private, existing state directory.
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst, Gio, GLib
    Gst.init(None)
    loop = GLib.MainLoop()
    def failed(_code):
        def stop():
            desktop.disconnect()
            loop.quit()
            return False
        GLib.idle_add(stop, priority=GLib.PRIORITY_HIGH)
    control = Writer(1, failed, 64, 16 << 20)
    media = Writer(args.media_fd, failed, 16, 80 << 20)
    desktop = HostDesktop(Gst, Gio, GLib, args.state, control.send, media.send)
    for signum in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signum, lambda: (failed('PROCESS_STOPPED'), False)[1])
    slots = threading.BoundedSemaphore(64)
    def read():
        # SIGTERM may leave the command pipe open. A daemon must not hold the
        # interpreter-owned stdin buffer lock during interpreter finalization.
        source = os.fdopen(os.dup(0), 'rb')
        try:
            while True:
                slots.acquire()
                command = read_command(source)
                if command is None:
                    break
                def dispatch(value=command):
                    try:
                        return desktop.command(value)
                    finally:
                        slots.release()
                GLib.idle_add(dispatch)
        except (OSError, DesktopError):
            pass
        source.close()
        failed('TRANSPORT_CLOSED')
    threading.Thread(target=read, name='floe-desktop-input', daemon=True).start()
    try:
        loop.run()
    finally:
        desktop.close()
        control.close()
        media.close()


if __name__ == '__main__':
    try:
        main()
    except DesktopError as error:
        print(error.code, file=sys.stderr)
        sys.exit(1)
