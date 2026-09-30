"""Request a real desktop grant without reading pixels or sending input.

Run in an owner-only task directory. Results intentionally omit restore tokens,
clipboard content and any desktop image. Only an explicit host user can approve
the system portal prompt. This is capability evidence, not media qualification.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib
from host_desktop_portal import PortalGrant, PortalSession


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', required=True)
    args = parser.parse_args()
    loop = GLib.MainLoop()
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    session = PortalSession(bus, Gio, GLib, PortalGrant(args.state),
        lambda state: print(json.dumps({'state': state}), flush=True))
    result = {'passed': False}

    def complete(streams, error):
        result.update(passed=error is None, error=error, clipboard=session.clipboard,
            devices=session.devices, streams=[{'node': node, 'size': props.get('size'),
                'position': props.get('position')} for node, props in streams or []])
        print(json.dumps(result), flush=True)
        session.close()
        loop.quit()

    GLib.timeout_add_seconds(120, lambda: (complete(None, 'HOST_CONSENT_TIMEOUT'), False)[1])
    print(json.dumps({'state': 'requesting_host_consent'}), flush=True)
    session.start(True, complete)
    loop.run()
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
