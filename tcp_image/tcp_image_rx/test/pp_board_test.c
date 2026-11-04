/* pp_board_test.c -- run the pp_layer test vectors (ai/pp_hls/vectors) on the real PL and compare bit for bit.
 *
 * Stop tcp-image-server first (the PL has one user at a time). Refuses to touch the pp_layer registers unless
 * the loaded firmware is listed in /lib/firmware/ai_sys_pp.sha256 (wrong bitstream = PS hangs).
 *
 *   gcc -O2 -std=c99 -D_GNU_SOURCE -Isrc test/pp_board_test.c src/pp_ai.c src/unet_ai.c src/unet_models.c \
 *       src/unet_net_data.c src/ai_view.c -o pp_board_test -lm
 *   ./pp_board_test <vectors dir> [cases.txt] [repeat]
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "ai_view.h"
#include "pp_ai.h"
#include "unet_ai.h"

static uint8_t buf[2u << 20];
static uint8_t mask[512 * 64];

static double ms_now(void)
{
    struct timespec t;

    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e3 + t.tv_nsec / 1e6;
}

int main(int argc, char **argv)
{
    char  path[512], name[256];
    FILE *cl;
    int   cases = 0, fails = 0, rep = argc > 3 ? atoi(argv[3]) : 1;
    const char *list = argc > 2 ? argv[2] : "cases.txt";

    if (argc < 2) {
        printf("usage: %s <vectors dir> [cases.txt] [repeat]\n", argv[0]);
        return 2;
    }
    if (!pp_firmware_ok()) {
        printf("loaded firmware is not listed in %s: refusing to touch pp_layer\n", PP_FIRMWARE_LIST);
        return 3;
    }
    if (unet_init(0xA0010000ull, 0xA0020000ull, 0xB0000000ull) != 0 || pp_init(PP_DEF_CTRL) != 0) {
        return 4;
    }
    snprintf(path, sizeof(path), "%s/%s", argv[1], list);
    cl = fopen(path, "r");
    if (cl == NULL) {
        printf("cannot open %s\n", path);
        return 2;
    }
    while (fscanf(cl, "%255s", name) == 1) {
        int32_t      hd[16], tiles[16];
        FILE        *f;
        size_t       n;
        pp_result_t  r;
        int          op, h, w, x0, y0, cw, ch, k, thr, tn, mh, mw, rc, bad = 0, i, y, x, it;
        const int8_t *q;
        const uint8_t *em;
        double       t0, pl_ms = 0, tot_ms = 0;
        size_t       mrow;

        snprintf(path, sizeof(path), "%s/%s.bin", argv[1], name);
        f = fopen(path, "rb");
        n = f ? fread(buf, 1, sizeof(buf), f) : 0;
        if (f) fclose(f);
        if (n < 128) {
            printf("FAIL %s: cannot read\n", name);
            fails++;
            continue;
        }
        memcpy(hd, buf, 64);
        memcpy(tiles, buf + 64, 64);
        op = hd[1]; h = hd[2]; w = hd[3]; x0 = hd[4]; y0 = hd[5]; cw = hd[6]; ch = hd[7]; k = hd[8];
        thr = hd[9]; tn = hd[10]; mh = hd[14]; mw = hd[15];
        q = (const int8_t *)(buf + 128);
        em = buf + 128 + h * w;
        mrow = pp_mask_row_bytes(mw);
        rc = 0;
        for (it = 0; it < rep && rc == 0; it++) {
            memset(mask, 0xA5, sizeof(mask));
            t0 = ms_now();
            rc = pp_run(q, h, w, (unsigned)op, x0, y0, cw, ch, k, thr, tn, mask, &r);
            tot_ms += ms_now() - t0;
            pl_ms += r.pl_us / 1000.0;
        }
        if (rc == 0 && (op & PP_OP_MASK)) {
            for (y = 0; y < mh; y++)
                for (x = 0; x < mw; x++)
                    bad += ((mask[y * mrow + (x >> 3)] >> (x & 7)) & 1) != em[y * mw + x];
        }
        int ok = rc == 0 && r.status == 0 && (int)r.count == hd[11] && (int)r.comps == hd[12] &&
                 (int)r.largest == hd[13] && bad == 0;
        if (op & PP_OP_TILES)
            for (i = 0; i < 16; i++) ok = ok && r.tiles[i] == tiles[i];
        cases++;
        if (!ok) {
            fails++;
            printf("FAIL %s: rc %d status %u count %u/%d comps %u/%d largest %u/%d mask px %d\n", name, rc,
                   r.status, r.count, hd[11], r.comps, hd[12], r.largest, hd[13], bad);
        }
        if (strncmp(name, "fundus", 6) == 0 || strncmp(name, "ct_", 3) == 0 || strncmp(name, "echo", 4) == 0) {
            printf("TIME %-36s calls %u pl %.3f ms total %.3f ms", name, r.calls, pl_ms / rep, tot_ms / rep);
            if (strncmp(name, "fundus", 6) == 0 && !(op & PP_OP_CONN8)) {
                uint32_t c = 0;
                double   a = ms_now();
                for (it = 0; it < rep; it++) (void)ai_view_count(q, w, h, thr, &c);
                printf("  | PS ai_view_count %.3f ms", (ms_now() - a) / rep);
            }
            printf("\n");
        }
    }
    fclose(cl);
    printf("%d cases, %d failures\n", cases, fails);
    printf(fails == 0 && cases > 0 ? "PP_BOARD_PASS\n" : "PP_BOARD_FAIL\n");
    pp_close();
    unet_close();
    return fails != 0;
}
