/* host_ovl_ps.c -- the PS fallback of JOB_OVERLAY (src/job_overlay.c overlay_ps) on the cases of check_driver.py */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../../src/job.h"
#include "../../src/vis_ai.h"
int job_fail(job_t *j, const char *m) { (void)j; (void)m; return 0; }
int job_blob(job_t *j, const char *n, const void *p, size_t s, int z) { (void)j; (void)n; (void)p; (void)s; (void)z; return 0; }
int job_result(job_t *j, const char *js) { (void)j; (void)js; return 0; }
uint64_t job_now_us(void) { return 0; }
void jb_init(jbuf_t *b, char *mem, size_t cap) { b->s = mem; b->cap = cap; b->len = 0; mem[0] = 0; }
void jb_add(jbuf_t *b, const char *fmt, ...) { (void)b; (void)fmt; }
int vis_ready(void) { return 0; }
int vis_overlay(const vis_ovl_t *c, const uint8_t *a, const int8_t *q, const uint8_t *bt, const uint8_t l[256][3], uint8_t *r,
                uint32_t *p, uint32_t *n) { (void)c; (void)a; (void)q; (void)bt; (void)l; (void)r; (void)p; (void)n; return -1; }
#include "../../src/job_overlay.c"

static unsigned char *rd(FILE *f, size_t n) { unsigned char *v = malloc(n ? n : 1); if (n && fread(v, 1, n, f) != n) exit(3); return v; }

int main(int argc, char **argv)
{
    FILE *f = fopen(argv[1], "rb"), *o = fopen(argv[2], "wb");
    int kind;
    while (fread(&kind, 4, 1, f) == 1) {
        if (kind == 1) {               /* registration case: skip */
            int h[9];
            if (fread(h, 4, 9, f) != 9) return 3;
            free(rd(f, (size_t)2 * h[0] * h[0] + 256));
            continue;
        }
        int h[16];
        if (fread(h, 4, 16, f) != 16) return 3;
        vis_ovl_t c;
        memset(&c, 0, sizeof(c));
        c.ow = h[0]; c.oh = h[1]; c.flags = h[2]; c.lw = h[3]; c.lh = h[4]; c.mw = h[5]; c.mh = h[6];
        c.thr = h[7]; c.band = h[8]; c.fill_a = h[9]; c.unsure_a = h[10];
        for (int k = 0; k < 3; k++) {
            c.fill[k] = (uint8_t)(h[11] >> (8 * k)); c.unsure[k] = (uint8_t)(h[12] >> (8 * k)); c.contour[k] = (uint8_t)(h[13] >> (8 * k));
        }
        int kb = c.flags & 3;
        unsigned char *base = rd(f, kb == 2 ? 3 * c.ow * c.oh : (kb == 1 ? c.ow * c.oh : 0));
        unsigned char *lg = rd(f, (c.flags & 8) ? c.lw * c.lh : 0);
        unsigned char *bits = rd(f, (c.flags & 16) ? (c.mw + 127) / 128 * 16 * c.mh : 0);
        unsigned char *lut = rd(f, kb == 3 ? 768 : 0);
        unsigned char *rgb = malloc(3 * c.ow * c.oh);
        int rc = overlay_ps(&c, base, (const int8_t *)lg, bits, (const uint8_t (*)[3])lut, rgb), calls = 0;
        fwrite(&rc, 4, 1, o); fwrite(&calls, 4, 1, o); fwrite(rgb, 1, 3 * c.ow * c.oh, o);
        free(base); free(lg); free(bits); free(lut); free(rgb);
    }
    fclose(o);
    return 0;
}
