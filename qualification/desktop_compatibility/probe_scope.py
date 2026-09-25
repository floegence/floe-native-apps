"""Exercise the production supervisor's real Snap scope without a display.

The fixture backend declaration proves identity only. Actual graphical readiness
and input/save are exercised separately by probe_wayland.py.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from launch_plan import prepare


def main():
    source = Path(sys.argv[1]).resolve()
    host_address = os.environ['DBUS_SESSION_BUS_ADDRESS']
    with tempfile.TemporaryDirectory(prefix='floe-scope-probe-') as directory:
        root = Path(directory)
        config = root / 'bus.conf'
        config.write_text('<busconfig><type>session</type><auth>EXTERNAL</auth>'
            '<listen>unix:tmpdir=/tmp</listen><policy context="default">'
            '<allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/>'
            '</policy></busconfig>')
        bus = subprocess.Popen(['dbus-daemon', '--nofork', '--config-file=' + str(config),
                                '--print-address=1'], stdout=subprocess.PIPE, text=True)
        try:
            environment = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=bus.stdout.readline().strip(),
                               FLOE_NATIVE_HOST_BUS=host_address)
            for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY'):
                environment.pop(key, None)
            desktop = root / 'fixture.desktop'
            desktop.write_text('[Desktop Entry]\nType=Application\nName=Scope fixture\n'
                               'Exec=/snap/bin/firefox --version\n')
            backend = {'id': 'wayland', 'component': 'identity-only-scope-fixture',
                       'protocols': ['wayland', 'x11'],
                       'services': ['user-systemd-scope', 'file-portal', 'document-portal']}
            plan = prepare(str(desktop), environment, [backend])
            plan_path, receipt = root / 'plan.json', root / 'receipt.json'
            plan_path.write_text(json.dumps(plan))
            child = subprocess.run(['python3', str(source / 'application.py'), '--plan', str(plan_path), str(receipt)],
                                   env=environment, capture_output=True, text=True, timeout=25)
            events = [json.loads(line) for line in child.stdout.splitlines() if line.startswith('{')]
            result = {'code': child.returncode, 'receipt': json.loads(receipt.read_text()),
                      'events': events, 'stdout': child.stdout, 'stderr': child.stderr}
            print(json.dumps(result, indent=2))
            assert child.returncode == 0 and result['receipt']['state'] == 'exited'
            assert [event['event'] for event in events] == ['scope-accepted', 'scope-completed']
            assert events[1]['result'] == 'done'
            assert events[0]['pid'] == result['receipt']['launchers'][0]['pid']
        finally:
            bus.terminate()
            bus.wait(timeout=5)


if __name__ == '__main__':
    main()
