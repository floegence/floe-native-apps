"""Create one auditable source archive for the disposable native fixture root."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile


def main():
    source = Path(__file__).resolve().parent
    repository = source.parent.parent
    output = Path(sys.argv[1])
    if not output.is_absolute():
        raise ValueError('Qualification source archive requires an absolute path')
    paths = [*repository.glob('*.py'), *source.glob('*.py'), *source.glob('*.sh'),
             *source.glob('*.c'), *source.glob('*.h'), *source.glob('*.cpp'), *source.glob('Dockerfile*'),
             repository / 'qualification/input_fixture.py', *repository.glob('native/qt_native.*')]
    files, payloads = {}, {}
    for path in sorted(paths):
        if path.name in files or path.is_symlink():
            raise ValueError('Ambiguous qualification source: ' + path.name)
        data = path.read_bytes()
        payloads[path.name] = data
        files[path.name] = {'source': str(path.relative_to(repository)),
                            'sha256': hashlib.sha256(data).hexdigest()}
    manifest = {'kind': 'floe-desktop-qualification-source-v1',
        'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repository, text=True).strip(),
        'modified': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=repository)),
        'files': files}
    payloads['source-manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream, tarfile.open(fileobj=stream, mode='w') as archive:
        for name, data in sorted(payloads.items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(data), 0o600
            archive.addfile(entry, io.BytesIO(data))
    print(json.dumps({'archive': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                      'commit': manifest['commit'], 'modified': manifest['modified'], 'files': len(files)}))


if __name__ == '__main__':
    main()
