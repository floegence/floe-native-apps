/* Shared commit transport; GTK generations retain their own module/event ABI. */
#ifndef FLOE_GTK_COMMIT_H
#define FLOE_GTK_COMMIT_H
#include <gio/gio.h>
#define BUS_NAME "org.floegence.ClientInput"
#define BUS_PATH "/org/floegence/ClientInput"
static GDBusConnection *bus;

typedef struct { GDBusConnection *connection; GtkIMContext *context; guint32 sequence; } FloeAck;
static void ack_free(gpointer data) {
    FloeAck *ack = data;
    g_object_unref(ack->connection);
    g_object_unref(ack->context);
    g_free(ack);
}
static gboolean acknowledged(gpointer data) {
    FloeAck *ack = data;
    if (!g_dbus_connection_is_closed(ack->connection))
        g_dbus_connection_call(ack->connection, BUS_NAME, BUS_PATH, BUS_NAME, "Done",
            g_variant_new("(u)", ack->sequence), NULL,
            G_DBUS_CALL_FLAGS_NONE, 3000, NULL, NULL, NULL);
    return G_SOURCE_REMOVE;
}
static void floe_bus_open(const char *toolkit) {
    if (bus) return;
    bus = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, NULL);
    if (!bus) return;
    GVariant *reply = g_dbus_connection_call_sync(bus, BUS_NAME, BUS_PATH,
        BUS_NAME, "Register", g_variant_new("(us)", 1, toolkit), NULL,
        G_DBUS_CALL_FLAGS_NONE, 3000, NULL, NULL);
    if (reply) g_variant_unref(reply);
    else g_clear_object(&bus);
}
static void floe_bus_close(void) { g_clear_object(&bus); }
static void floe_commit(GtkIMContext *context, guint32 timestamp, guint32 xid) {
    if (!bus || g_dbus_connection_is_closed(bus)) return;
    GVariant *reply = g_dbus_connection_call_sync(bus, BUS_NAME, BUS_PATH, BUS_NAME,
        "Take", g_variant_new("(uu)", timestamp, xid), G_VARIANT_TYPE("(us)"),
        G_DBUS_CALL_FLAGS_NONE, 3000, NULL, NULL);
    if (!reply) return;
    const char *text;
    guint32 sequence;
    g_variant_get(reply, "(u&s)", &sequence, &text);
    FloeAck *ack = g_new(FloeAck, 1);
    ack->connection = g_object_ref(bus);
    ack->context = g_object_ref(context);
    ack->sequence = sequence;
    g_signal_emit_by_name(context, "commit", text);
    g_variant_unref(reply);
    /* A transport reply is insufficient: return through the toolkit key filter
       before releasing subsequent input. Retain the original bus and context so
       its dynamic module stays loaded even when commit destroys the widget. */
    g_idle_add_full(G_PRIORITY_DEFAULT_IDLE, acknowledged, ack, ack_free);
}
#endif
