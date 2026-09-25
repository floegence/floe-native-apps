"""Unpublished feasibility probe: relay only one owned Snap scope operation.

This is not a production launcher or a general systemd proxy. The owning probe
retains/reaps its children, so a caller cannot be reaped and have its PID reused
while the real systemd request is in flight. Host and private buses are separate.
"""
import os
from pathlib import Path
import re

NAME = "org.freedesktop.systemd1"
PATH = "/org/freedesktop/systemd1"
INTERFACE = NAME + ".Manager"
XML = '''<node><interface name="org.freedesktop.systemd1.Manager">
<method name="StartTransientUnit"><arg type="s" direction="in"/>
<arg type="s" direction="in"/><arg type="a(sv)" direction="in"/>
<arg type="a(sa(sv))" direction="in"/><arg type="o" direction="out"/></method>
<signal name="JobRemoved"><arg type="u"/><arg type="o"/>
<arg type="s"/><arg type="s"/></signal></interface></node>'''


def validate_request(security_tag, caller_pid, request):
    unit, mode, properties, auxiliary = request
    uuid = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    if not re.fullmatch(re.escape(security_tag) + "-" + uuid + r"\.scope", unit):
        raise ValueError("Scope identity does not match the admitted package")
    if mode != "fail" or auxiliary or len(properties) != 1:
        raise ValueError("Only a new scope without auxiliary units is allowed")
    key, value = properties[0]
    if (key != "PIDs" or len(value) != 1 or type(value[0]) is not int
            or value != [caller_pid]):
        raise ValueError("A caller may move only itself to its scope")
    return unit


def identity(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[1]), int(fields[19])


class ScopeBridge:
    def __init__(self, private_address, host_address, security_tag, owner, record):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.security_tag, self.owner, self.record = security_tag, owner, record
        self.owner_start = identity(owner)[1]
        self.pending = {}
        flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                 | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
        self.private = Gio.DBusConnection.new_for_address_sync(private_address, flags, None, None)
        self.host = Gio.DBusConnection.new_for_address_sync(host_address, flags, None, None)
        for connection in (self.private, self.host):
            connection.set_exit_on_close(False)
        self.node = Gio.DBusNodeInfo.new_for_xml(XML)
        self.registration = self.private.register_object(PATH, self.node.interfaces[0], self.call, None, None)
        self.subscription = self.host.signal_subscribe(
            NAME, INTERFACE, "JobRemoved", PATH, None, Gio.DBusSignalFlags.NONE, self.job_removed)
        reply = self.private.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "RequestName", GLib.Variant("(su)", (NAME, 4)),
            GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 3000, None)
        if reply.unpack()[0] != 1:
            self.close()
            raise RuntimeError("Private systemd adapter is already owned")

    def owned(self, pid):
        if identity(self.owner)[1] != self.owner_start:
            return False
        for _ in range(128):
            parent, _start = identity(pid)
            if parent == self.owner:
                return True
            if parent <= 1 or parent == pid:
                return False
            pid = parent
        return False

    def call(self, connection, sender, _path, _interface, method, parameters, invocation):
        Gio, GLib = self.Gio, self.GLib
        descriptor = None
        try:
            if method != "StartTransientUnit" or len(self.pending) >= 8:
                raise ValueError("Scope operation is unavailable")
            credentials = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "GetConnectionCredentials", GLib.Variant("(s)", (sender,)),
                GLib.VariantType.new("(a{sv})"), Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
            pid = credentials["ProcessID"]
            if credentials["UnixUserID"] != os.getuid() or not self.owned(pid):
                raise ValueError("Caller is outside the admitted launch tree")
            descriptor = os.pidfd_open(pid)
            start = identity(pid)[1]
            unit = validate_request(self.security_tag, pid, parameters.unpack())
            if unit in self.pending or not self.owned(pid):
                raise ValueError("Caller or scope is no longer available")
            state = {"sender": sender, "pid": pid, "start": start,
                     "fd": descriptor, "job": None, "early": None}
            self.pending[unit] = state
            descriptor = None
        except (KeyError, ValueError, OSError) as error:
            if descriptor is not None:
                os.close(descriptor)
            invocation.return_dbus_error("org.freedesktop.DBus.Error.AccessDenied", str(error))
            return

        def completed(host, result, _data):
            try:
                reply = host.call_finish(result)
                state["job"] = reply.unpack()[0]
                self.record({"event": "real_systemd_reply", "unit": unit, "pid": pid,
                             "start_ticks": start, "job": state["job"]})
                invocation.return_value(reply)
                if state["early"] is not None:
                    self.relay(unit, state["early"])
            except GLib.Error as error:
                invocation.return_dbus_error(Gio.DBusError.get_remote_error(error)
                    or "org.freedesktop.DBus.Error.Failed", error.message)
                self.retire(unit)

        self.host.call(NAME, PATH, INTERFACE, method, parameters, GLib.VariantType.new("(o)"),
                       Gio.DBusCallFlags.NONE, 10000, None, completed, None)

    def job_removed(self, _connection, _sender, _path, _interface, _signal, parameters):
        unit = parameters.unpack()[2]
        state = self.pending.get(unit)
        if not state:
            return
        if state["job"] is None:
            state["early"] = parameters
        else:
            self.relay(unit, parameters)

    def relay(self, unit, parameters):
        state = self.pending[unit]
        _number, job, _unit, result = parameters.unpack()
        if job != state["job"]:
            return
        try:
            if identity(state["pid"])[1] != state["start"]:
                return
            cgroup = Path(f'/proc/{state["pid"]}/cgroup').read_text().strip()
            self.record({"event": "real_systemd_job", "unit": unit, "result": result,
                         "pid": state["pid"], "start_ticks": state["start"], "cgroup": cgroup})
            self.private.emit_signal(state["sender"], PATH, INTERFACE, "JobRemoved", parameters)
        finally:
            self.retire(unit)

    def retire(self, unit):
        state = self.pending.pop(unit, None)
        if state:
            os.close(state["fd"])

    def close(self):
        for unit in tuple(self.pending):
            self.retire(unit)
        self.host.signal_unsubscribe(self.subscription)
        self.private.unregister_object(self.registration)
        self.private.close_sync(None)
        self.host.close_sync(None)
