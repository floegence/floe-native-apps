"""Ordered nonblocking Portal input against a private synthetic D-Bus service.

Uses a task-owned bus and fixture session; no OS authorization or input occurs.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def client(args):
    sys.path.insert(0, str(Path(args.helper).resolve()))
    from gi.repository import Gio, GLib
    from host_desktop_portal import PortalSession
    from host_desktop_input import HeldInput
    bus = Gio.DBusConnection.new_for_address_sync(args.address,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    flags = Gio.DBusCallFlags.NONE
    xml = '''<node>
      <interface name="org.freedesktop.portal.RemoteDesktop">
        <method name="NotifyPointerMotionAbsolute"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="u" direction="in"/><arg type="d" direction="in"/><arg type="d" direction="in"/></method>
        <method name="NotifyPointerButton"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="in"/></method>
        <method name="NotifyKeyboardKeycode"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="in"/></method>
        <method name="NotifyPointerAxis"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="d" direction="in"/><arg type="d" direction="in"/></method>
      </interface></node>'''
    received, states, replies = [], [], []
    loop = GLib.MainLoop()
    started = time.monotonic()
    def method(connection, sender, path, interface, name, parameters, invocation):
        received.append((name, parameters.unpack(), time.monotonic() - started))
        # Hold every reply until all events arrive. A synchronous client would
        # stall the main loop. This needs no VM performance acceptance line.
        replies.append(invocation)
        if len(replies) == 7:
            for pending in replies:
                pending.return_value(GLib.Variant('()', ()))
    service = Gio.DBusConnection.new_for_address_sync(args.address,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    service.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'RequestName',
        GLib.Variant('(su)', (PortalSession.NAME, 0)), None, flags, 1000, None)
    registration = service.register_object(PortalSession.PATH, Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0], method, None, None)
    portal = PortalSession(bus, Gio, GLib, None, states.append)
    portal.session = PortalSession.PATH + '/session/fixture'
    held = HeldInput(portal)
    portal.pointer(7, 10, 20)
    held.button(272, True)
    portal.pointer(7, 30, 40)
    portal.scroll(0, 3)
    held.key(29, True)
    held.release()
    def finished():
        if portal.input_pending:
            return True
        loop.quit()
        return False
    GLib.timeout_add(10, finished)
    GLib.timeout_add(3000, lambda: (loop.quit(), False)[1])
    try:
        loop.run()
        assert portal.input_pending == 0 and states == [], (portal.input_pending, states)
        assert [r[0] for r in received] == ['NotifyPointerMotionAbsolute', 'NotifyPointerButton',
            'NotifyPointerMotionAbsolute', 'NotifyPointerAxis', 'NotifyKeyboardKeycode',
            'NotifyKeyboardKeycode', 'NotifyPointerButton']
        assert received[-2][1][-1] == received[-1][1][-1] == 0
        print(json.dumps({'ordered_events': len(received), 'pending': portal.input_pending,
            'delivery_span_ms': round(1000 * (received[-1][2] - received[0][2]), 2)}))
    finally:
        portal.session = None
        portal.close()
        service.unregister_object(registration)
        bus.close_sync(None)
        service.close_sync(None)


def run(args):
    with tempfile.TemporaryDirectory(prefix='floe-portal-input-') as directory:
        bus = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--print-address=1',
            '--address=unix:path=' + directory + '/bus'], stdout=subprocess.PIPE, text=True)
        try:
            address = bus.stdout.readline().strip()
            subprocess.run([str(Path(args.helper).resolve() / 'python3'), str(Path(__file__).resolve()),
                '--helper', args.helper, '--address', address], timeout=10, check=True)
        finally:
            bus.terminate()
            bus.wait(timeout=3)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--helper', required=True)
    parser.add_argument('--address')
    arguments = parser.parse_args()
    client(arguments) if arguments.address else run(arguments)
