"""Export a task-only Qt application with its actual baseline library closure.

Run inside the recorded native builder. No library is installed on the test host
or added to the production catalog. The private loader runs the actual native
application as a normal child of the qualification supervisor.
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
    major = os.environ['FLOE_QT_MAJOR']
    assert major in ('5', '6') and not output.exists() and output.is_absolute()
    output.mkdir(mode=0o700)
    flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'Qt' + major + 'Widgets'], text=True))
    binary = output / 'application'
    subprocess.run(['c++', '-fPIC', '-O2', '-std=c++17', str(source / 'qt_context_fixture.cpp'),
                    *flags, '-o', str(binary)], check=True)
    qmake = 'qmake6' if major == '6' else 'qmake'
    plugins = Path(subprocess.check_output([qmake, '-query', 'QT_INSTALL_PLUGINS'], text=True).strip())
    exported = output / 'platforms'
    exported.mkdir()
    roots = [binary, module]
    for name in ('libqxcb.so', 'libqwayland-generic.so'):
        path = plugins / 'platforms' / name
        if path.is_file():
            shutil.copyfile(path, exported / name)
            roots.append(path)
    assert (exported / 'libqxcb.so').is_file(), 'Baseline X11 platform is unavailable'
    libraries = output / 'lib'
    libraries.mkdir()
    copied = {}
    for path in roots:
        listing = subprocess.check_output(['ldd', str(path)], text=True)
        if 'not found' in listing:
            raise ValueError('Incomplete baseline library closure')
        for original in re.findall(r'(?:=>\s+|^\s*)(/\S+)\s+\(', listing, re.MULTILINE):
            original = Path(original)
            digest = hashlib.sha256(original.read_bytes()).hexdigest()
            if original.name in copied and copied[original.name]['sha256'] != digest:
                raise ValueError('Conflicting baseline library identities')
            target = libraries / original.name
            shutil.copyfile(original, target)
            target.chmod(0o700)
            copied[original.name] = {'source': str(original), 'sha256': digest}
    loaders = list(libraries.glob('ld-linux-*.so.*'))
    assert len(loaders) == 1, 'Native baseline loader is ambiguous'
    wrapper = output / 'run-application'
    wrapper.write_text('#!/bin/sh\nunset LD_PRELOAD LD_LIBRARY_PATH\nexport QT_QPA_PLATFORM_PLUGIN_PATH=' +
        shlex.quote(str(exported)) + '\nexec ' +
        shlex.join([str(loaders[0]), '--library-path', str(libraries), str(binary)]) + ' "$@"\n')
    wrapper.chmod(0o700)
    (output / 'runtime.json').write_text(json.dumps({'status': 'disposable native runtime fixture only',
        'qt': subprocess.check_output(['pkg-config', '--modversion', 'Qt' + major + 'Core'], text=True).strip(),
        'builder': os.environ['FLOE_DESKTOP_QT_BUILDER_IMAGE'], 'libraries': copied,
        'sources': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
            (source / 'qt_context_fixture.cpp', Path(__file__))},
        'module_sha256': hashlib.sha256(module.read_bytes()).hexdigest()}, indent=2) + '\n')


if __name__ == '__main__':
    main()
