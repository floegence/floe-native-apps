"""Bind native fixture receipts to the exact copied source snapshot."""
import hashlib
import json
from pathlib import Path


def record_sources(root):
    root = Path(root)
    data = (root / 'source-manifest.json').read_bytes()
    manifest = json.loads(data)
    if manifest.get('kind') != 'floe-desktop-qualification-source-v1' or not manifest.get('files'):
        raise ValueError('Native qualification source snapshot is unavailable')
    for name, entry in manifest['files'].items():
        if Path(name).name != name or name in ('.', '..'):
            raise ValueError('Native qualification source path is invalid')
        path = root / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Native qualification source snapshot differs: ' + name)
    return {'commit': manifest['commit'], 'modified': manifest['modified'],
            'manifest_sha256': hashlib.sha256(data).hexdigest(), 'files': len(manifest['files'])}
