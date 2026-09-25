/* Unpublished headless feasibility fixture, not the product compositor.
 * Built against pinned libweston 13.0.0. Internal seat entry points below are
 * exported by that version; a release must build with its reviewed source ABI.
 * The private inherited socket is available only to the owning test process.
 */
#define _GNU_SOURCE
#include <libweston/libweston.h>
#include <libweston/desktop.h>
#include <linux/input-event-codes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
#include "text-input-v3-server.h"

void weston_seat_init(struct weston_seat *, struct weston_compositor *, const char *);
void weston_seat_release(struct weston_seat *);
int weston_seat_init_pointer(struct weston_seat *);
int weston_seat_init_keyboard(struct weston_seat *, struct xkb_keymap *);
void notify_key(struct weston_seat *, const struct timespec *, uint32_t,
                enum wl_keyboard_key_state, enum weston_key_state_update);
void notify_button(struct weston_seat *, const struct timespec *, int32_t,
                   enum wl_pointer_button_state);
void notify_motion_absolute(struct weston_seat *, const struct timespec *,
                            struct weston_coord_global);
void notify_pointer_frame(struct weston_seat *);

struct probe {
    struct weston_compositor *compositor;
    struct weston_desktop *desktop;
    struct weston_layer layer;
    struct weston_seat seat;
    struct wl_list contexts;
    struct wl_list windows;
    struct wl_listener focus;
    struct wl_listener destroy;
    struct wl_listener capture_authority;
    struct weston_surface *current;
    pid_t capture_pid;
    int control;
    char buffer[65536];
    size_t used;
};
struct probe_window {
    struct wl_list link;
    struct weston_desktop_surface *desktop;
    struct weston_view *view;
};
struct text_context {
    struct wl_list link;
    struct probe *probe;
    struct wl_resource *resource;
    struct weston_surface *surface;
    uint32_t serial;
    bool enabled;
};

static void context_focus(struct text_context *ctx, struct weston_surface *surface) {
    if (ctx->surface == surface) return;
    if (ctx->surface)
        zwp_text_input_v3_send_leave(ctx->resource, ctx->surface->resource);
    ctx->surface = NULL;
    ctx->enabled = false;
    if (surface && surface->resource && wl_resource_get_client(ctx->resource) ==
        wl_resource_get_client(surface->resource)) {
        ctx->surface = surface;
        zwp_text_input_v3_send_enter(ctx->resource, surface->resource);
    }
}
static void focus_changed(struct wl_listener *listener, void *data) {
    (void)data;
    struct probe *p = wl_container_of(listener, p, focus);
    struct weston_keyboard *keyboard = weston_seat_get_keyboard(&p->seat);
    struct text_context *ctx;
    wl_list_for_each(ctx, &p->contexts, link) context_focus(ctx, keyboard->focus);
}
static void resource_destroy(struct wl_client *client, struct wl_resource *resource) {
    (void)client; wl_resource_destroy(resource);
}
static void context_destroyed(struct wl_resource *resource) {
    struct text_context *ctx = wl_resource_get_user_data(resource);
    wl_list_remove(&ctx->link); free(ctx);
}
static void context_enable(struct wl_client *client, struct wl_resource *resource) {
    (void)client;
    struct text_context *ctx = wl_resource_get_user_data(resource);
    ctx->enabled = ctx->surface != NULL;
}
static void context_disable(struct wl_client *client, struct wl_resource *resource) {
    (void)client;
    struct text_context *ctx = wl_resource_get_user_data(resource);
    ctx->enabled = false;
}
static void context_surrounding(struct wl_client *c, struct wl_resource *r,
                                const char *text, int32_t cursor, int32_t anchor) {
    (void)c; (void)r; (void)text; (void)cursor; (void)anchor;
}
static void context_cause(struct wl_client *c, struct wl_resource *r, uint32_t cause) {
    (void)c; (void)r; (void)cause;
}
static void context_type(struct wl_client *c, struct wl_resource *r, uint32_t hint, uint32_t purpose) {
    (void)c; (void)r; (void)hint; (void)purpose;
}
static void context_rectangle(struct wl_client *c, struct wl_resource *r,
                               int32_t x, int32_t y, int32_t w, int32_t h) {
    (void)c; (void)r; (void)x; (void)y; (void)w; (void)h;
}
static void context_commit(struct wl_client *c, struct wl_resource *r) {
    (void)c;
    struct text_context *ctx = wl_resource_get_user_data(r);
    ctx->serial++;
    dprintf(ctx->probe->control, "context %u %d\n", ctx->serial, ctx->enabled);
}
static const struct zwp_text_input_v3_interface context_api = {
    .destroy = resource_destroy, .enable = context_enable, .disable = context_disable,
    .set_surrounding_text = context_surrounding, .set_text_change_cause = context_cause,
    .set_content_type = context_type, .set_cursor_rectangle = context_rectangle,
    .commit = context_commit,
};
static void get_context(struct wl_client *client, struct wl_resource *manager,
                         uint32_t id, struct wl_resource *seat) {
    struct probe *p = wl_resource_get_user_data(manager);
    if (wl_resource_get_user_data(seat) != &p->seat) {
        wl_resource_post_error(manager, 0, "Unknown seat"); return;
    }
    struct text_context *ctx = calloc(1, sizeof *ctx);
    if (!ctx) { wl_client_post_no_memory(client); return; }
    ctx->probe = p;
    ctx->resource = wl_resource_create(client, &zwp_text_input_v3_interface, 1, id);
    if (!ctx->resource) { free(ctx); wl_client_post_no_memory(client); return; }
    wl_resource_set_implementation(ctx->resource, &context_api, ctx, context_destroyed);
    wl_list_insert(&p->contexts, &ctx->link);
    context_focus(ctx, weston_seat_get_keyboard(&p->seat)->focus);
}
static const struct zwp_text_input_manager_v3_interface manager_api = {
    .destroy = resource_destroy, .get_text_input = get_context,
};
static void manager_bind(struct wl_client *client, void *data, uint32_t version, uint32_t id) {
    struct wl_resource *r = wl_resource_create(client, &zwp_text_input_manager_v3_interface, version, id);
    if (!r) { wl_client_post_no_memory(client); return; }
    wl_resource_set_implementation(r, &manager_api, data, NULL);
}

static void surface_added(struct weston_desktop_surface *desktop, void *data) {
    struct probe *p = data;
    struct probe_window *window = calloc(1, sizeof *window);
    window->desktop = desktop;
    window->view = weston_desktop_surface_create_view(desktop);
    wl_list_insert(&p->windows, &window->link);
    weston_desktop_surface_set_user_data(desktop, window);
    weston_desktop_surface_set_size(desktop, 1000, 700);
    weston_desktop_surface_set_activated(desktop, true);
    dprintf(p->control, "window-added\n");
}
static void surface_removed(struct weston_desktop_surface *desktop, void *data) {
    struct probe *p = data;
    struct probe_window *window = weston_desktop_surface_get_user_data(desktop);
    struct weston_surface *surface = weston_desktop_surface_get_surface(desktop);
    struct text_context *ctx;
    wl_list_for_each(ctx, &p->contexts, link) {
        if (ctx->surface == surface) { ctx->surface = NULL; ctx->enabled = false; }
    }
    if (p->current == surface) p->current = NULL;
    wl_list_remove(&window->link);
    weston_desktop_surface_unlink_view(window->view);
    weston_view_destroy(window->view);
    free(window);
    if (!p->current) {
        wl_list_for_each(window, &p->windows, link) {
            if (!weston_view_is_mapped(window->view)) continue;
            p->current = weston_desktop_surface_get_surface(window->desktop);
            weston_seat_set_keyboard_focus(&p->seat, p->current);
            break;
        }
    }
    dprintf(p->control, "window-removed\n");
}
static void surface_committed(struct weston_desktop_surface *desktop,
                              struct weston_coord_surface offset, void *data) {
    (void)offset;
    struct probe *p = data;
    struct weston_surface *surface = weston_desktop_surface_get_surface(desktop);
    struct probe_window *window = weston_desktop_surface_get_user_data(desktop);
    struct weston_view *view = window->view;
    if (surface->width == 0 || weston_surface_is_mapped(surface)) return;
    struct weston_geometry geometry = weston_desktop_surface_get_geometry(desktop);
    weston_view_set_position(view, (struct weston_coord_global){ .c = { -geometry.x, -geometry.y } });
    weston_layer_entry_insert(&p->layer.view_list, &view->layer_link);
    weston_desktop_surface_propagate_layer(desktop);
    weston_view_update_transform(view);
    view->is_mapped = true;
    weston_surface_map(surface);
    p->current = surface;
    weston_seat_set_keyboard_focus(&p->seat, surface);
    weston_surface_damage(surface);
    dprintf(p->control, "frame %d %d\n", surface->width, surface->height);
}
static const struct weston_desktop_api desktop_api = {
    .struct_size = sizeof desktop_api,
    .surface_added = surface_added, .surface_removed = surface_removed,
    .committed = surface_committed,
};

static void command(struct probe *p, char *line) {
    unsigned int key, state;
    double x, y;
    struct timespec time;
    weston_compositor_get_time(&time);
    if (sscanf(line, "capture-authorize %u", &key) == 1) {
        p->capture_pid = key;
        dprintf(p->control, "capture-authorized %u\n", key);
    } else if (sscanf(line, "key %u %u", &key, &state) == 2 && state <= 1)
        notify_key(&p->seat, &time, key, state, STATE_UPDATE_AUTOMATIC);
    else if (sscanf(line, "button %u %u", &key, &state) == 2 && state <= 1) {
        notify_button(&p->seat, &time, key, state);
        notify_pointer_frame(&p->seat);
    } else if (sscanf(line, "motion %lf %lf", &x, &y) == 2) {
        notify_motion_absolute(&p->seat, &time, (struct weston_coord_global){ .c = {x, y} });
        notify_pointer_frame(&p->seat);
        struct weston_pointer *pointer = weston_seat_get_pointer(&p->seat);
        dprintf(p->control, "pointer %.0f %.0f %d %.0f %.0f\n", pointer->pos.c.x, pointer->pos.c.y,
            weston_pointer_has_focus_resource(pointer), wl_fixed_to_double(pointer->sx), wl_fixed_to_double(pointer->sy));
    } else if (!strncmp(line, "text ", 5)) {
        struct text_context *ctx, *selected = NULL;
        unsigned int count = 0;
        wl_list_for_each(ctx, &p->contexts, link) {
            if (ctx->enabled && ctx->surface == weston_seat_get_keyboard(&p->seat)->focus) {
                selected = ctx; count++;
            }
        }
        if (count == 1) {
            zwp_text_input_v3_send_commit_string(selected->resource, line + 5);
            zwp_text_input_v3_send_done(selected->resource, selected->serial);
            dprintf(p->control, "text-queued %u\n", selected->serial);
        } else dprintf(p->control, "text-unavailable %u\n", count);
    }
    wl_display_flush_clients(p->compositor->wl_display);
}
static int control_ready(int fd, uint32_t mask, void *data) {
    struct probe *p = data;
    if (mask & (WL_EVENT_HANGUP | WL_EVENT_ERROR)) {
        wl_display_terminate(p->compositor->wl_display); return 0;
    }
    ssize_t count = read(fd, p->buffer + p->used, sizeof p->buffer - p->used - 1);
    if (count <= 0) { wl_display_terminate(p->compositor->wl_display); return 0; }
    p->used += count; p->buffer[p->used] = 0;
    char *line = p->buffer, *next;
    while ((next = strchr(line, '\n'))) { *next++ = 0; command(p, line); line = next; }
    p->used -= line - p->buffer; memmove(p->buffer, line, p->used);
    if (p->used == sizeof p->buffer - 1) wl_display_terminate(p->compositor->wl_display);
    return 0;
}

static void authorize_capture(struct wl_listener *listener, struct weston_output_capture_attempt *attempt) {
    struct probe *p = wl_container_of(listener, p, capture_authority);
    pid_t pid; uid_t uid; gid_t gid;
    wl_client_get_credentials(attempt->who->client, &pid, &uid, &gid);
    if (pid == p->capture_pid && uid == getuid()) attempt->authorized = true;
    else attempt->denied = true;
}
static void destroy_probe(struct wl_listener *listener, void *data) {
    (void)data;
    struct probe *p = wl_container_of(listener, p, destroy);
    wl_list_remove(&p->destroy.link);
    wl_list_remove(&p->capture_authority.link);
    wl_list_remove(&p->focus.link);
    weston_desktop_destroy(p->desktop);
    weston_layer_fini(&p->layer);
    weston_seat_release(&p->seat);
}

WL_EXPORT int wet_shell_init(struct weston_compositor *compositor, int *argc, char *argv[]) {
    (void)argc; (void)argv;
    const char *descriptor = getenv("FLOE_PROBE_CONTROL_FD");
    if (!descriptor) return -1;
    struct probe *p = calloc(1, sizeof *p);
    if (!p) return -1;
    p->compositor = compositor; p->control = atoi(descriptor);
    wl_list_init(&p->contexts);
    wl_list_init(&p->windows);
    weston_layer_init(&p->layer, compositor);
    weston_layer_set_position(&p->layer, WESTON_LAYER_POSITION_NORMAL);
    weston_seat_init(&p->seat, compositor, "floe-prototype");
    weston_seat_init_pointer(&p->seat);
    struct xkb_context *xkb = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    const char *map = "xkb_keymap { xkb_keycodes { include \"evdev+aliases(qwerty)\" };"
        "xkb_types { include \"complete\" }; xkb_compatibility { include \"complete\" };"
        "xkb_symbols { include \"pc+us+inet(evdev)\" key <I248> { [ U0F0000 ] }; }; };";
    struct xkb_keymap *keymap = xkb_keymap_new_from_string(xkb, map,
        XKB_KEYMAP_FORMAT_TEXT_V1, XKB_KEYMAP_COMPILE_NO_FLAGS);
    if (!keymap || weston_seat_init_keyboard(&p->seat, keymap) < 0) return -1;
    xkb_keymap_unref(keymap);
    xkb_context_unref(xkb);
    p->focus.notify = focus_changed;
    wl_signal_add(&weston_seat_get_keyboard(&p->seat)->focus_signal, &p->focus);
    p->desktop = weston_desktop_create(compositor, &desktop_api, p);
    if (!p->desktop) return -1;
    p->destroy.notify = destroy_probe;
    wl_signal_add(&compositor->destroy_signal, &p->destroy);
    weston_compositor_add_screenshot_authority(compositor, &p->capture_authority, authorize_capture);
    wl_global_create(compositor->wl_display, &zwp_text_input_manager_v3_interface, 1, p, manager_bind);
    wl_event_loop_add_fd(wl_display_get_event_loop(compositor->wl_display), p->control,
                        WL_EVENT_READABLE, control_ready, p);
    dprintf(p->control, "ready\n");
    return 0;
}
