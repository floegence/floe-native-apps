"""Qualification-only SSH bridge; serializes two helper pipes without content logs."""
import argparse
import os
import struct
import subprocess
import sys
import threading

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
child = subprocess.Popen(command,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, pass_fds=(writer,))
os.close(writer)
lock = threading.Lock()


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
            import json
            count = json.loads(header).get('bytes', 0)
            if not 0 <= count <= 64 << 20:
                raise ValueError('invalid native payload')
            data = source.read(count)
            if len(header) != length or len(data) != count:
                raise ValueError('truncated native message')
            with lock:
                sys.stdout.buffer.write(prefix + header + data)
                sys.stdout.buffer.flush()
    finally:
        child.terminate()


def commands():
    try:
        while True:
            data = os.read(0, 65536)
            if not data:
                return
            child.stdin.write(data)
            child.stdin.flush()
    except BrokenPipeError:
        pass
    finally:
        child.terminate()


threading.Thread(target=commands, daemon=True).start()
threading.Thread(target=forward, args=(os.fdopen(reader, 'rb'),), daemon=True).start()
try:
    forward(child.stdout)
finally:
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
