"""Scoped package launch arguments, applied after immutable plan revalidation."""
from pathlib import Path

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
