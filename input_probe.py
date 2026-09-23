"""Read-only preflight for the shared managed/system input entrypoint."""
import ctypes
import json
import xpra
from xpra.scripts import server
from xpra.os_util import gi_import

if not xpra.__version__.startswith('6.'):
    raise RuntimeError('Client input requires the qualified Xpra 6 API')
if not callable(getattr(server, 'make_seamless_server', None)):
    raise RuntimeError('Installed Xpra input API is unsupported')
gi_import('Gio')
for name, symbols in (
        ('libxcb.so.1', ('xcb_connect', 'xcb_send_event', 'xcb_get_keyboard_mapping',
                       'xcb_poll_for_queued_event')),
        ('libxcb-util.so.1', ('xcb_aux_get_screen',)),
        ('libxcb-imdkit.so.1', ('xcb_im_create', 'xcb_im_commit_string', 'xcb_im_sync_xlib',
                              'xcb_im_input_context_get_focus_window', 'xcb_utf8_to_compound_text',
                              'xcb_im_set_use_sync_mode'))):
    library = ctypes.CDLL(name)
    if not all(getattr(library, symbol, None) for symbol in symbols):
        raise RuntimeError('Installed XIM protocol library is unsupported')
print(json.dumps({'version': 1, 'xpra_version': xpra.__version__}))
