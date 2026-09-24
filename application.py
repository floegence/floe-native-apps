"""GIO launch and process-tree observation, independent of graphical windows.

The consumer supplies the selected desktop entry and private receipt destination.
This module does not discover applications, authorize requests, expose a listener,
or own product sessions. An explicit supervisor SIGTERM ends its owned child tree.
"""

import ctypes
import json
import os
from pathlib import Path
import signal
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402


def write_receipt(path, state):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"state": state}), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def terminate_children(_signal, _frame):
    """Explicit supervisor termination affects only this launcher's child tree.

    Kernel handles and a checked parent relationship prevent PID reuse from
    targeting another process. Newly adopted descendants are reaped in the next
    pass. Normal viewer, window and consumer lifetimes never send this signal.
    """
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    def terminate(parent):
        try:
            children = Path(f"/proc/{parent}/task/{parent}/children").read_text().split()
        except FileNotFoundError:
            return
        for child in children:
            pid = int(child)
            try:
                descriptor = os.pidfd_open(pid)
            except ProcessLookupError:
                continue
            try:
                stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                if int(stat[1]) != parent:
                    continue
                terminate(pid)
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except (FileNotFoundError, ProcessLookupError):
                pass
            finally:
                os.close(descriptor)
    while True:
        terminate(os.getpid())
        try:
            os.waitpid(-1, 0)
        except ChildProcessError:
            return


def launch(app, receipt):
    receipt = Path(receipt)
    if not receipt.is_absolute():
        raise ValueError("An absolute private receipt path is required.")
    try:
        if not app or not app.should_show() or app.get_boolean("Terminal"):
            raise ValueError("The selected graphical application is unavailable.")
        # Adopt daemonized descendants so an intermediate launcher exiting cannot
        # tear down the application's display. This affects only our own tree.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "Cannot observe application descendants")
        # Fail before admission when exact process-tree termination is unavailable.
        descriptor = os.pidfd_open(os.getpid())
        os.close(descriptor)
        signal.signal(signal.SIGTERM, terminate_children)
        if app.get_boolean("DBusActivatable"):
            entry = GLib.KeyFile.new()
            entry.load_from_file(app.get_filename(), GLib.KeyFileFlags.NONE)
            entry.set_boolean("Desktop Entry", "DBusActivatable", False)
            path = receipt.with_name("launch.desktop")
            path.write_text(entry.to_data()[0], encoding="utf-8")
            path.chmod(0o600)
            app = Gio.DesktopAppInfo.new_from_filename(str(path))
        context = Gio.AppLaunchContext.new()
        for key, value in json.loads(os.environ.get("FLOE_NATIVE_APPLICATION_ENV", "{}")).items():
            if value is None:
                context.unsetenv(key)
            else:
                context.setenv(key, value)
        context.unsetenv("FLOE_NATIVE_APPLICATION_ENV")
        context.unsetenv("FLOE_NATIVE_ROOT")
        # Support-tool Python clears GTK_PATH and the saved host map restores it.
        # Apply the input launcher's explicit private path only to the final app.
        gtk_path = os.environ.get("FLOE_NATIVE_INPUT_GTK_PATH")
        if gtk_path:
            if not Path(gtk_path).is_absolute():
                raise ValueError("The private GTK input path must be absolute.")
            context.setenv("GTK_PATH", gtk_path)
        context.unsetenv("FLOE_NATIVE_INPUT_GTK_PATH")
        pids = []
        def started(_app, pid, _data):
            pids.append(pid)
        if not app.launch_uris_as_manager([], context, GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD,
                                          None, None, started, None) or not pids:
            raise ValueError("The application did not start an owned process.")
        write_receipt(receipt, "running")
    except Exception:
        write_receipt(receipt, "failed")
        raise
    # No display polling or first-window timeout: a background application is
    # still an application. Reap both direct and adopted children until none remain.
    while True:
        try:
            os.waitpid(-1, 0)
        except InterruptedError:
            continue
        except ChildProcessError:
            break
    write_receipt(receipt, "exited")


if __name__ == "__main__":
    desktop, receipt = sys.argv[1:]
    if not Path(desktop).is_absolute():
        raise ValueError("An absolute desktop entry is required.")
    try:
        app = Gio.DesktopAppInfo.new_from_filename(desktop)
    except Exception:
        write_receipt(Path(receipt), "failed")
        raise
    launch(app, receipt)
