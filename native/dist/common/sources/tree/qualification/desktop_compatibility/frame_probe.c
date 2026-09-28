/* Unpublished demand-driven output capture fixture. One reusable SHM buffer,
 * one capture request and one response are permitted at a time. The inherited
 * socket is private to the owning test; production authentication is separate.
 * Uses the unmodified Weston 13 MIT-licensed output capture protocol. */
#define _GNU_SOURCE
#include <wayland-client.h>
#include <drm_fourcc.h>
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>
#include <png.h>
#include <stdio.h>
#include <jpeglib.h>
#include <webp/encode.h>
#include <setjmp.h>
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
    unsigned char *previous;
    size_t previous_size;
    int x, y, region_width, region_height;
    uint32_t encoding;
};

#define MAX_FRAME_BYTES (4096u * 4096u * 4u)
#define FRAME_PNG 0x20474e50u
#define FRAME_JPEG 0x4745504au
#define FRAME_WEBP 0x50424557u

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
static int encode_png(struct capture *c) {
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
    png_set_IHDR(png, info, c->region_width, c->region_height, 8, PNG_COLOR_TYPE_RGB,
                 PNG_INTERLACE_NONE, PNG_COMPRESSION_TYPE_BASE, PNG_FILTER_TYPE_BASE);
    png_set_compression_level(png, 1);
    png_set_filter(png, PNG_FILTER_TYPE_BASE, PNG_FILTER_SUB);
    png_write_info(png, info);
    /* The compositor paints an opaque session background. Drop the framebuffer
     * padding/alpha byte; libpng owns conversion and lossless encoding. Native
     * qualified amd64/arm64 framebuffers both use little-endian BGRX/BGRA. */
    png_set_bgr(png);
    png_set_filler(png, 0, PNG_FILLER_AFTER);
    for (int row = c->y; row < c->y + c->region_height; row++)
        png_write_row(png, (png_bytep)c->pixels + ((size_t)row * c->buffer_width + c->x) * 4);
    png_write_end(png, info);
    png_destroy_write_struct(&png, &info);
    return 0;
}

struct jpeg_failure { struct jpeg_error_mgr error; jmp_buf jump; };
static void jpeg_failed(j_common_ptr jpeg) {
    struct jpeg_failure *error = (struct jpeg_failure *)jpeg->err;
    longjmp(error->jump, 1);
}
static int encode_jpeg(struct capture *c, int quality) {
    struct jpeg_compress_struct jpeg = {0};
    struct jpeg_failure error;
    unsigned char *bytes = NULL;
    unsigned long length = 0;
    jpeg.err = jpeg_std_error(&error.error);
    error.error.error_exit = jpeg_failed;
    if (setjmp(error.jump)) {
        jpeg_destroy_compress(&jpeg);
        free(bytes);
        return -1;
    }
    jpeg_create_compress(&jpeg);
    jpeg_mem_dest(&jpeg, &bytes, &length);
    jpeg.image_width = c->region_width; jpeg.image_height = c->region_height;
    jpeg.input_components = 4; jpeg.in_color_space = JCS_EXT_BGRX;
    jpeg_set_defaults(&jpeg);
    /* Preserve colored text edges; motion efficiency comes from native SIMD
     * compression and damage regions, not chroma smearing. */
    for (int i = 0; i < 3; i++) jpeg.comp_info[i].h_samp_factor = jpeg.comp_info[i].v_samp_factor = 1;
    jpeg_set_quality(&jpeg, quality, TRUE);
    jpeg.dct_method = JDCT_FASTEST;
    jpeg_start_compress(&jpeg, TRUE);
    while (jpeg.next_scanline < jpeg.image_height) {
        JSAMPROW row = (unsigned char *)c->pixels +
            ((size_t)(c->y + jpeg.next_scanline) * c->buffer_width + c->x) * 4;
        jpeg_write_scanlines(&jpeg, &row, 1);
    }
    jpeg_finish_compress(&jpeg);
    jpeg_destroy_compress(&jpeg);
    if (!length || length > MAX_FRAME_BYTES) { free(bytes); return -1; }
    free(c->encoded);
    c->encoded = bytes; c->encoded_size = c->encoded_capacity = length;
    return 0;
}
/* Lossless WebP retains text exactly and reuses repeated glyphs across a
 * region. The low effort preset bounds encoding latency for desktop updates. */
static int webp_append(const uint8_t *bytes, size_t length, const WebPPicture *picture) {
    struct capture *c = picture->custom_ptr;
    if (length > MAX_FRAME_BYTES - c->encoded_size) return 0;
    size_t needed = c->encoded_size + length;
    if (needed > c->encoded_capacity) {
        size_t capacity = c->encoded_capacity ? c->encoded_capacity : 65536;
        while (capacity < needed) capacity *= 2;
        if (capacity > MAX_FRAME_BYTES) capacity = MAX_FRAME_BYTES;
        unsigned char *next = realloc(c->encoded, capacity);
        if (!next) return 0;
        c->encoded = next; c->encoded_capacity = capacity;
    }
    memcpy(c->encoded + c->encoded_size, bytes, length);
    c->encoded_size += length;
    return 1;
}
static int encode_webp(struct capture *c, int effort) {
    WebPConfig config;
    WebPPicture picture;
    if (!WebPConfigInit(&config) || !WebPPictureInit(&picture)) return -1;
    config.lossless = 1; config.method = effort; config.quality = 80;
    picture.use_argb = 1;
    picture.width = c->region_width; picture.height = c->region_height;
    picture.writer = webp_append; picture.custom_ptr = c;
    c->encoded_size = 0;
    int ok = WebPPictureImportBGRX(&picture,
        (uint8_t *)c->pixels + ((size_t)c->y * c->buffer_width + c->x) * 4,
        c->buffer_width * 4) && WebPEncode(&config, &picture);
    WebPPictureFree(&picture);
    return ok ? 0 : -1;
}
/* The reference is the preceding ordered capture, never an input-authority
 * receipt. A new attachment/scene or discarded capture forces a complete image. */
static int encode_frame(struct capture *c, uint32_t mode, uint32_t flags) {
    bool reset = flags || c->previous_size != c->size || !c->previous;
    int left = c->buffer_width, top = c->buffer_height, right = 0, bottom = 0;
    if (!reset && mode) {
        for (int y = 0; y < c->buffer_height; y++) {
            const uint32_t *row = (const uint32_t *)c->pixels + (size_t)y * c->buffer_width;
            const uint32_t *previous = (const uint32_t *)c->previous + (size_t)y * c->buffer_width;
            for (int x = 0; x < c->buffer_width; x++) {
                if (((row[x] ^ previous[x]) & 0xffffffu) == 0) continue;
                if (x < left) left = x;
                if (x >= right) right = x + 1;
                if (y < top) top = y;
                bottom = y + 1;
            }
        }
        if (right <= left) { c->encoded_size = 0; return 4; }
    } else { left = top = 0; right = c->buffer_width; bottom = c->buffer_height; }
    c->x = left; c->y = top; c->region_width = right-left; c->region_height = bottom-top;
    size_t area = (size_t)c->region_width * c->region_height;
    /* Small edits remain exact. WebP handles repeated text and flat desktop
     * regions efficiently. Only dense imagery in latency-oriented modes tries
     * JPEG; keep the smaller encoding and refine lossy pixels when idle. */
    if (!mode || area <= 65536) {
        c->encoding = FRAME_PNG;
        if (encode_png(c) < 0) return 3;
    } else {
        c->encoding = FRAME_WEBP;
        if (encode_webp(c, mode == 2 || mode == 4 ? 3 : mode == 3 ? 0 : 1) < 0) return 3;
        if ((mode == 1 || mode == 3) && !(flags & 2) && c->encoded_size > area / 6) {
            size_t lossless_size = c->encoded_size;
            unsigned char *lossless = c->encoded;
            c->encoded = NULL; c->encoded_capacity = c->encoded_size = 0;
            if (encode_jpeg(c, mode == 3 ? 65 : 80) < 0) { free(lossless); return 3; }
            if (c->encoded_size < lossless_size) {
                c->encoding = FRAME_JPEG; free(lossless);
            } else {
                free(c->encoded); c->encoded = lossless;
                c->encoded_size = c->encoded_capacity = lossless_size;
            }
        }
    }
    if (c->previous_size != c->size) {
        unsigned char *next = realloc(c->previous, c->size);
        if (!next) return 3;
        c->previous = next; c->previous_size = c->size;
    }
    memcpy(c->previous, c->pixels, c->size);
    return 1;
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
    uint32_t previous = 0, request[3];
    /* Fixture ABI uses native-endian uint32 fields; it never crosses a host
     * boundary. Each request follows consumption of the previous full frame. */
    while (receive(fd, request, sizeof request) == 0) {
        uint32_t sequence = request[0], mode = request[1], flags = request[2];
        if (mode > 4 || flags > 3) goto done;
        /* Consume the output size announced before the helper's scene barrier
         * before allocating the next buffer. No cached-size retry loop. */
        if (wl_display_roundtrip(c.display) < 0) goto done;
        if (!sequence || sequence <= previous || prepare_buffer(&c) < 0) goto done;
        previous = sequence;
        c.result = 0;
        weston_capture_source_v1_capture(c.source, c.buffer);
        while (!c.result)
            if (wl_display_dispatch(c.display) < 0) goto done;
        if (c.result == 1) c.result = encode_frame(&c, mode, flags);
        uint32_t header[] = {sequence, (uint32_t)c.result, (uint32_t)c.buffer_width,
            (uint32_t)c.buffer_height, c.encoding, c.result == 1 ? (uint32_t)c.encoded_size : 0,
            (uint32_t)c.x, (uint32_t)c.y, (uint32_t)c.region_width, (uint32_t)c.region_height};
        if (send_all(fd, header, sizeof header) < 0) break;
        if (c.result == 1 && send_all(fd, c.encoded, c.encoded_size) < 0) break;
    }
    status = 0;
done:
    clear_buffer(&c);
    free(c.encoded);
    free(c.previous);
    if (c.source) weston_capture_source_v1_destroy(c.source);
    if (c.factory) weston_capture_v1_destroy(c.factory);
    if (c.output) wl_output_destroy(c.output);
    if (c.shm) wl_shm_destroy(c.shm);
    wl_registry_destroy(registry);
    wl_display_disconnect(c.display);
    close(fd);
    return status;
}
