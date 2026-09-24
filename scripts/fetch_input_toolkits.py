"""Acquire verified original toolkit sources for the disposable module builder."""
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent.parent
output = root / 'input_modules' / 'sources'
output.mkdir(exist_ok=True)
for name, record in json.loads((root / 'scripts/input_toolkits.json').read_text()).items():
    archive = output / (name + '-' + record['version'] + '.tar.xz')
    if archive.exists() and hashlib.sha256(archive.read_bytes()).hexdigest() == record['sha256']:
        continue
    pending = archive.with_suffix('.partial')
    try:
        subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
                        '--connect-timeout', '15', '--max-time', '180',
                        record['url'], '-o', str(pending)], check=True)
        if hashlib.sha256(pending.read_bytes()).hexdigest() != record['sha256']:
            raise RuntimeError('Original toolkit archive integrity failure: ' + name)
        pending.replace(archive)
    finally:
        pending.unlink(missing_ok=True)
