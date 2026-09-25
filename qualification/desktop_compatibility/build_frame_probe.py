"""Compile a bounded frame fixture with the original pinned Weston protocol."""
import hashlib
from pathlib import Path
import shlex
import subprocess
import sys
import urllib.request


def main():
    source = Path(__file__).resolve().parent
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    url = "https://raw.githubusercontent.com/wayland-mirror/weston/13.0/protocol/weston-output-capture.xml"
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(65537)
    if hashlib.sha256(data).hexdigest() != "8cedbfc3eca413505b7ea8bd99d5f05f3d0724e311f159186070d1f8c107bc10":
        raise RuntimeError("Original Weston capture protocol failed verification")
    protocol = output / "weston-output-capture.xml"
    protocol.write_bytes(data)
    subprocess.run(["wayland-scanner", "client-header", str(protocol), str(output / "weston-output-capture-client.h")], check=True)
    subprocess.run(["wayland-scanner", "private-code", str(protocol), str(output / "weston-output-capture-code.c")], check=True)
    flags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "--libs", "wayland-client", "libdrm"], text=True))
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-O2", str(source / "frame_probe.c"),
                    str(output / "weston-output-capture-code.c"), "-I" + str(output), *flags,
                    "-o", str(output / "frame-probe")], check=True)


if __name__ == "__main__":
    main()
