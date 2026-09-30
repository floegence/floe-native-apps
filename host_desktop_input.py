"""Physical keyboard identities shared by public portal and X11 adapters."""
from host_desktop_contract import DesktopError


KEY_CODES = {
    'Escape': 1, 'Minus': 12, 'Equal': 13, 'Backspace': 14, 'Tab': 15,
    'BracketLeft': 26, 'BracketRight': 27, 'Enter': 28, 'ControlLeft': 29,
    'Semicolon': 39, 'Quote': 40, 'Backquote': 41, 'ShiftLeft': 42, 'Backslash': 43,
    'Comma': 51, 'Period': 52, 'Slash': 53, 'ShiftRight': 54, 'NumpadMultiply': 55,
    'AltLeft': 56, 'Space': 57, 'CapsLock': 58, 'NumLock': 69, 'ScrollLock': 70,
    'Numpad7': 71, 'Numpad8': 72, 'Numpad9': 73, 'NumpadSubtract': 74,
    'Numpad4': 75, 'Numpad5': 76, 'Numpad6': 77, 'NumpadAdd': 78,
    'Numpad1': 79, 'Numpad2': 80, 'Numpad3': 81, 'Numpad0': 82, 'NumpadDecimal': 83,
    'IntlBackslash': 86, 'F11': 87, 'F12': 88, 'NumpadEnter': 96, 'ControlRight': 97,
    'NumpadDivide': 98, 'PrintScreen': 99, 'AltRight': 100, 'Home': 102,
    'ArrowUp': 103, 'PageUp': 104, 'ArrowLeft': 105, 'ArrowRight': 106,
    'End': 107, 'ArrowDown': 108, 'PageDown': 109, 'Insert': 110, 'Delete': 111,
    'Pause': 119, 'MetaLeft': 125, 'MetaRight': 126, 'ContextMenu': 127,
    'IntlRo': 89, 'IntlYen': 124, 'NumpadEqual': 117,
}
KEY_CODES.update(('Digit' + digit, code) for code, digit in enumerate('1234567890', 2))
KEY_CODES.update(('Key' + key, code) for start, row in ((16, 'QWERTYUIOP'), (30, 'ASDFGHJKL'), (44, 'ZXCVBNM'))
                 for code, key in enumerate(row, start))
KEY_CODES.update(('F' + str(index + 1), 59 + index) for index in range(10))


def physical_key(code):
    if not isinstance(code, str) or code not in KEY_CODES:
        raise DesktopError('KEY_UNSUPPORTED')
    return KEY_CODES[code]


class HeldInput:
    def __init__(self, backend):
        self.backend = backend
        self.keys, self.buttons = set(), set()

    def key(self, code, down):
        if down:
            self.keys.add(code)
        else:
            if code not in self.keys:
                return
        self.backend.key(code, down)
        if not down:
            self.keys.discard(code)

    def button(self, code, down):
        if down:
            self.buttons.add(code)
        else:
            if code not in self.buttons:
                return
        self.backend.button(code, down)
        if not down:
            self.buttons.discard(code)

    def release(self):
        # Retire ownership even if the OS has already closed its input channel.
        keys, buttons = self.keys, self.buttons
        self.keys, self.buttons = set(), set()
        for code in keys:
            try:
                self.backend.key(code, False)
            except Exception:
                pass
        for code in buttons:
            try:
                self.backend.button(code, False)
            except Exception:
                pass

    def paste(self):
        if self.keys or self.buttons:
            raise DesktopError('INPUT_KEYS_HELD')
        try:
            self.key(29, True)
            self.key(47, True)
            self.key(47, False)
            self.key(29, False)
        finally:
            self.release()
