"""Prepare private service resources from an already verified component.

The caller owns catalog verification and the instance directory. Preparation
never activates an installation or changes the original component. Only support
processes receive this environment; application launch retains its host runtime.
"""
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import xml.etree.ElementTree as ET


class DesktopServices:
    def __init__(self, component, instance):
        component, instance = Path(component), Path(instance)
        if not component.is_absolute() or not instance.is_absolute():
            raise ValueError('Absolute component and instance directories are required')
        info = instance.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError('Private instance directory is required')
        self.component = component.resolve(strict=True)
        if ':' in str(self.component):
            raise ValueError('Component path cannot contain a library-path separator')
        self.private = instance / 'desktop-services'
        architecture = {'aarch64': 'aarch64', 'arm64': 'aarch64', 'x86_64': 'x86_64'}.get(platform.machine())
        if architecture is None:
            raise ValueError('Unsupported native architecture')
        self.loader = self.resource('lib/ld-musl-' + architecture + '.so.1')
        self.libraries = ':'.join(str(self.component / name) for name in (
            'lib', 'usr/lib', 'usr/lib/gdk-pixbuf-2.0/2.10.0/loaders'))
        self.private.mkdir(mode=0o700)
        created = self.private.lstat()
        try:
            self._prepare()
        except BaseException:
            # Never remove a pre-existing directory or a replacement owned by
            # another operation, even when a native cache generator fails.
            current = self.private.lstat() if self.private.exists() else None
            if current and (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                shutil.rmtree(self.private)
            raise

    def _prepare(self):
        environment = self.environment({'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        mime = self.private / 'share/mime'
        shutil.copytree(self.resource('usr/share/mime/packages', directory=True), mime / 'packages')
        subprocess.run(self.command('usr/bin/update-mime-database') + [str(mime)],
                       env=environment, check=True, capture_output=True, timeout=30)
        subprocess.run(self.command('usr/bin/glib-compile-schemas') + [
            '--strict', '--targetdir=' + str(self.private),
            str(self.resource('usr/share/glib-2.0/schemas', directory=True))],
            env=environment, check=True, capture_output=True, timeout=30)
        for name, command in (
            ('pixbuf.loaders', self.command('usr/bin/gdk-pixbuf-query-loaders')),
            ('gtk.immodules', self.command('usr/bin/gtk-query-immodules-3.0') + [
                str(self.resource('usr/lib/gtk-3.0/3.0.0/immodules/im-ibus.so'))]),
        ):
            data = subprocess.check_output(command, env=environment, stderr=subprocess.PIPE, timeout=30)
            if not data.strip():
                raise ValueError('Native cache generator produced no data')
            (self.private / name).write_bytes(data)
        fonts = ET.Element('fontconfig')
        for path in ('/usr/share/fonts', '/usr/local/share/fonts', self.component / 'usr/share/fonts'):
            ET.SubElement(fonts, 'dir').text = str(path)
        ET.SubElement(fonts, 'cachedir').text = str(self.private / 'font-cache')
        ET.ElementTree(fonts).write(self.private / 'fonts.conf', encoding='utf-8', xml_declaration=True)

    def resource(self, relative, *, directory=False):
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Native resource must remain in the verified component')
        path = self.component / path
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.component) or not (path.is_dir() if directory else path.is_file()):
            raise ValueError('Native resource is unavailable or outside the verified component')
        return path

    def command(self, relative):
        binary = self.resource(relative)
        return [str(self.loader), '--library-path', self.libraries, str(binary)]

    def environment(self, base):
        environment = dict(base)
        for key in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'GTK_PATH',
                    'GIO_EXTRA_MODULES', 'DBUS_STARTER_ADDRESS', 'DBUS_STARTER_BUS_TYPE', 'FONTCONFIG_PATH'):
            environment.pop(key, None)
        environment.update({
            'GIO_MODULE_DIR': str(self.component / 'usr/lib/gio/modules'),
            'GI_TYPELIB_PATH': str(self.component / 'usr/lib/girepository-1.0'),
            'GDK_PIXBUF_MODULE_FILE': str(self.private / 'pixbuf.loaders'),
            'GDK_PIXBUF_MODULEDIR': str(self.component / 'usr/lib/gdk-pixbuf-2.0/2.10.0/loaders'),
            'GTK_IM_MODULE_FILE': str(self.private / 'gtk.immodules'),
            'GSETTINGS_SCHEMA_DIR': str(self.private), 'GSETTINGS_BACKEND': 'memory',
            'XDG_DATA_DIRS': str(self.private / 'share') + ':' + str(self.component / 'usr/share'),
            'FONTCONFIG_FILE': str(self.private / 'fonts.conf'),
            'PYTHONHOME': str(self.component / 'usr'), 'PYTHONNOUSERSITE': '1',
        })
        return environment
