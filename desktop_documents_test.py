"""Document forwarding preserves official grants and only admits chosen files."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop_documents import DocumentAuthority, DocumentCaller, DocumentGrants, file_identity


class Peer:
    def __init__(self, pid):
        self.pid, self.alive, self.closed = pid, True, False

    def valid(self):
        return self.alive and not self.closed

    def close(self):
        self.closed = True


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.chosen = self.root / 'chosen.txt'
        self.chosen.write_text('fixture')
        self.other = self.root / 'other.txt'
        self.other.write_text('unselected')
        self.descriptors = [os.open(p, os.O_RDONLY) for p in (self.chosen, self.other, self.root)]
        self.addCleanup(lambda: [os.close(fd) for fd in self.descriptors])
        self.grants = DocumentGrants('org.example.Editor')
        self.portal = DocumentCaller('portal', Peer(123), False)
        self.launcher = DocumentCaller('launcher', Peer(124), True, {file_identity(self.descriptors[0])})

    def export(self, flags=3, name='document', caller=None):
        request = self.grants.admit(caller or self.portal, 'AddFull',
            ([0], flags, 'org.example.Editor', ['read', 'write']), [self.descriptors[0]])
        self.grants.complete(request, ([name], {}))
        return request

    def test_original_reuse_and_persistence_flags_do_not_grant_deletion(self):
        request = self.export(3)
        self.assertFalse(request.unique)
        self.assertTrue(request.persistent)
        for method, values in [('Delete', ('document',)),
                               ('RevokePermissions', ('document', 'org.example.Editor', ['write']))]:
            with self.assertRaises(ValueError):
                self.grants.admit(self.portal, method, values, [])
        self.grants.close()
        self.assertFalse(self.grants.pending)

    def test_unique_grant_can_be_deleted_only_after_its_actual_host_reply(self):
        request = self.grants.admit(self.portal, 'Add', (0, False, False), [self.descriptors[0]])
        with self.assertRaises(ValueError):
            self.grants.admit(self.portal, 'Info', ('new',), [])
        self.grants.complete(request, ('new',))
        removal = self.grants.admit(self.portal, 'Delete', ('new',), [])
        self.assertIn('new', self.grants.documents)
        self.grants.complete(removal, ())
        self.assertNotIn('new', self.grants.documents)

    def test_reuse_never_upgrades_an_existing_entry_to_exclusive_ownership(self):
        self.export(0)
        self.export(3)
        self.assertFalse(self.grants.documents['document']['unique'])
        self.export(0)
        self.assertFalse(self.grants.documents['document']['unique'])

    def test_launcher_must_supply_an_explicit_initial_file(self):
        self.export(caller=self.launcher)
        with self.assertRaises(ValueError):
            self.grants.admit(self.launcher, 'Add', (0, True, True), [self.descriptors[1]])
        with self.assertRaises(ValueError):
            self.grants.admit(self.launcher, 'AddNamed', (0, b'new\0', True, True), [self.descriptors[2]])

    def test_actual_named_save_passes_directory_fd_and_bounded_basename(self):
        request = self.grants.admit(self.portal, 'AddNamedFull',
            (0, b'saved.txt\0', 7, 'org.example.Editor', ['read', 'write', 'grant-permissions']),
            [self.descriptors[2]])
        self.grants.complete(request, ('saved', {}))
        for name in (b'../other\0', b'.\0', b'..\0', b'missing-terminator', b'a\0b\0', b'x' * 256 + b'\0'):
            with self.assertRaises(ValueError):
                self.grants.admit(self.portal, 'AddNamed', (0, name, True, True), [self.descriptors[2]])
        with self.assertRaises(ValueError):
            self.grants.admit(self.portal, 'AddNamed', (0, b'file\0', True, True), [self.descriptors[0]])

    def test_no_other_app_session_or_extra_descriptor_can_acquire_a_grant(self):
        self.export()
        for method, values, fds in [
                ('AddFull', ([0], 3, 'org.other.App', ['read']), [self.descriptors[0]]),
                ('GrantPermissions', ('document', 'org.other.App', ['read']), []),
                ('Info', ('from-other-instance',), []), ('List', ('org.example.Editor',), []),
                ('Add', (0, True, True), self.descriptors[:2]),
                ('AddFull', ([0, 0], 3, 'org.example.Editor', ['read']), [self.descriptors[0]]),
                ('GetMountPoint', (), [self.descriptors[0]])]:
            with self.subTest(method=method, values=values), self.assertRaises(ValueError):
                self.grants.admit(self.portal, method, values, fds)

    def test_closed_unknown_flags_and_unknown_permissions_fail(self):
        for flags, permissions in ((16, ['read']), (0, ['admin']), (0, ['read', 'read'])):
            with self.assertRaises(ValueError):
                self.grants.admit(self.portal, 'AddFull',
                    ([0], flags, 'org.example.Editor', permissions), [self.descriptors[0]])
        self.portal.peer.alive = False
        with self.assertRaises(ValueError):
            self.grants.admit(self.portal, 'GetMountPoint', (), [])

    def test_pending_capacity_failure_and_late_callbacks_do_not_create_authority(self):
        with patch('desktop_documents.MAX_DOCUMENTS', 2):
            first = self.grants.admit(self.portal, 'AddFull',
                ([0, 1], 3, 'org.example.Editor', ['read']), self.descriptors[:2])
            with self.assertRaises(ValueError):
                self.export()
            self.grants.failed(first)
            self.export()
            with self.assertRaises(ValueError):
                self.grants.complete(first, (['late1', 'late2'], {}))
            self.assertEqual(set(self.grants.documents), {'document'})

    def test_invalid_official_reply_cannot_become_a_local_permission(self):
        for reply in (([], {}), (['bad/id'], {}), (['one', 'two'], {})):
            request = self.grants.admit(self.portal, 'AddFull',
                ([0], 7, 'org.example.Editor', ['read']), [self.descriptors[0]])
            with self.assertRaises(ValueError):
                self.grants.complete(request, reply)
            self.assertFalse(self.grants.documents)
        self.export(7, name='')
        self.assertFalse(self.grants.documents)  # Official as-needed response.


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.authority = DocumentAuthority()
        self.addCleanup(self.authority.close)
        self.portal = Peer(20)
        self.authority.bind_portal(self.portal)
        self.launcher = Peer(30)
        tree = SimpleNamespace(admit=lambda pid: self.launcher if pid == 30 else (_ for _ in ()).throw(ValueError()))
        mock = patch('desktop_documents.executable_identity', return_value=('dev', 'inode', 'version'))
        self.identity = mock.start()
        self.addCleanup(mock.stop)
        self.authority.bind_launcher(tree, '/usr/bin/flatpak', [])

    def test_only_prepared_portal_or_original_launcher_can_forward(self):
        for pid, role in ((20, 'portal'), (30, 'launcher')):
            caller = self.authority.admit({'ProcessID': pid, 'UnixUserID': os.getuid()})
            self.assertEqual(caller.role, role)
        for pid, uid in ((40, os.getuid()), (20, os.getuid() + 1)):
            with self.assertRaises(ValueError):
                self.authority.admit({'ProcessID': pid, 'UnixUserID': uid})
        self.portal.alive = False
        with self.assertRaises(ValueError):
            self.authority.admit({'ProcessID': 20, 'UnixUserID': os.getuid()})

    def test_launcher_exec_replacement_revokes_host_service_access(self):
        self.identity.return_value = ('different', 'image')
        with self.assertRaises(ValueError):
            self.authority.admit({'ProcessID': 30, 'UnixUserID': os.getuid()})
        self.assertTrue(self.launcher.closed)

    def test_service_and_launch_bindings_are_not_replaceable(self):
        with self.assertRaises(ValueError):
            self.authority.bind_portal(Peer(21))
        with self.assertRaises(ValueError):
            self.authority.bind_launcher(None, '/usr/bin/flatpak', [])
        self.authority.close()
        with self.assertRaises(ValueError):
            self.authority.admit({'ProcessID': 20, 'UnixUserID': os.getuid()})


if __name__ == '__main__':
    unittest.main()
