/* SPDX-License-Identifier: MIT */
#define _GNU_SOURCE
#include "native_pixels.h"
#include <assert.h>
#include <string.h>

static int slot(size_t length, int seals) {
  int fd = memfd_create("floe-pixel-test", MFD_CLOEXEC | MFD_ALLOW_SEALING);
  assert(fd >= 3);
  assert(ftruncate(fd, length) == 0);
  if (seals)
    assert(fcntl(fd, F_ADD_SEALS, seals) == 0);
  return fd;
}

int main(void) {
  const int seals = F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_SEAL;
  assert(native_pixels_fd("3") == 3);
  assert(native_pixels_fd("65535") == 65535);
  for (const char **value = (const char *[]){"", "2", "65536", "3x", "999999999999999999999", NULL}; *value; value++)
    assert(native_pixels_fd(*value) == -1);
  int fd = slot(16, seals);
  const char contents[16] = "shared pixels";
  assert(pwrite(fd, contents, sizeof(contents), 0) == sizeof(contents));
  void *pixels = native_pixels_map(fd, 16, PROT_READ);
  assert(pixels != MAP_FAILED);
  assert(memcmp(pixels, contents, sizeof(contents)) == 0);
  struct stat storage;
  assert(fstat(fd, &storage) == -1 && errno == EBADF);
  assert(munmap(pixels, 16) == 0);
  for (int seal = 0; seal < 2; seal++) {
    fd = slot(16, seal ? F_SEAL_SHRINK | F_SEAL_GROW : 0);
    assert(native_pixels_map(fd, 16, PROT_READ) == MAP_FAILED);
    close(fd);
  }
  fd = slot(16, seals);
  for (size_t size = 0; size < 32; size += 8)
    if (size != 16)
      assert(native_pixels_map(fd, size, PROT_READ) == MAP_FAILED);
  assert(native_pixels_map(fd, (64u << 20) + 1, PROT_READ) == MAP_FAILED);
  close(fd);
  int pipe_fds[2];
  assert(pipe(pipe_fds) == 0);
  assert(native_pixels_map(pipe_fds[0], 16, PROT_READ) == MAP_FAILED);
  close(pipe_fds[0]);
  close(pipe_fds[1]);
  return 0;
}
