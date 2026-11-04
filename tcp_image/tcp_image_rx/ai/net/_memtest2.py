# -*- coding: utf-8 -*-
"""定位 198 ns/访问 的来源: PL 通路 还是 /dev/mem 的 Device 映射语义?

同一段代码、同样的 128-bit 访问, 分别读三块:
  A) 0xB0000000  PL 里的张量 BRAM        (经 HPM1 -> 互联 -> bram_ctrl)
  B) 0x40000000  PS 的保留 DDR(no-map)   (纯 PS 内部, 不过 PL)
  C) malloc 的普通内存                   (可缓存, 作为"上限"参考)
A 与 B 的差 = PL 通路的代价; B 与 C 的差 = /dev/mem Device 映射的代价。
"""
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
RDIR = "/root/tcp_image_rx/ai_c"

SRC = r'''
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define BYTES (256*1024)
#define REPS  64

static uint64_t now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000ull + (uint64_t)ts.tv_nsec / 1000ull;
}

typedef unsigned __int128 u128;

static void bench(const char *tag, volatile u128 *p, unsigned n)
{
    uint64_t a = 0, t0, t1;
    unsigned r, i;

    (void)p[0];
    t0 = now_us();
    for (r = 0; r < REPS; r++)
        for (i = 0; i < n; i++)
            a ^= (uint64_t)p[i];
    t1 = now_us();
    printf("%-22s %6u acc x 16B | %8llu us | %6.1f ns/acc | %7.1f MB/s | %llx\n",
           tag, n, (unsigned long long)(t1 - t0),
           (double)(t1 - t0) * 1000.0 / ((double)n * REPS),
           (double)BYTES * REPS / (double)(t1 - t0), (unsigned long long)a);
}

int main(void)
{
    int fd = open("/dev/mem", O_RDWR | O_SYNC);
    void *bram, *ddr;
    volatile u128 *pb, *pd;
    void *heap = malloc(BYTES);
    unsigned n = BYTES / 16;

    if (fd < 0) { perror("open"); return 1; }
    bram = mmap(NULL, BYTES, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0xB0000000UL);
    ddr  = mmap(NULL, BYTES, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0x40000000UL);
    if (bram == MAP_FAILED || ddr == MAP_FAILED) { perror("mmap"); return 1; }
    pb = (volatile u128 *)bram;
    pd = (volatile u128 *)ddr;

    for (unsigned i = 0; i < n; i++) ((uint64_t *)heap)[i * 2] = 1;   /* 让堆里也有内容 */

    bench("A PL BRAM  0xB0", pb, n);
    bench("B PS DDR   0x40", pd, n);
    bench("C heap(cached)  ", (volatile u128 *)heap, n);
    bench("D PL BRAM again", pb, n);
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
    with sftp.open(RDIR + "/_memtest2.c", "w") as fh:
        fh.write(SRC)
    sftp.close()
    _i, o, _e = c.exec_command(
        "cd %s && gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE _memtest2.c -o _memtest2 2>&1 "
        "&& echo BUILD_OK && ./_memtest2 2>&1; echo RC=$?" % RDIR, timeout=300)
    print(o.read().decode(errors="replace").rstrip())
    c.close()
    print("MEMTEST2_DONE")


if __name__ == "__main__":
    main()
