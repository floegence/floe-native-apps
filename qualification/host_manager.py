"""Observe host user-manager readiness in a disposable qualification runner."""
import os
import time


def await_owner(observe, uid, timeout=10.0, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + timeout
    while True:
        owner = observe()
        if owner is not None:
            name, actual_uid = owner
            if actual_uid != uid:
                raise RuntimeError('Scope manager does not belong to the host user')
            return name
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError('Host user manager did not register on the session bus')
        sleep(min(0.1, remaining))


def main():
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def observe():
        try:
            owner = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'GetNameOwner',
                GLib.Variant('(s)', ('org.freedesktop.systemd1',)),
                GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
        except GLib.Error as error:
            if Gio.DBusError.get_remote_error(error) == 'org.freedesktop.DBus.Error.NameHasNoOwner':
                return None
            raise
        credentials = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetConnectionCredentials', GLib.Variant('(s)', (owner,)),
            GLib.VariantType.new('(a{sv})'), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
        return owner, credentials['UnixUserID']

    owner = await_owner(observe, os.getuid())
    print('Host user-systemd owner and kernel credentials verified: ' + owner)


if __name__ == '__main__':
    main()
