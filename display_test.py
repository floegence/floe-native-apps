"""Display density must preserve toolkit logical geometry on a private display."""
import unittest
from types import SimpleNamespace
from contextlib import nullcontext
from unittest.mock import patch
from display import install_display, density, scaled_settings

class DisplayTest(unittest.TestCase):
    def test_remote_resize_invalidates_the_accepted_native_surface(self):
        events = []
        class Window:
            xid = 37
            size = (2880, 1920)
            managed = shown = True
            def get_dimensions(self): return self.size
            def is_managed(self): return self.managed
            def get_property(self, name):
                self.assert_property = name
                return self.shown
        window = Window()
        def configure(win, geometry, resize_counter=0):
            events.append(('configure', geometry, resize_counter))
            # The application minimum, not the requested viewport, determines
            # the accepted drawable dimensions. A rejected counter changes none.
            if resize_counter != 9:
                win.size = (max(1240, geometry[2]), max(960, geometry[3]))
            return 'configured'
        server = install_display(SimpleNamespace(
            set_xsettings=lambda *_: None, parse_hello=lambda *_: None,
            init_packet_handlers=lambda: None, get_server_features=lambda *_: {},
            client_configure_window=configure))
        bindings = SimpleNamespace(send_expose=lambda *args: events.append(('expose', *args)))
        with patch.dict('sys.modules', {
            'xpra.x11.server.seamless': SimpleNamespace(X11WindowBindings=lambda: bindings, xlog=nullcontext()),
        }):
            self.assertEqual(server.client_configure_window(window, (0, 0, 800, 700), 4), 'configured')
            self.assertEqual(events, [('configure', (0, 0, 800, 700), 4),
                                      ('expose', 37, 0, 0, 1240, 960)])
            # Identical requests, movement and rejected old counters do not
            # create a redraw loop or claim a new surface.
            for geometry, counter in [((0, 0, 800, 700), 5), ((20, 30, 800, 700), 6),
                                      ((0, 0, 2000, 1600), 9)]:
                events.clear()
                server.client_configure_window(window, geometry, counter)
                self.assertEqual(events, [('configure', geometry, counter)])
            for state in ('shown', 'managed'):
                setattr(window, state, False)
                window.size = (2880, 1920)
                events.clear()
                server.client_configure_window(window, (0, 0, 1280, 960))
                self.assertEqual(events, [('configure', (0, 0, 1280, 960), 0)])
                setattr(window, state, True)

    def test_display_configuration_updates_workarea_before_native_resize(self):
        """Xpra 6.2 calculates workarea from screen_sizes, not monitors."""
        events = []
        class Source:
            screen_sizes = [('Canvas', 1440, 920, 381, 243, [], 0, 0, 1440, 920)]
            def set_screen_sizes(self, sizes):
                self.screen_sizes = sizes
        source = Source()
        class Server:
            xdpi = ydpi = dpi = 96
            def client_configure_window(self, *args): pass
            def set_xsettings(self, value): pass
            def parse_hello(self, *args): pass
            def get_server_features(self, source=None): return {}
            def get_server_source(self, protocol): return source if protocol == 'owned' else None
            def dpi_changed(self): events.append(('dpi', self.xdpi, self.ydpi))
            def init_packet_handlers(self):
                self._authenticated_ui_packet_handlers = {'configure-display': self.configure}
                self._authenticated_packet_handlers = {}
            def add_packet_handler(self, name, handler, ui):
                self._authenticated_ui_packet_handlers[name] = handler
            def configure(self, protocol, packet):
                events.append(('resize', packet[1]['desktop-size'], self.xdpi, self.ydpi))
                events.append(('workarea', source.screen_sizes[0][6:10]))
        server = install_display(Server())
        server.init_packet_handlers()
        handler = server._authenticated_ui_packet_handlers['configure-display']
        screens = [('Canvas', 2880, 1840, 381, 243, [], 0, 0, 2880, 1840)]
        packet = ['configure-display', {'desktop-size': [2880, 1840],
                  'screen-sizes': screens, 'dpi': {'x': 192, 'y': 192}, 'floe-display-density': 2}]
        handler('owned', packet)
        self.assertEqual(events, [('resize', [2880, 1840], 192, 192),
                                  ('workarea', (0, 0, 2880, 1840))])
        self.assertEqual(server.get_server_features()['floe-display'], 2)

    def test_invalid_complete_configuration_does_not_mutate_session(self):
        import copy
        original = {'desktop-size': [2880, 1840],
                    'screen-sizes': [('Canvas', 2880, 1840, 381, 243, [], 0, 0, 2880, 1840)],
                    'dpi': {'x': 192, 'y': 192}, 'floe-display-density': 2}
        for key, value in [('desktop-size', [0, 1840]), ('desktop-size', [1440, 920]),
                           ('dpi', {'x': 96, 'y': 96}), ('dpi', {'x': True, 'y': 192}),
                           ('screen-sizes', []), ('floe-display-density', 5)]:
            with self.subTest(key=key, value=value):
                server, events = self.fixture()
                server.parse_hello(None, {'floe-display': 1})
                packet = copy.deepcopy(original)
                packet[key] = value
                with self.assertRaises(ValueError):
                    server._authenticated_ui_packet_handlers['configure-display']('owned', ['configure-display', packet])
                self.assertEqual(server.floe_display_density, 1)
                self.assertEqual(len(events), 1)

    def test_toolkit_density_preserves_unrelated_settings(self):
        source=(12,[(1,b'Net/ThemeName','Adwaita',8),(0,b'Xft/DPI',196608,12)])
        serial,items=scaled_settings(source,2)
        values={name:value for _,name,value,_ in items}
        self.assertEqual(serial,12)
        self.assertEqual(values[b'Net/ThemeName'],'Adwaita')
        self.assertEqual(values[b'Gdk/WindowScalingFactor'],2)
        self.assertEqual(values[b'Gdk/UnscaledDPI'],96*1024)
        self.assertEqual(values[b'Xft/DPI'],192*1024)
        self.assertEqual(len(source[1]),2)

    def test_invalid_density_is_rejected_before_any_mutation(self):
        for value in [True,False,0,5,1.5,'2',None,float('nan')]:
            with self.subTest(value=value), self.assertRaises(ValueError):density(value)

    def fixture(self, packet_name='configure-display'):
        events=[]
        class Server:
            change_settings=True
            def client_configure_window(self, *args): pass
            def set_xsettings(self,value):events.append(('settings',value))
            def parse_hello(self,source,caps,*args):
                if self.change_settings:self.set_xsettings((2,[(1,b'Net/ThemeName','Adwaita',0)]))
                return args
            def init_packet_handlers(self):
                self._authenticated_ui_packet_handlers={}
                self._authenticated_packet_handlers={packet_name:lambda p,data:self.set_xsettings((3,[])) if self.change_settings else None}
            def get_server_features(self,source=None):return {'existing':True}
            def get_server_source(self,protocol):return object() if protocol=='owned' else None
            def add_packet_handler(self,name,handler,ui):
                self.assert_ui=ui;self._authenticated_ui_packet_handlers[name]=handler
        server=install_display(Server());server.init_packet_handlers()
        return server,events

    def test_authenticated_display_transition_preserves_existing_dispatch(self):
        server,events=self.fixture()
        self.assertEqual(server.parse_hello(None,{'floe-display':1},True),(True,))
        self.assertEqual(server.get_server_features(),{'existing':True,'floe-display':2})
        self.assertTrue(server.assert_ui)
        self.assertNotIn('configure-display',server._authenticated_packet_handlers)
        configure=server._authenticated_ui_packet_handlers['configure-display']
        configure('owned',['configure-display',{'floe-display-density':2}])
        configure('unowned',['configure-display',{'floe-display-density':4}])
        self.assertEqual(len(events),2);self.assertEqual(server.floe_display_density,2)
        with self.assertRaises(ValueError):configure('owned',['configure-display',{'floe-display-density':999}])
        self.assertEqual(server.floe_display_density,2)
        configure('owned',['configure-display',{'floe-display-density':1}])
        values={n:v for _,n,v,_ in events[-1][1][1]}
        self.assertEqual(values[b'Gdk/WindowScalingFactor'],1)
        self.assertEqual(values[b'Xft/DPI'],96*1024)

    def test_client_without_density_contract_preserves_original_settings(self):
        server,events=self.fixture();server.parse_hello(None,{})
        self.assertEqual(events,[('settings',(2,[(1,b'Net/ThemeName','Adwaita',0)]))])

    def test_reattachment_resets_density_when_xpra_deduplicates_base_settings(self):
        server,events=self.fixture();server.parse_hello(None,{'floe-display':1})
        server.change_settings=False
        configure=server._authenticated_ui_packet_handlers['configure-display']
        configure('owned',['configure-display',{'floe-display-density':2}])
        self.assertEqual(dict((n,v) for _,n,v,_ in events[-1][1][1])[b'Gdk/WindowScalingFactor'],2)
        server.parse_hello(None,{'floe-display':1})
        configure('owned',['configure-display',{'floe-display-density':1}])
        values={n:v for _,n,v,_ in events[-1][1][1]}
        self.assertEqual(values[b'Gdk/WindowScalingFactor'],1)
        self.assertEqual(values[b'Net/ThemeName'],'Adwaita')

    def test_new_xpra_display_packet_name_keeps_one_authenticated_boundary(self):
        server, events = self.fixture('display-configure')
        server.parse_hello(None, {'floe-display': 1})
        configure = server._authenticated_ui_packet_handlers['display-configure']
        configure('owned', ['display-configure', {'floe-display-density': 2}])
        self.assertEqual(server.floe_display_density, 2)
        self.assertNotIn('display-configure', server._authenticated_packet_handlers)
        self.assertEqual(len(events), 2)

if __name__=='__main__':unittest.main()
