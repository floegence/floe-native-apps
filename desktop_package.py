"""Realize confirmed-text adapters inside the selected package's existing policy.

The session owns one unique resource directory. This never changes package
permissions, user configuration, profiles, or installed toolkit libraries.
"""
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from desktop_graphics import private_directory, owned_identity, remove_owned


class DesktopPackageInput:
    def __init__(self, instance, package, environment, modules):
        instance = private_directory(instance)
        self.environment = dict(environment)
        self.environment.pop('FLOE_NATIVE_FLATPAK_QT', None)
        self.environment.pop('FLOE_NATIVE_DESKTOP_INPUT', None)
        self.directory = self.identity = None
        if package['kind'] == 'snap' and package['confinement'] == 'strict':
            # A strict package may replace its module search paths. Do not
            # assume host ABI libraries can be loaded across its sandbox.
            self.environment.pop('QT_PLUGIN_PATH', None)
            self.environment['QT_IM_MODULE'] = 'ibus'
            return
        source = Path(modules).resolve(strict=True)
        originals = []
        for major in (5, 6):
            path = source / 'platforminputcontexts' / f'libfloe-client-native-qt{major}.so'
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(source) or not resolved.is_file():
                raise ValueError('Verified native Qt module is unavailable')
            originals.append((path.name, resolved))
        flatpak = package['kind'] == 'flatpak'
        parent = instance
        if flatpak:
            app_id = package['id']
            if not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){2,}', app_id):
                raise ValueError('Verified Flatpak identity is required')
            parent = Path(environment.get('HOME', ''))
            if not parent.is_absolute():
                raise ValueError('Absolute application home is required')
            # Use the package's standard sandbox root, including first launch.
            # Only input resource directories are created; application profile
            # and XDG configuration locations remain unchanged.
            for part in ('.var', 'app', app_id):
                parent = parent / part
                try:
                    parent.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                info = parent.lstat()
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
                        info.st_mode & 0o022):
                    raise ValueError('Owned Flatpak resource root is unavailable')
        self.prefix = '.floe-native-input-' + hashlib.sha256(str(instance).encode()).hexdigest()[:16] + '-'
        self.directory = Path(tempfile.mkdtemp(prefix=self.prefix, dir=parent))
        self.identity = owned_identity(self.directory)
        try:
            plugins = self.directory / 'platforminputcontexts'
            plugins.mkdir(mode=0o700)
            for name, source_file in originals:
                shutil.copyfile(source_file, plugins / name)
                (plugins / name).chmod(0o600)
            paths = str(self.directory)
            if not flatpak and environment.get('QT_PLUGIN_PATH'):
                paths += ':' + environment['QT_PLUGIN_PATH']
            self.environment.update(QT_IM_MODULE='floe-client-native', QT_PLUGIN_PATH=paths)
            if flatpak:
                self.environment['FLOE_NATIVE_DESKTOP_INPUT'] = package['id'] + '.FloeClientInput'
                # Flatpak runtime metadata can override QT_PLUGIN_PATH. The
                # supervisor turns this one private path into an official
                # --env argument after revalidating the original desktop entry.
                self.environment['FLOE_NATIVE_FLATPAK_QT'] = str(self.directory)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.identity is not None:
            remove_owned(self.directory, self.identity, directory=True)
            self.identity = None
