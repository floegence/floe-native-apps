"""Scoped package launch arguments, applied after immutable plan revalidation."""
from pathlib import Path
import os
import stat

from launch_plan import Unavailable, launch_tokens


def token_ends(command):
    """Retain raw Exec bytes; GIO remains responsible for field-code expansion."""
    quote, escaped, active = None, False, False
    ends = []
    for index, char in enumerate(command):
        if escaped:
            escaped = False
        elif char == '\\' and quote != "'":
            escaped = True
            active = True
        elif quote:
            if char == quote:
                quote = None
        elif char in ('"', "'"):
            quote, active = char, True
        elif char.isspace():
            if active:
                ends.append(index)
                active = False
        else:
            active = True
    if quote or escaped:
        raise Unavailable('PACKAGE_LAUNCHER_UNSUPPORTED', 'package_resources')
    if active:
        ends.append(len(command))
    return ends


def private_flatpak_command(app, plan, environment, glib):
    """Return a scoped Exec value, without rewriting any original arguments."""
    plugins = environment.get('FLOE_NATIVE_FLATPAK_QT')
    if plugins is None:
        return None
    if (plan is None or plan['backend']['id'] != 'wayland' or
            plan['observation']['package']['kind'] != 'flatpak' or
            not Path(plugins).is_absolute() or '\x00' in plugins):
        raise Unavailable('PACKAGE_INPUT_UNAVAILABLE', 'package_resources')
    command = app.get_string('Exec')
    valid, original = glib.shell_parse_argv(command)
    executable, tokens, _ = launch_tokens(app, environment, glib)
    if (not valid or len(tokens) < 3 or tokens[1] != 'run' or
            executable != plan['observation']['executable']['path']):
        raise Unavailable('APPLICATION_PLAN_STALE', 'revalidation')
    ends = token_ends(command)
    if len(ends) != len(original) or original[-len(tokens):] != tokens:
        raise Unavailable('PACKAGE_LAUNCHER_UNSUPPORTED', 'package_resources')
    offset = ends[len(original) - len(tokens) + 1]
    # These are literal Desktop Entry arguments, not shell expressions. Escape
    # percent for GIO field codes and reserved characters within double quotes.
    argument = '--env=QT_PLUGIN_PATH=' + plugins
    argument = argument.replace('%', '%%')
    for char in ('\\', '"', '`', '$'):
        argument = argument.replace(char, '\\' + char)
    return command[:offset] + ' "' + argument + '"' + command[offset:]


def private_browser_command(app, plan, environment, glib):
    """Use one persistent private browser profile instead of an external singleton.

    The trusted host chooses placement. Revalidation binds the supported browser
    family and explicit profile flags remain authoritative. User profile bytes
    and locks are never inspected, copied, removed, or rewritten.
    """
    if not plan or not plan.get('browser_profile'):
        return None
    profile = Path(plan['browser_profile'])
    family = plan['observation'].get('browser_family')
    if family not in ('chromium', 'firefox') or not profile.is_absolute() or profile.resolve() != profile:
        raise Unavailable('APPLICATION_PROFILE_UNAVAILABLE', 'package_resources')
    try:
        profile.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = profile.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o700 or profile.resolve() != profile):
            raise ValueError('Browser profile is not private')
    except (OSError, ValueError):
        raise Unavailable('APPLICATION_PROFILE_UNAVAILABLE', 'package_resources') from None
    arguments = (['--user-data-dir=' + str(profile), '--no-first-run', '--no-default-browser-check']
                 if family == 'chromium' else ['--no-remote', '--profile', str(profile)])
    def quote(value):
        value = value.replace('%', '%%')
        for char in ('\\', '"', '`', '$'):
            value = value.replace(char, '\\' + char)
        return '"' + value + '"'
    command = app.get_string('Exec')
    valid, original = glib.shell_parse_argv(command)
    executable, tokens, _ = launch_tokens(app, environment, glib)
    ends = token_ends(command)
    if (not valid or not tokens or len(ends) != len(original) or original[-len(tokens):] != tokens or
            executable != plan['observation']['executable']['path']):
        raise Unavailable('APPLICATION_PLAN_STALE', 'revalidation')
    offset = ends[len(original) - len(tokens)]
    return command[:offset] + ' ' + ' '.join(quote(value) for value in arguments) + command[offset:]
