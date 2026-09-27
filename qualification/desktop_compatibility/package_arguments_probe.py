"""Actual GIO expansion after scoped Flatpak option insertion, without an app."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from gi.repository import Gio, GLib
from application_package import private_flatpak_command


def main():
    root = Path(tempfile.mkdtemp(prefix='package-arguments-', dir=sys.argv[1]))
    command, receipt = root / 'flatpak-fixture', root / 'arguments.json'
    command.write_text('#!/usr/bin/python3\nimport json,os,sys\npath=' + repr(str(receipt)) +
                       '\nopen(path+".tmp","w").write(json.dumps(sys.argv[1:]))\nos.replace(path+".tmp",path)\n')
    command.chmod(0o700)
    original = '"' + str(command) + '" run --command=editor org.example.Editor "argument with spaces" @@u %U @@'
    entry = GLib.KeyFile.new()
    entry.set_string('Desktop Entry', 'Type', 'Application')
    entry.set_string('Desktop Entry', 'Name', 'Floe package argument fixture')
    entry.set_string('Desktop Entry', 'Exec', original)
    path = root / 'fixture.desktop'
    path.write_text(entry.to_data()[0])
    app = Gio.DesktopAppInfo.new_from_filename(str(path))
    plugins = str(root / 'plugin " quote $ variable ` backtick %U \\ slash')
    plan = {'backend': {'id': 'wayland'}, 'observation': {'package': {'kind': 'flatpak'},
        'executable': {'path': str(command)}}}
    adapted = private_flatpak_command(app, plan, {'PATH': '/usr/bin:/bin',
                                      'FLOE_NATIVE_FLATPAK_QT': plugins}, GLib)
    uri = (root / 'document with spaces.txt').as_uri()
    def arguments(command_text):
        receipt.unlink(missing_ok=True)
        entry.set_string('Desktop Entry', 'Exec', command_text)
        app = Gio.DesktopAppInfo.new_from_keyfile(entry)
        assert app.launch_uris([uri], Gio.AppLaunchContext.new())
        deadline = time.monotonic() + 5
        while not receipt.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        return json.loads(receipt.read_text())
    original_arguments = arguments(original)
    actual = arguments(adapted)
    expected = original_arguments[:1] + ['--env=QT_PLUGIN_PATH=' + plugins] + original_arguments[1:]
    assert actual == expected, (actual, expected)
    result = {'passed': True, 'receipt': str(receipt), 'glib': [GLib.MAJOR_VERSION, GLib.MINOR_VERSION,
        GLib.MICRO_VERSION], 'literal_module_path': plugins, 'original_arguments': original_arguments, 'actual_arguments': actual}
    (root / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
