/* SPDX-License-Identifier: MIT */
#ifndef FLOE_SCANOUT_FORMAT_H
#define FLOE_SCANOUT_FORMAT_H
#include <stdint.h>
#include <string.h>

static inline uint32_t scanout_import_format(const char *driver, uint32_t format,
                                            uint64_t modifier, uint32_t planes,
                                            uint32_t eotf) {
 /* Rockchip's legacy Panfork scanout retains BGR render-target swizzling in
  * an AFBC RGBA8 payload. YTR imports require the canonical ABGR fourcc.
  * The private native-swizzle bit is deliberately excluded: it describes a
  * different component convention. Only the qualified SDR layout is mapped.
  * Output must be labelled XBGR so the existing pixel owner restores BGRx. */
 if(driver&&strcmp(driver,"rockchip")==0&&format==UINT32_C(0x34325258)&&
    modifier==UINT64_C(0x0800000000000051)&&planes==1&&eotf==0)
  return UINT32_C(0x34324241);
 return format;
}
#endif
