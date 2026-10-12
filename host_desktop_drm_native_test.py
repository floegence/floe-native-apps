"""Exercise the native import contract without a GPU or privileged service."""
from pathlib import Path
import array
import errno
import fcntl
import mmap
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
        pixels_fd = os.memfd_create('floe-test-drm-pixels', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        os.ftruncate(pixels_fd, 64 << 20)
        fcntl.fcntl(pixels_fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        pixels = mmap.mmap(pixels_fd, 64 << 20)
        pixels[:16] = b'x' * 16
        worker = subprocess.Popen([os.environ['FLOE_DRM_WORKER'], 'convert', str(child.fileno()), str(pixels_fd)],
                                  pass_fds=(child.fileno(), pixels_fd), stdout=subprocess.PIPE)
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
                self.assertEqual(pixels[:16], b'x' * 16, 'failed conversions must not publish new pixels')
            owner.close()
            self.assertEqual(worker.wait(timeout=3), 0, 'disconnect must close the converter')
        finally:
            owner.close()
            os.close(invalid)
            os.close(pixels_fd)
            pixels.close()
            if worker.poll() is None:
                worker.kill()
            worker.wait()
            worker.stdout.close()

    @unittest.skipUnless(os.environ.get('FLOE_DRM_WORKER'), 'physical converter qualification not requested')
    def test_converter_rejects_unsealed_or_wrong_capacity_pixel_storage(self):
        for size, sealed in ((64 << 20, False), (1024, True)):
            with self.subTest(size=size, sealed=sealed):
                owner, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                fd = os.memfd_create('floe-invalid-pixels', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
                try:
                    os.ftruncate(fd, size)
                    if sealed:
                        fcntl.fcntl(fd, fcntl.F_ADD_SEALS,
                            fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
                    completed = subprocess.run([os.environ['FLOE_DRM_WORKER'], 'convert',
                        str(child.fileno()), str(fd)], pass_fds=(child.fileno(), fd),
                        stdout=subprocess.PIPE, timeout=3)
                    self.assertEqual(completed.returncode, 64)
                    self.assertEqual(completed.stdout, b'')
                finally:
                    owner.close()
                    child.close()
                    os.close(fd)


if __name__ == '__main__':
    unittest.main()
