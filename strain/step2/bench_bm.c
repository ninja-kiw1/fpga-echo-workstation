/* Part E step 2: board micro-benchmark of the PS SAD block-matching kernel (bm_sad.c), one A53 core.
 * Replaces the step-1 estimates (NEON 60-100 cycles / candidate). Time does not depend on the pixel values
 * (no early exit); real CAMUS VAL frames are used anyway (step-1 data: s1_0_a.raw / s1_0_b.raw, 512 x 512).
 *
 *   bench_bm <step1 data dir> [seconds per case]
 * Output: one "RES" line per case: impl, R, candidates / block, ns / block, ns / candidate, cycles / candidate.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "bm_sad.h"

static double now(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

/* CPU clock from a dependent add chain (1 cycle each on the A53), 8 adds per iteration (10-17 run 1 counted 9 per
 * iteration -> 1197.7 MHz; with 8 it is 1064.6 MHz = cpufreq 1066.7, so the A53s run at 1.0667 GHz) */
static double measure_hz(void)
{
    double best = 0;
    for (int k = 0; k < 5; k++) {
        unsigned long x = 0, n = 50000000;
        const double t0 = now();
#if defined(__aarch64__)
        for (unsigned long i = 0; i < n; i++)
            __asm__ volatile("add %0, %0, #1\n\tadd %0, %0, #1\n\tadd %0, %0, #1\n\tadd %0, %0, #1\n\t"
                             "add %0, %0, #1\n\tadd %0, %0, #1\n\tadd %0, %0, #1\n\tadd %0, %0, #1" : "+r"(x));
        const double hz = (double)n * 8 / (now() - t0);   /* 8 dependent adds; the loop counter dual-issues */
#else
        for (unsigned long i = 0; i < n; i++) __asm__ volatile("" : "+r"(x));
        const double hz = (double)n / (now() - t0);
#endif
        if (hz > best) best = hz;
    }
    return best;
}

static unsigned char A[512 * 512], B[512 * 512];
static volatile uint32_t sink;

int main(int argc, char **argv)
{
    const char *dir = argc > 1 ? argv[1] : "data";
    const double secs = argc > 2 ? atof(argv[2]) : 2.0;
    char p[512];
    snprintf(p, sizeof p, "%s/s1_0_a.raw", dir);
    FILE *f = fopen(p, "rb");
    if (!f || fread(A, 1, sizeof A, f) != sizeof A) { printf("no %s\n", p); return 2; }
    fclose(f);
    snprintf(p, sizeof p, "%s/s1_0_b.raw", dir);
    f = fopen(p, "rb");
    if (!f || fread(B, 1, sizeof B, f) != sizeof B) { printf("no %s\n", p); return 2; }
    fclose(f);

    FILE *cf = fopen("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "r");
    long khz = 0;
    if (cf) { if (fscanf(cf, "%ld", &khz) != 1) khz = 0; fclose(cf); }
    const double hz = measure_hz();
    printf("CLK cpufreq %ld kHz, add-chain %.1f MHz\n", khz, hz / 1e6);

    /* 64 block positions spread over the frame (the window always inside) */
    enum { NB = 64 };
    int bx[NB], by[NB];
    for (int i = 0; i < NB; i++) { bx[i] = 40 + (i % 8) * 54; by[i] = 40 + (i / 8) * 54; }
    static uint32_t tab[(2 * BM_RMAX + 1) * (2 * BM_RMAX + 1)];
    static const int radii[] = {2, 4, 6, 8};
    const char *impls[] = {"c", "neon", "neon2", "neon+argmin"};   /* neon2: row reuse */
    for (int ri = 0; ri < 4; ri++) {
        const int R = radii[ri], nc = (2 * R + 1) * (2 * R + 1);
        for (int im = 0; im < 4; im++) {
#if !defined(__aarch64__)
            if (im) continue;
#endif
            long reps = 0;
            double t0 = now(), t1;
            do {
                for (int i = 0; i < NB; i++) {
                    const unsigned char *a = A + by[i] * 512 + bx[i], *b = B + by[i] * 512 + bx[i];
#if defined(__aarch64__)
                    if (im == 1 || im == 3)
                        bm_sad_table_neon(a, 512, b, 512, R, tab);
                    else if (im == 2)
                        bm_sad_table_neon2(a, 512, b, 512, R, tab);
                    else
#endif
                        bm_sad_table_c(a, 512, b, 512, R, tab);
                    if (im == 3) {
                        bm_best_t o;
                        bm_argmin(tab, R, &o);
                        sink += o.sad;
                    } else
                        sink += tab[nc / 2];
                }
                reps++;
                t1 = now();
            } while (t1 - t0 < secs);
            const double ns_blk = (t1 - t0) / (reps * NB) * 1e9;
            printf("RES impl=%-11s R=%d cand=%3d ns_per_block=%9.1f ns_per_cand=%7.2f cyc_per_cand=%7.1f\n", impls[im], R, nc,
                   ns_blk, ns_blk / nc, ns_blk / nc * hz * 1e-9);
        }
    }
    /* one frame pair: n points along a ring (bm_track_points, incl. sub-pixel), 512 and 256 tiers */
    static unsigned char A2[256 * 256], B2[256 * 256];
    for (int y = 0; y < 256; y++)
        for (int x = 0; x < 256; x++) { A2[y * 256 + x] = A[2 * y * 512 + 2 * x]; B2[y * 256 + x] = B[2 * y * 512 + 2 * x]; }
    static const int npts[] = {74, 222};
    for (int tier = 0; tier < 2; tier++)
        for (int ni = 0; ni < 2; ni++)
            for (int ri = 1; ri < 4; ri++)
                for (int neon = 0; neon < 2; neon++) {
#if !defined(__aarch64__)
                    if (neon) continue;
#endif
                    const int n = npts[ni], R = radii[ri], W = tier ? 256 : 512;
                    const unsigned char *a = tier ? A2 : A, *b = tier ? B2 : B;
                    int32_t x0[222], y0[222], x[222], y[222], fl[222];
                    uint32_t s[222];
                    for (int i = 0; i < n; i++) {
                        const double ang = 6.2831853 * i / n, rad = W * 0.3;
                        x0[i] = (int32_t)((W / 2 + rad * __builtin_cos(ang)) * 256);
                        y0[i] = (int32_t)((W / 2 + rad * __builtin_sin(ang)) * 256);
                    }
                    long reps = 0;
                    double t0 = now(), t1;
                    do {
                        memcpy(x, x0, sizeof x);
                        memcpy(y, y0, sizeof y);
                        sink += bm_track_points(a, b, W, W, W, n, R, neon, x, y, s, fl);
                        reps++;
                        t1 = now();
                    } while (t1 - t0 < secs * 0.5);
                    printf("PAIR tier=%d pts=%3d R=%d impl=%-4s ms_per_pair=%8.4f\n", W, n, R, neon ? "neon" : "c",
                           (t1 - t0) / reps * 1e3);
                }
    /* one frame pair with bm_track_line (anchored template + SAD aggregation over k neighbours, V fit) */
    static const int lcfg[][3] = {{512, 6, 9}, {512, 8, 9}, {256, 4, 5}, {256, 4, 9}};   /* tier, R, k */
    for (int ci = 0; ci < 4; ci++)
        for (int ni = 0; ni < 2; ni++)
            for (int neon = 0; neon < 2; neon++) {
#if !defined(__aarch64__)
                if (neon) continue;
#endif
                const int W = lcfg[ci][0], R = lcfg[ci][1], k = lcfg[ci][2], n = npts[ni];
                const unsigned char *a = W == 256 ? A2 : A, *b = W == 256 ? B2 : B;
                int32_t ax[222], ay[222], x[222], y[222], fl[222];
                uint32_t s[222];
                for (int i = 0; i < n; i++) {
                    const double ang = 6.2831853 * i / n, rad = W * 0.3;
                    ax[i] = (int32_t)((W / 2 + rad * __builtin_cos(ang)) * 256);
                    ay[i] = (int32_t)((W / 2 + rad * __builtin_sin(ang)) * 256);
                }
                long reps = 0;
                double t0 = now(), t1;
                do {
                    memcpy(x, ax, sizeof x);
                    memcpy(y, ay, sizeof y);
                    sink += bm_track_line(a, b, W, W, W, n, R, k, BM_OPT_VFIT | (neon ? BM_OPT_NEON : 0), ax, ay, x, y, s, fl);
                    reps++;
                    t1 = now();
                } while (t1 - t0 < secs * 0.5);
                printf("LINE tier=%d pts=%3d R=%d k=%d impl=%-4s ms_per_pair=%8.4f\n", W, n, R, k, neon ? "neon" : "c",
                       (t1 - t0) / reps * 1e3);
            }
    printf("BENCH_DONE\n");
    return 0;
}
