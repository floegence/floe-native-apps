"""Negotiated density for the session's private X11 display, never host settings."""


def density(value):
    if type(value) is not int or not 1 <= value <= 4:
        raise ValueError('Invalid client display density')
    return value


def scaled_settings(settings, scale):
    scale = density(scale)
    serial, items = settings
    owned = {b'Gdk/WindowScalingFactor': scale,
             b'Gdk/UnscaledDPI': 96 * 1024,
             b'Xft/DPI': 96 * scale * 1024}
    result = [item for item in items
              if (item[1].encode() if isinstance(item[1], str) else item[1]) not in owned]
    result.extend((0, key, value, serial) for key, value in owned.items())
    return serial, result


def install_display(server):
    # Bind at the concrete instance boundary shared by managed and system Xpra.
    original_settings = server.set_xsettings
    original_hello = server.parse_hello
    original_handlers = server.init_packet_handlers
    original_features = server.get_server_features
    server.floe_display_density = None
    serial = 0

    def settings(value):
        nonlocal serial
        if server.floe_display_density is None:
            original_settings(value)
            return
        # Modern GTK ignores an update whose XSETTINGS serial did not advance.
        serial = (max(serial, value[0]) + 1) & 0xffffffff
        original_settings(scaled_settings((serial, value[1]), server.floe_display_density))

    def hello(source, caps, *args):
        # Every connection starts at logical density. The prepared client changes
        # density only after startup, through authenticated configure-display.
        server.floe_display_density = 1 if caps.get('floe-display') == 1 else None
        return original_hello(source, caps, *args)

    def handlers():
        original_handlers()
        original = (server._authenticated_ui_packet_handlers.get('configure-display') or
                    server._authenticated_packet_handlers.get('configure-display'))
        if original is None:
            raise RuntimeError('Authenticated display configuration is unavailable')

        def configure(protocol, packet):
            if server.get_server_source(protocol) is None:
                return
            value = packet[1].get('floe-display-density')
            if value is not None:
                server.floe_display_density = density(value)
            original(protocol, packet)

        server._authenticated_packet_handlers.pop('configure-display', None)
        server.add_packet_handler('configure-display', configure, True)

    def features(source=None):
        return {**original_features(source), 'floe-display': 1}

    server.set_xsettings = settings
    server.parse_hello = hello
    server.init_packet_handlers = handlers
    server.get_server_features = features
    return server
