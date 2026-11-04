/* vessel_morph_dump.c -- host harness for src/vessel_morph.c
 *
 *   vessel_morph_dump <mask.bin> <fov.bin|-> <w> <h> <res512> <skel_out.bin>
 *
 * mask.bin : w*h bytes, nonzero = vessel; fov.bin : w*h bytes or "-" (no FOV)
 * prints   : one "key=value" per metric (same fields as the 64-byte block)
 * writes   : skel_out.bin, w*h skeleton map with the VM_* codes
 *
 * optional, appended:  <block_out.bin> <base.rgb> <x0> <y0> <rw> <rh> <view_out.rgb>
 *   block_out.bin : the 64-byte wire block (vm_fill_block)
 *   view_out.rgb  : vm_draw() on the 512x512 RGB888 base, grid mapped to (x0, y0, rw, rh)
 *
 * Build (MinGW or Linux gcc):
 *   gcc -std=c99 -O2 -Wall -Wextra -I../src -o vessel_morph_dump vessel_morph_dump.c ../src/vessel_morph.c -lm
 * Used by ai/net/vessel_morph_check.py.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "vessel_morph.h"

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

int main(int argc, char **argv)
{
    static uint8_t mask[VM_MAX_W * VM_MAX_H];
    static uint8_t fov[VM_MAX_W * VM_MAX_H];
    static uint8_t skel[VM_MAX_W * VM_MAX_H];
    vm_result_t    r;
    int            w, h, res;
    FILE          *f;

    if (argc != 7 && argc != 14) {
        fprintf(stderr, "usage: %s mask.bin fov.bin|- w h res512 skel_out.bin "
                "[block_out.bin base.rgb x0 y0 rw rh view_out.rgb]\n", argv[0]);
        return 2;
    }
    w = atoi(argv[3]);
    h = atoi(argv[4]);
    res = atoi(argv[5]);
    if (w < 1 || h < 1 || w > VM_MAX_W || h > VM_MAX_H ||
        read_file(argv[1], mask, (size_t)(w * h)) != 0 ||
        (strcmp(argv[2], "-") != 0 && read_file(argv[2], fov, (size_t)(w * h)) != 0)) {
        fprintf(stderr, "bad arguments or unreadable input\n");
        return 2;
    }
    if (vm_analyze(mask, strcmp(argv[2], "-") != 0 ? fov : NULL, w, h, res, skel, &r) != 0) {
        return 1;
    }
    f = fopen(argv[6], "wb");
    if (f == NULL || fwrite(skel, 1, (size_t)(w * h), f) != (size_t)(w * h)) {
        return 1;
    }
    fclose(f);
    if (argc == 14) {
        static uint8_t base[VM_MAX_W * VM_MAX_H * 3];
        static uint8_t view[VM_MAX_W * VM_MAX_H * 3];
        uint8_t        blk[VM_BLOCK_BYTES];

        vm_fill_block(blk, &r);
        if (read_file(argv[8], base, sizeof(base)) != 0) {
            return 2;
        }
        vm_draw(base, skel, w, h, atoi(argv[9]), atoi(argv[10]), atoi(argv[11]),
                atoi(argv[12]), view);
        f = fopen(argv[7], "wb");
        if (f == NULL || fwrite(blk, 1, sizeof(blk), f) != sizeof(blk)) {
            return 1;
        }
        fclose(f);
        f = fopen(argv[13], "wb");
        if (f == NULL || fwrite(view, 1, sizeof(view), f) != sizeof(view)) {
            return 1;
        }
        fclose(f);
    }
    printf("vessel_px=%u\nfov_px=%u\nlength_x10=%u\ncaliber_x100=%u\nbranches=%u\n"
           "endpoints=%u\nsegments=%u\ntortuosity_x1000=%u\nfractal_x1000=%u\n"
           "density_x1000=%u\nquad_x1000=%u,%u,%u,%u\n",
           (unsigned)r.vessel_px, (unsigned)r.fov_px, (unsigned)r.length_x10,
           (unsigned)r.caliber_x100, (unsigned)r.branches, (unsigned)r.endpoints,
           (unsigned)r.segments, (unsigned)r.tortuosity_x1000, (unsigned)r.fractal_x1000,
           (unsigned)r.density_x1000, (unsigned)r.quad_x1000[0], (unsigned)r.quad_x1000[1],
           (unsigned)r.quad_x1000[2], (unsigned)r.quad_x1000[3]);
    return 0;
}
