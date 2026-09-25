"""Private fixture bus with the approved, limited desktop-portal boundary."""
from xml.sax.saxutils import escape


def configuration(address):
    if not address.startswith('unix:') or ';' in address:
        raise ValueError('A single private local bus address is required')
    # Registry is the official Flatpak identity handshake. Request.Close allows
    # cancelling an open chooser. The other portal implementations, including
    # backend-independent Trash/Realtime/GameMode, are not an admitted service.
    interfaces = ('org.freedesktop.host.portal.Registry',
                  'org.freedesktop.portal.Settings', 'org.freedesktop.portal.FileChooser',
                  'org.freedesktop.portal.Request', 'org.freedesktop.DBus.Properties',
                  'org.freedesktop.DBus.Introspectable', 'org.freedesktop.DBus.Peer')
    rules = ''.join('<allow send_destination="org.freedesktop.portal.Desktop" '
                    'send_type="method_call" send_interface="' + name + '"/>' for name in interfaces)
    return ('<busconfig><type>session</type><auth>EXTERNAL</auth><listen>' + escape(address) +
            '</listen><policy context="default"><allow send_destination="*"/>'
            '<allow receive_sender="*"/><allow own="*"/>'
            '<deny send_destination="org.freedesktop.portal.Desktop" send_type="method_call"/>' +
            rules + '</policy></busconfig>')
