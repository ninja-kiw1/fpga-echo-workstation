/* ai_view_dump.c -- host harness: run the board's ai_view.c on a logit map and
 * dump the wire images, so the PC side (tcp_image_ui/confidence.py) can be
 * checked byte for byte against the exact code that runs on the board.
 *
 *   ai_view_dump <logits.bin> <lw> <lh> <base.rgb> <thr> <outdir>
 *
 * logits.bin : lw*lh int8 logits (row-major)
 * base.rgb   : 512x512 RGB888 frame used as the overlay background
 * writes     : <outdir>/mask.rgb, overlay.rgb, logit.rgb (512x512 RGB888 each)
 * prints     : "vessels=<n> comps=<n> total=<n>" (network resolution, like the stats block)
 *
 *   ai_view_dump --roi < rois.txt
 *     each stdin line "x y w h" -> stdout "cx cy cw ch" (ai_view_roi_crop)
 *   ai_view_dump --paste <crop.bin> <cx> <cy> <cw> <ch> <rx> <ry> <rw> <rh> <outdir>
 *     writes <outdir>/full.bin (512x512) and roi.bin (rw x rh)   (ai_view_roi_paste)
 *
 * Build (MinGW or Linux gcc):
 *   gcc -std=c99 -O2 -Wall -Wextra -I../src -o ai_view_dump ai_view_dump.c ../src/ai_view.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ai_view.h"
#include "image_proto.h"

static int read_file(const char *path, void *buf, size_t n)
{
    FILE  *f = fopen(path, "rb");
    size_t got;

    if (f == NULL) {
        return -1;
    }
    got = fread(buf, 1, n, f);
    fclose(f);
    return (got == n) ? 0 : -1;
}

static int write_file(const char *dir, const char *name, const void *buf, size_t n)
{
    char  path[1024];
    FILE *f;

    snprintf(path, sizeof(path), "%s/%s", dir, name);
    f = fopen(path, "wb");
    if (f == NULL || fwrite(buf, 1, n, f) != n) {
        if (f != NULL) {
            fclose(f);
        }
        return -1;
    }
    return fclose(f);
}

static int roi_table(void)
{
    int x, y, w, h, cx, cy, cw, ch;

    while (scanf("%d %d %d %d", &x, &y, &w, &h) == 4) {
        ai_view_roi_crop(x, y, w, h, &cx, &cy, &cw, &ch);
        printf("%d %d %d %d\n", cx, cy, cw, ch);
    }
    return 0;
}

static int roi_paste(char **argv)
{
    static int8_t crop[UNET_OUT_W * UNET_OUT_H];
    static int8_t full[UNET_OUT_W * UNET_OUT_H];
    static int8_t roi[UNET_OUT_W * UNET_OUT_H];
    int a[8], k;

    for (k = 0; k < 8; k++) {
        a[k] = atoi(argv[3 + k]);
    }
    if (read_file(argv[2], crop, (size_t)(a[2] * a[3])) != 0) {
        fprintf(stderr, "unreadable crop file\n");
        return 2;
    }
    ai_view_roi_paste(crop, a[0], a[1], a[2], a[4], a[5], a[6], a[7],
                      full, (int)UNET_OUT_W, (int)UNET_OUT_H, roi);
    if (write_file(argv[11], "full.bin", full, sizeof(full)) != 0 ||
        write_file(argv[11], "roi.bin", roi, (size_t)(a[6] * a[7])) != 0) {
        return 1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    static int8_t  logits[UNET_OUT_W * UNET_OUT_H];
    static uint8_t base[IMG_TOTAL_BYTES];
    static uint8_t out[IMG_TOTAL_BYTES];
    int            lw, lh, thr;
    uint32_t       vessels, comps = 0;

    if (argc == 2 && strcmp(argv[1], "--roi") == 0) {
        return roi_table();
    }
    if (argc == 12 && strcmp(argv[1], "--paste") == 0) {
        return roi_paste(argv);
    }
    if (argc != 7) {
        fprintf(stderr, "usage: %s logits.bin lw lh base.rgb thr outdir\n", argv[0]);
        return 2;
    }
    lw  = atoi(argv[2]);
    lh  = atoi(argv[3]);
    thr = atoi(argv[5]);
    if (lw <= 0 || lh <= 0 || lw * lh > (int)sizeof(logits)
        || read_file(argv[1], logits, (size_t)(lw * lh)) != 0
        || read_file(argv[4], base, sizeof(base)) != 0) {
        fprintf(stderr, "bad arguments or unreadable input\n");
        return 2;
    }

    vessels = ai_view_mask_rgb(logits, lw, lh, thr, out, (int)IMG_WIDTH, (int)IMG_HEIGHT, &comps);
    if (write_file(argv[6], "mask.rgb", out, sizeof(out)) != 0) { return 1; }
    ai_view_overlay_rgb(base, logits, lw, lh, thr, out, (int)IMG_WIDTH, (int)IMG_HEIGHT);
    if (write_file(argv[6], "overlay.rgb", out, sizeof(out)) != 0) { return 1; }
    ai_view_logit_rgb(logits, lw, lh, out, (int)IMG_WIDTH, (int)IMG_HEIGHT);
    if (write_file(argv[6], "logit.rgb", out, sizeof(out)) != 0) { return 1; }

    printf("vessels=%u comps=%u total=%u\n", (unsigned)vessels, (unsigned)comps,
           (unsigned)(lw * lh));
    return 0;
}
