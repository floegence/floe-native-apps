"""Export the actual GTK 4.0/glibc 2.31 application runtime for native tests.

Only disposable fixture resources receive these original libraries. They never
enter the component catalog or an application selected by a user.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


def main():
    source, module, output = map(Path, sys.argv[1:])
    assert not output.exists() and output.is_absolute()
    environment = dict(os.environ, PKG_CONFIG_PATH='/opt/gtk4/lib/pkgconfig', LD_LIBRARY_PATH='/opt/gtk4/lib')
    version = subprocess.check_output(['pkg-config', '--modversion', 'gtk4-x11'], env=environment, text=True).strip()
    assert version == '4.0.3'
    output.mkdir(mode=0o700)
    flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'gtk4-x11'],
                                              env=environment, text=True))
    binary = output / 'application'
    subprocess.run(['cc', '-O2', str(source), *flags, '-Wl,-rpath-link,/opt/gtk4/lib', '-o', str(binary)], check=True)
    libraries = output / 'lib'
    libraries.mkdir()
    copied = {}
    for path in (binary, module):
        listing = subprocess.check_output(['ldd', str(path)], env=environment, text=True)
        if 'not found' in listing:
            raise ValueError('Incomplete GTK baseline runtime')
        for original in re.findall(r'(?:=>\s+|^\s*)(/\S+)\s+\(', listing, re.MULTILINE):
            original = Path(original)
            digest = hashlib.sha256(original.read_bytes()).hexdigest()
            if original.name in copied and copied[original.name]['sha256'] != digest:
                raise ValueError('Conflicting GTK baseline libraries')
            target = libraries / original.name
            shutil.copyfile(original, target)
            target.chmod(0o700)
            copied[original.name] = {'source': str(original), 'sha256': digest}
    loaders = list(libraries.glob('ld-linux-*.so.*'))
    assert len(loaders) == 1
    # Let the kernel start this original loader through PT_INTERP. Invoking
    # glibc 2.31's loader as an executable loses AT_SECURE on newer kernels;
    # GLib correctly rejects that missing security metadata. Only the test
    # executable is relinked; no runtime library or security check is patched.
    subprocess.run(['cc', '-O2', str(source), *flags, '-Wl,-rpath-link,/opt/gtk4/lib',
                    '-Wl,--dynamic-linker=' + str(loaders[0]), '-o', str(binary)], check=True)
    # The runtime is copied to the same absolute task path on the native host.
    wrapper = output / 'run-application'
    wrapper.write_text('#!/bin/sh\nunset LD_PRELOAD\nexport GSK_RENDERER=cairo GTK_A11Y=none\n' +
        'export LD_LIBRARY_PATH=' + shlex.quote(str(libraries)) + '\nexec ' + shlex.quote(str(binary)) + ' "$@"\n')
    wrapper.chmod(0o700)
    (output / 'runtime.json').write_text(json.dumps({'status': 'disposable native runtime fixture only',
        'gtk': version, 'builder': os.environ['FLOE_DESKTOP_GTK_BUILDER_IMAGE'], 'libraries': copied,
        'sources': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, Path(__file__))},
        'application_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'module_sha256': hashlib.sha256(module.read_bytes()).hexdigest()}, indent=2) + '\n')


if __name__ == '__main__':
    main()
