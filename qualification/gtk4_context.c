/* Verify the native context lifecycle without synthesizing a text receipt. */
#include <gtk/gtk.h>
#include <gdk/x11/gdkx.h>
static void activate(GtkApplication *app, gpointer directory) {
    GtkWidget *window = gtk_application_window_new(app);
    GtkWidget *editor = gtk_text_view_new();
    gtk_window_set_child(GTK_WINDOW(window), editor);
    gtk_window_present(GTK_WINDOW(window));
    GList *modules = g_io_modules_load_all_in_directory(directory);
    GIOExtensionPoint *point = g_io_extension_point_lookup("gtk-im-module");
    g_assert_nonnull(point);
    GIOExtension *extension = g_io_extension_point_get_extension_by_name(point, "floe-client");
    g_assert_nonnull(extension);
    GtkIMContext *context = g_object_new(g_io_extension_get_type(extension), NULL);
    GdkDisplay *display = gtk_widget_get_display(editor);
    guint signal = g_signal_lookup("xevent", G_OBJECT_TYPE(display));
    g_assert_cmpuint(signal, !=, 0);
    gtk_im_context_set_client_widget(context, editor);
    gtk_im_context_focus_in(context);
    gulong before = g_signal_handler_find(display, G_SIGNAL_MATCH_ID | G_SIGNAL_MATCH_DATA,
                                         signal, 0, NULL, NULL, context);
    g_assert_cmpuint(before, !=, 0);
    /* GtkTextView/GtkSourceView can reassert the same client widget while its
       context remains focused. That is not a focus loss or a new input target. */
    gtk_im_context_set_client_widget(context, editor);
    gulong after = g_signal_handler_find(display, G_SIGNAL_MATCH_ID | G_SIGNAL_MATCH_DATA,
                                        signal, 0, NULL, NULL, context);
    g_assert_cmpuint(after, ==, before);
    gtk_im_context_focus_out(context);
    g_assert_cmpuint(g_signal_handler_find(display, G_SIGNAL_MATCH_ID | G_SIGNAL_MATCH_DATA,
                    signal, 0, NULL, NULL, context), ==, 0);
    gtk_im_context_focus_in(context);
    gtk_im_context_set_client_widget(context, NULL);
    g_assert_cmpuint(g_signal_handler_find(display, G_SIGNAL_MATCH_ID | G_SIGNAL_MATCH_DATA,
                    signal, 0, NULL, NULL, context), ==, 0);
    g_object_unref(context);
    g_list_free_full(modules, g_object_unref);
    g_print("PASS GTK4 focused same-widget binding, focus loss and disposal\n");
    g_application_quit(G_APPLICATION(app));
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    GtkApplication *app = gtk_application_new("org.floegence.ContextQualification", G_APPLICATION_FLAGS_NONE);
    g_signal_connect(app, "activate", G_CALLBACK(activate), argv[1]);
    int result = g_application_run(G_APPLICATION(app), 0, NULL);
    g_object_unref(app);
    return result;
}
