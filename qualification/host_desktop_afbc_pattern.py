"""Disposable known-color AFBC capture target; no user input is observed."""
import json
import os
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
os.environ.update(GDK_BACKEND='wayland', XDG_RUNTIME_DIR='/run/user/1000', WAYLAND_DISPLAY='wayland-0')
Gtk.init([])
window=Gtk.Window(title='Redeven AFBC color qualification')
window.fullscreen()
area=Gtk.DrawingArea()
colors=[(255,0,0),(0,255,0),(0,0,255),(255,255,255),(0,0,0),(0,255,255),(255,255,0),(255,0,255)]
reported=False
def draw(widget, context):
    global reported
    w,h=widget.get_allocated_width(),widget.get_allocated_height()
    context.set_antialias(1)
    for i,color in enumerate(colors):
        context.set_source_rgb(*(c/255 for c in color))
        context.rectangle(i*w//8,0,(i+1)*w//8-i*w//8,h)
        context.fill()
    for row in range(12):
        for col in range(64):
            context.set_source_rgb(*((1,1,1) if (row+col)%2 else (0,0,0)))
            context.rectangle(col*16, h-192+row*16,16,16)
            context.fill()
    context.set_source_rgb(1,1,1)
    context.select_font_face('monospace')
    context.set_font_size(32)
    context.move_to(w*4//8+12,h//2)
    context.show_text('AFBC 0123456789')
    if not reported:
        print(json.dumps({'pid':os.getpid(),'width':w,'height':h,'colors':colors,'strip_count':8,'checker_tile':16,'checker_extent':[0,h-192,1024,192]}),flush=True)
        reported=True
    return False
area.connect('draw', draw)
window.add(area)
window.connect('destroy', lambda _:Gtk.main_quit())
window.show_all()
GLib.timeout_add_seconds(420, lambda:(Gtk.main_quit(),False)[1])
Gtk.main()
