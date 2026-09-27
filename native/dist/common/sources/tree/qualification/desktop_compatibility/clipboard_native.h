/* Private compositor selection bridge. The seat remains the selection owner;
 * the helper transports bounded UTF-8 snapshots, never keyboard text injection.
 * Included after the shell's native command and identity helpers. */
#define CLIPBOARD_LIMIT 16000
struct native_clipboard;
struct clipboard_offer {
    struct weston_data_source base;
    struct wl_list link;
    struct native_clipboard *clipboard;
    struct wl_event_source *retirement;
    size_t size;
    char text[];
};
struct clipboard_writer {
    struct wl_list link;
    struct native_clipboard *clipboard;
    struct wl_event_source *event, *timer;
    int fd;
    size_t offset, size;
    char text[];
};
struct native_clipboard {
    struct probe *probe;
    struct wl_listener selection;
    struct wl_list offers, writers;
    size_t writer_count;
    struct wl_event_source *reader, *timer;
    int fd;
    uint64_t revision, connection, generation, window;
    size_t size;
    bool available;
    char text[CLIPBOARD_LIMIT + 1];
};
static struct wl_event_loop *clipboard_loop(struct native_clipboard *clipboard) {
    return wl_display_get_event_loop(clipboard->probe->compositor->wl_display);
}
static void clipboard_stop_read(struct native_clipboard *clipboard) {
    if (clipboard->reader) wl_event_source_remove(clipboard->reader);
    if (clipboard->timer) wl_event_source_remove(clipboard->timer);
    if (clipboard->fd >= 0) close(clipboard->fd);
    clipboard->reader = clipboard->timer = NULL;
    clipboard->fd = -1;
}
static void clipboard_pause(struct native_clipboard *clipboard) {
    clipboard_stop_read(clipboard);
    clipboard->available = false;
    clipboard->size = 0;
}
static void clipboard_state(struct native_clipboard *clipboard, bool available) {
    clipboard_stop_read(clipboard);
    clipboard->available = available;
    if (!available) clipboard->size = 0;
    if (clipboard->connection && clipboard->window)
        emit(clipboard->probe, "clipboard-state %" PRIu64 " %" PRIu64 " %" PRIu64
             " %" PRIu64 " %d\n", clipboard->revision, clipboard->connection,
             clipboard->generation, clipboard->window, available ? (int)clipboard->size : -1);
}
static int clipboard_read_timeout(void *data) {
    clipboard_state(data, false);
    return 0;
}
static int clipboard_readable(int fd, uint32_t mask, void *data) {
    (void)mask;
    struct native_clipboard *clipboard = data;
    /* One bounded read per event-loop turn; no application may block frames. */
    size_t capacity = CLIPBOARD_LIMIT + 1 - clipboard->size;
    if (capacity > 4096) capacity = 4096;
    ssize_t count = read(fd, clipboard->text + clipboard->size, capacity);
    if (count < 0 && (errno == EAGAIN || errno == EINTR)) return 0;
    if (count < 0) { clipboard_state(clipboard, false); return 0; }
    if (count == 0) { clipboard_state(clipboard, true); return 0; }
    clipboard->size += (size_t)count;
    if (clipboard->size > CLIPBOARD_LIMIT) clipboard_state(clipboard, false);
    return 0;
}
static void clipboard_writer_close(struct clipboard_writer *writer) {
    if (writer->event) wl_event_source_remove(writer->event);
    if (writer->timer) wl_event_source_remove(writer->timer);
    close(writer->fd);
    wl_list_remove(&writer->link);
    writer->clipboard->writer_count--;
    free(writer);
}
static int clipboard_write_timeout(void *data) {
    clipboard_writer_close(data);
    return 0;
}
static int clipboard_writable(int fd, uint32_t mask, void *data) {
    struct clipboard_writer *writer = data;
    if (mask & (WL_EVENT_HANGUP | WL_EVENT_ERROR)) { clipboard_writer_close(writer); return 0; }
    ssize_t count = write(fd, writer->text + writer->offset, writer->size - writer->offset);
    if (count < 0 && (errno == EAGAIN || errno == EINTR)) return 0;
    if (count <= 0) { clipboard_writer_close(writer); return 0; }
    writer->offset += (size_t)count;
    if (writer->offset == writer->size) clipboard_writer_close(writer);
    return 0;
}
static bool clipboard_mime(const char *mime) {
    return mime && (!strcmp(mime, "text/plain;charset=utf-8") ||
                    !strcmp(mime, "UTF8_STRING") || !strcmp(mime, "text/plain"));
}
static void clipboard_accept(struct weston_data_source *source, uint32_t serial, const char *mime) {
    (void)source; (void)serial; (void)mime;
}
static void clipboard_send(struct weston_data_source *source, const char *mime, int32_t fd) {
    struct clipboard_offer *offer = wl_container_of(source, offer, base);
    struct native_clipboard *clipboard = offer->clipboard;
    int flags = fcntl(fd, F_GETFL);
    if (!clipboard_mime(mime) || !offer->size || clipboard->writer_count >= 16 ||
        flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) { close(fd); return; }
    struct clipboard_writer *writer = calloc(1, sizeof *writer + offer->size);
    if (!writer) { close(fd); return; }
    writer->clipboard = clipboard; writer->fd = fd; writer->size = offer->size;
    memcpy(writer->text, offer->text, offer->size);
    wl_list_insert(&clipboard->writers, &writer->link);
    clipboard->writer_count++;
    writer->event = wl_event_loop_add_fd(clipboard_loop(clipboard), fd, WL_EVENT_WRITABLE,
                                       clipboard_writable, writer);
    writer->timer = wl_event_loop_add_timer(clipboard_loop(clipboard), clipboard_write_timeout, writer);
    if (!writer->event || !writer->timer) { clipboard_writer_close(writer); return; }
    wl_event_source_timer_update(writer->timer, 3000);
}
static void clipboard_offer_destroy(void *data) {
    struct clipboard_offer *offer = data;
    offer->retirement = NULL;
    wl_signal_emit(&offer->base.destroy_signal, &offer->base);
    char **mime;
    wl_array_for_each(mime, &offer->base.mime_types) free(*mime);
    wl_array_release(&offer->base.mime_types);
    wl_list_remove(&offer->link);
    free(offer);
}
static void clipboard_cancel(struct weston_data_source *source) {
    struct clipboard_offer *offer = wl_container_of(source, offer, base);
    /* libweston removes its selection listener after cancel returns. Retire at
     * the next dispatch boundary, never free that listener during cancellation. */
    if (!offer->retirement)
        offer->retirement = wl_event_loop_add_idle(clipboard_loop(offer->clipboard),
                                                 clipboard_offer_destroy, offer);
}
static void clipboard_capture(struct native_clipboard *clipboard) {
    struct probe *p = clipboard->probe;
    clipboard_stop_read(clipboard);
    clipboard->revision++;
    clipboard->connection = p->connection;
    clipboard->generation = p->scene;
    clipboard->window = input_window(p);
    clipboard->available = false; clipboard->size = 0;
    if (!clipboard->connection || !clipboard->window) return;
    struct weston_data_source *source = p->seat.selection_data_source;
    if (!source) { clipboard_state(clipboard, true); return; }
    if (source->send == clipboard_send) {
        struct clipboard_offer *offer = wl_container_of(source, offer, base);
        memcpy(clipboard->text, offer->text, offer->size);
        clipboard->size = offer->size;
        clipboard_state(clipboard, true);
        return;
    }
    const char *selected = NULL;
    const char *preferred[] = {"text/plain;charset=utf-8", "UTF8_STRING", "text/plain"};
    for (size_t i = 0; i < sizeof preferred / sizeof preferred[0] && !selected; i++) {
        char **mime;
        wl_array_for_each(mime, &source->mime_types)
            if (!strcmp(*mime, preferred[i])) { selected = *mime; break; }
    }
    int pipefd[2];
    if (!selected || pipe2(pipefd, O_CLOEXEC) < 0) { clipboard_state(clipboard, false); return; }
    if (fcntl(pipefd[0], F_SETFL, O_NONBLOCK) < 0) {
        close(pipefd[0]); close(pipefd[1]); clipboard_state(clipboard, false); return;
    }
    clipboard->fd = pipefd[0];
    clipboard->reader = wl_event_loop_add_fd(clipboard_loop(clipboard), pipefd[0], WL_EVENT_READABLE,
                                           clipboard_readable, clipboard);
    clipboard->timer = wl_event_loop_add_timer(clipboard_loop(clipboard), clipboard_read_timeout, clipboard);
    if (!clipboard->reader || !clipboard->timer) { close(pipefd[1]); clipboard_state(clipboard, false); return; }
    wl_event_source_timer_update(clipboard->timer, 3000);
    source->send(source, selected, pipefd[1]);
}
static void clipboard_selection(struct wl_listener *listener, void *data) {
    (void)data;
    struct native_clipboard *clipboard = wl_container_of(listener, clipboard, selection);
    struct weston_data_source *source = clipboard->probe->seat.selection_data_source;
    if (source && source->send == clipboard_send) {
        /* The viewer already owns this publication. Do not echo it back as a
         * new application copy; explicit attachment sync can still retrieve it. */
        clipboard_pause(clipboard);
        return;
    }
    clipboard_capture(clipboard);
}
static struct native_clipboard *clipboard_create_native(struct probe *p) {
    struct native_clipboard *clipboard = calloc(1, sizeof *clipboard);
    if (!clipboard) return NULL;
    clipboard->probe = p; clipboard->fd = -1;
    wl_list_init(&clipboard->offers); wl_list_init(&clipboard->writers);
    clipboard->selection.notify = clipboard_selection;
    wl_signal_add(&p->seat.selection_signal, &clipboard->selection);
    return clipboard;
}
static void clipboard_destroy_native(struct native_clipboard *clipboard) {
    if (!clipboard) return;
    clipboard_stop_read(clipboard);
    wl_list_remove(&clipboard->selection.link);
    struct weston_data_source *source = clipboard->probe->seat.selection_data_source;
    if (source && source->send == clipboard_send)
        weston_seat_set_selection(&clipboard->probe->seat, NULL,
                                 wl_display_next_serial(clipboard->probe->compositor->wl_display));
    struct clipboard_offer *offer, *next;
    wl_list_for_each_safe(offer, next, &clipboard->offers, link) {
        if (offer->retirement) wl_event_source_remove(offer->retirement);
        clipboard_offer_destroy(offer);
    }
    struct clipboard_writer *writer, *writer_next;
    wl_list_for_each_safe(writer, writer_next, &clipboard->writers, link) clipboard_writer_close(writer);
    free(clipboard);
}
static bool clipboard_publish(struct native_clipboard *clipboard, char *encoded) {
    size_t size = 0;
    if (wl_list_length(&clipboard->offers) >= 64) return false;
    if (strcmp(encoded, "-") && !decode_text(encoded, &size)) return false;
    struct clipboard_offer *offer = calloc(1, sizeof *offer + size);
    if (!offer) return false;
    offer->clipboard = clipboard; offer->size = size;
    memcpy(offer->text, encoded, size);
    wl_signal_init(&offer->base.destroy_signal);
    wl_array_init(&offer->base.mime_types);
    wl_list_insert(&clipboard->offers, &offer->link);
    const char *names[] = {"text/plain;charset=utf-8", "UTF8_STRING", "text/plain"};
    for (size_t i = 0; i < sizeof names / sizeof names[0]; i++) {
        char **mime = wl_array_add(&offer->base.mime_types, sizeof *mime);
        if (!mime) { clipboard_offer_destroy(offer); return false; }
        *mime = strdup(names[i]);
        if (!*mime) { clipboard_offer_destroy(offer); return false; }
    }
    offer->base.accept = clipboard_accept; offer->base.send = clipboard_send; offer->base.cancel = clipboard_cancel;
    weston_seat_set_selection(&clipboard->probe->seat, &offer->base,
                             wl_display_next_serial(clipboard->probe->compositor->wl_display));
    if (clipboard->probe->seat.selection_data_source != &offer->base) {
        clipboard_offer_destroy(offer);
        return false;
    }
    return true;
}
static void clipboard_read_chunk(struct native_clipboard *clipboard, uint64_t revision, size_t offset) {
    char encoded[2049];
    strcpy(encoded, "-");
    if (clipboard->available && clipboard->revision == revision && offset < clipboard->size &&
        clipboard->connection == clipboard->probe->connection) {
        size_t length = clipboard->size - offset;
        if (length > 1024) length = 1024;
        for (size_t i = 0; i < length; i++) {
            unsigned char byte = clipboard->text[offset + i];
            encoded[2 * i] = "0123456789abcdef"[byte >> 4];
            encoded[2 * i + 1] = "0123456789abcdef"[byte & 15];
        }
        encoded[2 * length] = 0;
    }
    emit(clipboard->probe, "clipboard-data %" PRIu64 " %zu %s\n", revision, offset, encoded);
}
