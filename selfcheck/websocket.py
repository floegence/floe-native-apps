"""Qualify masked short frames against the installed decoder in a subprocess."""
import gc
import struct

from xpra.net.websockets.header import decode_hybi

# Include control frames, all short payload lengths and every input alignment.
# Larger payloads exercise the unchanged native path and extended frame headers.
for size in (0, 1, 2, 3, 4, 5, 125, 126, 127, 1024, 65536):
    payload = bytes(i % 251 for i in range(size))
    for opcode in (2, 8, 9, 10):
        if opcode != 2 and size > 125:
            continue
        for offset in range(4):
            mask = b"\x17\x9b\x43\xef"
            header = bytes((0x80 | opcode, 0x80 | size)) if size < 126 else (
                bytes((0x80 | opcode, 0xfe)) + struct.pack("!H", size) if size < 65536 else
                bytes((0x80 | opcode, 0xff)) + struct.pack("!Q", size))
            encoded = bytes(value ^ mask[i & 3] for i, value in enumerate(payload))
            storage = b"x" * offset + header + mask + encoded
            frame = memoryview(storage)[offset:]
            decoded = decode_hybi(frame)
            assert decoded[0] == opcode and bytes(decoded[1]) == payload
            assert decoded[2] == len(frame) and decoded[3]
            del decoded
            gc.collect()
print("WebSocket short-frame qualification passed")
