/* test/host_test_rx.c 用的 POSIX 垫片头(MinGW 缺 POSIX socket 头)。详见 test/README.md。 */
#ifndef SHIM_NETINET_TCP_H
#define SHIM_NETINET_TCP_H

#define TCP_NODELAY 1

#endif /* SHIM_NETINET_TCP_H */
