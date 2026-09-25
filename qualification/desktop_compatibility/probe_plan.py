"""Real GIO execution checks for private launch plans; no user app is started."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(root))
    import launch_plan
    evidence = Path(tempfile.mkdtemp(prefix='launch-plan-', dir=root))
    executable = evidence / 'fixture app'
    received = evidence / 'argv.json'
    executable.write_text('#!/usr/bin/python3\nimport json,sys\nfrom pathlib import Path\n'
                          f'Path({str(received)!r}).write_text(json.dumps(sys.argv[1:]))\n')
    executable.chmod(0o700)
    desktop = evidence / 'fixture.desktop'
    original = ('[Desktop Entry]\nType=Application\nName=Floe plan fixture\n'
                f'Exec="{executable}" "literal with spaces" %% %c %k %F\n')
    desktop.write_text(original)
    # Identity tests do not certify a graphical backend. Its real qualification
    # is separate; no display or graphical readiness is asserted in this fixture.
    backend = {'id': 'wayland', 'component': 'identity-fixture-only',
               'protocols': ['wayland', 'x11']}
    environment = dict(os.environ)
    outcomes = []

    def plan():
        result = launch_plan.prepare(str(desktop), environment, [backend])
        path = evidence / 'plan.json'
        path.write_text(json.dumps(result))
        path.chmod(0o600)
        return path

    def run(path, expected_code):
        receipt = evidence / 'receipt.json'
        result = subprocess.run(['/usr/bin/python3', str(root / 'application.py'),
            '--plan', str(path), str(receipt)], env=environment, timeout=10,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert result.returncode == expected_code, result.stderr.decode()
        return json.loads(receipt.read_text())

    path = plan()
    receipt = run(path, 0)
    assert json.loads(received.read_text()) == ['literal with spaces', '%', 'Floe plan fixture', str(desktop)]
    assert receipt['state'] == 'exited' and receipt['exit_code'] == 0
    outcomes.append({'case': 'GIO field expansion and execution', 'passed': True})
    received.unlink()

    path = plan()
    desktop.write_text(original.replace('Floe plan fixture', 'Changed fixture'))
    receipt = run(path, 1)
    assert receipt['error_code'] == 'APPLICATION_PLAN_STALE' and not received.exists()
    outcomes.append({'case': 'changed desktop rejected before process creation', 'passed': True})
    desktop.write_text(original)

    path = plan()
    executable.write_text(executable.read_text() + '# changed executable\n')
    receipt = run(path, 1)
    assert receipt['error_code'] == 'APPLICATION_PLAN_STALE' and not received.exists()
    outcomes.append({'case': 'changed executable rejected before process creation', 'passed': True})

    path = plan()
    stale = json.loads(path.read_text())
    stale['version'] = 999
    path.write_text(json.dumps(stale))
    receipt = run(path, 1)
    assert receipt['error_code'] == 'APPLICATION_PLAN_STALE' and not received.exists()
    outcomes.append({'case': 'unknown plan rejected before process creation', 'passed': True})
    from gi.repository import Gio, GLib
    package_plans = []
    for candidate in sys.argv[2:]:
        source = Path(candidate).resolve()
        observed = launch_plan.inspect_application(str(source), environment)
        keyfile = GLib.KeyFile.new()
        keyfile.load_from_file(str(source), GLib.KeyFileFlags.NONE)
        for key in ('X-SnapInstanceName', 'X-SnapAppName', 'X-Flatpak'):
            if key in keyfile.get_keys('Desktop Entry')[0]:
                keyfile.remove_key('Desktop Entry', key)
        custom = evidence / ('custom-' + str(len(package_plans)) + '.desktop')
        custom.write_text(keyfile.to_data()[0])
        alternate = launch_plan.inspect_application(str(custom), environment)
        assert alternate['package'] == observed['package'], 'custom executable lost verified package identity'
        package_plans.append({'source_sha256': observed['desktop']['sha256'],
                              'package': observed['package'], 'custom_matches': True})
    record = {'passed': True, 'architecture': platform.machine(), 'outcomes': outcomes, 'packages': package_plans,
              'source': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                         for name in ('application.py', 'launch_plan.py')},
              'limits': 'Windowless task fixture; no package or graphical support claim'}
    (evidence / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    print(evidence)
    print(json.dumps(record))


if __name__ == '__main__':
    main()
