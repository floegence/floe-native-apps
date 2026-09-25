"""Fixture-only commit engine using IBus's shipped daemon and toolkit modules.

This experiment does not implement dictionaries, candidates or composition.
The private keymap marker must be received by the actual focused toolkit context;
protocol dispatch alone does not count as a successful application receipt.
"""
import gi
gi.require_version("IBus", "1.0")
from gi.repository import IBus
from input_marker import FIRST_CODE, SLOT_COUNT, MarkerTransactions, marker_command


class CommitEngine(IBus.Engine):
    __gtype_name__ = "FloePrototypeCommitEngine"

    def __init__(self, owner, path):
        super().__init__(connection=owner.bus.get_connection(), object_path=path, has_focus_id=True)
        self.owner = owner
        self.focused = False
        self.client = None
        self.input_path = None

    def do_focus_in(self):
        if not self.client or self.client == "fake":
            return
        self.focused = True
        self.owner.active = self
        self.owner.record({"ibus": "focus-in"})

    def do_focus_out(self):
        self.focused = False
        self.client = None
        if self.owner.active is self:
            self.owner.active = None
        # Fixture admission binds the native keyboard stream to one test window.
        # The widget context is selected when its native marker is processed,
        # after preceding pointer events. Production window revocation remains
        # unimplemented and must explicitly cancel the owning transaction.
        self.owner.record({"ibus": "focus-out"})

    def do_focus_in_id(self, object_path, client):
        self.owner.record({"ibus": "context", "path": object_path, "client": client})
        self.client = client
        self.input_path = object_path
        if client != "fake":
            self.do_focus_in()

    def do_focus_out_id(self, object_path):
        self.do_focus_out()

    def do_process_key_event(self, keyval, keycode, state):
        self.owner.record({"ibus": "key", "keyval": keyval, "keycode": keycode, "state": state})
        if keyval != 0xffd5 or not FIRST_CODE <= keycode < FIRST_CODE + SLOT_COUNT:
            return False
        released = bool(state & IBus.ModifierType.RELEASE_MASK)
        focused = self.owner.active is self and self.focused
        text = self.owner.transactions.key(keycode, released, focused)
        self.owner.record({"ibus": "native-marker", "focused": focused, "code": keycode,
                           "released": released, "submitted": text is not None})
        if text is not None:
            self.commit_text(IBus.Text.new_from_string(text))
        return True


class CommitFactory(IBus.Factory):
    __gtype_name__ = "FloePrototypeCommitFactory"

    def __init__(self, owner):
        super().__init__(connection=owner.bus.get_connection(), object_path=IBus.PATH_FACTORY)
        self.owner = owner
        self.engines = []

    def do_create_engine(self, name):
        if name != "floe-prototype-commit":
            return None
        engine = CommitEngine(self.owner, "/org/freedesktop/IBus/FloePrototype/" + str(len(self.engines)))
        self.engines.append(engine)
        return engine


class IBusProbe:
    def __init__(self, record):
        IBus.init()
        self.record = record
        self.transactions = MarkerTransactions()
        self.active = None
        self.bus = IBus.Bus.new()
        if not self.bus.is_connected():
            raise RuntimeError("Private IBus connection is unavailable")
        self.factory = CommitFactory(self)
        component = IBus.Component.new("org.freedesktop.IBus.FloePrototype", "Confirmed text fixture", "1",
            "MIT", "Floe", "https://github.com/floegence/floe-native-apps", "", "")
        component.add_engine(IBus.EngineDesc.new("floe-prototype-commit", "Confirmed text fixture",
            "Confirmed text only", "en", "MIT", "Floe", "", "us"))
        if not self.bus.register_component(component):
            raise RuntimeError("Private IBus component registration failed")

    def activate(self):
        if not self.bus.set_global_engine("floe-prototype-commit"):
            raise RuntimeError("Private IBus commit context selection failed")
        self.record({"ibus": "engine-selected"})

    def enqueue(self, text):
        if not self.active or not self.active.focused:
            raise RuntimeError("Private IBus context is unavailable")
        return marker_command(self.transactions.enqueue(text))

    @property
    def pending(self):
        return True if self.transactions.pending else None

    def revoke(self):
        self.transactions.revoke()
        self.record({"ibus": "revoked", "generation": self.transactions.generation})
