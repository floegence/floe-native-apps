"""Private relocation fixture for original Alpine Weston 14 component bytes.

No installed component is selected or activated. The input root must be the
unactivated output of TestNativeDesktopCandidateExtraction on this architecture.
Applications retain their host environment and never inherit this loader path.
"""
import hashlib
import os
from pathlib import Path
import platform
import secrets
import shlex
import subprocess


def prepare(component, evidence, environment, shell):
    component = Path(component).resolve()
    loader = component / 'lib' / ('ld-musl-aarch64.so.1' if platform.machine() == 'aarch64' else 'ld-musl-x86_64.so.1')
    libraries = ':'.join(str(component / path) for path in
                         ('lib', 'usr/lib', 'usr/lib/weston', 'usr/lib/libweston-14'))

    def command(relative):
        return [str(loader), '--library-path', libraries, str(component / relative)]

    version = subprocess.check_output(command('usr/bin/weston') + ['--version'], text=True).strip()
    if version != 'weston 14.0.2':
        raise RuntimeError('Unreviewed candidate Weston version')
    if (component / '.native-apps').exists():
        raise RuntimeError('Portable fixture must not mutate an activated installation')
    # Same bounded relocation as the released Xvfb recipe, applied only to this
    # new candidate executable. An unknown binary shape is a hard failure.
    original = component / 'usr/bin/Xwayland'
    data = original.read_bytes()
    if data.count(b'/usr/bin\0') != 1:
        raise RuntimeError('Unreviewed candidate Xwayland compiler path')
    binary = evidence / 'Xwayland'
    binary.write_bytes(data.replace(b'/usr/bin\0', b'.\0\0\0\0\0\0\0\0'))
    binary.chmod(0o700)
    authentication = evidence / 'Xauthority'
    authentication.touch(mode=0o600)
    compiler = evidence / 'xkbcomp'
    compiler.write_text('#!/bin/sh\nexec ' + shlex.join(command('usr/bin/xkbcomp')) + ' "$@"\n')
    compiler.chmod(0o700)
    wrapper = evidence / 'xwayland-launch'
    wrapper.write_text('#!/bin/sh\ncd ' + shlex.quote(str(evidence)) + ' || exit 1\nexec ' +
        shlex.join([str(loader), '--library-path', libraries, str(binary)]) +
        ' -shm -auth ' + shlex.quote(str(authentication)) + ' -xkbdir ' +
        shlex.quote(str(component / 'usr/share/X11/xkb')) + ' "$@"\n')
    wrapper.chmod(0o700)
    config = evidence / 'weston.ini'
    config.write_text('[xwayland]\npath=' + str(wrapper) + '\n')
    server_environment = {**environment,
        'XKB_CONFIG_ROOT': str(component / 'usr/share/X11/xkb'),
        'WESTON_MODULE_MAP': ';'.join(name + '=' + str(component / 'usr/lib/libweston-14' / name)
                                    for name in ('headless-backend.so', 'xwayland.so'))}
    arguments = command('usr/bin/weston') + ['--backend=headless', '--renderer=pixman', '--xwayland',
        '--shell=' + str(shell), '--socket=wayland-0', '--width=1000', '--height=700',
        '--idle-time=0', '--config=' + str(config)]

    def authorize(display):
        subprocess.run(command('usr/bin/xauth') + ['-f', str(authentication), 'add', display,
                       'MIT-MAGIC-COOKIE-1', secrets.token_hex(16)], check=True,
                       env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        environment['XAUTHORITY'] = str(authentication)

    return arguments, server_environment, authorize, {'version': version,
        'original_xwayland_sha256': hashlib.sha256(data).hexdigest(),
        'prepared_xwayland_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
