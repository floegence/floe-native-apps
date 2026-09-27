"""Run actual applications against a newly installed, catalog-backed helper.

Run only on an explicitly disposable native Linux system. Package installation
belongs to the fixture owner; missing applications are failures, never skips.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--preparer', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--suite', choices=('native', 'sandbox', 'gtk-baseline'), required=True)
    parser.add_argument('--source-archive', type=Path,
        help='Auditable snapshot_sources.py archive when qualification runs on another native host')
    args = parser.parse_args()
    if platform.system() != 'Linux' or any(not p.is_absolute() for p in
            (args.state, args.preparer, args.evidence)):
        parser.error('Native Linux and absolute fixture paths are required')
    args.evidence.mkdir(mode=0o700)
    repository = Path(__file__).resolve().parent.parent
    archive = args.evidence / 'source.tar'
    if args.source_archive:
        shutil.copyfile(args.source_archive, archive)
    else:
        subprocess.run([sys.executable,
            str(repository / 'qualification/desktop_compatibility/snapshot_sources.py'), str(archive)],
            check=True, stdout=subprocess.DEVNULL)
    source = args.evidence / 'source'
    source.mkdir(mode=0o700)
    with tarfile.open(archive) as contents:
        contents.extractall(source, filter='data')
    manifest = json.loads((source / 'source-manifest.json').read_text())
    snapshot = {'commit': manifest['commit'], 'modified': manifest['modified'],
        'files': len(manifest['files']), 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
    result = {'version': 1, 'suite': args.suite, 'architecture': platform.machine(),
        'system': platform.platform(), 'source': snapshot,
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'cases': [], 'passed': False}
    (args.evidence / 'qualification.json').write_text(json.dumps(result, indent=2) + '\n')
    environment = dict(os.environ, FLOE_PROBE_DESKTOP_STATE=str(args.state),
        FLOE_PROBE_DESKTOP_PREPARER=str(args.preparer))
    cases = []
    if args.suite == 'native':
        for toolkit in ('gtk', 'gtk4', 'qt5', 'qt6'):
            for mode in ('clipboard', 'clipboard-x11'):
                cases.append((toolkit + '-' + mode, 'session_probe.py', [mode], {'FLOE_PROBE_TOOLKIT': toolkit}))
        cases.append(('gtk4-clipboard-x11-ordered', 'session_probe.py', ['clipboard-x11'],
            {'FLOE_PROBE_TOOLKIT': 'gtk4', 'FLOE_PROBE_PAUSE_XWAYLAND': '1'}))
        for mode in ('slow-window', 'launcher-failure', 'capture-loss', 'bus-loss', 'compositor-loss', 'terminate', 'terminate-windowless'):
            cases.append((mode, 'session_probe.py', [mode], {'FLOE_PROBE_TOOLKIT': 'gtk4'}))
        cases.append(('chromium', 'session_probe.py', ['clipboard-chromium'], {'FLOE_PROBE_CURSOR': '1'}))
        for protocol in ('wayland', 'x11'):
            cases.append(('oversized-' + protocol, 'oversized_session_probe.py', [protocol], {}))
    elif args.suite == 'gtk-baseline':
        binary = Path(os.environ['FLOE_TEST_GTK4_BASELINE'])
        if not binary.is_absolute() or not binary.is_file():
            raise ValueError('Actual minimum GTK runtime application is required')
        result['runtime'] = json.loads(binary.with_name('runtime.json').read_text())
        cases.append(('gtk4-baseline', 'session_probe.py', ['clipboard-x11'], {'FLOE_PROBE_TOOLKIT': 'gtk4'}))
    else:
        # These are fixture prerequisites in each package's existing private
        # data namespace. No installed user application is started for setup.
        subprocess.run(['snap', 'list', 'firefox'], check=True, stdout=subprocess.DEVNULL)
        (Path.home() / 'snap/firefox/common').mkdir(parents=True, exist_ok=True)
        cases.append(('snap-firefox', 'firefox_session_probe.py', [], {}))
        cases.append(('snap-firefox-popup-close', 'firefox_session_probe.py', ['--close-popup'], {}))
        for application in ('org.gnome.TextEditor', 'org.kde.kwrite'):
            subprocess.run(['flatpak', 'info', '--user', application], check=True, stdout=subprocess.DEVNULL)
            (Path.home() / '.var/app' / application).mkdir(parents=True, exist_ok=True)
            cases.append((application, 'flatpak_session_probe.py', [application], {}))
    try:
        for name, script, arguments, overrides in cases:
            completed = subprocess.run([sys.executable, str(source / script), str(source), *arguments],
                env=environment | overrides, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            (args.evidence / (name + '.json')).write_text(completed.stdout)
            (args.evidence / (name + '.log')).write_text(completed.stderr)
            receipt = json.loads(completed.stdout)
            case = {'case': name, 'passed': completed.returncode == 0 and receipt.get('passed') is True,
                'evidence': receipt.get('evidence'), 'returncode': completed.returncode}
            # Retain observable fixture results, never helper credentials,
            # sandbox profiles, launch environments or private configuration.
            observed = Path(receipt['evidence'])
            observed.relative_to(source)
            retained = args.evidence / name
            retained.mkdir(mode=0o700)
            paths = list(observed.glob('*.png')) + [observed / item for item in
                ('result.json', 'helper.log', 'document.json', 'document.txt', 'portal-copy.txt',
                 'control-receipts.json', 'browser-receipts.jsonl', 'saved-text.txt',
                 'session/desktop-status.json', 'session/application.json')]
            for path in paths:
                if path.is_file() and not path.is_symlink():
                    shutil.copyfile(path, retained / path.name)
            case['artifacts'] = name
            result['cases'].append(case)
            print(json.dumps(case), flush=True)
            if not case['passed']:
                raise RuntimeError('Native application qualification failed: ' + name)
        result['passed'] = True
    finally:
        (args.evidence / 'qualification.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
