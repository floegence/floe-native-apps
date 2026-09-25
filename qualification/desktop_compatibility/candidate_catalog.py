"""Record downloaded publisher APKs for an unpublished portable-stack fixture.

Run apk's signature verification first in the disposable acquisition container.
This does not update the compiled production catalog or authorize installation.
"""
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tarfile
from urllib.parse import urlsplit


def main():
    root = Path(sys.argv[1]).resolve()
    architecture = sys.argv[2]
    upstream_architecture = {'amd64': 'x86_64', 'arm64': 'aarch64'}[architecture]
    urls = {}
    for line in (root / 'urls.txt').read_text().splitlines():
        parsed = urlsplit(line)
        if parsed.scheme != 'https' or parsed.hostname != 'dl-cdn.alpinelinux.org':
            raise ValueError('Unreviewed candidate publisher')
        name = parsed.path.rsplit('/', 1)[1]
        if name in urls or '/' + upstream_architecture + '/' not in parsed.path:
            raise ValueError('Duplicate or wrong-architecture candidate URL')
        urls[name] = line
    artifacts, installed = [], 0
    for path in sorted((root / 'apks').glob('*.apk')):
        url = urls.pop(path.name)
        with gzip.open(path, 'rb') as compressed:
            with tarfile.open(fileobj=compressed, mode='r|', ignore_zeros=True) as archive:
                metadata = None
                for member in archive:
                    installed += member.size
                    if member.name == '.PKGINFO':
                        if metadata is not None or member.size > 1 << 20:
                            raise ValueError('Invalid original package metadata')
                        metadata = dict(line.split(' = ', 1) for line in
                            archive.extractfile(member).read().decode().splitlines() if ' = ' in line)
        assert metadata and metadata['arch'] in (upstream_architecture, 'noarch')
        repository = url.split('/')[-3]
        artifacts.append({'name': path.name, 'url': url, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'size_bytes': path.stat().st_size, 'format': 'apk', 'license': metadata['license'],
            'source': 'https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.23-stable/' + repository + '/' + metadata['origin']})
    assert not urls and artifacts, 'Candidate archive set is incomplete'
    package = {'id': 'candidate-alpine-3.23-weston-14-' + architecture, 'architecture': architecture,
               'size_bytes': sum(item['size_bytes'] for item in artifacts), 'installed_bytes': installed + (16 << 20),
               'artifacts': artifacts}
    (root / 'candidate.json').write_text(json.dumps(package, indent=2) + '\n')
    print(json.dumps({key: value for key, value in package.items() if key != 'artifacts'}))
    print('artifacts:', len(artifacts), '; unpublished candidate only')


if __name__ == '__main__':
    main()
