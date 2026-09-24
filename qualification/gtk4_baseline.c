/* The GTK 4.0 runtime fixture needs no newer system GI typelib. */
#include <gtk/gtk.h>
static GtkTextBuffer *buffers[2];
static const char *receipt;
static void append_json(GString *out, const char *text) {
    g_string_append_c(out, '"');
    for (const unsigned char *p = (const unsigned char *)text; *p; p++) {
        if (*p == '"' || *p == '\\') g_string_append_c(out, '\\');
        if (*p < 32) g_string_append_printf(out, "\\u%04x", *p);
        else g_string_append_c(out, *p);
    }
    g_string_append_c(out, '"');
}
static void changed(GtkTextBuffer *buffer, gpointer unused) {
    (void)buffer; (void)unused;
    GString *out = g_string_new("[");
    for (int i = 0; i < 2; i++) {
        if (!buffers[i]) { g_string_free(out, TRUE); return; }
        GtkTextIter start, end;
        gtk_text_buffer_get_bounds(buffers[i], &start, &end);
        char *text = gtk_text_buffer_get_text(buffers[i], &start, &end, TRUE);
        if (i) g_string_append_c(out, ',');
        append_json(out, text);
        g_free(text);
    }
    g_string_append_c(out, ']');
    g_file_set_contents(receipt, out->str, out->len, NULL);
    g_string_free(out, TRUE);
}
static gboolean density(gpointer window) {
    int dpi = 0;
    g_object_get(gtk_settings_get_default(), "gtk-xft-dpi", &dpi, NULL);
    char *data = g_strdup_printf("{\"scale\":%d,\"dpi\":%d}", gtk_widget_get_scale_factor(window), dpi / 1024);
    char *name = g_strdup(receipt);
    char *dot = strrchr(name, '.');
    if (dot) *dot = 0;
    char *filename = g_strconcat(name, ".density.json", NULL);
    g_file_set_contents(filename, data, -1, NULL);
    g_free(filename); g_free(name); g_free(data);
    return G_SOURCE_CONTINUE;
}
static void activate(GtkApplication *app, gpointer unused) {
    (void)unused;
    GtkWidget *window = gtk_application_window_new(app);
    gtk_window_set_title(GTK_WINDOW(window), "Floe GTK 4.0 baseline qualification");
    gtk_window_set_default_size(GTK_WINDOW(window), 640, 320);
    GtkWidget *row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0), *first = NULL;
    gtk_box_set_homogeneous(GTK_BOX(row), TRUE);
    for (int i = 0; i < 2; i++) {
        GtkWidget *editor = gtk_text_view_new(), *scroll = gtk_scrolled_window_new();
        if (!first) first = editor;
        buffers[i] = gtk_text_view_get_buffer(GTK_TEXT_VIEW(editor));
        g_signal_connect(buffers[i], "changed", G_CALLBACK(changed), NULL);
        gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(scroll), editor);
        gtk_widget_set_hexpand(scroll, TRUE); gtk_widget_set_vexpand(scroll, TRUE);
        gtk_box_append(GTK_BOX(row), scroll);
    }
    gtk_window_set_child(GTK_WINDOW(window), row);
    gtk_window_present(GTK_WINDOW(window));
    gtk_widget_grab_focus(first);
    changed(NULL, NULL);
    g_timeout_add(100, density, window);
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    receipt = argv[1];
    g_print("GTK runtime %u.%u.%u\n", gtk_get_major_version(), gtk_get_minor_version(), gtk_get_micro_version());
    GtkApplication *app = gtk_application_new("org.floegence.InputBaseline", G_APPLICATION_FLAGS_NONE);
    g_signal_connect(app, "activate", G_CALLBACK(activate), NULL);
    int status = g_application_run(G_APPLICATION(app), 0, NULL);
    g_object_unref(app);
    return status;
}
