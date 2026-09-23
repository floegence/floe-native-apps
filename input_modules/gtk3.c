/* Commit-only GTK 3 input module. Composition belongs to the remote client. */
#include <gtk/gtk.h>
#include <gtk/gtkimmodule.h>
#include <gdk/gdkx.h>

#define BUS_NAME "org.floegence.ClientInput"
#define BUS_PATH "/org/floegence/ClientInput"

typedef struct { GtkIMContext parent; GtkIMContext *simple; } FloeContext;
typedef struct { GtkIMContextClass parent; } FloeContextClass;
G_DEFINE_DYNAMIC_TYPE(FloeContext, floe_context, GTK_TYPE_IM_CONTEXT)
static GDBusConnection *bus;

static gboolean acknowledged(gpointer data) {
    if (bus && !g_dbus_connection_is_closed(bus)) {
        g_dbus_connection_call(bus, BUS_NAME, BUS_PATH, BUS_NAME, "Done",
            g_variant_new("(u)", GPOINTER_TO_UINT(data)), NULL,
            G_DBUS_CALL_FLAGS_NONE, 3000, NULL, NULL, NULL);
    }
    return G_SOURCE_REMOVE;
}

static gboolean key(GtkIMContext *context, GdkEventKey *event) {
    /* Chromium reconstructs the GDK event without its X11 send_event flag. */
    if (event->hardware_keycode != 8)
        return gtk_im_context_filter_keypress(((FloeContext *)context)->simple, event);
    if (event->type != GDK_KEY_PRESS || !bus || !event->window ||
        !GDK_IS_X11_WINDOW(event->window))
        return TRUE;
    GVariant *reply = g_dbus_connection_call_sync(bus, BUS_NAME, BUS_PATH, BUS_NAME,
        "Take", g_variant_new("(uu)", event->time,
            (guint32)gdk_x11_window_get_xid(event->window)),
        G_VARIANT_TYPE("(us)"), G_DBUS_CALL_FLAGS_NONE, 3000, NULL, NULL);
    if (!reply)
        return TRUE;
    const char *text;
    guint32 sequence;
    g_variant_get(reply, "(u&s)", &sequence, &text);
    g_signal_emit_by_name(context, "commit", text);
    g_variant_unref(reply);
    /* Return through the toolkit's key filter before releasing later input. */
    g_idle_add(acknowledged, GUINT_TO_POINTER(sequence));
    return TRUE;
}

static void preedit(GtkIMContext *context, gchar **text, PangoAttrList **attrs, gint *position) {
    (void)context;
    if (text) *text = g_strdup("");
    if (attrs) *attrs = pango_attr_list_new();
    if (position) *position = 0;
}

static void reset(GtkIMContext *context) {
    gtk_im_context_reset(((FloeContext *)context)->simple);
}

static void dispose(GObject *object) {
    g_clear_object(&((FloeContext *)object)->simple);
    G_OBJECT_CLASS(floe_context_parent_class)->dispose(object);
}

static void ordinary_text(GtkIMContext *simple, const gchar *text, gpointer context) {
    (void)simple;
    g_signal_emit_by_name(context, "commit", text);
}

static void floe_context_init(FloeContext *self) {
    self->simple = gtk_im_context_simple_new();
    g_signal_connect(self->simple, "commit", G_CALLBACK(ordinary_text), self);
}

static void floe_context_class_init(FloeContextClass *klass) {
    GtkIMContextClass *context = GTK_IM_CONTEXT_CLASS(klass);
    context->filter_keypress = key;
    context->get_preedit_string = preedit;
    context->reset = reset;
    G_OBJECT_CLASS(klass)->dispose = dispose;
}

static void floe_context_class_finalize(FloeContextClass *klass) { (void)klass; }
static const GtkIMContextInfo info = {"floe-client", "Client confirmed text", "", "", ""};
static const GtkIMContextInfo *contexts[] = {&info};

G_MODULE_EXPORT void im_module_init(GTypeModule *module) {
    floe_context_register_type(module);
    if (!bus) {
        bus = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, NULL);
        if (bus) {
            GVariant *reply = g_dbus_connection_call_sync(bus, BUS_NAME, BUS_PATH,
                BUS_NAME, "Register", g_variant_new("(us)", 1, "gtk3"), NULL,
                G_DBUS_CALL_FLAGS_NONE, 3000, NULL, NULL);
            if (reply) g_variant_unref(reply);
            else g_clear_object(&bus);
        }
    }
}

G_MODULE_EXPORT void im_module_exit(void) { g_clear_object(&bus); }
G_MODULE_EXPORT void im_module_list(const GtkIMContextInfo ***out, int *count) {
    *out = contexts;
    *count = 1;
}
G_MODULE_EXPORT GtkIMContext *im_module_create(const gchar *id) {
    return g_strcmp0(id, "floe-client") == 0 ? g_object_new(floe_context_get_type(), NULL) : NULL;
}
