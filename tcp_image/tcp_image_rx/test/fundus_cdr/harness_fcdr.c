/* harness_fcdr.c -- host check of src/fundus_cdr.c against E:/PLD/fundus_cdr/cdr_ref.py (driven by compare.py).
 *
 * Input file: u32 count, then per case
 *   u8 kind (0 = whole case: img512 + q1 / qd / qc; 1 = measure only: side + qd / qc), u8 thr, u16 side (kind 1)
 *   kind 0: img512 RGB (512*512*3), q1, qd, qc (int8 128*128 each)
 *   kind 1: qd, qc
 * Output file, per case: i32 found, i32 cy, cx, size, area, side, y0, x0 (kind 0; else zeros except side),
 *   payload1 + payload2 (kind 0; 128*128*3 each), i32 disc_h, cup_h, disc_top, cup_top, i64 disc_area, cup_area,
 *   f64 vcdr, disc mask (side*side), cup mask (side*side).
 */
#include "fundus_cdr.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void rd(void *p, size_t n, FILE *f) { if (fread(p, 1, n, f) != n) { fprintf(stderr, "short read\n"); exit(2); } }
static void wr(const void *p, size_t n, FILE *f) { if (fwrite(p, 1, n, f) != n) { fprintf(stderr, "short write\n"); exit(2); } }
static void wi(int32_t v, FILE *f) { wr(&v, 4, f); }

int main(int argc, char **argv)
{
    if (argc != 3)
        return 2;
    FILE *fi = fopen(argv[1], "rb"), *fo = fopen(argv[2], "wb");
    if (!fi || !fo)
        return 2;
    uint32_t n;
    rd(&n, 4, fi);
    const size_t P = (size_t)FCDR_PAY * FCDR_PAY;
    uint8_t *img = malloc(512u * 512u * 3u), *p1 = malloc(P * 3), *p2 = malloc(P * 3);
    int8_t *q1 = malloc(P), *qd = malloc(P), *qc = malloc(P);
    uint8_t *dm = malloc(512u * 512u), *cm = malloc(512u * 512u);
    for (uint32_t i = 0; i < n; i++) {
        uint8_t kind, thr;
        uint16_t side;
        rd(&kind, 1, fi);
        rd(&thr, 1, fi);
        rd(&side, 2, fi);
        fcdr_loc_t loc;
        memset(&loc, 0, sizeof loc);
        int found = 0, y0 = 0, x0 = 0, s = side;
        memset(p1, 0, P * 3);
        memset(p2, 0, P * 3);
        if (kind == 0) {
            rd(img, 512u * 512u * 3u, fi);
            rd(q1, P, fi);
            rd(qd, P, fi);
            rd(qc, P, fi);
            fcdr_box_rgb(img, 512, 0, 0, 512, 4, p1);
            found = fcdr_locate(q1, thr, &loc);
            if (found == 1) {
                s = fcdr_side_for(loc.size);
                fcdr_window(loc.cy, loc.cx, s, &y0, &x0);
                fcdr_box_rgb(img, 512, x0, y0, s, s / FCDR_PAY, p2);
            } else {
                s = FCDR_PAY;
            }
        } else {
            rd(qd, P, fi);
            rd(qc, P, fi);
        }
        fcdr_result_t r;
        memset(&r, 0, sizeof r);
        memset(dm, 0, 512u * 512u);
        memset(cm, 0, 512u * 512u);
        if ((kind == 1 || found == 1) && fcdr_measure(qd, qc, s, thr, thr, &r, dm, cm) != 0) {
            fprintf(stderr, "measure failed\n");
            return 3;
        }
        wi(found, fo); wi(loc.cy, fo); wi(loc.cx, fo); wi(loc.size, fo); wi(loc.area, fo);
        wi(s, fo); wi(y0, fo); wi(x0, fo);
        wr(p1, P * 3, fo);
        wr(p2, P * 3, fo);
        wi(r.disc_h, fo); wi(r.cup_h, fo); wi(r.disc_top, fo); wi(r.cup_top, fo);
        int64_t a = r.disc_area, b = r.cup_area;
        wr(&a, 8, fo); wr(&b, 8, fo);
        wr(&r.vcdr, 8, fo);
        wr(dm, (size_t)s * s, fo);
        wr(cm, (size_t)s * s, fo);
    }
    fclose(fi);
    fclose(fo);
    return 0;
}
