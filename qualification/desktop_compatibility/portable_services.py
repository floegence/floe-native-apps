"""Relocate original candidate support processes, never application libraries.

This unpublished fixture derives caches from verified original component files.
It neither runs package-manager hooks nor mutates the extracted component root.
"""
from pathlib import Path
import platform
import shutil
import subprocess


class PortableServices:
    def __init__(self, component, evidence):
        self.component = Path(component).resolve()
        self.private = evidence / 'portable-services'
        self.private.mkdir(mode=0o700)
        self.loader = self.component / 'lib' / ('ld-musl-aarch64.so.1'
            if platform.machine() == 'aarch64' else 'ld-musl-x86_64.so.1')
        self.libraries = ':'.join(str(self.component / name) for name in (
            'lib', 'usr/lib', 'usr/lib/gdk-pixbuf-2.0/2.10.0/loaders'))
        mime = self.private / 'share/mime'
        shutil.copytree(self.component / 'usr/share/mime/packages', mime / 'packages')
        subprocess.run(self.command('usr/bin/update-mime-database') + [str(mime)],
                       check=True, capture_output=True)
        subprocess.run(self.command('usr/bin/glib-compile-schemas') + [
            '--strict', '--targetdir=' + str(self.private),
            str(self.component / 'usr/share/glib-2.0/schemas')], check=True, capture_output=True)
        environment = self.environment({})
        for name, command in (
            ('pixbuf.loaders', self.command('usr/bin/gdk-pixbuf-query-loaders')),
            ('gtk.immodules', self.command('usr/bin/gtk-query-immodules-3.0') + [
                str(self.component / 'usr/lib/gtk-3.0/3.0.0/immodules/im-ibus.so')]),
        ):
            data = subprocess.check_output(command, env=environment, stderr=subprocess.PIPE)
            if not data.strip():
                raise RuntimeError('Candidate cache generator produced no data')
            (self.private / name).write_bytes(data)
        (self.private / 'fonts.conf').write_text('<?xml version="1.0"?><fontconfig>'
            '<dir>/usr/share/fonts</dir><dir>/usr/local/share/fonts</dir>'
            '<dir>' + str(self.component / 'usr/share/fonts') + '</dir>'
            '<cachedir>' + str(self.private / 'font-cache') + '</cachedir></fontconfig>')

    def command(self, relative):
        binary = self.component / relative
        if not binary.is_file():
            raise RuntimeError('Missing candidate support executable: ' + relative)
        return [str(self.loader), '--library-path', self.libraries, str(binary)]

    def environment(self, base):
        environment = dict(base)
        for key in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'GTK_PATH',
                    'GIO_EXTRA_MODULES', 'DBUS_STARTER_ADDRESS', 'DBUS_STARTER_BUS_TYPE'):
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
