"""Acquire exact historical fixture bytes; never replace published catalog pins."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def prepare(architecture, destination):
    if architecture not in ('amd64', 'arm64'):
        raise ValueError('unsupported fixture architecture')
    destination = Path(destination)
    if not destination.is_dir():
        raise ValueError('existing fixture cache required')
    records = json.loads((Path(__file__).parent / 'retired/remote.json').read_text())
    for item in records:
        if item['architecture'] != architecture:
            continue
        target = destination / item['sha256']
        def valid(path):
            return path.is_file() and path.stat().st_size == item['size_bytes'] and hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
        if valid(target):
            continue
        with tempfile.TemporaryDirectory(prefix='retired-fixture-', dir=destination) as temporary:
            candidate = Path(temporary) / 'archive'
            for url in [item['url'], *item['mirrors']]:
                attempt = subprocess.run(['curl', '--proto', '=https', '--proto-redir', '=https', '-fLsS',
                                          '--max-time', '90', '--max-filesize', str(item['size_bytes']),
                                          url, '-o', str(candidate)], capture_output=True)
                if attempt.returncode == 0 and valid(candidate):
                    os.replace(candidate, target)
                    break
            else:
                raise RuntimeError('original fixture archive unavailable: ' + item['name'])
        print('Verified historical fixture:', architecture, item['name'], flush=True)


if __name__ == '__main__':
    prepare(*sys.argv[1:])
