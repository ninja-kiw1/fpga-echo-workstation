/* fundus_cdr.c -- part D vertical cup-to-disc ratio on the PS (see fundus_cdr.h; reference E:/PLD/fundus_cdr/cdr_ref.py). */
#include "fundus_cdr.h"

#include <stdlib.h>
#include <string.h>

void fcdr_box_rgb(const uint8_t *src, int sw, int x0, int y0, int cw, int k, uint8_t *dst)
{
    const int n = cw / k;
    const uint32_t kk = (uint32_t)(k * k), half = kk / 2u;
    for (int y = 0; y < n; y++)
        for (int x = 0; x < n; x++)
            for (int c = 0; c < 3; c++) {
                uint32_t s = 0;
                for (int dy = 0; dy < k; dy++) {
                    const uint8_t *row = src + ((size_t)(y0 + y * k + dy) * (size_t)sw + (size_t)(x0 + x * k)) * 3u + c;
                    for (int dx = 0; dx < k; dx++)
                        s += row[dx * 3];
                }
                dst[((size_t)y * n + x) * 3u + c] = (uint8_t)((s + half) / kk);
            }
}

long fcdr_largest_cc(uint8_t *m, int h, int w)
{
    const int np = h * w;
    int32_t *lab = calloc((size_t)np, sizeof *lab);
    int32_t *q = malloc((size_t)np * sizeof *q);
    if (!lab || !q) {
        free(lab);
        free(q);
        return -1;
    }
    int32_t n = 0, best = 0;
    long best_n = 0;
    for (int p0 = 0; p0 < np; p0++) {
        if (!m[p0] || lab[p0])
            continue;
        lab[p0] = ++n;
        int head = 0, tail = 0;
        q[tail++] = p0;
        while (head < tail) {
            const int p = q[head++], y = p / w, x = p % w;
            for (int dy = -1; dy <= 1; dy++)
                for (int dx = -1; dx <= 1; dx++) {
                    const int yy = y + dy, xx = x + dx;
                    if (yy < 0 || yy >= h || xx < 0 || xx >= w)
                        continue;
                    const int pp = yy * w + xx;
                    if (m[pp] && !lab[pp]) {
                        lab[pp] = n;
                        q[tail++] = pp;
                    }
                }
        }
        if ((long)tail > best_n) {      /* strictly larger: ties keep the earlier component */
            best_n = tail;
            best = n;
        }
    }
    for (int p = 0; p < np; p++)
        m[p] = (uint8_t)(best && lab[p] == best);
    free(lab);
    free(q);
    return best_n;
}

int fcdr_fill_holes(uint8_t *m, int h, int w)
{
    const int np = h * w;
    uint8_t *out = calloc((size_t)np, 1);
    int32_t *q = malloc((size_t)np * sizeof *q);
    if (!out || !q) {
        free(out);
        free(q);
        return -1;
    }
    int head = 0, tail = 0;
#define SEED(p) do { if (!m[p] && !out[p]) { out[p] = 1; q[tail++] = (p); } } while (0)
    for (int y = 0; y < h; y++) {
        SEED(y * w);
        SEED(y * w + w - 1);
    }
    for (int x = 0; x < w; x++) {
        SEED(x);
        SEED((h - 1) * w + x);
    }
    while (head < tail) {
        const int p = q[head++], y = p / w, x = p % w;
        if (y > 0) SEED(p - w);
        if (y < h - 1) SEED(p + w);
        if (x > 0) SEED(p - 1);
        if (x < w - 1) SEED(p + 1);
    }
#undef SEED
    for (int p = 0; p < np; p++)
        m[p] = (uint8_t)!out[p];
    free(out);
    free(q);
    return 0;
}

/* output index o of a bilinear x k: value = (wa * q[a] + wb * q[b]) / (2k) */
static void up_index(int o, int n, int k, int *a, int *b, int *wa, int *wb)
{
    int t = 2 * o + 1 - k;
    if (t < 0)
        t = 0;
    *a = t / (2 * k);
    *wb = t - 2 * k * *a;
    *wa = 2 * k - *wb;
    *b = *a + 1 < n ? *a + 1 : n - 1;
}

void fcdr_up_mask(const int8_t *q, int n, int k, int thr, uint8_t *out)
{
    const int N = n * k;
    if (k == 1) {
        for (int p = 0; p < n * n; p++)
            out[p] = (uint8_t)(q[p] >= thr);
        return;
    }
    const long lim = (long)(2 * thr - 1) * (long)(4 * k * k);
    for (int y = 0; y < N; y++) {
        int ya, yb, wya, wyb;
        up_index(y, n, k, &ya, &yb, &wya, &wyb);
        for (int x = 0; x < N; x++) {
            int xa, xb, wxa, wxb;
            up_index(x, n, k, &xa, &xb, &wxa, &wxb);
            const long ra = (long)q[ya * n + xa] * wya + (long)q[yb * n + xa] * wyb;
            const long rb = (long)q[ya * n + xb] * wya + (long)q[yb * n + xb] * wyb;
            out[y * N + x] = (uint8_t)(2 * (ra * wxa + rb * wxb) >= lim);
        }
    }
}

int fcdr_locate(const int8_t *q1, int thr, fcdr_loc_t *loc)
{
    const int n = FCDR_PAY;
    uint8_t *m = malloc((size_t)n * n);
    if (!m)
        return -1;
    for (int p = 0; p < n * n; p++)
        m[p] = (uint8_t)(q1[p] >= thr);
    const long area = fcdr_largest_cc(m, n, n);
    if (area < 0) {
        free(m);
        return -1;
    }
    memset(loc, 0, sizeof *loc);
    if (area < FCDR_MIN_DISC_PX) {
        free(m);
        return 0;
    }
    int r0 = n, r1 = -1, c0 = n, c1 = -1;
    for (int y = 0; y < n; y++)
        for (int x = 0; x < n; x++)
            if (m[y * n + x]) {
                if (y < r0) r0 = y;
                if (y > r1) r1 = y;
                if (x < c0) c0 = x;
                if (x > c1) c1 = x;
            }
    free(m);
    const int bh = r1 - r0 + 1, bw = c1 - c0 + 1;
    loc->cy = 2 * (r0 + r1 + 1);
    loc->cx = 2 * (c0 + c1 + 1);
    loc->size = 4 * (bh > bw ? bh : bw);
    loc->area = (int)area;
    loc->r0 = r0;
    loc->r1 = r1;
    loc->c0 = c0;
    loc->c1 = c1;
    return 1;
}

static int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }

int fcdr_side_for(int size)
{
    for (int k = 1; k <= 3; k++)
        if (128L * k * FCDR_RATIO_DEN >= (long)FCDR_RATIO_NUM * size)
            return 128 * k;
    return 512;
}

void fcdr_window(int cy, int cx, int side, int *y0, int *x0)
{
    *y0 = clampi(cy - side / 2, 0, 512 - side);
    *x0 = clampi(cx - side / 2, 0, 512 - side);
}

static void rows_spanned(const uint8_t *m, int side, int *h, int *top)
{
    int r0 = -1, r1 = -1;
    for (int y = 0; y < side; y++) {
        const uint8_t *row = m + (size_t)y * side;
        int any = 0;
        for (int x = 0; x < side && !any; x++)
            any = row[x];
        if (any) {
            if (r0 < 0)
                r0 = y;
            r1 = y;
        }
    }
    *top = r0;
    *h = r0 < 0 ? 0 : r1 - r0 + 1;
}

int fcdr_measure(const int8_t *qd, const int8_t *qc, int side, int thr_d, int thr_c, fcdr_result_t *r,
                 uint8_t *disc, uint8_t *cup)
{
    if (side < FCDR_PAY || side > 512 || side % FCDR_PAY)
        return -1;
    const int k = side / FCDR_PAY, np = side * side;
    uint8_t *d = malloc((size_t)np), *c = malloc((size_t)np);
    int rc = -1;
    if (!d || !c)
        goto out;
    fcdr_up_mask(qd, FCDR_PAY, k, thr_d, d);
    if (fcdr_largest_cc(d, side, side) < 0 || fcdr_fill_holes(d, side, side) < 0)
        goto out;
    fcdr_up_mask(qc, FCDR_PAY, k, thr_c, c);
    for (int p = 0; p < np; p++)
        c[p] &= d[p];
    if (fcdr_largest_cc(c, side, side) < 0 || fcdr_fill_holes(c, side, side) < 0)
        goto out;
    memset(r, 0, sizeof *r);
    rows_spanned(d, side, &r->disc_h, &r->disc_top);
    rows_spanned(c, side, &r->cup_h, &r->cup_top);
    for (int p = 0; p < np; p++) {
        r->disc_area += d[p];
        r->cup_area += c[p];
    }
    r->vcdr = r->disc_h ? (double)r->cup_h / (double)r->disc_h : 0.0;
    if (disc)
        memcpy(disc, d, (size_t)np);
    if (cup)
        memcpy(cup, c, (size_t)np);
    rc = 0;
out:
    free(d);
    free(c);
    return rc;
}
