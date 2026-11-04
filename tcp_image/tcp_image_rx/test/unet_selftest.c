/* unet_selftest.c -- run the whole U-Net through the driver and compare the
 * logits against a golden file produced by the HLS C simulation.
 *
 * Usage: unet_selftest <x.bin> <final.bin> [iters] [shift]
 *   x.bin     输入张量: (512>>shift)^2 x 8 int16 (lane 0..2 = R,G,B)
 *   final.bin 期望输出: 同分辨率的 8 lane int16 (lane 0 = int8 logit)
 *   iters     重复次数(计时用, 默认 1)
 *   shift     分辨率档位(默认 0 = 512); 4 -> 32x32, 用来和 P4 的 golden 对账
 *
 * 两个文件都能从 ai/net/vectors_small/golden/ 或 PLD 的
 * deployment/unet_network/vectors/ 里拿到。
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "unet_ai.h"

static uint64_t now_us(void)
{
    struct timespec ts;
    (void)clock_gettime(CLOCK_MONOTONIC, &ts);
    return ((uint64_t)ts.tv_sec * 1000000u) + ((uint64_t)ts.tv_nsec / 1000u);
}

static int load(const char *path, int16_t *dst, size_t n)
{
    FILE *f = fopen(path, "rb");
    size_t got;

    if (f == NULL) {
        printf("cannot open %s\n", path);
        return -1;
    }
    got = fread(dst, 1, n * sizeof(int16_t), f);
    fclose(f);
    if (got != n * sizeof(int16_t)) {
        printf("%s: short read (%u of %u bytes)\n", path,
               (unsigned)got, (unsigned)(n * sizeof(int16_t)));
        return -1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    static int16_t gx[(size_t)UNET_IN_H * UNET_IN_W * 8];
    static int16_t gf[(size_t)UNET_OUT_H * UNET_OUT_W * 8];
    static uint8_t rgb[(size_t)UNET_IN_H * UNET_IN_W * 3];
    static int8_t  logits[(size_t)UNET_OUT_H * UNET_OUT_W];
    int      iters = 1;
    int      shift = 0;
    int      i;
    uint32_t in_w, in_h, out_w, out_h, npix, opix, p;
    uint64_t t0, t1;

    (void)setvbuf(stdout, NULL, _IOLBF, 0);   /* 崩了也要把日志吐出来 */

    if (argc < 3) {
        printf("usage: %s <x.bin> <final.bin> [iters] [shift]\n", argv[0]);
        return 2;
    }
    if (argc > 3) {
        iters = atoi(argv[3]);
        if (iters < 1) {
            iters = 1;
        }
    }
    if (argc > 4) {
        shift = atoi(argv[4]);
    }
    if (shift < 0 || shift > 5) {
        printf("bad shift %d\n", shift);
        return 2;
    }

    in_w  = (uint32_t)UNET_IN_W  >> shift;
    in_h  = (uint32_t)UNET_IN_H  >> shift;
    out_w = (uint32_t)UNET_OUT_W >> shift;
    out_h = (uint32_t)UNET_OUT_H >> shift;
    npix  = in_w * in_h;
    opix  = out_w * out_h;
    printf("shift=%d -> input %ux%u, output %ux%u\n", shift, in_w, in_h, out_w, out_h);

    if (load(argv[1], gx, npix * 8u) != 0 || load(argv[2], gf, opix * 8u) != 0) {
        return 2;
    }

    for (p = 0u; p < npix; p++) {
        rgb[(size_t)p * 3u + 0u] = (uint8_t)gx[(size_t)p * 8u + 0u];
        rgb[(size_t)p * 3u + 1u] = (uint8_t)gx[(size_t)p * 8u + 1u];
        rgb[(size_t)p * 3u + 2u] = (uint8_t)gx[(size_t)p * 8u + 2u];
    }

    if (unet_init(0xA0010000ull, 0xA0020000ull, 0xB0000000ull) != 0) {
        return 1;
    }
    if (unet_set_shift(shift) != 0) {
        printf("unet_set_shift(%d) failed\n", shift);
        return 1;
    }

    {
        unet_stats_t st;

        t0 = now_us();
        for (i = 0; i < iters; i++) {
            if (unet_run(rgb, logits, &st) != 0) {
                printf("unet_run failed\n");
                return 1;
            }
        }
        t1 = now_us();
        printf("timing: %d iter(s) in %.1f ms  ->  %.2f ms/frame (whole net, shift=%d)\n",
               iters, (double)(t1 - t0) / 1000.0, (double)(t1 - t0) / 1000.0 / (double)iters,
               shift);
        printf("run: %u us, logits min=%d max=%d\n",
               (unsigned)st.run_us, (int)st.logit_min, (int)st.logit_max);
        printf("profile us : ap(PL)=%u  put(->BRAM)=%u  get(BRAM->)=%u  zero=%u  other=%d\n",
               (unsigned)st.ap_us, (unsigned)st.put_us, (unsigned)st.get_us,
               (unsigned)st.zero_us,
               (int)st.run_us - (int)(st.ap_us + st.put_us + st.get_us + st.zero_us));
        printf("profile mem: put=%llu get=%llu params=%llu words  strips=%u\n",
               (unsigned long long)st.put_words, (unsigned long long)st.get_words,
               (unsigned long long)st.param_words, (unsigned)st.strips);
    }

    {
        uint32_t bad = 0u;
        int      first = -1;
        int      vmin = 127, vmax = -128;
        uint32_t vpos = 0u;

        for (p = 0u; p < opix; p++) {
            uint8_t want = (uint8_t)(gf[(size_t)p * 8u + 0u] & 0xFF);
            uint8_t got  = (uint8_t)logits[p];
            int     sv   = (int)logits[p];

            if (want != got) {
                if (first < 0) {
                    first = (int)p;
                }
                bad++;
            }
            if (sv < vmin) { vmin = sv; }
            if (sv > vmax) { vmax = sv; }
            if (sv != 0) { vpos++; }
        }
        printf("compare: mismatch = %u / %u  ->  %s\n", (unsigned)bad, (unsigned)opix,
               (bad == 0u) ? "PASS" : "FAIL");
        if (first >= 0) {
            printf("  first diff at pixel %d (y=%d x=%d): want=%d got=%d\n",
                   first, first / (int)out_w, first % (int)out_w,
                   (int)gf[(size_t)first * 8u + 0u], (int)logits[first]);
        }
        printf("logit range [%d..%d], non-zero %u/%u\n",
               vmin, vmax, (unsigned)vpos, (unsigned)opix);
    }

    unet_close();
    printf("SELFTEST_DONE\n");
    return 0;
}
