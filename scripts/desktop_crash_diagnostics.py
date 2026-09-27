"""Retain content-free compositor stacks from disposable native qualification.

Core files stay outside the artifact tree. Only an explicitly recorded compositor
PID is inspected; application cores and memory contents are never published.
"""
import json
from pathlib import Path
import subprocess
import sys


def main():
    evidence, cores = map(Path, sys.argv[1:])
    component = evidence / 'component.json'
    if not component.is_file() or not cores.is_dir():
        return
    root = Path(json.loads(component.read_text())['root'])
    libraries = [root / name for name in
                 ('floe/desktop/artifacts', 'lib', 'usr/lib', 'usr/lib/weston', 'usr/lib/libweston-14')]
    seen = set()
    for receipt in sorted(evidence.glob('*/*/desktop-status.json')):
        value = json.loads(receipt.read_text())
        for process in value['processes']:
            if process['service'] != 'compositor' or process.get('exit_code') != -11:
                continue
            pid = process['pid']
            if type(pid) is not int or pid <= 0 or pid in seen:
                continue
            seen.add(pid)
            core = cores / f'core.{pid}'
            if not core.is_file() or core.is_symlink():
                continue
            output = receipt.with_name('compositor-backtrace.log')
            with output.open('w') as stream:
                subprocess.run(['gdb', '-q', '-batch', str(root / 'usr/bin/weston'),
                    '-ex', 'set pagination off', '-ex', 'set print frame-arguments none',
                    '-ex', 'set solib-search-path ' + ':'.join(map(str, libraries)),
                    '-ex', 'core-file ' + str(core), '-ex', 'thread apply all bt',
                    '-ex', 'info sharedlibrary', '-ex', 'info proc mappings',
                    '-ex', 'x/12i $pc-16'], stdout=stream, stderr=subprocess.STDOUT,
                    timeout=60, check=False)


if __name__ == '__main__':
    main()
