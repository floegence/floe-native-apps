"""Stage reviewed native outputs with their complete corresponding source.

This runs only in release development, never on an application host. Original
runtime archives stay at their publishers. No catalog is activated by this tool.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--qt', type=Path, required=True)
    parser.add_argument('--gtk', type=Path, required=True)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--candidates', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    output = root / 'native/dist'
    if output.exists():
        raise FileExistsError('Review/remove the previous generated distribution explicitly')
    common = output / 'common'
    common.mkdir(parents=True)
    required = {'desktop-shell.so', 'desktop-capture', 'libweston-14.so.0',
                'xwayland.so', 'headless-backend.so', 'ibus-daemon', 'ibus-portal', 'libibus-1.0.so.5'}
    catalogs = []
    originals = {'weston-14.0.2.tar.xz': 'b47216b3530da76d02a3a1acbf1846a9cd41d24caa86448f9c46f78f20b6e0ac',
                 'ibus-1.5.33.tar.gz': '58941c9b8285891c776b67fb2039eebe0d61d63a51578519febfc5481b91e831'}
    def copy(source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and sha256(source) != sha256(destination):
            raise ValueError('Conflicting corresponding source: ' + str(destination))
        shutil.copyfile(source, destination)
    for name, digest in originals.items():
        assert sha256(args.sources / name) == digest, name
        copy(args.sources / name, common / 'sources' / name)
    for license in ('LICENSE', 'licenses/weston-14-COPYING.txt', 'licenses/ibus-COPYING.txt'):
        copy(root / license, common / 'licenses' / Path(license).name)
    protocol = root / 'native/protocols/text-input-unstable-v3.xml'
    assert sha256(protocol) == '2d08f2cddb463e169c23f1c34769de12ae255540e51ee8f515b54667d60b90ba'
    copy(protocol, common / 'sources/tree/native/protocols' / protocol.name)
    for architecture in ('amd64', 'arm64'):
        native = args.native / architecture
        record = json.loads((native / 'build.json').read_text())
        assert record['architecture'] == architecture and set(record['artifacts']) == required
        assert record['original_sources'] == originals
        target = output / architecture
        for name, expected in record['artifacts'].items():
            path = native / 'artifacts' / name
            assert sha256(path) == expected['sha256'] and path.stat().st_size == expected['size_bytes'], name
            assert '(RPATH)' not in expected['elf'] and '(RUNPATH)' not in expected['elf']
            copy(path, target / 'artifacts' / name)
        for name, digest in record['sources'].items():
            assert sha256(root / name) == digest, name
            copy(root / name, common / 'sources/tree' / name)
        copy(native / 'build.json', target / 'provenance/native.json')
        for major in (5, 6):
            qt = args.qt / architecture / f'qt{major}'
            proof = json.loads((qt / 'build.json').read_text())
            assert proof['architecture'] == {'amd64': 'x86_64', 'arm64': 'aarch64'}[architecture]
            for name, digest in proof['source_sha256'].items():
                relative = ('native/' if name.startswith('qt_native.') else 'qualification/desktop_compatibility/') + name
                assert sha256(root / relative) == digest, name
                copy(root / relative, common / 'sources/tree' / relative)
            path = qt / 'libfloe-client-native.so'
            assert sha256(path) == proof['artifact']['sha256']
            copy(path, target / f'qt/platforminputcontexts/libfloe-client-native-qt{major}.so')
            copy(qt / 'build.json', target / f'provenance/qt{major}.json')
        gtk = args.gtk / architecture
        proof = json.loads((gtk / 'build.json').read_text())
        assert proof['architecture'] == {'amd64': 'x86_64', 'arm64': 'aarch64'}[architecture]
        assert proof['toolkits']['4'] == '4.0.3' and proof['signed_builder_archives']
        assert set(proof['artifacts']) == {'libfloe-gtk3-native.so', 'libfloe-gtk4-native.so'}
        for relative, digest in proof['source_sha256'].items():
            assert sha256(root / relative) == digest, relative
            copy(root / relative, common / 'sources/tree' / relative)
        for name, expected in proof['artifacts'].items():
            path = gtk / name
            assert sha256(path) == expected['sha256'], name
            assert '(RPATH)' not in expected['elf'] and '(RUNPATH)' not in expected['elf']
            copy(path, target / 'gtk' / name)
        copy(gtk / 'build.json', target / 'provenance/gtk.json')
        # Patch provenance contains the original publisher URL and source hashes.
        for name in ('ibus-1.5-context-source.json', 'weston-14-input-events.json'):
            copy(root / 'native/patches' / name, common / 'sources/tree/native/patches' / name)
        manifest = {'version': 1, 'architecture': architecture, 'files': {}}
        for directory, shared in ((common, True), (target, False)):
            for path in sorted(directory.rglob('*')):
                if path.is_file():
                    name = str(path.relative_to(directory))
                    assert name not in manifest['files']
                    manifest['files'][name] = {'sha256': sha256(path), 'size_bytes': path.stat().st_size,
                        'common': shared, 'executable': name.startswith('artifacts/')}
        data = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
        (target / 'manifest.json').write_bytes(data)
        candidate = json.loads((args.candidates / f'{architecture}.json').read_text())
        candidate['id'] = f'alpine-3.23-desktop-14.0.2-{architecture}-r2'
        candidate['preparation'] = {'contract': 'wayland-xwayland-private-v1',
            'native_sha256': hashlib.sha256(data).hexdigest()}
        catalogs.append(candidate)
    (root / 'desktop_catalog.json').write_text(json.dumps(catalogs, indent=2) + '\n')


if __name__ == '__main__':
    main()
