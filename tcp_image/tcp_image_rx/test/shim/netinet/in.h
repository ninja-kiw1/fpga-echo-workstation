/* test/host_test_rx.c 用的 POSIX 垫片头(MinGW 缺 POSIX socket 头)。详见 test/README.md。 */
#ifndef SHIM_NETINET_IN_H
#define SHIM_NETINET_IN_H

#include <stdint.h>
#include <sys/socket.h>

typedef uint32_t in_addr_t;

struct in_addr {
    in_addr_t s_addr;
};

struct sockaddr_in {
    unsigned short sin_family;
    uint16_t       sin_port;
    struct in_addr sin_addr;
    char           sin_zero[8];
};

#define INADDR_ANY  0x00000000UL
#define IPPROTO_TCP 6

uint16_t htons(uint16_t hostshort);
uint16_t ntohs(uint16_t netshort);
uint32_t htonl(uint32_t hostlong);
uint32_t ntohl(uint32_t netlong);

#endif /* SHIM_NETINET_IN_H */
