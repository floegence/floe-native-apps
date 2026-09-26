"""Real document-portal receipts and cleanup restricted to unique fixture files."""
import os
from pathlib import Path
import subprocess

from gi.repository import Gio, GLib

NAME, PATH = 'org.freedesktop.portal.Documents', '/org/freedesktop/portal/documents'


class DocumentProbe:
    def __init__(self, address, app_id, document):
        self.root = document.parent.resolve()
        self.host = Gio.DBusConnection.new_for_address_sync(address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        self.host.set_exit_on_close(False)
        self.owner = self.host.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', (NAME,)),
            GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
        fd = os.open(document, os.O_PATH | os.O_CLOEXEC)
        try:
            descriptors = Gio.UnixFDList.new()
            handle = descriptors.append(fd)
            reply, _out_fds = self.host.call_with_unix_fd_list_sync(self.owner, PATH, NAME, 'AddFull',
                GLib.Variant('(ahusas)', ([handle], 3, app_id, ['read', 'write'])),
                GLib.VariantType.new('(asa{sv})'), Gio.DBusCallFlags.NONE, 3000, descriptors, None)
            self.existing = reply.unpack()[0][0]
            assert self.existing, 'No pre-existing official fixture grant'
            self.ids = {self.existing}
        finally:
            os.close(fd)

    def assert_unrelated_denied(self, address):
        result = subprocess.run(['gdbus', 'call', '--address', address, '--dest', NAME,
            '--object-path', PATH, '--method', NAME + '.GetMountPoint'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        assert result.returncode != 0 and 'org.freedesktop.DBus.Error.AccessDenied' in result.stderr, result

    def info(self, document):
        return self.host.call_sync(self.owner, PATH, NAME, 'Info', GLib.Variant('(s)', (document,)),
            GLib.VariantType.new('(aya{sas})'), Gio.DBusCallFlags.NONE, 3000, None).unpack()

    def preserve_then_clean(self, service):
        self.ids.update(service.grants.documents)
        service.close()
        # The production facade must not delete an entry that predates it.
        path, _permissions = self.info(self.existing)
        assert Path(os.fsdecode(bytes(path).rstrip(b'\0'))).is_relative_to(self.root)
        result = {'preexisting_grant_preserved': True, 'unrelated_process_denied': True,
                  'grants': len(self.ids), 'fixture_grants_removed': 0}
        for document in self.ids:
            path, _permissions = self.info(document)
            # Only task-created documents beneath this random fixture directory
            # may be removed by qualification cleanup. Never use product state.
            actual = Path(os.fsdecode(bytes(path).rstrip(b'\0')))
            assert actual.is_absolute() and actual.is_relative_to(self.root)
            self.host.call_sync(self.owner, PATH, NAME, 'Delete', GLib.Variant('(s)', (document,)),
                GLib.VariantType.new('()'), Gio.DBusCallFlags.NONE, 3000, None)
            result['fixture_grants_removed'] += 1
        self.host.close_sync(None)
        return result
