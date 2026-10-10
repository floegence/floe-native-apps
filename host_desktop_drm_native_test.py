"""Exercise the native import contract without a GPU or privileged service."""
from pathlib import Path
import array
import errno
import os
import socket
import struct
import subprocess
import tempfile
import unittest


class NativeScanoutFormatTests(unittest.TestCase):
    def test_rockchip_ytr_normalization_has_no_effect_on_other_layouts(self):
        root = Path(__file__).parent / 'native/host-desktop/drm'
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / 'scanout-format-test'
            subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra',
                            '-Werror', str(root / 'scanout_format_test.c'), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    @unittest.skipUnless(os.environ.get('FLOE_DRM_WORKER'), 'physical converter qualification not requested')
    def test_real_worker_rejects_non_dma_fds_and_releases_received_fds(self):
        self.assertNotEqual(os.geteuid(), 0, 'converter qualification must be unprivileged')
        owner, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        worker = subprocess.Popen([os.environ['FLOE_DRM_WORKER'], 'convert', str(child.fileno())],
                                  pass_fds=(child.fileno(),), stdout=subprocess.PIPE)
        child.close()
        invalid = os.memfd_create('floe-invalid-drm', os.MFD_CLOEXEC)
        os.ftruncate(invalid, 1024)
        packet = struct.pack('<iIiIIIQII10I', 0, 1, -1, 16, 16, 0x34325258,
                             0, 0, 1, 0, 0, 0, 0, 64, 0, 0, 0, 0, 0)
        try:
            fd_count = None
            for _ in range(100):
                owner.sendmsg([packet], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [invalid]))])
                reply = worker.stdout.read(24)
                self.assertEqual(len(reply), 24)
                self.assertEqual(struct.unpack('<i5I', reply), (-errno.EINVAL, 0, 0, 0, 0, 0),
                                 'a non-DMA fd must retain EINVAL rather than become unsupported hardware')
                current = len(os.listdir(f'/proc/{worker.pid}/fd'))
                if fd_count is None:
                    fd_count = current
                self.assertEqual(current, fd_count, 'received descriptors must not accumulate')
            owner.close()
            self.assertEqual(worker.wait(timeout=3), 0, 'disconnect must close the converter')
        finally:
            owner.close()
            os.close(invalid)
            if worker.poll() is None:
                worker.kill()
            worker.wait()
            worker.stdout.close()


if __name__ == '__main__':
    unittest.main()
