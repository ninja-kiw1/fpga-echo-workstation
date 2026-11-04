/* kf.c -- see kf.h. Numeric file: built with -ffp-contract=off (makefile, keyframe/build_core.sh). */
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "bm_sad.h"
#include "echo_num.h"
#include "kf.h"

/* ---------------------------------------------------------------- contour and fill */

static const int DY[4] = {0, 1, 0, -1}, DX[4] = {1, 0, -1, 0};   /* E S W N (y down) */

static int px_at(const uint8_t *m, int W, int H, int r, int c)
{
    return r >= 0 && r < H && c >= 0 && c < W && m[r * W + c];
}

/* pixels on the right / left of the pixel edge from corner (R, C) in direction d; corner (R, C) is the top-left
 * corner of pixel (R, C) */
static void edge_sides(int R, int C, int d, int *rr, int *rc, int *lr, int *lc)
{
    switch (d) {
    case 0:  *rr = R;     *rc = C;     *lr = R - 1; *lc = C;     break;   /* E */
    case 1:  *rr = R;     *rc = C - 1; *lr = R;     *lc = C;     break;   /* S */
    case 2:  *rr = R - 1; *rc = C - 1; *lr = R;     *lc = C - 1; break;   /* W */
    default: *rr = R - 1; *rc = C;     *lr = R - 1; *lc = C - 1; break;   /* N */
    }
}

int kf_contour(const uint8_t *m, int W, int H, double spacing, double *py, double *px, int maxp)
{
    int     r0 = -1, c0 = -1, R, C, d = 0, nv = 0, cap = 4 * (W + H) + 8 * W, i, n, j, start;
    double *vy, *vx, *s, L, step;

    for (i = 0; i < W * H; i++) {
        if (m[i]) {
            r0 = i / W;
            c0 = i % W;
            break;
        }
    }
    if (r0 < 0) {
        return 0;
    }
    vy = (double *)malloc((size_t)(cap + 1) * sizeof(double));
    vx = (double *)malloc((size_t)(cap + 1) * sizeof(double));
    s = (double *)malloc((size_t)(cap + 2) * sizeof(double));
    if (vy == NULL || vx == NULL || s == NULL) {
        free(vy);
        free(vx);
        free(s);
        return 0;
    }
    R = r0;
    C = c0;
    do {                                         /* walk the border, foreground on the right */
        int rr, rc, lr, lc;

        vy[nv] = (R - 0.5) + 0.5 * DY[d];        /* middle of the pixel edge */
        vx[nv] = (C - 0.5) + 0.5 * DX[d];
        nv++;
        R += DY[d];
        C += DX[d];
        edge_sides(R, C, d, &rr, &rc, &lr, &lc); /* the two pixels ahead */
        if (!px_at(m, W, H, rr, rc)) {
            d = (d + 1) & 3;                     /* ahead-right is background: turn right */
        } else if (px_at(m, W, H, lr, lc)) {
            d = (d + 3) & 3;                     /* ahead-left is foreground: turn left */
        }
    } while (!(R == r0 && C == c0 && d == 0) && nv < cap);
    /* arc length on the closed polyline, then n points every L / n */
    s[0] = 0.0;
    for (i = 0; i < nv; i++) {
        int    k = (i + 1) % nv;
        double dy = vy[k] - vy[i], dx = vx[k] - vx[i];

        s[i + 1] = s[i] + sqrt(dy * dy + dx * dx);
    }
    L = s[nv];
    n = (int)nearbyint(L / spacing);
    n = n > 8 ? n : 8;
    n = n < maxp ? n : maxp;
    step = L / n;
    j = 0;
    for (i = 0; i < n; i++) {
        double t = i * step, f;
        int    k;

        while (j + 1 < nv && s[j + 1] <= t) {
            j++;
        }
        k = (j + 1) % nv;
        f = (s[j + 1] > s[j]) ? (t - s[j]) / (s[j + 1] - s[j]) : 0.0;
        py[i] = vy[j] + f * (vy[k] - vy[j]);
        px[i] = vx[j] + f * (vx[k] - vx[j]);
    }
    start = 0;                                   /* start at the top-most point (first one) */
    for (i = 1; i < n; i++) {
        if (py[i] < py[start]) {
            start = i;
        }
    }
    if (start) {
        for (i = 0; i < n; i++) {
            vy[i] = py[(start + i) % n];
            vx[i] = px[(start + i) % n];
        }
        memcpy(py, vy, (size_t)n * sizeof(double));
        memcpy(px, vx, (size_t)n * sizeof(double));
    }
    free(vy);
    free(vx);
    free(s);
    return n;
}

void kf_fill(const double *py, const double *px, int n, int W, int H, uint8_t *m)
{
    double xs[KF_MAXPTS + 8];
    int    y, i;

    memset(m, 0, (size_t)W * (size_t)H);
    if (n < 3) {
        return;
    }
    for (y = 0; y < H; y++) {
        int nx = 0, a;

        for (i = 0; i < n && nx < KF_MAXPTS + 8; i++) {
            int    k = (i + 1) % n;
            double y0 = py[i], y1 = py[k];

            if ((y0 <= y && y < y1) || (y1 <= y && y < y0)) {
                xs[nx++] = px[i] + (y - y0) * (px[k] - px[i]) / (y1 - y0);
            }
        }
        qsort(xs, (size_t)nx, sizeof(double), en_cmp_d);
        for (a = 0; a + 1 < nx; a += 2) {
            int c0 = (int)ceil(xs[a]), c1 = (int)ceil(xs[a + 1]), c;

            c0 = c0 > 0 ? c0 : 0;
            c1 = c1 < W ? c1 : W;
            for (c = c0; c < c1; c++) {
                m[y * W + c] = 1u;
            }
        }
    }
}

/* ---------------------------------------------------------------- state */

int kf_init(kf_t *s, const kf_cfg_t *c, int W, int H, double fps, int max_frames)
{
    memset(s, 0, sizeof(*s));
    s->c = *c;
    s->W = W;
    s->H = H;
    s->PW = W + 2 * c->pad;
    s->PH = H + 2 * c->pad;
    s->fps = fps;
    s->kt = -1000000;
    s->hist_cap = max_frames;
    s->last_res = -1.0;
    s->prev = (uint8_t *)calloc((size_t)s->PW * (size_t)s->PH, 1u);
    s->cur = (uint8_t *)calloc((size_t)s->PW * (size_t)s->PH, 1u);
    s->last_mask = (uint8_t *)calloc((size_t)W * (size_t)H, 1u);
    s->area = (double *)calloc((size_t)max_frames + 1u, sizeof(double));
    if (s->prev == NULL || s->cur == NULL || s->last_mask == NULL || s->area == NULL) {
        kf_free(s);
        return -1;
    }
    return 0;
}

void kf_free(kf_t *s)
{
    free(s->prev);
    free(s->cur);
    free(s->last_mask);
    free(s->area);
    s->prev = s->cur = s->last_mask = NULL;
    s->area = NULL;
}

static void put_padded(kf_t *s, uint8_t *dst, const uint8_t *frame)
{
    int y, p = s->c.pad;

    memset(dst, 0, (size_t)s->PW * (size_t)s->PH);
    for (y = 0; y < s->H; y++) {
        memcpy(dst + (size_t)(y + p) * (size_t)s->PW + (size_t)p, frame + (size_t)y * (size_t)s->W, (size_t)s->W);
    }
}

static void push_area(kf_t *s, const uint8_t *mask)
{
    int i, a = 0;

    for (i = 0; i < s->W * s->H; i++) {
        a += mask[i];
    }
    if (s->t < s->hist_cap) {
        s->area[s->t] = (double)a;
    }
    s->t++;
}

/* kf.py Phase.predicted: is frame t within +-win of a predicted ED or ES? */
static int phase_hit(const kf_t *s)
{
    int    n = s->t < s->hist_cap ? s->t : s->hist_cap, dist = (int)nearbyint(0.3 * s->fps), sg, hit = 0;
    double rng, pm;
    double *a;
    int    *pk;

    dist = dist > 2 ? dist : 2;
    if (n < 2 * dist) {
        return 0;
    }
    a = (double *)malloc((size_t)n * sizeof(double));
    pk = (int *)malloc((size_t)(n + 1) * sizeof(int));
    if (a == NULL || pk == NULL) {
        free(a);
        free(pk);
        return 0;
    }
    rng = en_percentile(s->area, n, 95.0) - en_percentile(s->area, n, 5.0);
    pm = 0.2 * rng;
    pm = pm > 1e-9 ? pm : 1e-9;
    for (sg = 1; sg >= -1 && !hit; sg -= 2) {
        int i, np, m = 0;

        for (i = 0; i < n; i++) {
            a[i] = sg * s->area[i];
        }
        np = en_find_peaks(a, n, dist, pm, pk);
        for (i = 0; i < np; i++) {               /* confirmed: two later frames exist */
            if (pk[i] < n - 2) {
                pk[m++] = pk[i];
            }
        }
        if (m >= 2) {
            int pred = pk[m - 1] + (pk[m - 1] - pk[m - 2]);

            hit = abs(s->t - pred) <= s->c.win;
        }
    }
    free(a);
    free(pk);
    return hit;
}

int kf_decide(kf_t *s)
{
    int since = s->t - s->kt;

    if (s->c.kind == KF_FULL || s->t == 0 || s->npts == 0) {
        return s->t == 0 ? KF_START : (s->npts == 0 ? KF_LOST : KF_EVERY);
    }
    if (s->c.kind == KF_FIXED || s->c.kind == KF_HOLD) {
        return since >= s->c.n ? KF_INTERVAL : KF_TRACK;
    }
    if (since >= s->c.n_max) {
        return KF_INTERVAL;
    }
    if (since >= s->c.n_min) {
        if (s->c.res > 0.0 && s->base_set && s->last_res >= 0.0 && s->last_res > s->c.res * s->base) {
            return KF_RESIDUAL;
        }
        if (s->c.win >= 0 && phase_hit(s)) {
            return KF_PHASE;
        }
    }
    return KF_TRACK;
}

void kf_key(kf_t *s, const uint8_t *frame, const uint8_t *mask, int reason, uint8_t *mask_out)
{
    int i;

    (void)reason;
    memcpy(mask_out, mask, (size_t)s->W * (size_t)s->H);
    memcpy(s->last_mask, mask, (size_t)s->W * (size_t)s->H);
    s->npts = kf_contour(mask, s->W, s->H, s->c.spacing, s->py, s->px, KF_MAXPTS);
    for (i = 0; i < s->npts; i++) {
        s->py[i] += s->c.pad;
        s->px[i] += s->c.pad;
    }
    put_padded(s, s->prev, frame);
    s->kt = s->t;
    s->base_set = 0;
    s->last_res = -1.0;
    push_area(s, mask_out);
}

double kf_track(kf_t *s, const uint8_t *frame, uint8_t *mask_out)
{
    int32_t  ax[BM_NMAX], ay[BM_NMAX], x[BM_NMAX], y[BM_NMAX], fl[BM_NMAX];
    uint32_t sad[BM_NMAX];
    double   d[2][KF_MAXPTS], res = 0.0, hy[KF_MAXPTS], hx[KF_MAXPTS];
    int      n = s->npts, h = s->c.k / 2, m = n + 2 * h, i, j, cnt = 0, r = s->c.reg, kk = s->c.k > 1 ? s->c.k : 1;
    uint8_t *tmp;

    if (s->c.kind == KF_HOLD) {
        memcpy(mask_out, s->last_mask, (size_t)s->W * (size_t)s->H);
        push_area(s, mask_out);
        return -1.0;
    }
    put_padded(s, s->cur, frame);
    for (i = 0; i < m; i++) {                    /* wrap-padded point list: circular SAD aggregation */
        int src = ((i - h) % n + n) % n;

        ax[i] = x[i] = (int32_t)nearbyint(s->px[src] * 256.0);
        ay[i] = y[i] = (int32_t)nearbyint(s->py[src] * 256.0);
    }
    /* NEON SAD tables on the board (bit-exact with the C kernel, part E); the host DLL ignores the flag */
    bm_track_line(s->prev, s->cur, s->PW, s->PH, s->PW, m, s->c.R, s->c.k, BM_OPT_VFIT | BM_OPT_NEON, ax, ay, x, y, sad,
                  fl);
    for (i = 0; i < n; i++) {
        d[0][i] = y[h + i] / 256.0 - s->py[i];
        d[1][i] = x[h + i] / 256.0 - s->px[i];
        if (!(fl[h + i] & BM_EDGE)) {
            res += (double)sad[h + i] / (255.0 * 256.0 * kk);
            cnt++;
        }
    }
    for (i = 0; i < n; i++) {                    /* circular moving average of the displacement */
        double sy = 0.0, sx = 0.0;

        if (r > 1) {
            for (j = -(r / 2); j <= r / 2; j++) {
                int q = ((i + j) % n + n) % n;

                sy += d[0][q];
                sx += d[1][q];
            }
            sy /= r;
            sx /= r;
        } else {
            sy = d[0][i];
            sx = d[1][i];
        }
        hy[i] = s->py[i] + sy;
        hx[i] = s->px[i] + sx;
    }
    for (i = 0; i < n; i++) {
        s->py[i] = hy[i];
        s->px[i] = hx[i];
        hy[i] -= s->c.pad;
        hx[i] -= s->c.pad;
    }
    kf_fill(hy, hx, n, s->W, s->H, mask_out);
    res = cnt ? res / cnt : 1.0;
    if (!s->base_set) {
        s->base = res > 1e-6 ? res : 1e-6;
        s->base_set = 1;
    }
    s->last_res = res;
    tmp = s->prev;                               /* chain mode: this frame is the next template */
    s->prev = s->cur;
    s->cur = tmp;
    push_area(s, mask_out);
    return res;
}

int kf_run_sequence(const kf_cfg_t *c, const uint8_t *frames, const uint8_t *key_masks, int T, int W, int H,
                    double fps, uint8_t *masks, int32_t *reason, double *resid)
{
    kf_t   s;
    int    t, nk = 0;
    size_t fs = (size_t)W * (size_t)H;

    if (kf_init(&s, c, W, H, fps, T) != 0) {
        return -1;
    }
    for (t = 0; t < T; t++) {
        int why = kf_decide(&s);

        reason[t] = why;
        if (why != KF_TRACK) {
            kf_key(&s, frames + t * fs, key_masks + t * fs, why, masks + t * fs);
            resid[t] = NAN;
            nk++;
        } else {
            resid[t] = kf_track(&s, frames + t * fs, masks + t * fs);
        }
    }
    kf_free(&s);
    return nk;
}
