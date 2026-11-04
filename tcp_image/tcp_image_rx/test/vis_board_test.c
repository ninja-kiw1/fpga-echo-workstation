/* vis_board_test.c -- vis_layer on the real PL against the vectors of ai/vis_hls/gen_vectors.py (as tb/test_vis.cpp).
 *
 * Each vector: the whole tensor BRAM is filled with a pseudo-random pattern, the vector's chunks are written, the
 * kernel runs at the descriptor, and every one of the 16384 words must equal the expected image (outputs as given,
 * everything else unchanged; a bad descriptor writes nothing). AIV vectors are also compared with the board's own
 * ai_view_overlay_rgb. Only runs when the loaded firmware is listed in /lib/firmware/ai_sys_vis.sha256.
 *   ./vis_board_test <vectors dir> [case list] -> VIS_BOARD_PASS
 * build: gcc -O2 -std=c99 -D_GNU_SOURCE -I. vis_board_test.c vis_ai.c unet_ai.c unet_net_data.c unet_models.c ai_view.c -lm
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ai_view.h"
#include "unet_ai.h"
#include "vis_ai.h"

#define WORDS 16384u

static uint8_t want[WORDS * 16u];
static uint32_t rnd_state;

static uint32_t rnd(void)
{
    rnd_state = rnd_state * 1103515245u + 12345u;
    return rnd_state >> 8;
}

static uint8_t *read_all(const char *path, size_t *n)
{
    FILE    *f = fopen(path, "rb");
    uint8_t *b;
    long     len;

    if (f == NULL) {
        return NULL;
    }
    fseek(f, 0, SEEK_END);
    len = ftell(f);
    fseek(f, 0, SEEK_SET);
    b = (uint8_t *)malloc((size_t)len);
    if (b == NULL || fread(b, 1, (size_t)len, f) != (size_t)len) {
        fclose(f);
        free(b);
        return NULL;
    }
    fclose(f);
    *n = (size_t)len;
    return b;
}

int main(int argc, char **argv)
{
    const char *dir = argc > 1 ? argv[1] : "vectors", *list = argc > 2 ? argv[2] : "cases.txt";
    char        path[512], name[256];
    FILE       *cl;
    uint8_t    *bram;
    int         cases = 0, fails = 0, aiv = 0;
    uint64_t    pl_total = 0;

    if (!vis_firmware_ok()) {
        printf("firmware not listed in %s: vis_layer may be absent, not touching 0xA0040000\n", VIS_FIRMWARE_LIST);
        return 2;
    }
    if (unet_init(0xA0010000ull, 0xA0020000ull, 0xB0000000ull) != 0 || vis_init(VIS_DEF_CTRL) != 0) {
        printf("init failed\n");
        return 2;
    }
    bram = unet_bram_ptr();
    snprintf(path, sizeof(path), "%s/%s", dir, list);
    cl = fopen(path, "r");
    if (cl == NULL) {
        printf("cannot open %s\n", path);
        return 2;
    }
    while (fscanf(cl, "%255s", name) == 1) {
        size_t   n = 0, p;
        uint8_t *b;
        int32_t  hd[6], i, c, bad = 0, first = -1;
        uint32_t ret = 0, pl_us = 0;

        snprintf(path, sizeof(path), "%s/%s.bin", dir, name);
        b = read_all(path, &n);
        if (b == NULL) {
            printf("FAIL %s: cannot read\n", name);
            fails++;
            continue;
        }
        memcpy(hd, b, sizeof(hd));
        p = sizeof(hd);
        rnd_state = 0x9E3779B9u ^ (uint32_t)cases;
        for (i = 0; i < (int32_t)(WORDS * 4u); i++) {
            const uint32_t v = rnd();

            memcpy(want + 4u * (size_t)i, &v, 4u);
        }
        for (c = 0; c < hd[3]; c++) {
            int32_t off, nw;

            memcpy(&off, b + p, 4u);
            memcpy(&nw, b + p + 4, 4u);
            p += 8u;
            memcpy(want + (size_t)off * 16u, b + p, (size_t)nw * 16u);
            p += (size_t)nw * 16u;
        }
        memcpy(bram, want, sizeof(want));                 /* inputs + pattern */
        for (c = 0; c < hd[4]; c++) {
            int32_t off, nw;

            memcpy(&off, b + p, 4u);
            memcpy(&nw, b + p + 4, 4u);
            p += 8u;
            memcpy(want + (size_t)off * 16u, b + p, (size_t)nw * 16u);
            p += (size_t)nw * 16u;
        }
        if (vis_run_raw((uint32_t)hd[1], &ret, &pl_us) != 0) {
            printf("FAIL %s: kernel timeout\n", name);
            fails++;
            free(b);
            cases++;
            continue;
        }
        pl_total += pl_us;
        for (i = 0; i < (int32_t)WORDS; i++) {
            if (memcmp(bram + 16u * (size_t)i, want + 16u * (size_t)i, 16u) != 0) {
                if (first < 0) {
                    first = i;
                }
                bad++;
            }
        }
        if ((int32_t)ret != hd[2] || bad) {
            printf("FAIL %s: ret %u (want %d), %d words differ (first %d)\n", name, ret, hd[2], bad, first);
            fails++;
        } else if (hd[5] & 1) {
            int32_t        e[9];
            const uint8_t *base;
            const int8_t  *q;
            uint8_t       *img;

            memcpy(e, b + p, sizeof(e));
            p += sizeof(e);
            base = e[8] == 2 ? b + p : NULL;
            if (base) {
                p += (size_t)e[0] * e[1] * 3u;
            }
            q = (const int8_t *)(b + p);
            img = (uint8_t *)malloc((size_t)e[0] * e[1] * 3u);
            ai_view_overlay_rgb(base, q, e[2], e[3], e[4], img, e[0], e[1]);
            if (memcmp(bram + (size_t)e[7] * 16u, img + (size_t)e[5] * e[0] * 3u, (size_t)(e[6] - e[5]) * e[0] * 3u) != 0) {
                printf("FAIL %s: differs from ai_view_overlay_rgb\n", name);
                fails++;
            }
            free(img);
            aiv++;
        }
        printf("TIME %s %u us\n", name, pl_us);
        free(b);
        cases++;
    }
    fclose(cl);
    printf("vis_layer on the PL: %d cases, %d failed, %d also vs ai_view_overlay_rgb, PL total %.1f ms\n", cases, fails,
           aiv, pl_total / 1000.0);
    if (fails == 0 && cases > 0) {
        printf("VIS_BOARD_PASS\n");
    }
    return fails ? 1 : 0;
}
