"""Unpublished fixture: bounded forwarding to the official host document portal.

Flatpak 1.14 forwards the host mount path verbatim but exposes documents at the
standard sandbox runtime path. A per-session, relocated document mount does not
work. The official host document service is headless; FileChooser remains local
to the private graphical session. Only fixture-owned files are admitted here.
"""
import os
from pathlib import Path
import xml.etree.ElementTree as ET

from gi.repository import Gio, GLib
from application_processes import identity

NAME = "org.freedesktop.portal.Documents"
PATH = "/org/freedesktop/portal/documents"
METHODS = {"GetMountPoint", "Add", "AddFull", "AddNamed", "AddNamedFull",
           "GrantPermissions", "RevokePermissions", "Delete", "Info"}


class DocumentBridge:
    def __init__(self, private, host_address, owner, app_id, root, record):
        self.private, self.owner, self.app_id = private, owner, app_id
        self.root, self.record, self.documents = root.resolve(), record, set()
        self.owner_start = identity(owner)[1]
        flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        self.host = Gio.DBusConnection.new_for_address_sync(host_address, flags, None, None)
        self.host.set_exit_on_close(False)
        xml = self.host.call_sync(NAME, PATH, "org.freedesktop.DBus.Introspectable", "Introspect",
                                 None, GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        source = ET.fromstring(xml)
        node = ET.Element("node")
        interface = next(i for i in source.findall("interface") if i.get("name") == NAME)
        for method in list(interface.findall("method")):
            if method.get("name") not in METHODS:
                interface.remove(method)
        node.append(interface)
        self.node = Gio.DBusNodeInfo.new_for_xml(ET.tostring(node, encoding="unicode"))
        self.registration = private.register_object(PATH, self.node.interfaces[0], self.call, self.property, None)
        reply = private.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                  "RequestName", GLib.Variant("(su)", (NAME, 4)),
                                  GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 2000, None)
        if reply.unpack()[0] != 1:
            raise RuntimeError("Private document facade is already owned")

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

    def property(self, _connection, _sender, _path, _interface, name):
        if name != "version":
            return None
        return self.host.call_sync(NAME, PATH, "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", (NAME, name)), GLib.VariantType.new("(v)"),
            Gio.DBusCallFlags.NONE, 2000, None).get_child_value(0).get_variant()

    def call(self, connection, sender, _path, _interface, method, parameters, invocation):
        try:
            credentials = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "GetConnectionCredentials", GLib.Variant("(s)", (sender,)),
                GLib.VariantType.new("(a{sv})"), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
            if credentials["UnixUserID"] != os.getuid() or not self.owned(credentials["ProcessID"]):
                raise ValueError("Document caller is outside the fixture tree")
            if method not in METHODS:
                raise ValueError("Document operation is unavailable")
            values = parameters.unpack()
            if method in ("GrantPermissions", "RevokePermissions", "Delete", "Info"):
                if values[0] not in self.documents:
                    raise ValueError("Document does not belong to this fixture")
            app_index = {"AddFull": 2, "AddNamedFull": 3, "GrantPermissions": 1, "RevokePermissions": 1}.get(method)
            if app_index is not None and values[app_index] != self.app_id:
                raise ValueError("Document app identity does not match the fixture")
            fds = invocation.get_message().get_unix_fd_list()
            if fds:
                for index in range(fds.get_length()):
                    fd = fds.get(index)
                    try:
                        target = Path(os.readlink(f"/proc/self/fd/{fd}")).resolve()
                        if not target.is_relative_to(self.root):
                            raise ValueError("Document path is outside the fixture")
                    finally:
                        os.close(fd)
            self.record({"document-portal": "request", "method": method})
        except (KeyError, ValueError, OSError) as error:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.AccessDenied", str(error))
            return

        def completed(host, result, _data):
            try:
                reply, reply_fds = host.call_with_unix_fd_list_finish(result)
                if method.startswith("Add"):
                    ids = reply.unpack()[0]
                    self.documents.update(value for value in ([ids] if isinstance(ids, str) else ids) if value)
                self.record({"document-portal": "reply", "method": method})
                invocation.return_value_with_unix_fd_list(reply, reply_fds)
            except GLib.Error as error:
                invocation.return_dbus_error(Gio.DBusError.get_remote_error(error) or
                                              "org.freedesktop.DBus.Error.Failed", error.message)

        self.host.call_with_unix_fd_list(NAME, PATH, NAME, method, parameters, None,
            Gio.DBusCallFlags.NONE, 5000, fds, None, completed, None)

    def close(self):
        self.private.unregister_object(self.registration)
        # These grants refer only to unique fixture files. Never remove an
        # unrelated portal grant, nor stop the host's official portal process.
        for document in self.documents:
            self.host.call_sync(NAME, PATH, NAME, "Delete", GLib.Variant("(s)", (document,)),
                                None, Gio.DBusCallFlags.NONE, 2000, None)
        self.host.close_sync(None)
