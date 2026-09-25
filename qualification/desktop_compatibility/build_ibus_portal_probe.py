"""Build unmodified official portal sources in a disposable native fixture.

IBus 1.5.29's portal cannot proxy GTK4's synchronous post-processing properties.
This is not a production component installer or an aggregate binary recipe.
"""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import urllib.request

SOURCES = {
    "portal.c": "418c3422313812548a4117369288fe862b45907182d40d1dcaf8064ddca3c6f4",
    "org.freedesktop.IBus.Portal.xml": "bfb62774d531e38374cdfb716455f71e06c4b258d6c229cd59d18379a8379905",
}


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    sources = []
    for name, digest in SOURCES.items():
        url = "https://raw.githubusercontent.com/ibus/ibus/1.5.34/portal/" + name
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024 or hashlib.sha256(data).hexdigest() != digest:
            raise RuntimeError("Official IBus portal source failed verification: " + name)
        (output / name).write_bytes(data)
        sources.append({"url": url, "sha256": digest, "bytes": len(data)})
    (output / "config.h").write_text('#define VERSION "1.5.34"\n')
    subprocess.run(["gdbus-codegen", "--interface-prefix", "org.freedesktop.IBus.",
                    "--c-namespace", "IBusDbus", "--generate-c-code", "ibus-portal-dbus",
                    "org.freedesktop.IBus.Portal.xml"], cwd=output, check=True)
    flags = shlex.split(subprocess.check_output(
        ["pkg-config", "--cflags", "--libs", "ibus-1.0", "gio-2.0"], text=True))
    subprocess.run(["cc", "-Wall", "-I.", "portal.c", "ibus-portal-dbus.c", *flags,
                    "-o", "ibus-portal"], cwd=output, check=True)
    (output / "build.json").write_text(json.dumps({
        "sources": sources,
        "artifact_sha256": hashlib.sha256((output / "ibus-portal").read_bytes()).hexdigest(),
        "compiler": subprocess.check_output(["cc", "--version"], text=True).splitlines()[0],
        "versions": subprocess.check_output(["pkg-config", "--modversion", "ibus-1.0", "gio-2.0"], text=True),
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
