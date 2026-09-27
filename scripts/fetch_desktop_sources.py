"""Acquire bounded, pinned original source archives for native build candidates."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request


def main():
    repository = Path(__file__).resolve().parent.parent
    output = Path(sys.argv[1])
    output.mkdir(mode=0o700)
    for metadata, url_key, hash_key, filename in (
        ('weston-14-input-events.json', 'source', 'source_sha512', 'weston-14.0.2.tar.xz'),
        ('ibus-1.5-context-source.json', 'original_archive', 'original_sha512', 'ibus-1.5.33.tar.gz'),
    ):
        record = json.loads((repository / 'native/patches' / metadata).read_text())
        with urllib.request.urlopen(record[url_key], timeout=60) as response:
            data = response.read((64 << 20) + 1)
        if len(data) > 64 << 20 or hashlib.sha512(data).hexdigest() != record[hash_key]:
            raise ValueError('Original native source archive failed verification: ' + filename)
        with (output / filename).open('xb') as destination:
            destination.write(data)
        print(filename, hashlib.sha256(data).hexdigest())


if __name__ == '__main__':
    main()
