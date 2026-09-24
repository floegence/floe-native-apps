/* Commit-only GTK 4 GIO module. Composition belongs to the remote client. */
#include <gtk/gtk.h>
#include <gdk/x11/gdkx.h>
#include "gtk_commit.h"

static GType floe_type;
static gpointer parent_class;
static guint parent_instance_size;
typedef struct {
    GtkIMContext *simple;
    GtkWidget *widget;
    GdkDisplay *display;
    gulong handler;
    Window xid;
} FloeState;
static FloeState *state(GtkIMContext *context) {
    return (FloeState *)((char *)context + parent_instance_size);
}
static gboolean marker_surface(FloeState *self, Window window) {
    /* Older GDK versions focus a private child of the native surface. Check
       ancestry rather than assuming that X input focus equals the surface ID. */
    Display *display = gdk_x11_display_get_xdisplay(self->display);
    gboolean matches = FALSE;
    gdk_x11_display_error_trap_push(self->display);
    for (unsigned depth = 0; window && depth < 64; depth++) {
        if (window == self->xid) { matches = TRUE; break; }
        Window root, parent, *children = NULL;
        unsigned count;
        Status found = XQueryTree(display, window, &root, &parent, &children, &count);
        if (children) XFree(children);
        if (!found || parent == window) break;
        window = parent;
    }
    if (gdk_x11_display_error_trap_pop(self->display)) matches = FALSE;
    return matches;
}
static gboolean marker(GdkDisplay *display, XEvent *event, gpointer data) {
    (void)display;
    GtkIMContext *context = data;
    FloeState *self = state(context);
    if (event->type != KeyPress || !event->xkey.send_event || event->xkey.keycode != 8 ||
        !self->widget || !marker_surface(self, event->xkey.window))
        return FALSE;
    floe_commit(context, event->xkey.time, (guint32)self->xid);
    return TRUE;
}
static void disconnect_marker(GtkIMContext *context) {
    FloeState *self = state(context);
    if (self->handler) g_signal_handler_disconnect(self->display, self->handler);
    self->handler = 0;
    self->xid = 0;
    g_clear_object(&self->display);
}

static gboolean key(GtkIMContext *context, GdkEvent *event) {
    return gtk_im_context_filter_keypress(state(context)->simple, event);
}
static void client_widget(GtkIMContext *context, GtkWidget *widget) {
    FloeState *self = state(context);
    /* GtkTextView may reassert the same widget without a focus transition.
       Preserve its current native subscription; a new widget still revokes it. */
    if (widget && self->widget == widget) return;
    disconnect_marker(context);
    if (self->widget) g_object_remove_weak_pointer(G_OBJECT(self->widget), (gpointer *)&self->widget);
    self->widget = widget;
    if (widget) g_object_add_weak_pointer(G_OBJECT(widget), (gpointer *)&self->widget);
    if (self->simple) gtk_im_context_set_client_widget(self->simple, widget);
}
static void focus_in(GtkIMContext *context) {
    gtk_im_context_focus_in(state(context)->simple);
    disconnect_marker(context);
    FloeState *self = state(context);
    GtkNative *native = self->widget ? gtk_widget_get_native(self->widget) : NULL;
    GdkSurface *surface = native ? gtk_native_get_surface(native) : NULL;
    if (surface && GDK_IS_X11_SURFACE(surface)) {
        self->display = g_object_ref(gdk_surface_get_display(surface));
        self->xid = gdk_x11_surface_get_xid(surface);
        /* GTK4 uses XI2 exclusively. Receive the ordered core marker through
           GDK's native queue without synthesizing a second keyboard event. */
        self->handler = g_signal_connect(self->display, "xevent", G_CALLBACK(marker), context);
    }
}
static void focus_out(GtkIMContext *context) {
    disconnect_marker(context);
    gtk_im_context_focus_out(state(context)->simple);
}
static void reset(GtkIMContext *context) {
    gtk_im_context_reset(state(context)->simple);
}
static void preedit(GtkIMContext *context, gchar **text, PangoAttrList **attrs, gint *position) {
    (void)context;
    if (text) *text = g_strdup("");
    if (attrs) *attrs = pango_attr_list_new();
    if (position) *position = 0;
}
static void dispose(GObject *object) {
    client_widget(GTK_IM_CONTEXT(object), NULL);
    g_clear_object(&state(GTK_IM_CONTEXT(object))->simple);
    G_OBJECT_CLASS(parent_class)->dispose(object);
}
static void ordinary_text(GtkIMContext *simple, const gchar *text, gpointer context) {
    (void)simple;
    g_signal_emit_by_name(context, "commit", text);
}
static void instance_init(GTypeInstance *instance, gpointer klass) {
    (void)klass;
    GtkIMContext *context = GTK_IM_CONTEXT(instance);
    state(context)->simple = gtk_im_context_simple_new();
    g_signal_connect(state(context)->simple, "commit", G_CALLBACK(ordinary_text), context);
}
static void class_init(gpointer klass, gpointer data) {
    (void)data;
    parent_class = g_type_class_peek_parent(klass);
    GtkIMContextClass *context = GTK_IM_CONTEXT_CLASS(klass);
    context->set_client_widget = client_widget;
    context->filter_keypress = key;
    context->focus_in = focus_in;
    context->focus_out = focus_out;
    context->get_preedit_string = preedit;
    context->reset = reset;
    G_OBJECT_CLASS(klass)->dispose = dispose;
}

G_MODULE_EXPORT void g_io_module_load(GIOModule *module) {
    /* GTK 4.14 grew GtkIMContextClass beyond its 4.0 reserved slots. Register
       against the actual parent sizes; only the stable 4.0 vfunc prefix is used. */
    GTypeQuery query;
    g_type_query(GTK_TYPE_IM_CONTEXT, &query);
    parent_instance_size = query.instance_size;
    const GTypeInfo info = {
        .class_size = query.class_size,
        .class_init = class_init,
        .instance_size = query.instance_size + sizeof(FloeState),
        .instance_init = instance_init,
    };
    floe_type = g_type_module_register_type(G_TYPE_MODULE(module), GTK_TYPE_IM_CONTEXT,
                                           "FloeGTK4Context", &info, 0);
    g_io_extension_point_implement("gtk-im-module", floe_type, "floe-client", 0);
    floe_bus_open("gtk4");
}
G_MODULE_EXPORT void g_io_module_unload(GIOModule *module) {
    (void)module;
    floe_bus_close();
}
G_MODULE_EXPORT char **g_io_module_query(void) {
    return g_strsplit("gtk-im-module", ":", -1);
}
