/* test/host_test_defer.c 用的 POSIX 垫片(MinGW 没有 <sys/ioctl.h>)。
 * 编码方式与 Linux 不同也没关系: 测试里两边用的是同一组宏。 */
#ifndef SHIM_SYS_IOCTL_H
#define SHIM_SYS_IOCTL_H

#define _IOR(t, n, type) (0x80000000u | ((unsigned)sizeof(type) << 16) | ((t) << 8) | (n))
#define _IOW(t, n, type) (0x40000000u | ((unsigned)sizeof(type) << 16) | ((t) << 8) | (n))

int ioctl(int fd, unsigned long req, ...);

#endif /* SHIM_SYS_IOCTL_H */
