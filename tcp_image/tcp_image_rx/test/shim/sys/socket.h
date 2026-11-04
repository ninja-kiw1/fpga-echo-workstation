/* test/host_test_rx.c 用的 POSIX 垫片头(MinGW 缺 POSIX socket 头)。详见 test/README.md。 */
#ifndef SHIM_SYS_SOCKET_H
#define SHIM_SYS_SOCKET_H

#include <sys/types.h>
#include <stddef.h>

typedef unsigned int socklen_t;

struct sockaddr {
    unsigned short sa_family;
    char           sa_data[14];
};

#define AF_INET      2
#define SOCK_STREAM  1
#define SOL_SOCKET   0xFFFF
#define SO_REUSEADDR 0x0004
#define SO_RCVBUF    0x1002
#define SO_SNDBUF    0x1001
#define MSG_NOSIGNAL 0x4000     /* Linux 值; 这个测试只用来占位, 不真的发 */
#define SHUT_RDWR    2

int     socket(int domain, int type, int protocol);
int     bind(int fd, const struct sockaddr *addr, socklen_t len);
int     listen(int fd, int backlog);
int     accept(int fd, struct sockaddr *addr, socklen_t *len);
int     setsockopt(int fd, int level, int optname,
                   const void *optval, socklen_t optlen);
ssize_t recv(int fd, void *buf, size_t len, int flags);
ssize_t send(int fd, const void *buf, size_t len, int flags);
int     shutdown(int fd, int how);
int     close(int fd);

#endif /* SHIM_SYS_SOCKET_H */
