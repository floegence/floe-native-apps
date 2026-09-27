"""Download the signed, fixed system-Xpra fixture for disposable Linux runners.

This is qualification tooling, never application installation or a production
fallback. Keep the complete original indexes so their signed hashes remain
verifiable even while the publisher's live repository is being regenerated.
"""
import argparse
from email.parser import Parser
import gzip
import hashlib
import json
from pathlib import Path
import subprocess


VERSION = '6.5.3-r0-1'
PACKAGES = ('xpra', 'xpra-x11', 'xpra-common', 'xpra-client',
            'xpra-client-gtk3', 'xpra-server', 'xpra-codecs')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--arch', choices=('amd64', 'arm64'), required=True)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    destination = args.directory.resolve()
    destination.mkdir(mode=0o700)
    metadata = Path(__file__).resolve().parent.parent / 'qualification/system_xpra'
    keyring = destination / 'keyring.gpg'
    keyhome = destination / 'gnupg'
    keyhome.mkdir(mode=0o700)
    subprocess.run(['gpg', '--batch', '--homedir', str(keyhome), '--dearmor',
        '--output', str(keyring), str(metadata / 'signing-key.asc')], check=True)
    release = destination / 'Release'
    subprocess.run(['gpgv', '--homedir', str(keyhome), '--keyring', str(keyring),
        '--output', str(release), str(metadata / 'InRelease')], check=True)
    signed = Parser().parsestr(release.read_text())
    index_path = 'main/binary-' + args.arch + '/Packages'
    hashes = [line.split() for line in signed['SHA256'].splitlines() if line.strip()]
    expected = [item for item in hashes if len(item) == 3 and item[2] == index_path]
    if len(expected) != 1:
        raise ValueError('Signed Xpra index identity is unavailable')
    contents = gzip.decompress((metadata / (args.arch + '-Packages.gz')).read_bytes())
    if (len(contents) != int(expected[0][1]) or
            hashlib.sha256(contents).hexdigest() != expected[0][0]):
        raise ValueError('Original Xpra index differs from its signed Release')
    records = [Parser().parsestr(block) for block in contents.decode().split('\n\n') if block.strip()]
    receipt = {'version': VERSION, 'architecture': args.arch,
        'signed_release_sha256': hashlib.sha256((metadata / 'InRelease').read_bytes()).hexdigest(),
        'index_sha256': expected[0][0], 'packages': []}
    for name in PACKAGES:
        matches = [item for item in records if item['Package'] == name and item['Version'] == VERSION]
        if len(matches) != 1:
            raise ValueError('Pinned Xpra package is unavailable: ' + name)
        item = matches[0]
        filename = name + '_' + VERSION + '_' + args.arch + '.deb'
        if item['Filename'] != 'dists/noble/main/binary-' + args.arch + '/' + filename:
            raise ValueError('Unexpected publisher package location')
        target = destination / filename
        partial = target.with_suffix('.part')
        url = 'https://xpra.org/' + item['Filename']
        subprocess.run(['curl', '--fail', '--silent', '--show-error', '--location',
            '--proto', '=https', '--proto-redir', '=https', '--max-time', '300',
            '--output', str(partial), url], check=True)
        data = partial.read_bytes()
        if len(data) != int(item['Size']) or hashlib.sha256(data).hexdigest() != item['SHA256']:
            raise ValueError('Publisher package differs from signed index: ' + name)
        partial.rename(target)
        receipt['packages'].append({'name': name, 'url': url, 'sha256': item['SHA256'], 'size': len(data)})
        print('Verified', name, VERSION, args.arch, flush=True)
    (destination / 'provenance.json').write_text(json.dumps(receipt, indent=2) + '\n')


if __name__ == '__main__':
    main()
