/* test/host_test_rx.c 用的 POSIX 垫片(补充): MinGW 缺 sysconf/_SC_PAGESIZE、
 * O_SYNC、MAP_* 与 POSIX 的 sigaction(minGW 的 <signal.h> 只有 ANSI 那套)。
 * 这些在 Linux(glibc) 上都是有的。
 *
 * ⚠ 本文件用 -include 强制在最前面展开, 所以里面的声明必须自洽,
 *   不要依赖任何还没 include 的头。 */
#ifndef SHIM_EXTRA_H
#define SHIM_EXTRA_H

#ifndef _SC_PAGESIZE
#define _SC_PAGESIZE 30
#endif

#ifndef O_SYNC
#define O_SYNC 0x101000
#endif

#ifndef MAP_PRIVATE
#define MAP_PRIVATE 0x02
#endif

#ifndef MAP_ANONYMOUS
#define MAP_ANONYMOUS 0x20
#endif

long sysconf(int name);

/* ---- POSIX 的 sigaction: MinGW-w64 6.2 的 <signal.h> 里没有 ---- */
#include <signal.h>

#ifndef SHIM_HAVE_SIGACTION
#define SHIM_HAVE_SIGACTION

struct sigaction {
    void (*sa_handler)(int);   /* 只用传统 handler 形式, 不碰 sa_sigaction */
    int    sa_mask;            /* 简化成一个 int, 够这个测试用 */
    int    sa_flags;
};

int sigemptyset(int *set);
int sigaction(int sig, const struct sigaction *act, struct sigaction *oldact);

#endif /* SHIM_HAVE_SIGACTION */

#endif /* SHIM_EXTRA_H */
