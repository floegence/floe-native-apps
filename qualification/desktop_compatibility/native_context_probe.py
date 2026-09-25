"""Private native Qt context experiment, not the production admission boundary.

The bus caller is checked against the fixture process tree. The production
surface/process/generation binding is deliberately not claimed by this probe.
"""
import os
from threading import Condition

from gi.repository import Gio, GLib
from marker_probe import MarkerTransactions, marker_command
from application_processes import ProcessTree, identity

INTERFACE = "org.floegence.ClientInput"
PATH = "/org/floegence/ClientInput"
XML = '''<node><interface name="org.floegence.ClientInput">
<method name="Register"/>
<method name="Take"><arg type="u" direction="in"/><arg type="u" direction="out"/><arg type="s" direction="out"/></method>
<method name="Released"><arg type="u" direction="in"/></method>
<method name="Done"><arg type="u" direction="in"/></method>
</interface></node>'''


class NativeContextProbe:
    def __init__(self, connection, service, record):
        self.connection, self.record = connection, record
        self.owner, self.owner_start = os.getpid(), identity(os.getpid())[1]
        self.tree, self.peers = ProcessTree(self.owner, self.owner_start), {}
        self.clients, self.transactions = {}, MarkerTransactions()
        self.current, self.sequence = None, 0
        self.completed = None
        self.condition = Condition()
        self.node = Gio.DBusNodeInfo.new_for_xml(XML)
        self.registration = connection.register_object(PATH, self.node.interfaces[0], self.call, None, None)
        self.subscription = connection.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus',
            'NameOwnerChanged', '/org/freedesktop/DBus', None, Gio.DBusSignalFlags.NONE, self.owner_changed)
        reply = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "RequestName", GLib.Variant("(su)", (service, 4)),
            GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 2000, None)
        if reply.unpack()[0] != 1:
            raise RuntimeError("Private native fixture service is already owned")

    def owner_changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        sender, _old, new = parameters.unpack()
        if new:
            return
        with self.condition:
            peer = self.peers.pop(sender, None)
            self.clients.pop(sender, None)
            if peer:
                peer.close()
            if self.current and self.current[2] == sender:
                completed = self.completed
                self.revoke()
                if completed:
                    completed('INPUT_CONTEXT_UNAVAILABLE')

    def call(self, connection, sender, _path, _interface, method, parameters, invocation):
        try:
            with self.condition:
                if method == "Register":
                    credentials = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                        "org.freedesktop.DBus", "GetConnectionCredentials", GLib.Variant("(s)", (sender,)),
                        GLib.VariantType.new("(a{sv})"), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
                    pid = credentials["ProcessID"]
                    if credentials["UnixUserID"] != os.getuid() or sender in self.peers:
                        raise ValueError("Native input client is outside the fixture tree")
                    peer = self.tree.admit(pid)
                    self.peers[sender] = peer
                    self.clients[sender] = (peer.pid, peer.started)
                    self.record({"native": "registered", "sender": sender, "pid": pid})
                    invocation.return_value(None)
                    return
                if not self.peers[sender].valid():
                    raise ValueError("Native input client is no longer owned")
                number = parameters.unpack()[0]
                if method == "Released":
                    self.transactions.key(number, True, True, owner=sender)
                elif method == "Take":
                    # Original peers may observe and retire their cancelled
                    # markers. Other peers must not consume even a tombstone.
                    text = self.transactions.key(number, False, True, owner=sender)
                    if self.current is None or self.current[0] != number or self.current[2] != sender:
                        raise ValueError("Native input marker is unavailable")
                    if text is None:
                        raise ValueError("Native input marker is unavailable")
                    invocation.return_value(GLib.Variant("(us)", (self.current[1], text)))
                    return
                elif method == "Done":
                    if self.current is None or self.current[1:] != (number, sender):
                        raise ValueError("Native input completion is unavailable")
                    self.record({"native": "completed", "sequence": number})
                    self.current = None
                    completed, self.completed = self.completed, None
                    if completed:
                        completed(None)
                    self.condition.notify_all()
                else:
                    raise ValueError("Native input method is unavailable")
                invocation.return_value(None)
        except (KeyError, ValueError, OSError) as error:
            invocation.return_dbus_error(INTERFACE + ".Unavailable", str(error))

    def enqueue(self, text, completed=None):
        with self.condition:
            if self.current is not None or len(self.clients) != 1:
                raise RuntimeError("Native input context is unavailable")
            sender = next(iter(self.clients))
            code = self.transactions.enqueue(text, owner=sender)
            self.sequence += 1
            self.current = (code, self.sequence, sender)
            self.completed = completed
            return marker_command(code)

    def revoke(self):
        with self.condition:
            self.transactions.revoke()
            self.current = None
            self.completed = None
            self.condition.notify_all()

    def wait_completed(self, timeout):
        with self.condition:
            if not self.condition.wait_for(lambda: self.current is None, timeout):
                raise RuntimeError("Toolkit event-loop completion was not received")

    @property
    def pending(self):
        with self.condition:
            return self.current

    def close(self):
        self.revoke()
        self.connection.signal_unsubscribe(self.subscription)
        self.connection.unregister_object(self.registration)
        self.tree.close()
        self.peers.clear()
        self.clients.clear()
