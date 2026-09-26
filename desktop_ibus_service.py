"""Private official IBus service adapter; no dictionaries or composition engine."""
import gi
gi.require_version('IBus', '1.0')
from gi.repository import Gio, GLib, IBus

from desktop_ibus import IBusContexts, IBusSources


class CommitEngine(IBus.Engine):
    __gtype_name__ = 'FloeNativeCommitEngine'

    def __init__(self, owner, path):
        super().__init__(connection=owner.bus.get_connection(), object_path=path, has_focus_id=True)
        self.owner, self.context_path = owner, None

    def do_focus_in_id(self, path, _description):
        self.context_path = path
        self.owner.adapter.focus(self, path)

    def do_focus_out_id(self, path):
        if self.context_path == path:
            self.context_path = None
            self.owner.adapter.focus(self, None)

    def do_process_key_event(self, keyval, code, state):
        if keyval != 0xffd5:
            return False
        def commit(text):
            try:
                self.commit_text(IBus.Text.new_from_string(text))
            except GLib.Error:
                raise ValueError('Native IBus commit is unavailable') from None
        return self.owner.adapter.key(self, code, bool(state & IBus.ModifierType.RELEASE_MASK), commit)

    def do_destroy(self):
        self.owner.adapter.focus(self, None)
        self.owner.engines.discard(self)
        IBus.Engine.do_destroy(self)


class CommitFactory(IBus.Factory):
    __gtype_name__ = 'FloeNativeCommitFactory'

    def __init__(self, owner):
        super().__init__(connection=owner.bus.get_connection(), object_path=IBus.PATH_FACTORY)
        self.owner = owner

    def do_create_engine(self, name):
        if (name != self.owner.NAME or self.owner.closed or len(self.owner.engines) >= 64 or
                self.owner.sequence >= 0xffffffff):
            return None
        self.owner.sequence += 1
        engine = CommitEngine(self.owner, '/org/freedesktop/IBus/FloeNative/' + str(self.owner.sequence))
        self.owner.engines.add(engine)
        return engine


class NativeIBusService:
    NAME = 'floe-client-commit'

    def __init__(self, contexts, session, daemon, portal=None):
        """daemon/portal are live process references from trusted preparation.

        Preparation starts the dedicated bus and enforces GTK synchronous mode
        1. References remain owned by preparation, which stops these services
        only on application-instance disposal, never on viewer disconnection.
        """
        IBus.init()
        self.bus, self.session = IBus.Bus.new(), session
        self.engines, self.sequence, self.closed = set(), 0, False
        self.activation = None
        if not self.bus.is_connected():
            raise ValueError('Private IBus connection is unavailable')
        sources = IBusSources(contexts.tree, contexts.runtime, daemon, portal,
            lambda path: self.describe(self.bus.get_connection(), 'org.freedesktop.IBus', path, '(usuubb)'),
            lambda path: self.describe(session, 'org.freedesktop.portal.IBus', path, '(uso)'), self.credentials)
        self.adapter = IBusContexts(contexts, sources)
        self.factory = CommitFactory(self)
        self.disconnected = self.bus.connect('disconnected', lambda _bus: self.adapter.close())
        component = IBus.Component.new('org.freedesktop.IBus.FloeNative', 'Client confirmed text', '1',
            'MIT', 'Floe', 'https://github.com/floegence/floe-native-apps', '', '')
        component.add_engine(IBus.EngineDesc.new(self.NAME, 'Client confirmed text',
            'Confirmed text only', 'en', 'MIT', 'Floe', '', 'us'))
        if not self.bus.register_component(component):
            self.close()
            raise ValueError('Private IBus registration failed')

    @staticmethod
    def describe(connection, destination, path, result_type):
        try:
            return connection.call_sync(destination, '/org/freedesktop/IBus',
                'org.floegence.IBus.ContextSource', 'Describe', GLib.Variant('(o)', (path,)),
                GLib.VariantType.new(result_type), Gio.DBusCallFlags.NONE, 1000, None).unpack()
        except GLib.Error:
            raise ValueError('Private IBus context source is unavailable') from None

    def credentials(self, sender):
        try:
            return self.session.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'GetConnectionCredentials', GLib.Variant('(s)', (sender,)),
                GLib.VariantType.new('(a{sv})'), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
        except GLib.Error:
            raise ValueError('Private IBus context peer is unavailable') from None

    def activate(self, completed):
        if self.closed or self.activation is not None:
            raise ValueError('Private IBus context selection is unavailable')
        operation = (Gio.Cancellable(), completed)
        self.activation = operation
        def selected(bus, result, _data):
            try:
                ready = bus.set_global_engine_async_finish(result)
            except GLib.Error:
                ready = False
            if self.activation is operation:
                self.activation = None
                completed(None if ready else 'INPUT_CONTEXT_UNAVAILABLE')
        # CreateEngine is dispatched on this same event loop. A synchronous
        # selection here blocks its own factory callback.
        self.bus.set_global_engine_async(self.NAME, 3000, operation[0], selected, None)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.activation:
            operation, self.activation = self.activation, None
            operation[0].cancel()
            operation[1]('INPUT_CONTEXT_UNAVAILABLE')
        self.adapter.close()
        self.bus.disconnect(self.disconnected)
        for engine in tuple(self.engines):
            engine.destroy()
        self.factory.destroy()
