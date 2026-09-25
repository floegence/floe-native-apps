"""Private native Qt context experiment, not the production admission boundary.

The bus caller is checked against the fixture process tree. The production
surface/process/generation binding is deliberately not claimed by this probe.
"""
import os
from threading import Condition

from gi.repository import Gio, GLib
from marker_probe import MarkerTransactions, marker_command
from application_processes import identity

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
        self.clients, self.transactions = {}, MarkerTransactions()
        self.current, self.sequence = None, 0
        self.completed = None
        self.condition = Condition()
        self.node = Gio.DBusNodeInfo.new_for_xml(XML)
        self.registration = connection.register_object(PATH, self.node.interfaces[0], self.call, None, None)
        reply = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "RequestName", GLib.Variant("(su)", (service, 4)),
            GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 2000, None)
        if reply.unpack()[0] != 1:
            raise RuntimeError("Private native fixture service is already owned")

    def owned(self, pid):
        if identity(self.owner)[1] != self.owner_start:
            return False
        for _ in range(128):
            parent, _ticks = identity(pid)
            if parent == self.owner:
                return True
            if parent <= 1 or parent == pid:
                return False
            pid = parent
        return False

    def call(self, connection, sender, _path, _interface, method, parameters, invocation):
        try:
            with self.condition:
                if method == "Register":
                    credentials = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                        "org.freedesktop.DBus", "GetConnectionCredentials", GLib.Variant("(s)", (sender,)),
                        GLib.VariantType.new("(a{sv})"), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
                    pid = credentials["ProcessID"]
                    if credentials["UnixUserID"] != os.getuid() or not self.owned(pid):
                        raise ValueError("Native input client is outside the fixture tree")
                    self.clients[sender] = (pid, identity(pid)[1])
                    self.record({"native": "registered", "sender": sender, "pid": pid})
                    invocation.return_value(None)
                    return
                pid, ticks = self.clients[sender]
                if identity(pid)[1] != ticks or not self.owned(pid):
                    raise ValueError("Native input client is no longer owned")
                number = parameters.unpack()[0]
                if method == "Released":
                    self.transactions.key(number, True, True)
                elif method == "Take":
                    text = self.transactions.key(number, False, True)
                    if text is None or self.current is None or self.current[0] != number:
                        raise ValueError("Native input marker is unavailable")
                    self.current = (number, self.current[1], sender)
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
            code = self.transactions.enqueue(text)
            self.sequence += 1
            self.current = (code, self.sequence, None)
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
        self.connection.unregister_object(self.registration)
