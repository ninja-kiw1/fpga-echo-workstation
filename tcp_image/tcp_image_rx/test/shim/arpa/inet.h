/* test/host_test_rx.c 用的 POSIX 垫片头(MinGW 缺 POSIX socket 头)。详见 test/README.md。 */
#ifndef SHIM_ARPA_INET_H
#define SHIM_ARPA_INET_H

#include <netinet/in.h>

#define INET_ADDRSTRLEN 16

const char *inet_ntop(int af, const void *src, char *dst, socklen_t size);

#endif /* SHIM_ARPA_INET_H */
