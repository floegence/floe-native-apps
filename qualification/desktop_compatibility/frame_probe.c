/* Unpublished demand-driven output capture fixture. One reusable SHM buffer,
 * one capture request and one response are permitted at a time. The inherited
 * socket is private to the owning test; production authentication is separate.
 * Uses the unmodified Weston 13 MIT-licensed output capture protocol. */
#define _GNU_SOURCE
#include <wayland-client.h>
#include <drm_fourcc.h>
#include <errno.h>
#include <stdint.h>
#include <png.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <unistd.h>
#include "weston-output-capture-client.h"

struct capture {
    struct wl_display *display;
    struct wl_shm *shm;
    struct wl_output *output;
    struct weston_capture_v1 *factory;
    struct weston_capture_source_v1 *source;
    struct wl_buffer *buffer;
    void *pixels;
    size_t size;
    int width, height, buffer_width, buffer_height;
    uint32_t format, buffer_format;
    int result;
    unsigned char *encoded;
    size_t encoded_size, encoded_capacity;
};

#define MAX_FRAME_BYTES (4096u * 4096u * 4u)
#define FRAME_PNG 0x20474e50u

static void png_failed(png_structp png, png_const_charp message) {
    (void)message;
    png_longjmp(png, 1);
}
static void png_warning_ignored(png_structp png, png_const_charp message) { (void)png; (void)message; }
static void png_append(png_structp png, png_bytep bytes, png_size_t length) {
    struct capture *c = png_get_io_ptr(png);
    if (length > MAX_FRAME_BYTES - c->encoded_size) png_error(png, "Frame exceeds limit");
    size_t needed = c->encoded_size + length;
    if (needed > c->encoded_capacity) {
        size_t capacity = c->encoded_capacity ? c->encoded_capacity : 65536;
        while (capacity < needed) capacity *= 2;
        if (capacity > MAX_FRAME_BYTES) capacity = MAX_FRAME_BYTES;
        unsigned char *next = realloc(c->encoded, capacity);
        if (!next) png_error(png, "Frame allocation failed");
        c->encoded = next; c->encoded_capacity = capacity;
    }
    memcpy(c->encoded + c->encoded_size, bytes, length);
    c->encoded_size += length;
}
static void png_flush_ignored(png_structp png) { (void)png; }
static int encode_frame(struct capture *c) {
    c->encoded_size = 0;
    png_structp png = png_create_write_struct(PNG_LIBPNG_VER_STRING, NULL, png_failed, png_warning_ignored);
    if (!png) return -1;
    png_infop info = png_create_info_struct(png);
    if (!info || setjmp(png_jmpbuf(png))) {
        png_destroy_write_struct(&png, &info);
        c->encoded_size = 0;
        return -1;
    }
    png_set_write_fn(png, c, png_append, png_flush_ignored);
    png_set_IHDR(png, info, c->buffer_width, c->buffer_height, 8, PNG_COLOR_TYPE_RGB,
                 PNG_INTERLACE_NONE, PNG_COMPRESSION_TYPE_BASE, PNG_FILTER_TYPE_BASE);
    png_set_compression_level(png, 1);
    png_set_filter(png, PNG_FILTER_TYPE_BASE, PNG_FILTER_NONE);
    png_write_info(png, info);
    /* The compositor paints an opaque session background. Drop the framebuffer
     * padding/alpha byte; libpng owns conversion and lossless encoding. Native
     * qualified amd64/arm64 framebuffers both use little-endian BGRX/BGRA. */
    png_set_bgr(png);
    png_set_filler(png, 0, PNG_FILLER_AFTER);
    for (int row = 0; row < c->buffer_height; row++)
        png_write_row(png, (png_bytep)c->pixels + (size_t)row * c->buffer_width * 4);
    png_write_end(png, info);
    png_destroy_write_struct(&png, &info);
    return 0;
}

static void format(void *data, struct weston_capture_source_v1 *source, uint32_t value) {
    (void)source;
    ((struct capture *)data)->format = value;
}
static void size(void *data, struct weston_capture_source_v1 *source, int32_t width, int32_t height) {
    (void)source;
    struct capture *c = data;
    c->width = width; c->height = height;
}
static void complete(void *data, struct weston_capture_source_v1 *source) {
    (void)source;
    ((struct capture *)data)->result = 1;
}
static void retry(void *data, struct weston_capture_source_v1 *source) {
    (void)source;
    ((struct capture *)data)->result = 2;
}
static void failed(void *data, struct weston_capture_source_v1 *source, const char *message) {
    (void)source; (void)message;
    ((struct capture *)data)->result = 3;
}
static const struct weston_capture_source_v1_listener listener = {
    .format = format, .size = size, .complete = complete, .retry = retry, .failed = failed,
};

static void global(void *data, struct wl_registry *registry, uint32_t name, const char *interface, uint32_t version) {
    (void)version;
    struct capture *c = data;
    if (!strcmp(interface, "wl_shm"))
        c->shm = wl_registry_bind(registry, name, &wl_shm_interface, 1);
    else if (!strcmp(interface, "wl_output") && !c->output)
        c->output = wl_registry_bind(registry, name, &wl_output_interface, 1);
    else if (!strcmp(interface, "weston_capture_v1"))
        c->factory = wl_registry_bind(registry, name, &weston_capture_v1_interface, 1);
}
static void removed(void *data, struct wl_registry *registry, uint32_t name) {
    (void)data; (void)registry; (void)name;
}
static const struct wl_registry_listener registry_listener = { .global = global, .global_remove = removed };

static void clear_buffer(struct capture *c) {
    if (c->buffer) wl_buffer_destroy(c->buffer);
    if (c->pixels) munmap(c->pixels, c->size);
    c->buffer = NULL; c->pixels = NULL; c->size = 0;
}
static int prepare_buffer(struct capture *c) {
    if (c->width <= 0 || c->height <= 0 || c->width > 4096 || c->height > 4096 ||
        (c->format != DRM_FORMAT_XRGB8888 && c->format != DRM_FORMAT_ARGB8888)) return -1;
    if (c->buffer && c->buffer_width == c->width && c->buffer_height == c->height &&
        c->buffer_format == c->format) return 0;
    clear_buffer(c);
    c->size = (size_t)c->width * (size_t)c->height * 4;
    int fd = memfd_create("floe-frame-fixture", MFD_CLOEXEC);
    if (fd < 0) return -1;
    if (ftruncate(fd, (off_t)c->size) < 0) { close(fd); return -1; }
    c->pixels = mmap(NULL, c->size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (c->pixels == MAP_FAILED) { c->pixels = NULL; close(fd); return -1; }
    struct wl_shm_pool *pool = wl_shm_create_pool(c->shm, fd, (int)c->size);
    close(fd);
    c->buffer = wl_shm_pool_create_buffer(pool, 0, c->width, c->height, c->width * 4,
        c->format == DRM_FORMAT_XRGB8888 ? WL_SHM_FORMAT_XRGB8888 : WL_SHM_FORMAT_ARGB8888);
    wl_shm_pool_destroy(pool);
    c->buffer_width = c->width; c->buffer_height = c->height; c->buffer_format = c->format;
    return c->buffer ? 0 : -1;
}
static int receive(int fd, void *data, size_t length) {
    size_t offset = 0;
    while (offset < length) {
        ssize_t count = read(fd, (char *)data + offset, length - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return -1;
        offset += (size_t)count;
    }
    return 0;
}
static int send_all(int fd, const void *data, size_t length) {
    size_t offset = 0;
    while (offset < length) {
        ssize_t count = send(fd, (const char *)data + offset, length - offset, MSG_NOSIGNAL);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return -1;
        offset += (size_t)count;
    }
    return 0;
}

int main(void) {
    const char *value = getenv("FLOE_PROBE_FRAME_FD");
    if (!value) return 2;
    int fd = atoi(value), status = 1;
    struct capture c = {0};
    c.display = wl_display_connect(NULL);
    if (!c.display) return 3;
    struct wl_registry *registry = wl_display_get_registry(c.display);
    wl_registry_add_listener(registry, &registry_listener, &c);
    if (wl_display_roundtrip(c.display) < 0 || !c.shm || !c.output || !c.factory) goto done;
    c.source = weston_capture_v1_create(c.factory, c.output, WESTON_CAPTURE_V1_SOURCE_FRAMEBUFFER);
    weston_capture_source_v1_add_listener(c.source, &listener, &c);
    if (wl_display_roundtrip(c.display) < 0) goto done;
    uint32_t previous = 0, sequence;
    /* Fixture ABI uses native-endian uint32 fields; it never crosses a host
     * boundary. Each request follows consumption of the previous full frame. */
    while (receive(fd, &sequence, sizeof sequence) == 0) {
        if (!sequence || sequence <= previous || prepare_buffer(&c) < 0) goto done;
        previous = sequence;
        c.result = 0;
        weston_capture_source_v1_capture(c.source, c.buffer);
        while (!c.result)
            if (wl_display_dispatch(c.display) < 0) goto done;
        if (c.result == 1 && encode_frame(&c) < 0) c.result = 3;
        uint32_t header[] = {sequence, (uint32_t)c.result, (uint32_t)c.buffer_width,
            (uint32_t)c.buffer_height, FRAME_PNG, c.result == 1 ? (uint32_t)c.encoded_size : 0};
        if (send_all(fd, header, sizeof header) < 0) break;
        if (c.result == 1 && send_all(fd, c.encoded, c.encoded_size) < 0) break;
    }
    status = 0;
done:
    clear_buffer(&c);
    free(c.encoded);
    if (c.source) weston_capture_source_v1_destroy(c.source);
    if (c.factory) weston_capture_v1_destroy(c.factory);
    if (c.output) wl_output_destroy(c.output);
    if (c.shm) wl_shm_destroy(c.shm);
    wl_registry_destroy(registry);
    wl_display_disconnect(c.display);
    close(fd);
    return status;
}
