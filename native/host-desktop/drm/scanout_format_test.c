#include <assert.h>
#include <stdint.h>
#include "scanout_format.h"

int main(void) {
 const uint32_t xr24=0x34325258, ab24=0x34324241;
 const uint64_t afbc=UINT64_C(0x0800000000000051);
 assert(scanout_import_format("rockchip",xr24,afbc,1,0)==ab24);
 assert(scanout_import_format(NULL,xr24,afbc,1,0)==xr24);
 assert(scanout_import_format("i915",xr24,afbc,1,0)==xr24);
 assert(scanout_import_format("rockchip",xr24,0,1,0)==xr24);
 assert(scanout_import_format("rockchip",xr24,UINT64_MAX,1,0)==xr24);
 assert(scanout_import_format("rockchip",xr24,afbc|UINT64_C(0x100000000),1,0)==xr24);
 assert(scanout_import_format("rockchip",xr24,afbc|0x20,1,0)==xr24);
 assert(scanout_import_format("rockchip",xr24,afbc,2,0)==xr24);
 assert(scanout_import_format("rockchip",xr24,afbc,1,2)==xr24);
 assert(scanout_import_format("rockchip",ab24,afbc,1,0)==ab24);
 assert(scanout_import_format("rockchip",0x34325241,afbc,1,0)==0x34325241);
 return 0;
}
