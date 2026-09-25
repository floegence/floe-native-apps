/* Unpublished headless feasibility fixture, not the product compositor.
 * Host fixtures use libweston 13.0.0; the portable candidate pins 14.0.2.
 * Internal seat entry points below are exported by both reviewed versions;
 * a release must retain the corresponding original source and native ABI proof.
 * The private inherited socket is available only to the owning test process.
 */
#define _GNU_SOURCE
#include <libweston/libweston.h>
#include <libweston/desktop.h>
#include <libweston/xwayland-api.h>
#include <libweston/shell-utils.h>
#include <linux/input-event-codes.h>
#include <inttypes.h>
#include <math.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
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
void floe_notify_axis_value120(struct weston_seat *, const struct timespec *,
                               struct weston_pointer_axis_event *, int32_t);
void notify_axis_source(struct weston_seat *, uint32_t);

struct probe {
    struct weston_compositor *compositor;
    struct weston_desktop *desktop;
    struct weston_layer layer;
    struct weston_layer hidden_layer;
    struct weston_layer background_layer;
    struct wl_list backgrounds;
    struct wl_listener output_created;
    struct wl_listener output_resized;
    struct wl_listener surface_created;
    struct wl_list surfaces;
    uint64_t damage;
    struct weston_seat seat;
    struct wl_list contexts;
    struct wl_list windows;
    struct wl_listener focus;
    struct wl_listener destroy;
    struct wl_listener capture_authority;
    struct weston_surface *current;
    pid_t capture_pid;
    int control;
    struct wl_event_source *control_source;
    char output[128 * 1024];
    size_t output_start, output_end;
    char buffer[65536];
    size_t used;
    uint64_t next_window;
    uint64_t next_surface;
    uint64_t connection;
    uint64_t last_connection;
    uint64_t scene;
    bool keys[2080];
    bool buttons[8];
    double wheel_remainder[2];
    int32_t wheel_detents[2];
};
struct probe_window {
    struct wl_list link;
    struct weston_desktop_surface *desktop;
    struct weston_view *view;
    uint64_t identity;
    int32_t width, height;
    struct weston_geometry geometry;
    struct probe_window *parent;
};
struct probe_background {
    struct wl_list link;
    struct probe *probe;
    struct weston_output *output;
    struct weston_curtain *curtain;
    struct wl_listener destroy;
};
struct probe_surface {
    struct wl_list link;
    struct probe *probe;
    struct weston_surface *surface;
    struct wl_listener commit, destroy;
    uint64_t identity;
    int32_t width, height;
    double x, y;
};
struct text_context {
    struct wl_list link;
    struct probe *probe;
    struct wl_resource *resource;
    struct weston_surface *surface;
    uint32_t serial;
    bool enabled;
};

static void release_input(struct probe *p);
static void control_lost(struct probe *p) {
    if (p->control < 0) return;
    int descriptor = p->control;
    p->control = -1;
    if (p->control_source) wl_event_source_remove(p->control_source);
    p->control_source = NULL;
    close(descriptor);
    p->output_start = p->output_end = p->used = 0;
    p->connection = 0;
    p->capture_pid = 0;
    release_input(p);
}
static void flush_control(struct probe *p) {
    size_t budget = 16 * 1024;
    while (p->control >= 0 && p->output_start < p->output_end && budget) {
        size_t length = p->output_end - p->output_start;
        if (length > budget) length = budget;
        ssize_t count = send(p->control, p->output + p->output_start, length, MSG_NOSIGNAL);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
        if (count <= 0) { control_lost(p); return; }
        p->output_start += (size_t)count;
        budget -= (size_t)count;
    }
    if (p->output_start == p->output_end) p->output_start = p->output_end = 0;
    if (p->control_source)
        wl_event_source_fd_update(p->control_source, WL_EVENT_READABLE |
            (p->output_end > p->output_start ? WL_EVENT_WRITABLE : 0));
}
static void emit(struct probe *p, const char *format, ...) {
    if (p->control < 0) return;
    char record[1024];
    va_list arguments;
    va_start(arguments, format);
    int length = vsnprintf(record, sizeof record, format, arguments);
    va_end(arguments);
    if (length < 0 || (size_t)length >= sizeof record) { control_lost(p); return; }
    if (p->output_end + (size_t)length > sizeof p->output && p->output_start) {
        memmove(p->output, p->output + p->output_start, p->output_end - p->output_start);
        p->output_end -= p->output_start;
        p->output_start = 0;
    }
    /* Sharing control may fail, but a stalled observer must never freeze the
     * compositor or terminate the applications whose surfaces it owns. */
    if (p->output_end + (size_t)length > sizeof p->output) { control_lost(p); return; }
    memcpy(p->output + p->output_end, record, (size_t)length);
    p->output_end += (size_t)length;
    flush_control(p);
}

static void release_input(struct probe *p) {
    struct timespec time;
    weston_compositor_get_time(&time);
    for (unsigned int key = 0; key < 2080; key++) {
        if (!p->keys[key]) continue;
        p->keys[key] = false;
        notify_key(&p->seat, &time, key, WL_KEYBOARD_KEY_STATE_RELEASED, STATE_UPDATE_AUTOMATIC);
    }
    for (unsigned int button = 0; button < 8; button++) {
        if (!p->buttons[button]) continue;
        p->buttons[button] = false;
        notify_button(&p->seat, &time, BTN_LEFT + button, WL_POINTER_BUTTON_STATE_RELEASED);
    }
    notify_pointer_frame(&p->seat);
    memset(p->wheel_remainder, 0, sizeof p->wheel_remainder);
    memset(p->wheel_detents, 0, sizeof p->wheel_detents);
}

static uint64_t current_window(struct probe *p) {
    struct probe_window *window;
    uint64_t current = 0;
    wl_list_for_each(window, &p->windows, link) {
        if (weston_desktop_surface_get_surface(window->desktop) == p->current) {
            current = window->identity;
            break;
        }
    }
    return current;
}
static struct probe_window *window_for_surface(struct probe *p, struct weston_surface *surface) {
    if (!surface) return NULL;
    struct weston_surface *root = weston_surface_get_main_surface(surface);
    struct weston_desktop_surface *desktop = weston_surface_is_desktop_surface(root) ?
        weston_surface_get_desktop_surface(root) : NULL;
    for (unsigned int depth = 0; desktop && depth < 128; depth++) {
        struct weston_desktop_surface *parent = weston_desktop_surface_get_parent(desktop);
        if (!parent) break;
        desktop = parent;
        root = weston_desktop_surface_get_surface(desktop);
    }
    struct probe_window *window;
    wl_list_for_each(window, &p->windows, link)
        if (weston_desktop_surface_get_surface(window->desktop) == root) return window;
    return NULL;
}
static uint64_t input_window(struct probe *p) {
    struct weston_surface *focus = weston_seat_get_keyboard(&p->seat)->focus;
    struct probe_window *window = window_for_surface(p, focus);
    return focus && focus->width > 0 && focus->height > 0 && window &&
        window->identity == current_window(p) ? window->identity : 0;
}
static void emit_focus(struct probe *p) {
    struct weston_surface *focus = weston_seat_get_keyboard(&p->seat)->focus;
    struct probe_window *window = window_for_surface(p, focus);
    struct probe_surface *content;
    if (focus && focus->resource && window) {
        wl_list_for_each(content, &p->surfaces, link) {
            if (content->surface != focus) continue;
            pid_t pid; uid_t uid; gid_t gid;
            wl_client_get_credentials(wl_resource_get_client(focus->resource), &pid, &uid, &gid);
            emit(p, "focus %" PRIu64 " %" PRIu64 " %d %u %u\n", content->identity,
                window->identity, (int)pid, wl_resource_get_id(focus->resource),
                focus->width > 0 && focus->height > 0);
            return;
        }
    }
    emit(p, "focus 0 0 0 0 0\n");
}
static void window_state(struct probe *p, struct probe_window *window) {
    struct weston_surface *surface = weston_desktop_surface_get_surface(window->desktop);
    const struct weston_xwayland_surface_api *api = weston_xwayland_surface_get_api(p->compositor);
    bool x11 = api && api->is_xwayland_surface(surface);
    /* X11's reported PID is advisory metadata; it is not SO_PEERCRED and must
     * never authorize a text context. Wayland PID comes from the native peer. */
    emit(p, "window-state %" PRIu64 " %" PRIu64 " %s %d %d %d\n", window->identity,
        window->parent ? window->parent->identity : 0, x11 ? "x11" : "wayland",
        (int)weston_desktop_surface_get_pid(window->desktop), window->width, window->height);
}
static void scene_changed(struct probe *p) {
    emit(p, "scene %" PRIu64 " %" PRIu64 "\n", ++p->scene, input_window(p));
}

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
    release_input(p);
    emit_focus(p);
    scene_changed(p);
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
    emit(ctx->probe, "context %u %d\n", ctx->serial, ctx->enabled);
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

static bool ancestor(struct probe_window *parent, struct probe_window *child) {
    for (unsigned int depth = 0; child && depth < 128; depth++) {
        if (parent == child) return true;
        child = child->parent;
    }
    return false;
}
static void background_paint(struct probe_background *background) {
    struct weston_output *output = background->output;
    if (background->curtain) weston_shell_utils_curtain_destroy(background->curtain);
    struct weston_curtain_params params = {
        .a = 1.0, .pos = output->pos, .width = output->width, .height = output->height,
        .capture_input = false,
    };
    background->curtain = weston_shell_utils_curtain_create(background->probe->compositor, &params);
    struct weston_view *view = background->curtain->view;
    weston_surface_set_role(view->surface, "floe-background", NULL, 0);
    view->surface->output = output;
    weston_view_move_to_layer(view, &background->probe->background_layer.view_list);
    weston_view_set_output(view, output);
}
static void background_destroy(struct wl_listener *listener, void *data) {
    (void)data;
    struct probe_background *background = wl_container_of(listener, background, destroy);
    wl_list_remove(&background->destroy.link);
    wl_list_remove(&background->link);
    weston_shell_utils_curtain_destroy(background->curtain);
    free(background);
}
static void output_created(struct wl_listener *listener, void *data) {
    struct probe *p = wl_container_of(listener, p, output_created);
    struct weston_output *output = data;
    struct probe_background *background = calloc(1, sizeof *background);
    background->probe = p; background->output = output;
    background->destroy.notify = background_destroy;
    wl_signal_add(&output->destroy_signal, &background->destroy);
    wl_list_insert(&p->backgrounds, &background->link);
    background_paint(background);
}
static void output_resized(struct wl_listener *listener, void *data) {
    struct probe *p = wl_container_of(listener, p, output_resized);
    struct probe_background *background;
    wl_list_for_each(background, &p->backgrounds, link)
        if (background->output == data) background_paint(background);
}
static void content_committed(struct wl_listener *listener, void *data) {
    (void)data;
    struct probe_surface *content = wl_container_of(listener, content, commit);
    struct probe *p = content->probe;
    struct weston_view *view;
    double x = 0, y = 0;
    wl_list_for_each(view, &content->surface->views, surface_link) {
        if (!weston_view_is_mapped(view)) continue;
        weston_view_update_transform(view);
        struct weston_coord_global position = weston_coord_surface_to_global(view,
            (struct weston_coord_surface){ .c = {0, 0}, .coordinate_space_id = content->surface });
        x = position.c.x; y = position.c.y;
        break;
    }
    struct probe_window *owner = window_for_surface(p, content->surface);
    bool geometry_changed = content->width != content->surface->width ||
        content->height != content->surface->height || content->x != x || content->y != y;
    if (geometry_changed && owner && owner->view->layer_link.layer == &p->layer) {
        /* Child surfaces can change hit regions without a top-level commit.
         * Revoke coordinates on native geometry changes, not ordinary repaint. */
        release_input(p);
        emit_focus(p);
        scene_changed(p);
    }
    content->width = content->surface->width; content->height = content->surface->height;
    content->x = x; content->y = y;
    /* xdg_popup is a desktop child, not a wl_subsurface or a top-level added
     * through the shell callback. Its commits still damage the parent's frame. */
    if (owner && owner->view->layer_link.layer == &p->layer)
        emit(p, "damage %" PRIu64 " %" PRIu64 "\n", ++p->damage, p->scene);
}
static void content_destroyed(struct wl_listener *listener, void *data) {
    (void)data;
    struct probe_surface *content = wl_container_of(listener, content, destroy);
    emit(content->probe, "surface-retired %" PRIu64 "\n", content->identity);
    if (content->probe->current)
        emit(content->probe, "damage %" PRIu64 " %" PRIu64 "\n",
                ++content->probe->damage, content->probe->scene);
    wl_list_remove(&content->commit.link);
    wl_list_remove(&content->destroy.link);
    wl_list_remove(&content->link);
    free(content);
}
static void surface_created(struct wl_listener *listener, void *data) {
    struct probe *p = wl_container_of(listener, p, surface_created);
    struct probe_surface *content = calloc(1, sizeof *content);
    content->probe = p; content->surface = data;
    content->identity = ++p->next_surface;
    content->commit.notify = content_committed;
    content->destroy.notify = content_destroyed;
    wl_signal_add(&content->surface->commit_signal, &content->commit);
    wl_signal_add(&content->surface->destroy_signal, &content->destroy);
    wl_list_insert(&p->surfaces, &content->link);
    emit(p, "surface-instance %" PRIu64 "\n", content->identity);
}
static void apply_selection(struct probe *p, struct probe_window *selected) {
    struct weston_surface *surface = selected ? weston_desktop_surface_get_surface(selected->desktop) : NULL;
    if (p->current != surface) {
        release_input(p);
        weston_seat_break_desktop_grabs(&p->seat);
    }
    p->current = surface;
    struct probe_window *window;
    /* Map parents first so later transient children remain above them. Hidden
     * windows keep their native lifetime; they never contribute pixels/input. */
    wl_list_for_each_reverse(window, &p->windows, link) {
        if (!weston_view_is_mapped(window->view)) continue;
        bool visible = selected && (ancestor(window, selected) || ancestor(selected, window));
        weston_view_move_to_layer(window->view, visible ? &p->layer.view_list : &p->hidden_layer.view_list);
        weston_desktop_surface_propagate_layer(window->desktop);
        weston_desktop_surface_set_activated(window->desktop, window == selected);
    }
    weston_seat_set_keyboard_focus(&p->seat, surface);
    weston_compositor_damage_all(p->compositor);
}

static void surface_added(struct weston_desktop_surface *desktop, void *data) {
    struct probe *p = data;
    struct probe_window *window = calloc(1, sizeof *window);
    window->desktop = desktop;
    window->identity = ++p->next_window;
    window->view = weston_desktop_surface_create_view(desktop);
    wl_list_insert(&p->windows, &window->link);
    weston_desktop_surface_set_user_data(desktop, window);
    weston_desktop_surface_set_size(desktop, 1000, 700);
    weston_desktop_surface_set_activated(desktop, true);
    emit(p, "window-added\n");
    emit(p, "window-instance %" PRIu64 "\n", window->identity);
}
static void surface_removed(struct weston_desktop_surface *desktop, void *data) {
    struct probe *p = data;
    struct probe_window *window = weston_desktop_surface_get_user_data(desktop);
    struct probe_window *parent = window->parent, *child;
    wl_list_for_each(child, &p->windows, link)
        if (child->parent == window) {
            child->parent = NULL;
            if (weston_view_is_mapped(child->view)) window_state(p, child);
        }
    struct weston_surface *surface = weston_desktop_surface_get_surface(desktop);
    struct text_context *ctx;
    wl_list_for_each(ctx, &p->contexts, link) {
        if (ctx->surface == surface) { ctx->surface = NULL; ctx->enabled = false; }
    }
    if (p->current == surface) { release_input(p); p->current = NULL; }
    emit(p, "window-retired %" PRIu64 "\n", window->identity);
    wl_list_remove(&window->link);
    weston_desktop_surface_unlink_view(window->view);
    weston_view_destroy(window->view);
    free(window);
    if (!p->current && parent && weston_view_is_mapped(parent->view)) {
        apply_selection(p, parent);
        emit(p, "window-restored\n");
    }
    if (!p->current) {
        wl_list_for_each(window, &p->windows, link) {
            if (!weston_view_is_mapped(window->view)) continue;
            apply_selection(p, window);
            emit(p, "window-restored\n");
            break;
        }
    }
    emit(p, "window-removed\n");
    scene_changed(p);
}
static void position_window(struct probe_window *window) {
    double x = -window->geometry.x, y = -window->geometry.y;
    if (window->parent && weston_view_is_mapped(window->parent->view)) {
        struct weston_coord_global parent = weston_view_get_pos_offset_global(window->parent->view);
        x += parent.c.x + window->parent->geometry.x +
            (window->parent->geometry.width - window->geometry.width) / 2.0;
        y += parent.c.y + window->parent->geometry.y +
            (window->parent->geometry.height - window->geometry.height) / 2.0;
    }
    weston_view_set_position(window->view, (struct weston_coord_global){ .c = {x, y} });
}
static void surface_parent(struct weston_desktop_surface *desktop,
                           struct weston_desktop_surface *parent, void *data) {
    struct probe *p = data;
    struct probe_window *window = weston_desktop_surface_get_user_data(desktop);
    struct probe_window *new_parent = parent ? weston_desktop_surface_get_user_data(parent) : NULL;
    if (new_parent && ancestor(window, new_parent)) return;
    window->parent = new_parent;
    /* Dialogs choose their natural size; ordinary top-levels initially fit the
     * application viewport. Their relationship comes only from native shell
     * metadata, never the title, application ID or a guessed surface number. */
    weston_desktop_surface_set_size(desktop, parent ? 0 : 1000, parent ? 0 : 700);
    if (weston_view_is_mapped(window->view)) {
        position_window(window);
        window_state(p, window);
        struct probe_window *selected;
        wl_list_for_each(selected, &p->windows, link) {
            if (weston_desktop_surface_get_surface(selected->desktop) == p->current) {
                apply_selection(p, selected);
                release_input(p);
                scene_changed(p);
                break;
            }
        }
    }
}
static void surface_committed(struct weston_desktop_surface *desktop,
                              struct weston_coord_surface offset, void *data) {
    (void)offset;
    struct probe *p = data;
    struct weston_surface *surface = weston_desktop_surface_get_surface(desktop);
    struct probe_window *window = weston_desktop_surface_get_user_data(desktop);
    struct weston_view *view = window->view;
    if (surface->width == 0) return;
    struct weston_geometry geometry = weston_desktop_surface_get_geometry(desktop);
    bool mapped = weston_surface_is_mapped(surface);
    bool changed = window->width != surface->width || window->height != surface->height ||
        window->geometry.x != geometry.x || window->geometry.y != geometry.y ||
        window->geometry.width != geometry.width || window->geometry.height != geometry.height;
    if (mapped && !changed) return;
    window->width = surface->width; window->height = surface->height; window->geometry = geometry;
    position_window(window);
    window_state(p, window);
    if (mapped) {
        weston_view_update_transform(view);
        if (p->current == surface) {
            release_input(p);
            scene_changed(p);
        }
        weston_surface_damage(surface);
        return;
    }
    weston_view_move_to_layer(view, &p->layer.view_list);
    weston_desktop_surface_propagate_layer(desktop);
    weston_view_update_transform(view);
    view->is_mapped = true;
    weston_surface_map(surface);
    apply_selection(p, window);
    weston_surface_damage(surface);
    emit(p, "frame %d %d\n", surface->width, surface->height);
    emit(p, "window-mapped %" PRIu64 "\n", window->identity);
    const struct weston_xwayland_surface_api *xwayland = weston_xwayland_surface_get_api(p->compositor);
    emit(p, "window-protocol %" PRIu64 " %s\n", window->identity,
        xwayland && xwayland->is_xwayland_surface(surface) ? "x11" : "wayland");
    scene_changed(p);
}
static const struct weston_desktop_api desktop_api = {
    .struct_size = sizeof desktop_api,
    .surface_added = surface_added, .surface_removed = surface_removed,
    .committed = surface_committed, .set_parent = surface_parent,
};

static void command(struct probe *p, char *line) {
    unsigned int key, state;
    uint64_t connection, identity, generation;
    int prefix = 0;
    double x, y;
    struct timespec time;
    weston_compositor_get_time(&time);
    if (sscanf(line, "scene-query %" SCNu64, &generation) == 1) {
        emit(p, "scene-at %" PRIu64 " %" PRIu64 " %" PRIu64 "\n",
                generation, p->scene, input_window(p));
        return;
    }
    if (sscanf(line, "connection %" SCNu64, &connection) == 1) {
        if (connection > p->last_connection) {
            release_input(p);
            p->connection = connection;
            p->last_connection = connection;
            emit(p, "connection-ready %" PRIu64 "\n", connection);
        }
        return;
    }
    if (sscanf(line, "detach %" SCNu64, &connection) == 1) {
        if (connection == p->connection) {
            release_input(p);
            p->connection = 0;
        }
        return;
    }
    if (sscanf(line, "release %" SCNu64, &connection) == 1) {
        if (connection == p->connection) release_input(p);
        return;
    }
    if (sscanf(line, "select %" SCNu64 " %" SCNu64, &connection, &identity) == 2) {
        struct probe_window *window;
        if (connection && connection == p->connection) {
            wl_list_for_each(window, &p->windows, link) {
                if (window->identity != identity || !weston_view_is_mapped(window->view)) continue;
                apply_selection(p, window);
                scene_changed(p);
                return;
            }
        }
        emit(p, "selection-unavailable %" PRIu64 "\n", identity);
        return;
    }
    if (sscanf(line, "input %" SCNu64 " %" SCNu64 " %" SCNu64 " %n", &connection, &identity, &generation, &prefix) == 3 && prefix > 0) {
        struct probe_window *window;
        bool valid = false;
        wl_list_for_each(window, &p->windows, link) {
            if (window->identity == identity && weston_view_is_mapped(window->view) &&
                weston_desktop_surface_get_surface(window->desktop) == p->current) {
                valid = true;
                break;
            }
        }
        if (!connection || connection != p->connection || generation != p->scene ||
            identity != input_window(p) || !valid) {
            emit(p, "input-rejected %" PRIu64 " %" PRIu64 "\n", connection, identity);
            return;
        }
        /* Fixture controls remain separate from target-bearing input. Never
         * permit an input payload to grant capture or replace a connection. */
        line += prefix;
        if (strncmp(line, "key ", 4) && strncmp(line, "button ", 7) &&
            strncmp(line, "motion ", 7) && strncmp(line, "scroll ", 7) &&
            strncmp(line, "text ", 5) && strcmp(line, "close")) return;
    }
    if (sscanf(line, "capture-authorize %u", &key) == 1) {
        p->capture_pid = key;
        emit(p, "capture-authorized %u\n", key);
    } else if (sscanf(line, "key %u %u", &key, &state) == 2 && state <= 1 && key < 2080) {
        p->keys[key] = state;
        notify_key(&p->seat, &time, key, state, STATE_UPDATE_AUTOMATIC);
    } else if (sscanf(line, "button %u %u", &key, &state) == 2 && state <= 1 && key >= BTN_LEFT && key <= BTN_TASK) {
        p->buttons[key - BTN_LEFT] = state;
        notify_button(&p->seat, &time, key, state);
        notify_pointer_frame(&p->seat);
    } else if (sscanf(line, "scroll %lf %lf", &x, &y) == 2 &&
               isfinite(x) && isfinite(y) && fabs(x) <= 4096 && fabs(y) <= 4096) {
        /* These are complete wheel operations, without a host kinetic gesture
         * or inferred finger lifetime. Keep fractional axes; do not emit a
         * discrete detent or synthesize momentum after the client stops. */
        notify_axis_source(&p->seat, WL_POINTER_AXIS_SOURCE_WHEEL);
        double deltas[2] = { y, x };
        for (unsigned int axis = 0; axis < 2; axis++) {
            /* 120 CSS pixels form one wheel detent. value120 retains sub-step
             * delivery; sub-pixel remainder belongs only to this target. */
            p->wheel_remainder[axis] += deltas[axis];
            int32_t units = (int32_t)p->wheel_remainder[axis];
            p->wheel_remainder[axis] -= units;
            if (!units) continue;
            p->wheel_detents[axis] += units;
            int32_t detents = p->wheel_detents[axis] / 120;
            p->wheel_detents[axis] -= detents * 120;
            struct weston_pointer_axis_event event = {
                .axis = axis, .value = units / 12.0,
                .has_discrete = detents != 0, .discrete = detents,
            };
            floe_notify_axis_value120(&p->seat, &time, &event, units);
        }
        notify_pointer_frame(&p->seat);
    } else if (sscanf(line, "motion %lf %lf", &x, &y) == 2 &&
               isfinite(x) && isfinite(y) && x >= 0 && y >= 0 && x <= 4096 && y <= 4096) {
        notify_motion_absolute(&p->seat, &time, (struct weston_coord_global){ .c = {x, y} });
        notify_pointer_frame(&p->seat);
        struct weston_pointer *pointer = weston_seat_get_pointer(&p->seat);
        emit(p, "pointer %.0f %.0f %d %.0f %.0f\n", pointer->pos.c.x, pointer->pos.c.y,
            weston_pointer_has_focus_resource(pointer), wl_fixed_to_double(pointer->sx), wl_fixed_to_double(pointer->sy));
    } else if (!strcmp(line, "close") && p->current) {
        weston_desktop_surface_close(weston_surface_get_desktop_surface(p->current));
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
            emit(p, "text-queued %u\n", selected->serial);
        } else emit(p, "text-unavailable %u\n", count);
    }
    wl_display_flush_clients(p->compositor->wl_display);
}
static int control_ready(int fd, uint32_t mask, void *data) {
    struct probe *p = data;
    if (mask & (WL_EVENT_HANGUP | WL_EVENT_ERROR)) {
        control_lost(p); return 0;
    }
    if (mask & WL_EVENT_WRITABLE) flush_control(p);
    if (!(mask & WL_EVENT_READABLE) || p->control < 0) return 0;
    ssize_t count = read(fd, p->buffer + p->used, sizeof p->buffer - p->used - 1);
    if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return 0;
    if (count <= 0) { control_lost(p); return 0; }
    p->used += count; p->buffer[p->used] = 0;
    char *line = p->buffer, *next;
    while (p->control >= 0 && (next = strchr(line, '\n'))) { *next++ = 0; command(p, line); line = next; }
    if (p->control < 0) return 0;
    p->used -= line - p->buffer; memmove(p->buffer, line, p->used);
    if (p->used == sizeof p->buffer - 1) control_lost(p);
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
    control_lost(p);
    wl_list_remove(&p->destroy.link);
    wl_list_remove(&p->capture_authority.link);
    wl_list_remove(&p->focus.link);
    wl_list_remove(&p->output_created.link);
    wl_list_remove(&p->output_resized.link);
    wl_list_remove(&p->surface_created.link);
    struct probe_surface *content, *content_next;
    wl_list_for_each_safe(content, content_next, &p->surfaces, link)
        content_destroyed(&content->destroy, NULL);
    struct probe_background *background, *next;
    wl_list_for_each_safe(background, next, &p->backgrounds, link)
        background_destroy(&background->destroy, NULL);
    weston_desktop_destroy(p->desktop);
    weston_layer_fini(&p->layer);
    weston_layer_fini(&p->hidden_layer);
    weston_layer_fini(&p->background_layer);
    weston_seat_release(&p->seat);
}

WL_EXPORT int wet_shell_init(struct weston_compositor *compositor, int *argc, char *argv[]) {
    (void)argc; (void)argv;
    const char *descriptor = getenv("FLOE_PROBE_CONTROL_FD");
    if (!descriptor) return -1;
    struct probe *p = calloc(1, sizeof *p);
    if (!p) return -1;
    p->compositor = compositor; p->control = atoi(descriptor);
    int flags = fcntl(p->control, F_GETFL);
    if (flags < 0 || fcntl(p->control, F_SETFL, flags | O_NONBLOCK) < 0) return -1;
    int descriptor_flags = fcntl(p->control, F_GETFD);
    if (descriptor_flags < 0 || fcntl(p->control, F_SETFD, descriptor_flags | FD_CLOEXEC) < 0) return -1;
    emit(p, "native-version 1\n");
    wl_list_init(&p->contexts);
    wl_list_init(&p->windows);
    wl_list_init(&p->backgrounds);
    wl_list_init(&p->surfaces);
    p->surface_created.notify = surface_created;
    wl_signal_add(&compositor->create_surface_signal, &p->surface_created);
    weston_layer_init(&p->layer, compositor);
    weston_layer_set_position(&p->layer, WESTON_LAYER_POSITION_NORMAL);
    weston_layer_init(&p->hidden_layer, compositor);
    /* Retain native frame callbacks for inactive applications. HIDDEN is
     * rendered, so an opaque background must cover it and clear prior pixels. */
    weston_layer_set_position(&p->hidden_layer, WESTON_LAYER_POSITION_HIDDEN);
    weston_layer_init(&p->background_layer, compositor);
    weston_layer_set_position(&p->background_layer, WESTON_LAYER_POSITION_BACKGROUND);
    p->output_created.notify = output_created;
    p->output_resized.notify = output_resized;
    wl_signal_add(&compositor->output_created_signal, &p->output_created);
    wl_signal_add(&compositor->output_resized_signal, &p->output_resized);
    struct weston_output *output;
    wl_list_for_each(output, &compositor->output_list, link) output_created(&p->output_created, output);
    weston_seat_init(&p->seat, compositor, "floe-prototype");
    weston_seat_init_pointer(&p->seat);
    struct xkb_context *xkb = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    /* Fixture-only, non-text, non-repeating marker slots outside physical
     * evdev codes. Xwayland's narrower keycode range is not qualified here. */
    char map[8192];
    size_t used = (size_t)snprintf(map, sizeof map,
        "xkb_keymap { xkb_keycodes { include \"evdev+aliases(qwerty)\" ");
    for (unsigned int i = 0; i < 32; i++)
        used += (size_t)snprintf(map + used, sizeof map - used, "<F%03u> = %u; ", i, 2048 + i + 8);
    used += (size_t)snprintf(map + used, sizeof map - used,
        "}; xkb_types { include \"complete\" }; xkb_compatibility { include \"complete\" };"
        "xkb_symbols { include \"pc+us+inet(evdev)\" ");
    for (unsigned int i = 0; i < 32; i++)
        used += (size_t)snprintf(map + used, sizeof map - used,
            "key <F%03u> { repeat=no, [ F24 ] }; ", i);
    snprintf(map + used, sizeof map - used, "}; };");
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
    p->control_source = wl_event_loop_add_fd(wl_display_get_event_loop(compositor->wl_display), p->control,
                                            WL_EVENT_READABLE, control_ready, p);
    emit(p, "ready\n");
    return 0;
}
