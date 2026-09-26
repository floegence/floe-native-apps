"""Qualify real IBus and portal source identities without client-name inference."""
import os

from gi.repository import Gio, GLib
from application_peer import ApplicationPeer
from application_processes import ProcessTree, identity


def qualify(ibus, session_bus, portal_pid, runtime, surface_pid):
    context = ibus.active.input_path
    source = ibus.bus.get_connection().call_sync('org.freedesktop.IBus', '/org/freedesktop/IBus',
        'org.floegence.IBus.ContextSource', 'Describe', GLib.Variant('(o)', (context,)),
        GLib.VariantType.new('(usuubb)'), Gio.DBusCallFlags.NONE, 2000, None).unpack()
    version, owner, pid, uid, focused, post_process = source
    assert version == 1 and uid == os.getuid() and focused and post_process
    record = {'version': version, 'ibus_owner': owner, 'ibus_peer_pid': pid,
              'context': context, 'focused': focused, 'post_process': post_process}
    if pid == portal_pid:
        portal = session_bus.call_sync('org.freedesktop.portal.IBus', '/org/freedesktop/IBus',
            'org.floegence.IBus.ContextSource', 'Describe', GLib.Variant('(o)', (context,)),
            GLib.VariantType.new('(uso)'), Gio.DBusCallFlags.NONE, 2000, None).unpack()
        assert portal[0] == 1 and portal[1].startswith(':')
        credentials = session_bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetConnectionCredentials', GLib.Variant('(s)', (portal[1],)),
            GLib.VariantType.new('(a{sv})'), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
        assert credentials['UnixUserID'] == os.getuid()
        pid = credentials['ProcessID']
        record.update(portal_owner=portal[1], portal_context=portal[2], bus_proxy_pid=pid)
    tree = ProcessTree(os.getpid(), identity(os.getpid())[1])
    try:
        peer = ApplicationPeer(tree, pid, runtime)
        surface = ApplicationPeer(tree, surface_pid, runtime)
        try:
            assert peer.matches(surface), 'IBus source is not the actual native surface owner'
            record.update(native_pid=surface_pid, matched=True,
                          instance=peer.package.instance if peer.package else None)
        finally:
            peer.close()
            surface.close()
    finally:
        tree.close()
    return record
