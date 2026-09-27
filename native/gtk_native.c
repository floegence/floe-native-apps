/* Native GTK context: negotiated Wayland text input or an ordered X11 marker.
 * No composition engine, host IBus module or document mirror is required. */
#define _GNU_SOURCE
#include <gtk/gtk.h>
#include <gio/gio.h>
#if GTK_MAJOR_VERSION == 3
#include <gtk/gtkimmodule.h>
#include <gdk/gdkx.h>
#include <dlfcn.h>
#else
#include <gdk/x11/gdkx.h>
#endif

#define INPUT_NAME "org.floegence.DesktopInput"
#define INPUT_PATH "/org/floegence/DesktopInput"
#define CONTEXT_ID "floe-client-native"

static GDBusConnection *bus;
static GType context_type;
static gpointer parent_class;
static guint parent_size;
#if GTK_MAJOR_VERSION == 4
static GType wayland_delegate_type;
static gpointer wayland_parent_class;
static guint wayland_parent_size;
#else
static GTypeModule *owner_module;
static GModule *wayland_library;
static GtkIMContext *(*wayland_create)(const char *);
static void (*wayland_exit)(void);

static gboolean load_wayland(void) {
    if (wayland_library) return TRUE;
    /* GTK3 ships this implementation as a toolkit module. Resolve it beside
     * the application's loaded GTK, never from a host input-method cache or
     * another bundled toolkit. Use its documented IM module ABI unchanged. */
    Dl_info library;
    if (!dladdr((void *)gtk_get_major_version, &library)) return FALSE;
    char *directory = g_path_get_dirname(library.dli_fname);
    char *path = g_build_filename(directory, "gtk-3.0", "3.0.0", "immodules", "im-wayland.so", NULL);
    GModule *module = g_module_open(path, G_MODULE_BIND_LAZY | G_MODULE_BIND_LOCAL);
    g_free(path);
    g_free(directory);
    void (*initialize)(GTypeModule *);
    if (!module) return FALSE;
    if (!g_module_symbol(module, "im_module_init", (gpointer *)&initialize) ||
        !g_module_symbol(module, "im_module_create", (gpointer *)&wayland_create) ||
        !g_module_symbol(module, "im_module_exit", (gpointer *)&wayland_exit)) {
        g_module_close(module);
        wayland_create = NULL;
        wayland_exit = NULL;
        return FALSE;
    }
    initialize(owner_module);
    wayland_library = module;
    return TRUE;
}
#endif

static gboolean is_wayland(void) {
    GdkDisplay *display = gdk_display_get_default();
    GType wayland = g_type_from_name("GdkWaylandDisplay");
    return display && wayland && G_TYPE_CHECK_INSTANCE_TYPE(display, wayland);
}

typedef struct {
    GtkIMContext *delegate;
    GObject *client;
    gboolean focused;
    gboolean wayland;
} Context;

typedef struct {
    GDBusConnection *connection;
    GtkIMContext *context;
    guint32 sequence;
} Completion;

static Context *context_state(GtkIMContext *context) {
    return (Context *)((char *)context + parent_size);
}

static const char *destination(void) {
    const char *name = g_getenv("FLOE_NATIVE_DESKTOP_INPUT");
    return name ? name : INPUT_NAME;
}

static void notify(GDBusConnection *connection, const char *method, guint32 value) {
    if (connection && !g_dbus_connection_is_closed(connection))
        g_dbus_connection_call(connection, destination(), INPUT_PATH, INPUT_NAME,
            method, g_variant_new("(u)", value), NULL, G_DBUS_CALL_FLAGS_NONE,
            3000, NULL, NULL, NULL);
}

static gboolean completed(gpointer data) {
    Completion *value = data;
    notify(value->connection, "Done", value->sequence);
    return G_SOURCE_REMOVE;
}

static void completion_free(gpointer data) {
    Completion *value = data;
    g_object_unref(value->connection);
    g_object_unref(value->context);
    g_free(value);
}

static void register_client(void) {
    if (bus) return;
    bus = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, NULL);
    if (!bus) return;
    const char *toolkit = GTK_MAJOR_VERSION == 3 ? "gtk3-native" : "gtk4-native";
    GVariant *reply = g_dbus_connection_call_sync(bus, destination(), INPUT_PATH,
        INPUT_NAME, "Register", g_variant_new("(us)", 1, toolkit), NULL,
        G_DBUS_CALL_FLAGS_NONE, 3000, NULL, NULL);
    if (reply) g_variant_unref(reply);
    else g_clear_object(&bus);
}

static guint32 native_window(GtkIMContext *context) {
    Context *state = context_state(context);
    if (!state->focused || !state->client || state->wayland) return 0;
#if GTK_MAJOR_VERSION == 3
    GdkWindow *window = gdk_window_get_toplevel(GDK_WINDOW(state->client));
    return window && GDK_IS_X11_WINDOW(window) ? (guint32)gdk_x11_window_get_xid(window) : 0;
#else
    GtkNative *native = gtk_widget_get_native(GTK_WIDGET(state->client));
    GdkSurface *surface = native ? gtk_native_get_surface(native) : NULL;
    return surface && GDK_IS_X11_SURFACE(surface) ? (guint32)gdk_x11_surface_get_xid(surface) : 0;
#endif
}

static void commit_marker(GtkIMContext *context) {
    Context *state = context_state(context);
    if (!bus || g_dbus_connection_is_closed(bus)) return;
    GObject *original = state->client;
    guint32 window = native_window(context);
    GVariant *reply = g_dbus_connection_call_sync(bus, destination(), INPUT_PATH,
        INPUT_NAME, "Take", g_variant_new("(uu)", 0, window), G_VARIANT_TYPE("(us)"),
        G_DBUS_CALL_FLAGS_NONE, 3000, NULL, NULL);
    if (!reply) return;
    guint32 sequence;
    const char *text;
    g_variant_get(reply, "(u&s)", &sequence, &text);
    if (!window || state->client != original || native_window(context) != window) {
        notify(bus, "Failed", sequence);
        g_variant_unref(reply);
        return;
    }
    Completion *value = g_new(Completion, 1);
    value->connection = g_object_ref(bus);
    value->context = g_object_ref(context);
    value->sequence = sequence;
    g_signal_emit_by_name(context, "commit", text);
    g_variant_unref(reply);
    /* Return through the real toolkit filter before acknowledging. The context
     * reference also keeps its dynamic module loaded if commit removes a field. */
    g_idle_add_full(G_PRIORITY_DEFAULT_IDLE, completed, value, completion_free);
}

#if GTK_MAJOR_VERSION == 3
static gboolean key(GtkIMContext *context, GdkEventKey *event) {
    if (!context_state(context)->wayland && event->hardware_keycode == 8) {
        if (event->type == GDK_KEY_PRESS) commit_marker(context);
        else if (event->type == GDK_KEY_RELEASE) notify(bus, "Released", 0);
        return TRUE;
    }
    return gtk_im_context_filter_keypress(context_state(context)->delegate, event);
}
#else
static gboolean key(GtkIMContext *context, GdkEvent *event) {
    GdkEventType type = gdk_event_get_event_type(event);
    if (!context_state(context)->wayland && (type == GDK_KEY_PRESS || type == GDK_KEY_RELEASE) &&
            gdk_key_event_get_keycode(event) == 8) {
        if (type == GDK_KEY_PRESS) commit_marker(context);
        else notify(bus, "Released", 0);
        return TRUE;
    }
    return gtk_im_context_filter_keypress(context_state(context)->delegate, event);
}
#endif

static void forward_commit(GtkIMContext *delegate, const char *text, gpointer context) {
    (void)delegate;
    g_signal_emit_by_name(context, "commit", text);
}
static void forward_preedit_start(GtkIMContext *delegate, gpointer context) {
    (void)delegate; g_signal_emit_by_name(context, "preedit-start");
}
static void forward_preedit_changed(GtkIMContext *delegate, gpointer context) {
    (void)delegate; g_signal_emit_by_name(context, "preedit-changed");
}
static void forward_preedit_end(GtkIMContext *delegate, gpointer context) {
    (void)delegate; g_signal_emit_by_name(context, "preedit-end");
}
static gboolean retrieve(GtkIMContext *delegate, gpointer context) {
    (void)delegate;
    gboolean result = FALSE;
    g_signal_emit_by_name(context, "retrieve-surrounding", &result);
    return result;
}
static gboolean delete_surrounding(GtkIMContext *delegate, int offset, int length, gpointer context) {
    (void)delegate;
    gboolean result = FALSE;
    g_signal_emit_by_name(context, "delete-surrounding", offset, length, &result);
    return result;
}

static void focus_in(GtkIMContext *context) {
    Context *state = context_state(context);
    state->focused = TRUE;
    if (!state->wayland) register_client();
    gtk_im_context_focus_in(state->delegate);
}
static void focus_out(GtkIMContext *context) {
    Context *state = context_state(context);
    state->focused = FALSE;
    gtk_im_context_focus_out(state->delegate);
}
static void reset(GtkIMContext *context) {
    gtk_im_context_reset(context_state(context)->delegate);
}
static void preedit(GtkIMContext *context, gchar **text, PangoAttrList **attrs, gint *position) {
    gtk_im_context_get_preedit_string(context_state(context)->delegate, text, attrs, position);
}
static void cursor(GtkIMContext *context, GdkRectangle *rectangle) {
    gtk_im_context_set_cursor_location(context_state(context)->delegate, rectangle);
}
static void use_preedit(GtkIMContext *context, gboolean value) {
    gtk_im_context_set_use_preedit(context_state(context)->delegate, value);
}
static void set_surrounding(GtkIMContext *context, const char *text, int length, int cursor) {
    gtk_im_context_set_surrounding(context_state(context)->delegate, text, length, cursor);
}
static gboolean get_surrounding(GtkIMContext *context, char **text, int *cursor) {
    return gtk_im_context_get_surrounding(context_state(context)->delegate, text, cursor);
}

#if GTK_MAJOR_VERSION == 3
static void client(GtkIMContext *context, GdkWindow *window) {
    GObject *object = window ? G_OBJECT(window) : NULL;
#else
static void client(GtkIMContext *context, GtkWidget *widget) {
    GObject *object = widget ? G_OBJECT(widget) : NULL;
#endif
    Context *state = context_state(context);
    if (state->client == object) return;
    state->focused = FALSE;
    if (state->client) g_object_remove_weak_pointer(state->client, (gpointer *)&state->client);
    state->client = object;
    if (object) g_object_add_weak_pointer(object, (gpointer *)&state->client);
#if GTK_MAJOR_VERSION == 3
    if (state->delegate) gtk_im_context_set_client_window(state->delegate, window);
#else
    if (state->delegate) gtk_im_context_set_client_widget(state->delegate, widget);
#endif
}

static void dispose(GObject *object) {
    client(GTK_IM_CONTEXT(object), NULL);
    g_clear_object(&context_state(GTK_IM_CONTEXT(object))->delegate);
    G_OBJECT_CLASS(parent_class)->dispose(object);
}

#if GTK_MAJOR_VERSION == 4
static gboolean *wayland_configured(GtkIMContext *context) {
    return (gboolean *)((char *)context + wayland_parent_size);
}

static void wayland_reset(GtkIMContext *context) {
    if (*wayland_configured(context))
        GTK_IM_CONTEXT_CLASS(wayland_parent_class)->reset(context);
}

static void wayland_init(GTypeInstance *instance, gpointer klass) {
    (void)klass;
    /* GTK's public setter first calls reset, which lazily creates the default
     * context. That default is this outer module, causing recursive creation.
     * Suppress only that initial reset while selecting the explicit delegate;
     * all subsequent resets use GtkIMMulticontext's normal implementation. */
    gtk_im_multicontext_set_context_id(GTK_IM_MULTICONTEXT(instance), "wayland");
    *wayland_configured(GTK_IM_CONTEXT(instance)) = TRUE;
}

static void wayland_class_init(gpointer klass, gpointer data) {
    (void)data;
    wayland_parent_class = g_type_class_peek_parent(klass);
    GTK_IM_CONTEXT_CLASS(klass)->reset = wayland_reset;
}
#endif

static void instance_init(GTypeInstance *instance, gpointer klass) {
    (void)klass;
    GtkIMContext *context = GTK_IM_CONTEXT(instance);
    Context *state = context_state(context);
    /* Backend GTypes are registered by the active display. Reflection avoids
     * linking optional Wayland symbols into the GTK 4.0 X11 build baseline. */
    state->wayland = is_wayland();
    if (state->wayland) {
#if GTK_MAJOR_VERSION == 3
        state->delegate = wayland_create("wayland");
#else
        state->delegate = g_object_new(wayland_delegate_type, NULL);
#endif
    } else {
        state->delegate = gtk_im_context_simple_new();
    }
    g_object_bind_property(context, "input-purpose", state->delegate, "input-purpose", G_BINDING_SYNC_CREATE);
    g_object_bind_property(context, "input-hints", state->delegate, "input-hints", G_BINDING_SYNC_CREATE);
    g_signal_connect(state->delegate, "commit", G_CALLBACK(forward_commit), context);
    g_signal_connect(state->delegate, "preedit-start", G_CALLBACK(forward_preedit_start), context);
    g_signal_connect(state->delegate, "preedit-changed", G_CALLBACK(forward_preedit_changed), context);
    g_signal_connect(state->delegate, "preedit-end", G_CALLBACK(forward_preedit_end), context);
    g_signal_connect(state->delegate, "retrieve-surrounding", G_CALLBACK(retrieve), context);
    g_signal_connect(state->delegate, "delete-surrounding", G_CALLBACK(delete_surrounding), context);
}

static void class_init(gpointer klass, gpointer data) {
    (void)data;
    parent_class = g_type_class_peek_parent(klass);
    GtkIMContextClass *context = GTK_IM_CONTEXT_CLASS(klass);
#if GTK_MAJOR_VERSION == 3
    context->set_client_window = client;
#else
    context->set_client_widget = client;
#endif
    context->filter_keypress = key;
    context->focus_in = focus_in;
    context->focus_out = focus_out;
    context->reset = reset;
    context->get_preedit_string = preedit;
    context->set_cursor_location = cursor;
    context->set_use_preedit = use_preedit;
    context->set_surrounding = set_surrounding;
    context->get_surrounding = get_surrounding;
    G_OBJECT_CLASS(klass)->dispose = dispose;
}

static void register_type(GTypeModule *module) {
    GTypeQuery query;
#if GTK_MAJOR_VERSION == 4
    g_type_query(GTK_TYPE_IM_MULTICONTEXT, &query);
    wayland_parent_size = query.instance_size;
    const GTypeInfo wayland_info = {.class_size = query.class_size, .class_init = wayland_class_init,
        .instance_size = query.instance_size + sizeof(gboolean), .instance_init = wayland_init};
    wayland_delegate_type = g_type_module_register_type(module, GTK_TYPE_IM_MULTICONTEXT,
        GTK_MAJOR_VERSION == 3 ? "FloeNativeGTK3Wayland" : "FloeNativeGTK4Wayland", &wayland_info, 0);
#else
    owner_module = module;
#endif
    g_type_query(GTK_TYPE_IM_CONTEXT, &query);
    parent_size = query.instance_size;
    const GTypeInfo info = {.class_size = query.class_size, .class_init = class_init,
        .instance_size = query.instance_size + sizeof(Context), .instance_init = instance_init};
    context_type = g_type_module_register_type(module, GTK_TYPE_IM_CONTEXT,
        GTK_MAJOR_VERSION == 3 ? "FloeNativeGTK3Context" : "FloeNativeGTK4Context", &info, 0);
}

#if GTK_MAJOR_VERSION == 3
static const GtkIMContextInfo info = {CONTEXT_ID, "Client confirmed text", "", "", ""};
static const GtkIMContextInfo *contexts[] = {&info};
G_MODULE_EXPORT void im_module_init(GTypeModule *module) { register_type(module); }
G_MODULE_EXPORT void im_module_exit(void) {
    g_clear_object(&bus);
    if (wayland_library) {
        wayland_exit();
        g_module_close(wayland_library);
        wayland_library = NULL;
        wayland_create = NULL;
        wayland_exit = NULL;
    }
}
G_MODULE_EXPORT void im_module_list(const GtkIMContextInfo ***out, int *count) {
    *out = contexts; *count = 1;
}
G_MODULE_EXPORT GtkIMContext *im_module_create(const char *id) {
    if (is_wayland() && !load_wayland()) {
        g_warning("Native GTK Wayland input module is unavailable");
        return NULL;
    }
    return g_strcmp0(id, CONTEXT_ID) == 0 ? g_object_new(context_type, NULL) : NULL;
}
#else
G_MODULE_EXPORT void g_io_module_load(GIOModule *module) {
    register_type(G_TYPE_MODULE(module));
    g_io_extension_point_implement("gtk-im-module", context_type, CONTEXT_ID, 0);
}
G_MODULE_EXPORT void g_io_module_unload(GIOModule *module) { (void)module; g_clear_object(&bus); }
G_MODULE_EXPORT char **g_io_module_query(void) { return g_strsplit("gtk-im-module", ":", -1); }
#endif
