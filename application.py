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
import threading

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402
from launch_plan import Unavailable, revalidate, restored_environment  # noqa: E402
from application_processes import LaunchChildren  # noqa: E402


def write_receipt(path, state, **details):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"state": state, **details}), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def terminate_children():
    """Explicit supervisor termination affects only this launcher's child tree.

    Kernel handles and a checked parent relationship prevent PID reuse from
    targeting another process. Newly adopted descendants are reaped in the next
    pass by the sole wait owner. Normal viewer, window and consumer lifetimes
    never request this operation.
    """
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
    terminate(os.getpid())


def launch(app, receipt, plan=None):
    receipt = Path(receipt)
    if not receipt.is_absolute():
        raise ValueError("An absolute private receipt path is required.")
    pids = []
    root_statuses = {}
    stopped = False
    scope = None

    def observed(pid, status):
        if pid in pids and pid not in root_statuses:
            root_statuses[pid] = os.waitstatus_to_exitcode(status)

    def terminate(number, frame):
        del number, frame
        nonlocal stopped
        stopped = True
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        terminate_children()

    children = LaunchChildren(observed)

    try:
        if plan is not None:
            revalidate(plan, restored_environment(os.environ), [plan['backend']])
            if app.get_filename() != plan['observation']['desktop']['path']:
                raise Unavailable('APPLICATION_PLAN_STALE', 'revalidation')
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
        signal.signal(signal.SIGTERM, terminate)
        if plan is not None and 'user-systemd-scope' in plan['observation']['services']:
            from application_scope import ScopeService
            host_bus = os.environ.get('FLOE_NATIVE_HOST_BUS')
            private_bus = os.environ.get('DBUS_SESSION_BUS_ADDRESS')
            if not host_bus or not private_bus or host_bus == private_bus:
                raise Unavailable('APPLICATION_HOST_SERVICE_UNAVAILABLE', 'host_services')
            try:
                scope = ScopeService(private_bus, host_bus, plan['observation']['package']['security_tag'],
                    children, lambda event: print(json.dumps(event), flush=True))
            except Exception:
                raise Unavailable('APPLICATION_HOST_SERVICE_UNAVAILABLE', 'host_services') from None
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
        context.unsetenv('FLOE_NATIVE_HOST_BUS')
        # Support-tool Python clears GTK_PATH and the saved host map restores it.
        # Apply the input launcher's explicit private path only to the final app.
        gtk_path = os.environ.get("FLOE_NATIVE_INPUT_GTK_PATH")
        if gtk_path:
            if not Path(gtk_path).is_absolute():
                raise ValueError("The private GTK input path must be absolute.")
            context.setenv("GTK_PATH", gtk_path)
        context.unsetenv("FLOE_NATIVE_INPUT_GTK_PATH")
        def started(_app, pid, _data):
            children.register(pid)
            pids.append(pid)
        if not app.launch_uris_as_manager([], context, GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD,
                                          None, None, started, None) or not pids:
            raise ValueError("The application did not start an owned process.")
        write_receipt(receipt, "running", phase="spawn", launcher_pids=pids)
    except Exception as error:
        if scope is not None:
            scope.close()
        write_receipt(receipt, "failed", phase=error.stage if isinstance(error, Unavailable) else "spawn",
                      error_code=error.code if isinstance(error, Unavailable) else "APPLICATION_LAUNCH_FAILED")
        raise
    # No display polling or first-window timeout: a background application is
    # still an application. Reap both direct and adopted children until none remain.
    def before_wait():
        if stopped:
            terminate_children()
    if scope is None:
        children.wait(before_wait)
    else:
        # GIO services run on the main context while exactly one worker waits
        # for children. Its protected wait boundary preserves in-flight scope
        # identities; neither the D-Bus callbacks nor SIGTERM reap a child.
        loop, failures = GLib.MainLoop(), []
        def wait():
            try:
                children.wait(before_wait)
            except Exception as error:
                failures.append(error)
            finally:
                GLib.idle_add(loop.quit)
        waiter = threading.Thread(target=wait, name='application-wait')
        waiter.start()
        loop.run()
        waiter.join()
        scope.close()
        if failures:
            raise failures[0]
    # Preserve actual direct-launcher status. Window readiness is owned by the
    # graphical backend; a process result must not guess whether a window ever
    # existed. In particular, Snap's exit 46 must survive this supervisor.
    outcomes = [{"pid": pid, "exit_code": root_statuses.get(pid)} for pid in pids]
    code = next((root_statuses[pid] for pid in pids if root_statuses.get(pid)), 0)
    write_receipt(receipt, "exited", phase="process_exit", exit_code=code,
                  launchers=outcomes, termination_requested=stopped)
    return 128 + -code if code < 0 else code


if __name__ == "__main__":
    plan = None
    receipt = sys.argv[-1]
    try:
        if len(sys.argv) == 4 and sys.argv[1] == '--plan':
            path = Path(sys.argv[2])
            if not path.is_absolute() or path.stat().st_size > 2 << 20:
                raise Unavailable('APPLICATION_PLAN_INVALID')
            plan = json.loads(path.read_bytes())
            desktop = plan['observation']['desktop']['path']
        else:
            desktop, receipt = sys.argv[1:]
        if not Path(desktop).is_absolute():
            raise ValueError("An absolute desktop entry is required.")
        app = Gio.DesktopAppInfo.new_from_filename(desktop)
        code = launch(app, receipt, plan)
    except Unavailable as error:
        write_receipt(Path(receipt), 'failed', phase=error.stage, error_code=error.code)
        sys.exit(1)
    except Exception:
        write_receipt(Path(receipt), 'failed', phase='spawn', error_code='APPLICATION_LAUNCH_FAILED')
        sys.exit(1)
    sys.exit(code)
