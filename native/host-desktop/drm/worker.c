/* SPDX-License-Identifier: MIT
 * Split DRM scanout capture. The exporter never invokes EGL/GLES; the converter
 * runs with the Runtime user's credentials. Both accept only inherited private
 * sockets, never paths or network addresses from a client.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <linux/capability.h>
#include <unistd.h>
#include "drmtap.h"

#define MAX_BYTES (64u << 20)
_Static_assert(sizeof(drmtap_dmabuf_desc) == 72, "descriptor ABI changed");

typedef struct { int32_t status; uint32_t version; drmtap_dmabuf_desc desc; } export_packet;
_Static_assert(sizeof(export_packet) == 80, "export ABI changed");

static int send_packet(int sock, const void *data, size_t length, int fd) {
 struct iovec iov = {.iov_base=(void *)data,.iov_len=length};
 char control[CMSG_SPACE(sizeof(int))] = {0};
 struct msghdr msg = {.msg_iov=&iov,.msg_iovlen=1};
 if (fd >= 0) {
  msg.msg_control=control; msg.msg_controllen=sizeof(control);
  struct cmsghdr *c=CMSG_FIRSTHDR(&msg);
  c->cmsg_level=SOL_SOCKET;c->cmsg_type=SCM_RIGHTS;c->cmsg_len=CMSG_LEN(sizeof(int));
  memcpy(CMSG_DATA(c),&fd,sizeof(fd));
 }
 return sendmsg(sock,&msg,MSG_NOSIGNAL)==(ssize_t)length ? 0 : -1;
}
static int receive_packet(int sock, void *data, size_t length, int *received_fd) {
 struct iovec iov={.iov_base=data,.iov_len=length};
 char control[CMSG_SPACE(sizeof(int)*8)]={0};
 struct msghdr msg={.msg_iov=&iov,.msg_iovlen=1,.msg_control=control,.msg_controllen=sizeof(control)};
 ssize_t count=recvmsg(sock,&msg,MSG_CMSG_CLOEXEC);
 *received_fd=-1;
 int fds=0;
 for(struct cmsghdr *c=CMSG_FIRSTHDR(&msg);c;c=CMSG_NXTHDR(&msg,c)) {
  if(c->cmsg_level!=SOL_SOCKET||c->cmsg_type!=SCM_RIGHTS) continue;
  if(c->cmsg_len<CMSG_LEN(0)) continue;
  size_t n=(c->cmsg_len-CMSG_LEN(0))/sizeof(int);
  for(size_t i=0;i<n;i++) {
   int fd;memcpy(&fd,(char *)CMSG_DATA(c)+i*sizeof(int),sizeof(fd));
   if(fds++==0)*received_fd=fd;else close(fd);
  }
 }
 if(count!=(ssize_t)length||(msg.msg_flags&(MSG_TRUNC|MSG_CTRUNC))||fds>1) {
  if(*received_fd>=0)close(*received_fd);
  *received_fd=-1;
  return -1;
 }
 return 0;
}
static int exporter(int sock) {
 /* Only DRM export and device access remain in this worker. In particular,
  * the parent's process-image verification capability does not reach libdrm. */
 struct __user_cap_header_struct header = { .version = _LINUX_CAPABILITY_VERSION_3, .pid = 0 };
 struct __user_cap_data_struct caps[2] = {{0}, {0}};
 caps[0].effective = (1u << CAP_SYS_ADMIN) | (1u << CAP_DAC_OVERRIDE);
 caps[0].permitted = caps[0].effective;
 if (syscall(SYS_capset, &header, caps) != 0) return 77;
 drmtap_ctx *ctx=NULL;
 for(;;) {
  uint8_t command=0;int unexpected=-1;
  if(receive_packet(sock,&command,1,&unexpected)!=0)break;
  if(unexpected>=0){close(unexpected);break;}
  if(command!=1)break;
  export_packet packet={.version=1};
  packet.desc.dma_buf_fd=-1;
  if(!ctx)ctx=drmtap_open(NULL);
  drmtap_frame_info frame={0};
  if(!ctx)packet.status=-ENODEV;
  else packet.status=drmtap_grab_desc(ctx,&packet.desc,&frame);
  int fd=packet.status==0?packet.desc.dma_buf_fd:-1;
  packet.desc.dma_buf_fd=-1;
  int sent=send_packet(sock,&packet,sizeof(packet),fd);
  if(packet.status==0)drmtap_frame_release(ctx,&frame);
  else if(ctx){drmtap_close(ctx);ctx=NULL;}
  if(sent!=0)break;
 }
 if(ctx)drmtap_close(ctx);
 return 0;
}
static int write_all(int fd,const void *data,size_t length) {
 const uint8_t *p=data;
 while(length){ssize_t n=write(fd,p,length);if(n<0&&errno==EINTR)continue;if(n<=0)return -1;p+=n;length-=n;}
 return 0;
}
static int converter(int sock) {
 if(geteuid()==0)return 77; // GPU drivers never load in a privileged converter.
 struct __user_cap_header_struct cap_header = { .version = _LINUX_CAPABILITY_VERSION_3, .pid = 0 };
 struct __user_cap_data_struct caps[2] = {{0}, {0}};
 if (syscall(SYS_capset, &cap_header, caps) != 0) return 77;
 if (syscall(SYS_capget, &cap_header, caps) != 0) return 77;
 for (int i=0; i<2; i++) if (caps[i].effective || caps[i].permitted || caps[i].inheritable) return 77;
 drmtap_ctx *ctx=drmtap_open_render(NULL);
 if(!ctx)return 69;
 for(;;) {
  export_packet packet;int fd=-1;
  if(receive_packet(sock,&packet,sizeof(packet),&fd)!=0)break;
  if(packet.version!=1||packet.status!=0||fd<0||packet.desc.num_planes<1||packet.desc.num_planes>4||packet.desc.width<2||packet.desc.height<2||packet.desc.width>8192||packet.desc.height>8192){if(fd>=0)close(fd);break;}
  packet.desc.dma_buf_fd=fd;
  drmtap_frame_info frame={0};
  int result=drmtap_convert_dmabuf(ctx,&packet.desc,&frame);
  close(fd);
  uint32_t header[6]={(uint32_t)result,0,0,0,0,0};
  if(result==0&&frame.data&&frame.stride>=frame.width*4&&(uint64_t)frame.stride*frame.height<=MAX_BYTES) {
   header[1]=frame.width;header[2]=frame.height;header[3]=frame.stride;header[4]=frame.format;header[5]=frame.stride*frame.height;
  } else header[0]=(uint32_t)-ENOTSUP;
  if(write_all(STDOUT_FILENO,header,sizeof(header))!=0)break;
  if(header[5]&&write_all(STDOUT_FILENO,frame.data,header[5])!=0)break;
 }
 drmtap_close(ctx);return 0;
}
int main(int argc,char **argv) {
 int type=0;socklen_t size=sizeof(type);
 if(argc!=2||getsockopt(3,SOL_SOCKET,SO_TYPE,&type,&size)!=0||type!=SOCK_SEQPACKET)return 64;
 if(prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0)!=0)return 77;
 if(strcmp(argv[1],"export")==0)return geteuid()==0?exporter(3):77;
 if(strcmp(argv[1],"convert")==0)return converter(3);
 return 64;
}
