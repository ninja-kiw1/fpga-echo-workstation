# -*- coding: utf-8 -*-
"""判定 PS 读 BRAM: 是"每次访问的延迟"主导, 还是"字节数"主导。

同样搬 BYTES x REPS 字节:
  A) 128-bit(ldp) 访问, 每次 16 B, N 次
  B)  64-bit(ldr) 访问, 每次  8 B, 2N 次
若 B 的时间 ~= 2*A  -> 延迟/访问次数主导 (int8 同访问次数 = 白干)
若 B 的时间 ~= A    -> 字节数主导      (int8 减半 = 真省一半)
"""
import os
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
RDIR = "/root/tcp_image_rx/ai_c"

SRC = r'''
#include <stdio.h>
#include <stdint.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define BASE  0xB0000000UL
#define BYTES (256*1024)
#define REPS  64

static uint64_t now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000ull + (uint64_t)ts.tv_nsec / 1000ull;
}

typedef unsigned __int128 u128;

int main(void)
{
    int fd = open("/dev/mem", O_RDWR | O_SYNC);
    void *m;
    volatile uint64_t *p64;
    volatile u128 *p128;
    unsigned n64 = BYTES / 8, n128 = BYTES / 16;
    uint64_t a, t0, t1;
    unsigned r, i;

    if (fd < 0) { perror("open /dev/mem"); return 1; }
    m = mmap(NULL, BYTES, PROT_READ | PROT_WRITE, MAP_SHARED, fd, BASE);
    if (m == MAP_FAILED) { perror("mmap"); return 1; }
    p64  = (volatile uint64_t *)m;
    p128 = (volatile u128 *)m;

    a = 0; (void)p128[0];
    t0 = now_us();
    for (r = 0; r < REPS; r++)
        for (i = 0; i < n128; i++)
            a ^= (uint64_t)p128[i];
    t1 = now_us();
    printf("A 128bit: %u acc x 16B = %u KB x %u reps | %llu us | %.1f ns/acc | %.1f MB/s | %llx\n",
           n128, BYTES / 1024, REPS, (unsigned long long)(t1 - t0),
           (double)(t1 - t0) * 1000.0 / ((double)n128 * REPS),
           (double)BYTES * REPS / (double)(t1 - t0), (unsigned long long)a);

    a = 0; (void)p64[0];
    t0 = now_us();
    for (r = 0; r < REPS; r++)
        for (i = 0; i < n64; i++)
            a ^= p64[i];
    t1 = now_us();
    printf("B  64bit: %u acc x  8B = %u KB x %u reps | %llu us | %.1f ns/acc | %.1f MB/s | %llx\n",
           n64, BYTES / 1024, REPS, (unsigned long long)(t1 - t0),
           (double)(t1 - t0) * 1000.0 / ((double)n64 * REPS),
           (double)BYTES * REPS / (double)(t1 - t0), (unsigned long long)a);
    return 0;
}
'''


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    c.exec_command("mkdir -p %s" % RDIR)[1].read()
    sftp = c.open_sftp()
    with sftp.open(RDIR + "/_memtest.c", "w") as fh:
        fh.write(SRC)
    sftp.close()

    _i, o, _e = c.exec_command(
        "cd %s && gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE _memtest.c -o _memtest 2>&1 "
        "&& echo BUILD_OK && ./_memtest 2>&1; echo RC=$?" % RDIR, timeout=300)
    print(o.read().decode(errors="replace").rstrip())
    c.close()
    print("MEMTEST_DONE")


if __name__ == "__main__":
    main()
