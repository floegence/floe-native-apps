"""Disposable native helper wire fixture; no graphical support claim.

The real control, attachment, target validation and ordered input modules run
unchanged. Only the compositor/capture callbacks are synthetic. Go qualification
asserts request correlation, authentication, first-frame admission and reattachment.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desktop_attachment import DesktopAttachment
from desktop_control import DesktopControl
from desktop_control_test import Loop
from desktop_native import NativeDesktop


class Frames:
    def configure(self, mode):
        pass

    def refine(self):
        pass

    def cancel(self):
        pass

    def capture(self, target, completed):
        # Keep this test in the waiting state. Real PNG parsing is exercised by
        # the client tests; native captures have their own application receipts.
        completed(None, None, 'CAPTURE_UNAVAILABLE')


loop = Loop()
native = NativeDesktop(lambda _: None, Frames())
native.observe('native-version 1')
attachment = DesktopAttachment(native, loop.later, loop.cancel)
native.attachment = attachment
server = DesktopControl(sys.argv[1], 'wire-fixture', 'a' * 64, attachment, loop)
print('ready', flush=True)
try:
    while True:
        loop.step()
finally:
    server.close()
    attachment.close()
    loop.selector.close()
