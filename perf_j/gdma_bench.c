/* gdma_bench.c -- J1 microbenchmark: GDMA readback fixed vs per-byte cost (2026-10-16)
 *
 * Runs with the TCP server stopped (IP idle). Uses the deployed /dev/plbdma
 * (BRAM -> bounce DMA, one descriptor per ioctl) and /dev/plbram (Normal-NC
 * CPU mapping of the 256 KB tensor BRAM). Nothing is installed or changed.
 *
 *   sweep   : ioctl(BRAM->bounce) time vs length; CPU memcpy read / write of
 *             the same length through /dev/plbram; bounce->malloc memcpy.
 *   overlap : GDMA read of region X on a worker thread (core 2) while the main
 *             thread (core 1) CPU-writes region Y -- both while the IP is idle.
 *             Times serial vs overlapped and verifies every byte of X and Y.
 *
 * gcc -O2 -Wall -std=gnu99 -pthread gdma_bench.c -o gdma_bench
 * ./gdma_bench sweep [reps]  |  ./gdma_bench overlap [iters] [rbytes] [wbytes]
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

struct plbdma_req  { uint32_t bram_off, len, dir, pad; };
struct plbdma_info { uint32_t bounce_size, dma_ok, xfers, errs; };
#define PLBDMA_IOC_INFO _IOR('D', 0x01, struct plbdma_info)
#define PLBDMA_IOC_XFER _IOW('D', 0x02, struct plbdma_req)

#define BRAM_BYTES 0x40000u

static int      g_dfd, g_bfd;
static uint8_t *g_bounce, *g_bram;
static size_t   g_bsize;

static double now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e6 + ts.tv_nsec / 1e3;
}

static void pin(int cpu)
{
    cpu_set_t s;
    CPU_ZERO(&s);
    CPU_SET(cpu, &s);
    pthread_setaffinity_np(pthread_self(), sizeof(s), &s);
}

static int dma_rd(uint32_t off, uint32_t len)
{
    struct plbdma_req r = { off, len, 0u, 0u };
    return ioctl(g_dfd, PLBDMA_IOC_XFER, &r);
}

static void commit(uint32_t byte_off)          /* same trick as unet_ai.c bram_commit */
{
    (void)*(volatile const uint32_t *)(const void *)(g_bram + byte_off);
}

static void fill_pat(uint8_t *p, size_t n, uint32_t seed)
{
    uint32_t *w = (uint32_t *)(void *)p;
    size_t i;
    for (i = 0; i < n / 4; i++) {
        seed = seed * 1664525u + 1013904223u;
        w[i] = seed;
    }
}

static int cmp_d(const void *a, const void *b)
{
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

static void stats(double *v, int n, double *med, double *mean, double *p99, double *mn)
{
    double s = 0;
    int i;
    qsort(v, n, sizeof(double), cmp_d);
    for (i = 0; i < n; i++) s += v[i];
    *med = v[n / 2]; *mean = s / n; *p99 = v[(int)(n * 0.99)]; *mn = v[0];
}

static int open_all(void)
{
    struct plbdma_info info;

    g_dfd = open("/dev/plbdma", O_RDWR);
    g_bfd = open("/dev/plbram", O_RDWR | O_SYNC);
    if (g_dfd < 0 || g_bfd < 0) { perror("open"); return -1; }
    memset(&info, 0, sizeof(info));
    if (ioctl(g_dfd, PLBDMA_IOC_INFO, &info) != 0 || !info.dma_ok) { perror("info"); return -1; }
    g_bsize  = info.bounce_size;
    g_bounce = mmap(NULL, g_bsize, PROT_READ | PROT_WRITE, MAP_SHARED, g_dfd, 0);
    g_bram   = mmap(NULL, BRAM_BYTES, PROT_READ | PROT_WRITE, MAP_SHARED, g_bfd, 0);
    if (g_bounce == MAP_FAILED || g_bram == MAP_FAILED) { perror("mmap"); return -1; }
    printf("INFO bounce=%zu KB xfers_so_far=%u errs=%u\n", g_bsize >> 10, info.xfers, info.errs);
    return 0;
}

/* ------------------------------------------------------------------ sweep */
static int sweep(int reps)
{
    static const uint32_t sizes[] = { 64, 256, 1024, 2048, 4096, 8192, 16384, 32768,
                                      65536, 103424, 131072, 196608, 262144 };
    double *v = malloc(sizeof(double) * reps);
    uint8_t *dst = aligned_alloc(64, BRAM_BYTES);
    uint8_t *src = aligned_alloc(64, BRAM_BYTES);
    unsigned k;
    int i;

    fill_pat(src, BRAM_BYTES, 7u);
    memset(dst, 0, BRAM_BYTES);
    pin(1);
    for (i = 0; i < 20; i++) (void)dma_rd(0, 65536);       /* warm */
    printf("SWEEP reps=%d (us; med mean p99 min)\n", reps);
    for (k = 0; k < sizeof(sizes) / sizeof(sizes[0]); k++) {
        uint32_t L = sizes[k];
        double med, mean, p99, mn, cmed, cmean, c99, cmn;
        double wmed, wmean, w99, wmn, mmed, mmean, m99, mmn;
        int creps = (L <= 16384) ? reps : (reps < 20 ? reps : 20);

        for (i = 0; i < reps; i++) {
            double t0 = now_us();
            if (dma_rd(0, L) != 0) { printf("ERR dma len=%u %s\n", L, strerror(errno)); return 1; }
            v[i] = now_us() - t0;
        }
        stats(v, reps, &med, &mean, &p99, &mn);
        for (i = 0; i < creps; i++) {                     /* CPU read through Normal-NC map */
            double t0 = now_us();
            memcpy(dst, g_bram, L);
            v[i] = now_us() - t0;
        }
        stats(v, creps, &cmed, &cmean, &c99, &cmn);
        for (i = 0; i < reps; i++) {                      /* CPU write (= put path) */
            double t0 = now_us();
            memcpy(g_bram, src, L);
            commit(L - 4u);
            v[i] = now_us() - t0;
        }
        stats(v, reps, &wmed, &wmean, &w99, &wmn);
        for (i = 0; i < reps; i++) {                      /* bounce -> malloc (deferred memcpy) */
            double t0;
            (void)dma_rd(0, L);
            t0 = now_us();
            memcpy(dst, g_bounce, L);
            v[i] = now_us() - t0;
        }
        stats(v, reps, &mmed, &mmean, &m99, &mmn);
        printf("SIZE %6u  dma %8.2f %8.2f %8.2f %8.2f  | cpu_rd %9.2f %9.2f | cpu_wr %8.2f %8.2f | bounce_mc %7.2f %7.2f\n",
               L, med, mean, p99, mn, cmed, cmean, wmed, wmean, mmed, mmean);
        printf("JSON {\"len\":%u,\"dma_med\":%.3f,\"dma_mean\":%.3f,\"dma_p99\":%.3f,\"dma_min\":%.3f,"
               "\"cpu_rd_med\":%.3f,\"cpu_wr_med\":%.3f,\"bounce_mc_med\":%.3f}\n",
               L, med, mean, p99, mn, cmed, wmed, mmed);
    }
    /* data check: DMA readback equals what the CPU wrote */
    memcpy(g_bram, src, BRAM_BYTES);
    commit(BRAM_BYTES - 4u);
    (void)dma_rd(0, BRAM_BYTES);
    printf("CHECK dma_vs_cpu_written %s\n", memcmp(g_bounce, src, BRAM_BYTES) ? "MISMATCH" : "OK");
    return 0;
}

/* ---------------------------------------------------------------- overlap */
static volatile int g_go, g_done, g_quit;
static uint32_t g_roff, g_rlen;
static int g_rrc;

static void *worker(void *arg)
{
    (void)arg;
    pin(2);
    for (;;) {
        while (!__atomic_load_n(&g_go, __ATOMIC_ACQUIRE)) {
            if (g_quit) return NULL;
        }
        g_go = 0;
        g_rrc = dma_rd(g_roff, g_rlen);
        __atomic_store_n(&g_done, 1, __ATOMIC_RELEASE);
    }
}

static int overlap(int iters, uint32_t R, uint32_t W)
{
    pthread_t th;
    uint8_t *px = aligned_alloc(64, R), *py = aligned_alloc(64, W);
    double *ts = malloc(sizeof(double) * iters), *to = malloc(sizeof(double) * iters);
    double *tr = malloc(sizeof(double) * iters), *tw = malloc(sizeof(double) * iters);
    double a, b, c, d;
    long bad_x = 0, bad_y = 0;
    int i;
    const uint32_t xo = 0u, yo = R;                       /* X = [0,R)  Y = [R,R+W) */

    if (R + W > BRAM_BYTES || (R & 63u) || (W & 63u) || W > g_bsize) {
        printf("bad sizes\n");
        return 1;
    }
    pin(1);
    g_roff = xo; g_rlen = R;
    pthread_create(&th, NULL, worker, NULL);
    for (i = 0; i < iters; i++) {
        double t0, t1;
        fill_pat(px, R, 1000u + (uint32_t)i);
        fill_pat(py, W, 5000u + (uint32_t)i);
        memcpy(g_bram + xo, px, R);                       /* X = known pattern (IP idle) */
        commit(xo + R - 4u);
        memset(g_bounce, 0xEE, R);

        if (i & 1) {                                      /* serial: DMA read, then CPU write */
            t0 = now_us();
            (void)dma_rd(xo, R);
            t1 = now_us();
            memcpy(g_bram + yo, py, W);
            commit(yo + W - 4u);
            ts[i / 2] = now_us() - t0;
            tr[i / 2] = t1 - t0;
            tw[i / 2] = now_us() - t1;
        } else {                                          /* overlapped */
            t0 = now_us();
            __atomic_store_n(&g_done, 0, __ATOMIC_RELAXED);
            __atomic_store_n(&g_go, 1, __ATOMIC_RELEASE);
            memcpy(g_bram + yo, py, W);
            commit(yo + W - 4u);
            while (!__atomic_load_n(&g_done, __ATOMIC_ACQUIRE)) { }
            to[i / 2] = now_us() - t0;
            if (g_rrc != 0) { printf("ERR worker dma\n"); return 1; }
        }
        if (memcmp(g_bounce, px, R) != 0) bad_x++;        /* DMA read saw X intact */
        /* Y check: read back via DMA (serial) and via CPU */
        if (W <= g_bsize) {
            (void)dma_rd(yo, W);
            if (memcmp(g_bounce, py, W) != 0) bad_y++;
        }
    }
    g_quit = 1;
    g_go = 0;
    pthread_join(th, NULL);
    {
        int n = iters / 2;
        double med_s, med_o, med_r, med_w, p99_s, p99_o;
        stats(ts, n, &med_s, &a, &p99_s, &b);
        stats(to, n, &med_o, &c, &p99_o, &d);
        stats(tr, n, &med_r, &a, &b, &c);
        stats(tw, n, &med_w, &a, &b, &c);
        printf("OVERLAP R=%u W=%u iters=%d  serial_med=%.2f (dma %.2f + cpu_wr %.2f) p99=%.2f  overlapped_med=%.2f p99=%.2f  saved=%.2f us/strip\n",
               R, W, iters, med_s, med_r, med_w, p99_s, med_o, p99_o, med_s - med_o);
        printf("VERIFY bad_x=%ld bad_y=%ld of %d -> %s\n", bad_x, bad_y, iters,
               (bad_x || bad_y) ? "OVERLAP_DATA_FAIL" : "OVERLAP_DATA_OK");
        printf("JSON {\"R\":%u,\"W\":%u,\"iters\":%d,\"serial_med\":%.3f,\"dma_med\":%.3f,\"cpuwr_med\":%.3f,"
               "\"overlap_med\":%.3f,\"overlap_p99\":%.3f,\"bad_x\":%ld,\"bad_y\":%ld}\n",
               R, W, iters, med_s, med_r, med_w, med_o, p99_o, bad_x, bad_y);
    }
    return (bad_x || bad_y) ? 2 : 0;
}

int main(int argc, char **argv)
{
    if (argc < 2 || open_all() != 0) {
        fprintf(stderr, "usage: %s sweep [reps] | overlap [iters] [R] [W]\n", argv[0]);
        return 1;
    }
    if (strcmp(argv[1], "sweep") == 0) {
        return sweep(argc > 2 ? atoi(argv[2]) : 200);
    }
    if (strcmp(argv[1], "overlap") == 0) {
        return overlap(argc > 2 ? atoi(argv[2]) : 400,
                       argc > 3 ? (uint32_t)atoi(argv[3]) : 103424u,
                       argc > 4 ? (uint32_t)atoi(argv[4]) : 141824u);
    }
    return 1;
}
