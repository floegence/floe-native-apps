"""One actual frame from the authorized portable capture fixture."""
import hashlib
import io
import socket
import struct
import subprocess


def capture(command, environment, start, control, wait_authorized, destination):
    parent, child = socket.socketpair()
    parent.settimeout(10)
    try:
        process = start(['python3', '-c', 'import os,sys; os.read(0,1); os.execv(sys.argv[1],sys.argv[1:])',
                         *command], {**environment, 'FLOE_PROBE_FRAME_FD': str(child.fileno())},
                        'capture-' + destination.name, stdin=subprocess.PIPE, pass_fds=(child.fileno(),))
        child.close()
        control.sendall(f'capture-authorize {process.pid}\n'.encode())
        wait_authorized(process.pid)
        process.stdin.write(b'x')
        process.stdin.close()
        def receive(length):
            data = bytearray()
            while len(data) < length:
                chunk = parent.recv(length - len(data))
                if not chunk:
                    raise RuntimeError('Portable frame disconnected')
                data.extend(chunk)
            return bytes(data)
        parent.sendall(struct.pack('=I', 1))
        sequence, status, width, height, fmt, size = struct.unpack('=6I', receive(24))
        assert sequence == 1 and status == 1
        assert 0 < width <= 4096 and 0 < height <= 4096 and 45 <= size <= 4096 * 4096 * 4 and fmt == 0x20474e50
        pixels = receive(size)
        from PIL import Image
        image = Image.open(io.BytesIO(pixels))
        assert image.format == 'PNG' and image.size == (width, height)
        image.load()
        assert len(image.convert('RGB').getcolors(width * height)) > 16, 'No painted application pixels'
        (destination / 'frame.png').write_bytes(pixels)
        result = {'stage': destination.name, 'width': width, 'height': height,
                  'format': fmt, 'sha256': hashlib.sha256(pixels).hexdigest()}
    finally:
        parent.close()
        child.close()
    process.wait(timeout=5)
    assert process.returncode == 0
    return result
