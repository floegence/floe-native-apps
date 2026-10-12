/* SPDX-License-Identifier: MIT
 * Private Rockchip hardware encoder. The media owner supplies one packed NV12
 * frame per request; this process has no display or input authority. Only the
 * installed driver implements MPP. Synthetic and actual-size encodes qualify
 * it.
 */
#define _GNU_SOURCE
#include "mpp_buffer.h"
#include "mpp_frame.h"
#include "mpp_packet.h"
#include "native_pixels.h"
#include "rk_mpi.h"
#include "rk_venc_cfg.h"
#include <arpa/inet.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define LIMIT (64u << 20)
static int transfer(int fd, void *data, size_t size, int writing) {
  uint8_t *p = data;
  while (size) {
    ssize_t n = writing ? write(fd, p, size) : read(fd, p, size);
    if (n < 0 && errno == EINTR)
      continue;
    if (n == 0)
      return writing || p != (uint8_t *)data ? -1 : 1;
    if (n < 0)
      return -1;
    p += n;
    size -= (size_t)n;
  }
  return 0;
}
#define CHECK(c)                                                               \
  do {                                                                         \
    int e = (c);                                                               \
    if (e) {                                                                   \
      fprintf(stderr, "MPP %s: %d\n", #c, e);                                  \
      goto cleanup;                                                            \
    }                                                                          \
  } while (0)
#define LOAD(n)                                                                \
  do {                                                                         \
    *(void **)(&p_##n) = dlsym(library, #n);                                   \
    if (!p_##n) {                                                              \
      fprintf(stderr, "Missing %s\n", #n);                                     \
      goto cleanup;                                                            \
    }                                                                          \
  } while (0)
static __typeof__(&mpp_create) p_mpp_create;
static __typeof__(&mpp_init) p_mpp_init;
static __typeof__(&mpp_destroy) p_mpp_destroy;
static __typeof__(&mpp_enc_cfg_init) p_mpp_enc_cfg_init;
static __typeof__(&mpp_enc_cfg_deinit) p_mpp_enc_cfg_deinit;
static __typeof__(&mpp_enc_cfg_set_s32) p_mpp_enc_cfg_set_s32;
static __typeof__(&mpp_buffer_group_get) p_mpp_buffer_group_get;
static __typeof__(&mpp_buffer_group_put) p_mpp_buffer_group_put;
static __typeof__(&mpp_buffer_get_with_tag) p_mpp_buffer_get_with_tag;
static __typeof__(&mpp_buffer_put_with_caller) p_mpp_buffer_put_with_caller;
static __typeof__(&mpp_buffer_get_ptr_with_caller)
    p_mpp_buffer_get_ptr_with_caller;
static __typeof__(&mpp_buffer_sync_begin_f) p_mpp_buffer_sync_begin_f;
static __typeof__(&mpp_buffer_sync_end_f) p_mpp_buffer_sync_end_f;
static __typeof__(&mpp_frame_init) p_mpp_frame_init;
static __typeof__(&mpp_frame_deinit) p_mpp_frame_deinit;
static __typeof__(&mpp_frame_set_width) p_mpp_frame_set_width;
static __typeof__(&mpp_frame_set_height) p_mpp_frame_set_height;
static __typeof__(&mpp_frame_set_hor_stride) p_mpp_frame_set_hor_stride;
static __typeof__(&mpp_frame_set_ver_stride) p_mpp_frame_set_ver_stride;
static __typeof__(&mpp_frame_set_fmt) p_mpp_frame_set_fmt;
static __typeof__(&mpp_frame_set_buffer) p_mpp_frame_set_buffer;
static __typeof__(&mpp_frame_set_pts) p_mpp_frame_set_pts;
static __typeof__(&mpp_packet_get_pos) p_mpp_packet_get_pos;
static __typeof__(&mpp_packet_get_length) p_mpp_packet_get_length;
static __typeof__(&mpp_packet_get_pts) p_mpp_packet_get_pts;
static __typeof__(&mpp_packet_deinit) p_mpp_packet_deinit;
int main(int argc, char **argv) {
  if (argc == 2 && strcmp(argv[1], "--check") == 0)
    return 0;
  if (argc != 3 || strcmp(argv[1], "--pixels") != 0 || geteuid() == 0)
    return 2;
  int pixels_fd = native_pixels_fd(argv[2]);
  if (pixels_fd < 0)
    return 2;
  uint32_t header[5];
  if (transfer(0, header, sizeof(header), 0) || ntohl(header[0]) != 0x46445631)
    return 2;
  uint32_t w = ntohl(header[1]), h = ntohl(header[2]), fps = ntohl(header[3]),
           bitrate = ntohl(header[4]);
  if (w < 2 || h < 2 || w > 8192 || h > 8192 || w % 2 || h % 2 ||
      (uint64_t)w * h > LIMIT / 4 || (fps != 15 && fps != 30 && fps != 60) ||
      bitrate < 1000000 || bitrate > 100000000)
    return 2;
  int result = 1;
  void *library = NULL;
  MppCtx ctx = NULL;
  MppApi *api = NULL;
  MppEncCfg cfg = NULL;
  MppBufferGroup group = NULL;
  MppBuffer buffer = NULL;
  MppFrame frame = NULL;
  MppPacket packet = NULL;
  size_t input_size = (size_t)w * h * 3 / 2;
  uint8_t *input = native_pixels_map(pixels_fd, input_size, PROT_READ);
  if (input == MAP_FAILED)
    return 2;
  library = dlopen("librockchip_mpp.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!library)
    goto cleanup;
  LOAD(mpp_create);
  LOAD(mpp_init);
  LOAD(mpp_destroy);
  LOAD(mpp_enc_cfg_init);
  LOAD(mpp_enc_cfg_deinit);
  LOAD(mpp_enc_cfg_set_s32);
  LOAD(mpp_buffer_group_get);
  LOAD(mpp_buffer_group_put);
  LOAD(mpp_buffer_get_with_tag);
  LOAD(mpp_buffer_put_with_caller);
  LOAD(mpp_buffer_get_ptr_with_caller);
  LOAD(mpp_buffer_sync_begin_f);
  LOAD(mpp_buffer_sync_end_f);
  LOAD(mpp_frame_init);
  LOAD(mpp_frame_deinit);
  LOAD(mpp_frame_set_width);
  LOAD(mpp_frame_set_height);
  LOAD(mpp_frame_set_hor_stride);
  LOAD(mpp_frame_set_ver_stride);
  LOAD(mpp_frame_set_fmt);
  LOAD(mpp_frame_set_buffer);
  LOAD(mpp_frame_set_pts);
  LOAD(mpp_packet_get_pos);
  LOAD(mpp_packet_get_length);
  LOAD(mpp_packet_get_pts);
  LOAD(mpp_packet_deinit);
  CHECK(p_mpp_create(&ctx, &api));
  CHECK(p_mpp_init(ctx, MPP_CTX_ENC, MPP_VIDEO_CodingAVC));
  CHECK(p_mpp_enc_cfg_init(&cfg));
  RK_S64 timeout = 2000;
  CHECK(api->control(ctx, MPP_SET_OUTPUT_TIMEOUT, &timeout));
  uint32_t stride = (w + 15) & ~15u, rows = (h + 15) & ~15u;
#define SET(k, v) CHECK(p_mpp_enc_cfg_set_s32(cfg, k, v))
  SET("prep:width", w);
  SET("prep:height", h);
  SET("prep:hor_stride", stride);
  SET("prep:ver_stride", rows);
  SET("prep:format", MPP_FMT_YUV420SP);
  SET("prep:colorspace", MPP_FRAME_SPC_BT709);
  SET("prep:colorprim", MPP_FRAME_PRI_BT709);
  SET("prep:colortrc", MPP_FRAME_TRC_BT709);
  SET("prep:colorrange", MPP_FRAME_RANGE_MPEG);
  SET("rc:mode", 1);
  SET("rc:bps_target", bitrate);
  SET("rc:bps_max", bitrate * 17 / 16);
  SET("rc:bps_min", bitrate * 15 / 16);
  SET("rc:fps_in_flex", 0);
  SET("rc:fps_in_num", fps);
  SET("rc:fps_in_denom", 1);
  SET("rc:fps_out_flex", 0);
  SET("rc:fps_out_num", fps);
  SET("rc:fps_out_denom", 1);
  SET("rc:gop", 120);
  SET("codec:type", MPP_VIDEO_CodingAVC);
  SET("h264:profile", 100);
  uint32_t macroblocks = ((w + 15) / 16) * ((h + 15) / 16);
  SET("h264:level", macroblocks <= 8192 && macroblocks * fps <= 522240 &&
                            bitrate <= 62500000
                        ? 42
                        : macroblocks <= 36864 && macroblocks * fps <= 2073600
                              ? 52
                              : 62);
  SET("h264:cabac_en", 1);
  SET("h264:cabac_idc", 0);
  SET("h264:trans8x8", 1);
  CHECK(api->control(ctx, MPP_ENC_SET_CFG, cfg));
  MppEncHeaderMode mode = MPP_ENC_HEADER_MODE_EACH_IDR;
  CHECK(api->control(ctx, MPP_ENC_SET_HEADER_MODE, &mode));
  CHECK(p_mpp_buffer_group_get(&group, MPP_BUFFER_TYPE_DRM, MPP_BUFFER_INTERNAL,
                               "floe-mpp", __func__));
  size_t bytes = (size_t)stride * rows * 3 / 2;
  CHECK(p_mpp_buffer_get_with_tag(group, &buffer, bytes, "floe-mpp", __func__));
  uint8_t *target = p_mpp_buffer_get_ptr_with_caller(buffer, __func__);
  if (!target)
    goto cleanup;
  CHECK(p_mpp_frame_init(&frame));
  p_mpp_frame_set_width(frame, w);
  p_mpp_frame_set_height(frame, h);
  p_mpp_frame_set_hor_stride(frame, stride);
  p_mpp_frame_set_ver_stride(frame, rows);
  p_mpp_frame_set_fmt(frame, MPP_FMT_YUV420SP);
  p_mpp_frame_set_buffer(frame, buffer);
  uint32_t sequence = 0;
  for (;;) {
    uint32_t command[2];
    int received = transfer(0, command, 8, 0);
    if (received == 1) {
      result = 0;
      break;
    }
    if (received)
      goto cleanup;
    uint32_t length = ntohl(command[0]), seq = ntohl(command[1]);
    if (length != (uint64_t)w * h * 3 / 2 || seq != sequence + 1 || !seq)
      goto cleanup;
    sequence = seq;
    CHECK(p_mpp_buffer_sync_begin_f(buffer, 0, __func__));
    for (uint32_t y = 0; y < h; y++)
      memcpy(target + (size_t)y * stride, input + (size_t)y * w, w);
    for (uint32_t y = 0; y < h / 2; y++)
      memcpy(target + (size_t)stride * (rows + y), input + (size_t)w * (h + y),
             w);
    CHECK(p_mpp_buffer_sync_end_f(buffer, 0, __func__));
    p_mpp_frame_set_pts(frame, seq);
    CHECK(api->encode_put_frame(ctx, frame));
    CHECK(api->encode_get_packet(ctx, &packet));
    if (!packet || p_mpp_packet_get_pts(packet) != seq)
      goto cleanup;
    size_t out = p_mpp_packet_get_length(packet);
    void *pos = p_mpp_packet_get_pos(packet);
    if (!pos || out == 0 || out > LIMIT)
      goto cleanup;
    uint32_t reply[2] = {htonl((uint32_t)out), htonl(seq)};
    if (transfer(1, reply, 8, 1) || transfer(1, pos, out, 1))
      goto cleanup;
    CHECK(p_mpp_packet_deinit(&packet));
  }
cleanup:
  if (packet)
    p_mpp_packet_deinit(&packet);
  if (frame)
    p_mpp_frame_deinit(&frame);
  if (ctx)
    p_mpp_destroy(ctx);
  if (buffer)
    p_mpp_buffer_put_with_caller(buffer, __func__);
  if (group)
    p_mpp_buffer_group_put(group);
  if (cfg)
    p_mpp_enc_cfg_deinit(cfg);
  munmap(input, input_size);
  if (library)
    dlclose(library);
  return result;
}
