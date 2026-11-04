/* test/host_test_rx.c 用的 POSIX 垫片头(MinGW 缺 POSIX socket 头)。详见 test/README.md。 */
#ifndef SHIM_SYS_MMAN_H
#define SHIM_SYS_MMAN_H

#include <sys/types.h>
#include <stddef.h>

#define PROT_READ   0x1
#define PROT_WRITE  0x2
#define MAP_SHARED  0x01
#define MAP_FAILED  ((void *)-1)

void *mmap(void *addr, size_t length, int prot, int flags,
           int fd, off_t offset);
int   munmap(void *addr, size_t length);

#endif /* SHIM_SYS_MMAN_H */
