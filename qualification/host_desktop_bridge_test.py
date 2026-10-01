"""The SSH qualification transport must retire even unresponsive descendants."""
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest


class BridgeLifecycleTests(unittest.TestCase):
    def test_eof_and_termination_reap_fixture_process_group(self):
        for termination in ('eof', 'signal'):
            with self.subTest(termination=termination), tempfile.TemporaryDirectory() as directory:
                helper = Path(directory, 'stuck.py')
                helper.write_text('''import json, os, signal, struct, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
descendant = os.fork()
if descendant:
    header = json.dumps({'pids': [os.getpid(), descendant], 'bytes': 0}).encode()
    os.write(1, struct.pack('!I', len(header)) + header)
while True:
    time.sleep(1)
''')
                bridge = subprocess.Popen([sys.executable, str(Path(__file__).with_name('host_desktop_bridge.py')),
                    '--python', sys.executable, '--helper', str(helper), '--state', directory],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                pids = []
                try:
                    size = struct.unpack('!I', bridge.stdout.read(4))[0]
                    pids = json.loads(bridge.stdout.read(size))['pids']
                    if termination == 'eof':
                        bridge.stdin.close()
                        bridge.stdin = None
                    else:
                        bridge.send_signal(signal.SIGTERM)
                    _, error = bridge.communicate(timeout=5)
                    self.assertEqual(bridge.returncode, 0, error.decode())
                    for pid in pids:
                        # Orphan zombies await the host's init; they hold no pipes.
                        deadline = time.monotonic() + 2
                        while True:
                            status = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='],
                                capture_output=True, text=True).stdout.strip()
                            if not status or status.startswith('Z'):
                                break
                            self.assertLess(time.monotonic(), deadline, 'fixture descendant remains running')
                            time.sleep(.05)
                finally:
                    if bridge.poll() is None:
                        bridge.kill()
                        bridge.communicate()
                    for pid in pids:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass


if __name__ == '__main__':
    unittest.main()
