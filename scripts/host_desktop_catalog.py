"""Derive the media closure from original Alpine archives.

Run publisher signature verification on the acquired archives before committing
the candidate catalog. This script neither activates nor publishes a component.
"""
import concurrent.futures
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

parser = argparse.ArgumentParser()
parser.add_argument('--work', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
BASE = args.work.resolve()
OUTPUT = args.output.resolve()
BASE.mkdir(parents=True, exist_ok=True)
SEEDS = 'python3 py3-gobject3 py3-gst py3-xlib gtk+3.0 gst-plugin-pipewire gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugins-ugly'.split()


def download(url, path):
    if not path.exists():
        subprocess.run(['curl', '--fail', '--silent', '--show-error', '--location', '--retry', '2',
                        '--output', str(path) + '.part', url], check=True)
        Path(str(path) + '.part').rename(path)
    return path.read_bytes()


def build(architecture, alpine):
    records = {}
    providers = {}
    for repository in ('main', 'community'):
        data = download(f'https://dl-cdn.alpinelinux.org/alpine/v3.23/{repository}/{alpine}/APKINDEX.tar.gz',
                        BASE / f'{repository}-{alpine}.tar.gz')
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            index = archive.extractfile('APKINDEX').read().decode()
        for text in index.split('\n\n'):
            record = dict(line.split(':', 1) for line in text.splitlines() if ':' in line)
            if not record:
                continue
            record['repository'] = repository
            records[record['P']] = record
            for provided in record.get('p', '').split():
                providers.setdefault(re.split('[=<>~]', provided)[0], []).append(record['P'])
    selected = {}
    def add(specification):
        if specification.startswith('!'):
            return
        name = re.split('[=<>~]', specification)[0]
        if name not in records:
            options = providers.get(name, [])
            if len(options) != 1:
                # Explicit default distribution provider for a virtual capability.
                choices = {'pipewire-session-manager': 'wireplumber', '/bin/sh': 'busybox-binsh', 'so:libudev.so.1': 'eudev-libs'}
                name = choices.get(name)
                if name not in options:
                    raise ValueError((specification, options))
            else:
                name = options[0]
        if name in selected:
            return
        selected[name] = records[name]
        for dependency in records[name].get('D', '').split():
            add(dependency)
    for seed in SEEDS:
        add(seed)
    directory = BASE / 'media-archives' / architecture
    directory.mkdir(parents=True, exist_ok=True)
    def artifact(record):
        name = record['P'] + '-' + record['V'] + '.apk'
        url = f'https://dl-cdn.alpinelinux.org/alpine/v3.23/{record["repository"]}/{alpine}/{name}'
        data = download(url, directory / name)
        if len(data) != int(record['S']):
            raise ValueError('index size mismatch: ' + name)
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(data)), mode='r:', ignore_zeros=True) as archive:
            props = dict(line.split(' = ', 1) for line in archive.extractfile('.PKGINFO').read().decode().splitlines() if ' = ' in line)
            installed = sum(member.size for member in archive.getmembers())
        if props['pkgname'] != record['P'] or props['pkgver'] != record['V'] or props['arch'] not in (alpine, 'noarch'):
            raise ValueError('archive identity mismatch: ' + name)
        return dict(name=name, url=url, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
                    format='apk', license=props['license'], source=f'https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.23-stable/{record["repository"]}/{props["origin"]}'), installed
    print(architecture, len(selected), sum(int(record['S']) for record in selected.values()), flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(artifact, sorted(selected.values(), key=lambda item: item['P'])))
    return dict(id='alpine-3.23-host-desktop-1-' + architecture, architecture=architecture,
                size_bytes=sum(value['size_bytes'] for value, _ in values),
                installed_bytes=sum(size for _, size in values) + (16 << 20),
                artifacts=[value for value, _ in values])


if __name__ == '__main__':
    packages = [build('amd64', 'x86_64'), build('arm64', 'aarch64')]
    OUTPUT.write_text(json.dumps(packages, indent=2) + '\n')
    print('catalog ready', flush=True)
