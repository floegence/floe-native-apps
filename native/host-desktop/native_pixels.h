/* SPDX-License-Identifier: MIT */
#ifndef FLOE_NATIVE_PIXELS_H
#define FLOE_NATIVE_PIXELS_H

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static int native_pixels_fd(const char *argument) {
  char *end = NULL;
  long value = strtol(argument, &end, 10);
  return end && !*end && value >= 3 && value <= 65535 ? (int)value : -1;
}

static void *native_pixels_map(int fd, size_t length, int protection) {
  struct stat storage;
  const int required = F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_SEAL;
  int seals = fcntl(fd, F_GET_SEALS);
  if (fd < 3 || !length || length > (64u << 20) || fstat(fd, &storage) != 0 ||
      !S_ISREG(storage.st_mode) || storage.st_uid != geteuid() ||
      storage.st_size != (int64_t)length || seals < 0 ||
      (seals & required) != required) {
    errno = EINVAL;
    return MAP_FAILED;
  }
  void *pixels = mmap(NULL, length, protection, MAP_SHARED, fd, 0);
  close(fd);
  return pixels;
}

#endif
