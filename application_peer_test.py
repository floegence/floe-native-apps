"""Bus proxies must match native surfaces through a live official sandbox instance."""
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import application_peer as peers


class Reference:
    def __init__(self, tree, pid):
        self.tree, self.pid, self.started, self.closed = tree, pid, tree.live[pid], False

    def valid(self):
        return not self.closed and self.tree.live.get(self.pid) == self.started

    def close(self):
        self.closed = True


class Tree:
    def __init__(self):
        self.live, self.references = {101: 11, 102: 12, 103: 13, 104: 14}, []

    def admit(self, pid):
        if pid not in self.live:
            raise ValueError('Unowned peer')
        ref = Reference(self, pid)
        self.references.append(ref)
        return ref


class PeerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc, self.runtime = self.root / 'proc', self.root / 'runtime'
        self.proc.mkdir()
        self.runtime.mkdir(mode=0o700)
        self.tree = Tree()
        mock = patch.object(peers, 'PROC', self.proc)
        mock.start()
        self.addCleanup(mock.stop)
        for pid in self.tree.live:
            (self.proc / str(pid) / 'root').mkdir(parents=True)
            (self.proc / str(pid) / 'ns').mkdir()
            (self.proc / str(pid) / 'ns/pid').symlink_to('pid:[1]')

    def metadata(self, pid, instance='1234', app='org.example.Editor', revision='a' * 64):
        path = self.proc / str(pid) / 'root/.flatpak-info'
        path.write_text('[Application]\nname=' + app + '\n[Instance]\ninstance-id=' + instance +
                        '\napp-commit=' + revision + '\nruntime-commit=' + 'b' * 64 + '\n')
        path.chmod(0o600)
        return path

    def sandbox(self):
        for pid in (101, 102, 103):
            self.metadata(pid)
        directory = self.runtime / '.flatpak/1234'
        directory.mkdir(parents=True, mode=0o700)
        (directory / 'bwrapinfo.json').write_text(json.dumps({'child-pid': 102}))
        return directory

    def peer(self, pid):
        peer = peers.ApplicationPeer(self.tree, pid, self.runtime)
        self.addCleanup(peer.close)
        return peer

    def test_native_peers_require_exact_process_identity(self):
        first, same, other = self.peer(101), self.peer(101), self.peer(104)
        self.assertTrue(first.matches(same))
        self.assertFalse(first.matches(other))
        independent = peers.ApplicationPeer(Tree(), 101, self.runtime)
        self.addCleanup(independent.close)
        self.assertFalse(first.matches(independent))
        self.tree.live[101] += 1
        self.assertFalse(first.matches(same))

    def test_flatpak_proxy_matches_window_via_actual_instance_and_bwrap_child(self):
        self.sandbox()
        proxy, window = self.peer(101), self.peer(103)
        self.assertNotEqual(proxy.process.pid, window.process.pid)
        self.assertTrue(proxy.matches(window))
        self.assertEqual(proxy.package.instance, '1234')
        self.assertFalse(proxy.matches(self.peer(104)))

    def test_same_package_in_another_instance_cannot_take_the_window(self):
        self.sandbox()
        first = self.peer(101)
        self.metadata(104, instance='5678')
        directory = self.runtime / '.flatpak/5678'
        directory.mkdir(mode=0o700)
        (directory / 'bwrapinfo.json').write_text(json.dumps({'child-pid': 104}))
        self.assertFalse(first.matches(self.peer(104)))

    def test_replaced_metadata_or_exited_bwrap_revokes_existing_admission(self):
        self.sandbox()
        proxy, window = self.peer(101), self.peer(103)
        self.metadata(103, revision='c' * 64)
        self.assertFalse(proxy.matches(window))
        self.metadata(103)
        del self.tree.live[102]
        self.assertFalse(proxy.matches(window))

    def test_unowned_or_reused_bwrap_and_mismatched_namespace_are_rejected(self):
        directory = self.sandbox()
        (directory / 'bwrapinfo.json').write_text(json.dumps({'child-pid': 999}))
        with self.assertRaises(ValueError):
            self.peer(101)
        (directory / 'bwrapinfo.json').write_text(json.dumps({'child-pid': 102}))
        proxy = self.peer(101)
        namespace = self.proc / '103/ns/pid'
        namespace.unlink()
        namespace.symlink_to('pid:[2]')
        self.assertFalse(proxy.matches(self.peer(103)))
        self.tree.live[102] += 1
        self.assertFalse(proxy.valid())

    def test_unsafe_and_missing_metadata_fail_without_treating_proxy_as_native(self):
        directory = self.sandbox()
        for instance in ('../outside', '', '1/2'):
            self.metadata(101, instance=instance)
            with self.subTest(instance=instance), self.assertRaises(ValueError):
                self.peer(101)
        path = self.metadata(101)
        path.chmod(0o666)
        with self.assertRaises(ValueError):
            self.peer(101)
        path.chmod(0o600)
        (directory / 'bwrapinfo.json').unlink()
        with self.assertRaises((OSError, ValueError)):
            self.peer(101)
        self.assertTrue(all(ref.closed for ref in self.tree.references))

    def test_symlinks_and_unbounded_metadata_are_rejected(self):
        self.sandbox()
        path = self.metadata(101)
        content = path.with_name('other')
        path.rename(content)
        path.symlink_to(content)
        with self.assertRaises((OSError, ValueError)):
            self.peer(101)
        path.unlink()
        path.write_bytes(b'x' * (256 * 1024 + 1))
        with self.assertRaises(ValueError):
            self.peer(101)


if __name__ == '__main__':
    unittest.main()
