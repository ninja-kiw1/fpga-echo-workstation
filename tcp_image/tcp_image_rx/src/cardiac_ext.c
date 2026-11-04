/* cardiac_ext.c -- regional wall motion + right-heart plausibility (see cardiac_ext.h). Built with -ffp-contract=off
 * like the other echo C, so E:/PLD/keyframe/echo_core.dll (the same file) gives the board's numbers bit for bit. */
#include "cardiac_ext.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

static uint32_t le32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static double le64d(const uint8_t *p)
{
    uint64_t u = (uint64_t)le32(p) | (uint64_t)le32(p + 4) << 32;
    double   d;

    memcpy(&d, &u, sizeof(d));
    return d;
}

int hc_parse(const uint8_t *x, uint32_t nbytes, hc_par_t *p)
{
    int k;

    if (nbytes < HC_BYTES || le32(x) != HC_MAGIC) {
        return -1;
    }
    memset(p, 0, sizeof(*p));
    p->flags = x[4];
    p->rv_model = x[5];
    p->ra_model = x[6];
    for (k = 0; k < WM_SEG; k++) {
        p->mu[k] = le64d(x + 8 + 8 * k);
        p->sd[k] = le64d(x + 56 + 8 * k);
    }
    p->t_seg = le64d(x + 104);
    for (k = 0; k < 4; k++) {
        p->w[k] = le64d(x + 112 + 8 * k);
    }
    p->t_rec = le64d(x + 144);
    p->min_frac = le64d(x + 152);
    p->max_overlap = le64d(x + 160);
    return 0;
}

/* region index side * 3 + third -> HMC-QU order */
static const int REG[WM_SEG] = {0, 1, 2, 5, 4, 3};

typedef struct {
    double cx, cy, ax, ay, nx, ny, s0, len;
} wm_frame_t;

static int wm_frame(const uint8_t *ed, int w, int h, wm_frame_t *f)
{
    double sx = 0.0, sy = 0.0, sxx = 0.0, syy = 0.0, sxy = 0.0, lam, v1x, v1y, v2x, v2y, n1, n2, smin = 0.0, smax = 0.0;
    int    x, y, cnt = 0, first = 1;

    for (y = 0; y < h; y++) {
        for (x = 0; x < w; x++) {
            if (ed[y * w + x]) {
                sx += x;
                sy += y;
                cnt++;
            }
        }
    }
    if (cnt < 50) {
        return -1;
    }
    f->cx = sx / cnt;
    f->cy = sy / cnt;
    for (y = 0; y < h; y++) {
        for (x = 0; x < w; x++) {
            if (ed[y * w + x]) {
                double px = x - f->cx, py = y - f->cy;

                sxx += px * px;
                syy += py * py;
                sxy += px * py;
            }
        }
    }
    lam = 0.5 * (sxx + syy) + sqrt(0.25 * (sxx - syy) * (sxx - syy) + sxy * sxy);
    v1x = sxy;
    v1y = lam - sxx;
    v2x = lam - syy;
    v2y = sxy;
    n1 = v1x * v1x + v1y * v1y;
    n2 = v2x * v2x + v2y * v2y;
    if (n1 >= n2 && n1 > 0.0) {
        f->ax = v1x / sqrt(n1);
        f->ay = v1y / sqrt(n1);
    } else if (n2 > 0.0) {
        f->ax = v2x / sqrt(n2);
        f->ay = v2y / sqrt(n2);
    } else {
        f->ax = 0.0;
        f->ay = -1.0;
    }
    if (f->ay > 0.0) {                  /* point to the apex (up = smaller y) */
        f->ax = -f->ax;
        f->ay = -f->ay;
    }
    f->nx = -f->ay;                     /* perpendicular, pointing to the image right */
    f->ny = f->ax;
    if (f->nx < 0.0) {
        f->nx = -f->nx;
        f->ny = -f->ny;
    }
    for (y = 0; y < h; y++) {
        for (x = 0; x < w; x++) {
            if (ed[y * w + x]) {
                double s = (x - f->cx) * f->ax + (y - f->cy) * f->ay;

                if (first || s < smin) {
                    smin = s;
                }
                if (first || s > smax) {
                    smax = s;
                }
                first = 0;
            }
        }
    }
    f->s0 = smin;
    f->len = smax - smin;
    return f->len > 0.0 ? 0 : -1;
}

static void wm_regions(const uint8_t *m, int w, int h, const wm_frame_t *f, long cnt[WM_SEG])
{
    int x, y;

    memset(cnt, 0, sizeof(long) * WM_SEG);
    for (y = 0; y < h; y++) {
        for (x = 0; x < w; x++) {
            if (m[y * w + x]) {
                double px = x - f->cx, py = y - f->cy, u = (px * f->ax + py * f->ay - f->s0) / f->len;
                int    side = px * f->nx + py * f->ny > 0.0, third;

                if (u < 0.0) {
                    u = 0.0;
                }
                if (u > 1.0 - 1e-9) {
                    u = 1.0 - 1e-9;
                }
                third = (int)(u * 3.0);
                cnt[side * 3 + third]++;
            }
        }
    }
}

int wm_beat(const uint8_t *ed, const uint8_t *es, int w, int h, double fac[WM_SEG])
{
    wm_frame_t f;
    long       a_ed[WM_SEG], a_es[WM_SEG];
    int        k;

    if (wm_frame(ed, w, h, &f) != 0) {
        return -1;
    }
    wm_regions(ed, w, h, &f, a_ed);
    wm_regions(es, w, h, &f, a_es);
    for (k = 0; k < WM_SEG; k++) {
        long e = a_ed[REG[k]];

        fac[k] = 1.0 - (double)a_es[REG[k]] / (double)(e > 1 ? e : 1);
    }
    return 0;
}

void wm_eval(const hc_par_t *p, const double fac[WM_SEG], int nbeats, double ef, wm_out_t *o)
{
    double zsum = 0.0, zmin = 0.0;
    int    k;

    memset(o, 0, sizeof(*o));
    o->nbeats = nbeats;
    for (k = 0; k < WM_SEG; k++) {
        o->fac[k] = fac[k];
        o->z[k] = (fac[k] - p->mu[k]) / p->sd[k];
        o->seg[k] = -o->z[k] >= p->t_seg;
        zsum += o->z[k];
        zmin = (k == 0 || o->z[k] < zmin) ? o->z[k] : zmin;
    }
    if (!isnan(ef)) {
        o->score = p->w[0] + p->w[1] * ((ef - 50.0) / 10.0) + p->w[2] * (zsum / WM_SEG) + p->w[3] * zmin;
        o->flag = o->score >= p->t_rec;
        o->has_score = 1;
    }
}

int rh_largest(const uint8_t *in, int w, int h, uint8_t *out)
{
    int *lab = (int *)calloc((size_t)w * h, sizeof(int)), *stack = (int *)malloc((size_t)w * h * sizeof(int));
    int  n = 0, best = 0, best_area = 0, i;

    memset(out, 0, (size_t)w * h);
    if (lab == NULL || stack == NULL) {
        free(lab);
        free(stack);
        return 0;
    }
    for (i = 0; i < w * h; i++) {               /* scan order labels = scipy.ndimage.label order */
        if (in[i] && !lab[i]) {
            int sp = 0, area = 0;

            lab[i] = ++n;
            stack[sp++] = i;
            while (sp) {
                int q = stack[--sp], x = q % w, y = q / w;

                area++;
                if (x > 0 && in[q - 1] && !lab[q - 1]) {
                    lab[q - 1] = n;
                    stack[sp++] = q - 1;
                }
                if (x < w - 1 && in[q + 1] && !lab[q + 1]) {
                    lab[q + 1] = n;
                    stack[sp++] = q + 1;
                }
                if (y > 0 && in[q - w] && !lab[q - w]) {
                    lab[q - w] = n;
                    stack[sp++] = q - w;
                }
                if (y < h - 1 && in[q + w] && !lab[q + w]) {
                    lab[q + w] = n;
                    stack[sp++] = q + w;
                }
            }
            if (area > best_area) {             /* first of equal areas wins, like numpy argmax */
                best_area = area;
                best = n;
            }
        }
    }
    for (i = 0; i < w * h; i++) {
        out[i] = (uint8_t)(best && lab[i] == best);
    }
    free(lab);
    free(stack);
    return best_area;
}

void rh_check(const uint8_t *part, const uint8_t *lv, int w, int h, const hc_par_t *p, rh_part_t *o)
{
    double sy = 0.0, sx = 0.0, ly = 0.0, lx = 0.0;
    int    x, y, inside = 0;

    memset(o, 0, sizeof(*o));
    for (y = 0; y < h; y++) {
        for (x = 0; x < w; x++) {
            int i = y * w + x;

            if (part[i]) {
                o->area++;
                sy += y;
                sx += x;
                inside += lv[i] != 0;
            }
            if (lv[i]) {
                o->lv_area++;
                ly += y;
                lx += x;
            }
        }
    }
    o->cy = o->area ? sy / o->area : NAN;
    o->cx = o->area ? sx / o->area : NAN;
    o->overlap = (double)inside / (double)(o->area > 1 ? o->area : 1);
    o->present = o->area >= p->min_frac * (double)(o->lv_area > 1 ? o->lv_area : 1);
    o->side = o->area > 0 && o->lv_area > 0 && o->cx < lx / o->lv_area;
    o->ok = o->present && o->side && o->overlap < p->max_overlap;
}
