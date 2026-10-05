/* SPDX-FileCopyrightText: Copyright 2018 Wim Taymans
 * SPDX-FileCopyrightText: Copyright 2026 Floegence
 * SPDX-License-Identifier: MIT
 * Adapted from PipeWire's src/examples/video-src.c. Synthetic pixels only.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <sys/mman.h>
#include <unistd.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <spa/param/video/format-utils.h>
#include <pipewire/pipewire.h>

struct fixture {
    struct pw_main_loop *loop;
    struct pw_stream *stream;
    struct spa_hook listener;
    struct spa_source *timer;
    struct spa_video_info_raw format;
    uint64_t sequence;
    bool memfd;
    bool failed;
};

static void process(void *userdata) {
    struct fixture *f = userdata;
    struct pw_buffer *b = pw_stream_dequeue_buffer(f->stream);
    if (!b) return;
    struct spa_buffer *buffer = b->buffer;
    uint32_t width = f->format.size.width, height = f->format.size.height;
    uint32_t *pixels = buffer->datas[0].data;
    if (!pixels) { pw_stream_queue_buffer(f->stream, b); return; }
    uint64_t sequence = f->sequence++;
    struct spa_meta_header *header = spa_buffer_find_meta_data(buffer, SPA_META_Header, sizeof(*header));
    if (header) { memset(header, 0, sizeof(*header)); header->seq = sequence; }
    struct spa_meta *damage = spa_buffer_find_meta(buffer, SPA_META_VideoDamage);
    if (damage) {
        memset(damage->data, 0, damage->size);
        struct spa_meta_region *r = damage->data;
        if (!sequence) r->region = SPA_REGION(0, 0, width, height);
    }
    /* Constant background: cursor animation must never contaminate control pixels. */
    for (uint32_t i = 0; i < width * height; i++) pixels[i] = 0xff404040;
    struct spa_meta_cursor *cursor = spa_buffer_find_meta_data(buffer, SPA_META_Cursor,
        sizeof(*cursor) + sizeof(struct spa_meta_bitmap) + 16 * 16 * 4);
    if (cursor) {
        memset(cursor, 0, sizeof(*cursor));
        cursor->id = 1;
        cursor->position = SPA_POINT(20 + sequence % 80, 40);
        cursor->bitmap_offset = sizeof(*cursor);
        struct spa_meta_bitmap *bitmap = SPA_PTROFF(cursor, cursor->bitmap_offset, struct spa_meta_bitmap);
        bitmap->format = SPA_VIDEO_FORMAT_BGRA;
        bitmap->size = SPA_RECTANGLE(16, 16);
        bitmap->stride = 64;
        bitmap->offset = sizeof(*bitmap);
        uint32_t *shape = SPA_PTROFF(bitmap, bitmap->offset, uint32_t);
        for (int i = 0; i < 256; i++) shape[i] = sequence % 2 ? 0xff00ff00 : 0xffff0000;
    }
    buffer->datas[0].chunk->offset = 0;
    buffer->datas[0].chunk->stride = width * 4;
    buffer->datas[0].chunk->size = width * height * 4;
    pw_stream_queue_buffer(f->stream, b);
}

static void tick(void *userdata, uint64_t expirations) {
    struct fixture *f = userdata;
    pw_stream_trigger_process(f->stream);
}

static void state(void *userdata, enum pw_stream_state old, enum pw_stream_state current, const char *error) {
    struct fixture *f = userdata;
    if (current == PW_STREAM_STATE_STREAMING) {
        struct timespec start = {0, 1}, interval = {0, 40 * SPA_NSEC_PER_MSEC};
        pw_loop_update_timer(pw_main_loop_get_loop(f->loop), f->timer, &start, &interval, false);
    } else {
        pw_loop_update_timer(pw_main_loop_get_loop(f->loop), f->timer, NULL, NULL, false);
    }
    if (current == PW_STREAM_STATE_PAUSED) printf("%u\n", pw_stream_get_node_id(f->stream));
    if (current == PW_STREAM_STATE_ERROR) {
        fprintf(stderr, "%s\n", error);
        pw_main_loop_quit(f->loop);
    }
}

static void format(void *userdata, uint32_t id, const struct spa_pod *param) {
    struct fixture *f = userdata;
    if (id != SPA_PARAM_Format || !param) return;
    if (spa_format_video_raw_parse(param, &f->format) < 0) return;
    f->sequence = 0;
    uint8_t bytes[1024];
    struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(bytes, sizeof(bytes));
    const struct spa_pod *params[4];
    params[0] = spa_pod_builder_add_object(&builder, SPA_TYPE_OBJECT_ParamBuffers, SPA_PARAM_Buffers,
        SPA_PARAM_BUFFERS_buffers, SPA_POD_CHOICE_RANGE_Int(4, 2, 8),
        SPA_PARAM_BUFFERS_blocks, SPA_POD_Int(1),
        SPA_PARAM_BUFFERS_size, SPA_POD_Int(f->format.size.width * f->format.size.height * 4),
        SPA_PARAM_BUFFERS_stride, SPA_POD_Int(f->format.size.width * 4),
        SPA_PARAM_BUFFERS_dataType, SPA_POD_CHOICE_FLAGS_Int(1 << SPA_DATA_MemFd));
    params[1] = spa_pod_builder_add_object(&builder, SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta,
        SPA_PARAM_META_type, SPA_POD_Id(SPA_META_Header),
        SPA_PARAM_META_size, SPA_POD_Int(sizeof(struct spa_meta_header)));
    params[2] = spa_pod_builder_add_object(&builder, SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta,
        SPA_PARAM_META_type, SPA_POD_Id(SPA_META_VideoDamage),
        SPA_PARAM_META_size, SPA_POD_Int(sizeof(struct spa_meta_region) * 16));
    params[3] = spa_pod_builder_add_object(&builder, SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta,
        SPA_PARAM_META_type, SPA_POD_Id(SPA_META_Cursor),
        SPA_PARAM_META_size, /* Match Mutter 46's fixed cursor metadata allocation for producer-owned buffers. */
        SPA_POD_Int(sizeof(struct spa_meta_cursor) + sizeof(struct spa_meta_bitmap) +
            (f->memfd ? 384 * 384 : 16 * 16) * 4));
    pw_stream_update_params(f->stream, params, 4);
}

static void add_buffer(void *userdata, struct pw_buffer *b) {
    struct fixture *f = userdata;
    if (!f->memfd) return;
    struct spa_data *data = &b->buffer->datas[0];
    data->type = SPA_DATA_MemFd;
    /* Reproduce older compositors: READWRITE without MAPPABLE. */
    data->flags = SPA_DATA_FLAG_READWRITE;
    data->mapoffset = 0;
    data->maxsize = f->format.size.width * f->format.size.height * 4;
    data->data = NULL;
    data->fd = memfd_create("floe-qualification-pixels", MFD_CLOEXEC);
    if (data->fd < 0 || ftruncate(data->fd, data->maxsize) < 0) goto error;
    data->data = mmap(NULL, data->maxsize, PROT_READ | PROT_WRITE, MAP_SHARED, data->fd, 0);
    if (data->data == MAP_FAILED) { data->data = NULL; goto error; }
    return;
error:
    pw_stream_set_error(f->stream, -errno, "Synthetic buffer allocation failed");
    f->failed = true;
    pw_main_loop_quit(f->loop);
}

static void remove_buffer(void *userdata, struct pw_buffer *b) {
    if (!((struct fixture *)userdata)->memfd) return;
    struct spa_data *data = &b->buffer->datas[0];
    if (data->data) munmap(data->data, data->maxsize);
    if (data->fd >= 0) close(data->fd);
}

static void quit(void *userdata, int signal) {
    pw_main_loop_quit(((struct fixture *)userdata)->loop);
}

int main(int argc, char **argv) {
    setbuf(stdout, NULL);
    pw_init(&argc, &argv);
    struct fixture f = {.memfd = argc == 2 && strcmp(argv[1], "--memfd") == 0};
    if (argc > 1 && !f.memfd) return 2;
    f.loop = pw_main_loop_new(NULL);
    struct pw_loop *loop = pw_main_loop_get_loop(f.loop);
    f.timer = pw_loop_add_timer(loop, tick, &f);
    pw_loop_add_signal(loop, SIGINT, quit, &f);
    pw_loop_add_signal(loop, SIGTERM, quit, &f);
    struct pw_context *context = pw_context_new(loop, NULL, 0);
    struct pw_core *core = pw_context_connect(context, NULL, 0);
    if (!core) return 1;
    f.stream = pw_stream_new(core, "floe-qualification-source", pw_properties_new(
        PW_KEY_NODE_NAME, "floe-qualification-source", PW_KEY_MEDIA_CLASS, "Video/Source", NULL));
    const struct pw_stream_events events = {.version = PW_VERSION_STREAM_EVENTS,
        .process = process, .state_changed = state, .param_changed = format,
        .add_buffer = add_buffer, .remove_buffer = remove_buffer};
    pw_stream_add_listener(f.stream, &f.listener, &events, &f);
    uint8_t bytes[1024];
    struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(bytes, sizeof(bytes));
    const struct spa_pod *param = spa_pod_builder_add_object(&builder, SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat,
        SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video),
        SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
        SPA_FORMAT_VIDEO_format, SPA_POD_Id(SPA_VIDEO_FORMAT_BGRA),
        SPA_FORMAT_VIDEO_size, SPA_POD_Rectangle(&SPA_RECTANGLE(320, 240)),
        SPA_FORMAT_VIDEO_framerate, SPA_POD_Fraction(&SPA_FRACTION(0, 1)));
    if (pw_stream_connect(f.stream, PW_DIRECTION_OUTPUT, PW_ID_ANY,
        PW_STREAM_FLAG_DRIVER | (f.memfd ? PW_STREAM_FLAG_ALLOC_BUFFERS : PW_STREAM_FLAG_MAP_BUFFERS), &param, 1) < 0) return 1;
    pw_main_loop_run(f.loop);
    pw_stream_destroy(f.stream);
    pw_core_disconnect(core);
    pw_context_destroy(context);
    pw_main_loop_destroy(f.loop);
    pw_deinit();
    return f.failed ? 1 : 0;
}
