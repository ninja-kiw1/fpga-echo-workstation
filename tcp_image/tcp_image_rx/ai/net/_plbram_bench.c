/* _plbram_bench.c -- 直接对比同一片 BRAM 上两种映射的读写耗时:
 *     /dev/mem     -> Device    (强序, 不可缓存)   <-- 现状
 *     /dev/plbram  -> Normal-NC (写合并)           <-- plbram.ko 给的
 *
 * 编译(板上):
 *   gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE _plbram_bench.c -o _plbram_bench
 * 运行: 需要先 insmod plbram.ko
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define DEF_BASE 0xB0000000UL
#define BYTES (256 * 1024)
#define REPS  64
#define PAGE  4096

typedef unsigned __int128 u128;

static uint64_t now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000ull + (uint64_t)ts.tv_nsec / 1000ull;
}

static void bench_read(const char *tag, volatile u128 *p, unsigned n)
{
    uint64_t a = 0, t0, t1;
    unsigned r, i;

    (void)p[0];
    t0 = now_us();
    for (r = 0; r < REPS; r++)
        for (i = 0; i < n; i++)
            a ^= (uint64_t)p[i];
    t1 = now_us();
    printf("%-24s READ  %6u x 16B | %8llu us | %6.1f ns/word | %7.1f MB/s | %llx\n",
           tag, n, (unsigned long long)(t1 - t0),
           (double)(t1 - t0) * 1000.0 / ((double)n * REPS),
           (double)BYTES * REPS / (double)(t1 - t0), (unsigned long long)a);
}

static void bench_write(const char *tag, volatile u128 *p, unsigned n)
{
    uint64_t t0, t1;
    unsigned r, i;

    t0 = now_us();
    for (r = 0; r < REPS; r++)
        for (i = 0; i < n; i++)
            p[i] = (u128)0x0123456789abcdefull;
    t1 = now_us();
    printf("%-24s WRITE %6u x 16B | %8llu us | %6.1f ns/word | %7.1f MB/s\n",
           tag, n, (unsigned long long)(t1 - t0),
           (double)(t1 - t0) * 1000.0 / ((double)n * REPS),
           (double)BYTES * REPS / (double)(t1 - t0));
}

int main(int argc, char **argv)
{
    int fdmem, fdpl;
    void *m, *q;
    volatile u128 *pm, *pp;
    unsigned n = BYTES / 16;
    unsigned long base = (argc > 1) ? strtoul(argv[1], NULL, 0) : DEF_BASE;

    fdmem = open("/dev/mem", O_RDWR | O_SYNC);
    if (fdmem < 0) { perror("open /dev/mem"); return 1; }
    fdpl = open("/dev/plbram", O_RDWR | O_SYNC);
    if (fdpl < 0) { perror("open /dev/plbram (insmod plbram.ko 了吗?)"); return 1; }

    m = mmap(NULL, BYTES, PROT_READ | PROT_WRITE, MAP_SHARED, fdmem, base);
    q = mmap(NULL, BYTES, PROT_READ | PROT_WRITE, MAP_SHARED, fdpl, 0);
    if (m == MAP_FAILED) { perror("mmap /dev/mem"); return 1; }
    if (q == MAP_FAILED) { perror("mmap /dev/plbram"); return 1; }
    pm = (volatile u128 *)m;
    pp = (volatile u128 *)q;

    printf("== phys 0x%lx, %u KB, %d reps ==\n", base, BYTES / 1024, REPS);
    bench_read("dev/mem (Device)      ", pm, n);
    bench_read("dev/plbram (Normal-NC) ", pp, n);
    bench_read("dev/mem again          ", pm, n);
    bench_write("dev/mem (Device)      ", pm, n);
    bench_write("dev/plbram (Normal-NC) ", pp, n);
    return 0;
}
