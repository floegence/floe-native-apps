"""Qualification-only SSH bridge; serializes two helper pipes without content logs."""
import argparse
import json
import os
import signal
import struct
import subprocess
import sys
import threading


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', required=True)
    parser.add_argument('--helper', required=True)
    parser.add_argument('--state', required=True)
    parser.add_argument('--media-source', help='Explicit source directory for the synthetic qualification helper')
    args = parser.parse_args()
    reader, writer = os.pipe()
    command = [args.python, args.helper, '--state', args.state, '--media-fd', str(writer)]
    if args.media_source:
        command.extend(['--helper', args.media_source])
    # The bridge owns this new process group, including fixture descendants.
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             pass_fds=(writer,), start_new_session=True)
    os.close(writer)
    lock = threading.Lock()
    stopped = threading.Event()

    def forward(source):
        try:
            while True:
                prefix = source.read(4)
                if not prefix:
                    return
                length = struct.unpack('!I', prefix)[0]
                if not 0 < length <= 8 << 20:
                    raise ValueError('invalid native header')
                header = source.read(length)
                count = json.loads(header).get('bytes', 0)
                if not 0 <= count <= 64 << 20:
                    raise ValueError('invalid native payload')
                data = source.read(count)
                if len(header) != length or len(data) != count:
                    raise ValueError('truncated native message')
                with lock:
                    sys.stdout.buffer.write(prefix + header + data)
                    sys.stdout.buffer.flush()
        except (OSError, ValueError, struct.error):
            pass
        finally:
            stopped.set()

    def commands():
        try:
            while True:
                data = os.read(0, 65536)
                if not data:
                    return
                child.stdin.write(data)
                child.stdin.flush()
        except OSError:
            pass
        finally:
            stopped.set()

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, lambda *_: stopped.set())
    threading.Thread(target=commands, daemon=True).start()
    threading.Thread(target=forward, args=(os.fdopen(reader, 'rb'),), daemon=True).start()
    threading.Thread(target=forward, args=(child.stdout,), daemon=True).start()
    stopped.wait()
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # A fixture may exit while a descendant still holds a media/stdout pipe.
        # Neither a blocked reader nor a stuck fixture may keep an SSH test alive.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


if __name__ == '__main__':
    main()
