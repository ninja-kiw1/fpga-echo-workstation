/* echo_quality.c -- see echo_quality.h. Numeric file: built with -ffp-contract=off. */
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "echo_num.h"
#include "echo_quality.h"
#include "kf.h"

/* ---------------------------------------------------------------- binary morphology (scipy defaults: 4-neighbour
 * cross, outside the image = 0) */

static void erode1(const uint8_t *a, uint8_t *b, int W, int H)
{
    int y, x;

    for (y = 0; y < H; y++) {
        for (x = 0; x < W; x++) {
            int p = y * W + x;

            b[p] = a[p] && y > 0 && a[p - W] && y < H - 1 && a[p + W] && x > 0 && a[p - 1] && x < W - 1 && a[p + 1];
        }
    }
}

static void dilate1(const uint8_t *a, uint8_t *b, int W, int H)
{
    int y, x;

    for (y = 0; y < H; y++) {
        for (x = 0; x < W; x++) {
            int p = y * W + x;

            b[p] = a[p] || (y > 0 && a[p - W]) || (y < H - 1 && a[p + W]) || (x > 0 && a[p - 1]) || (x < W - 1 && a[p + 1]);
        }
    }
}

/* it iterations of erode (dil = 0) or dilate (dil = 1); out may not alias in */
static void morph(const uint8_t *in, uint8_t *out, uint8_t *tmp, int W, int H, int it, int dil)
{
    int i;

    memcpy(out, in, (size_t)W * (size_t)H);
    for (i = 0; i < it; i++) {
        if (dil) {
            dilate1(out, tmp, W, H);
        } else {
            erode1(out, tmp, W, H);
        }
        memcpy(out, tmp, (size_t)W * (size_t)H);
    }
}

/* largest 4-connected component (ties: first in raster order) + fill holes; returns the component count */
static int clean_wh(const uint8_t *m, uint8_t *out, int W, int H)
{
    int  n = 0, best = -1, i, sp = 0, N = W * H;
    int *lab = (int *)malloc((size_t)N * sizeof(int)), *size = (int *)malloc((size_t)N * sizeof(int));
    int *stack = (int *)malloc((size_t)N * sizeof(int));

    if (lab == NULL || size == NULL || stack == NULL) {
        free(lab);
        free(size);
        free(stack);
        memcpy(out, m, (size_t)N);
        return 0;
    }
    for (i = 0; i < N; i++) {
        lab[i] = -1;
    }
    for (i = 0; i < N; i++) {
        if (!m[i] || lab[i] >= 0) {
            continue;
        }
        size[n] = 0;
        stack[sp++] = i;
        lab[i] = n;
        while (sp > 0) {
            int p = stack[--sp], py = p / W, px = p % W, k;
            const int nb[4] = {p - W, p + W, p - 1, p + 1};
            const int ok[4] = {py > 0, py < H - 1, px > 0, px < W - 1};

            size[n]++;
            for (k = 0; k < 4; k++) {
                if (ok[k] && m[nb[k]] && lab[nb[k]] < 0) {
                    lab[nb[k]] = n;
                    stack[sp++] = nb[k];
                }
            }
        }
        if (best < 0 || size[n] > size[best]) {
            best = n;
        }
        n++;
    }
    for (i = 0; i < N; i++) {
        out[i] = (uint8_t)(n > 1 ? lab[i] == best : m[i] != 0u);
        lab[i] = 0;
    }
    for (i = 0; i < N; i++) {                    /* background 4-connected to the border stays background */
        int y = i / W, x = i % W;

        if ((y == 0 || y == H - 1 || x == 0 || x == W - 1) && !out[i] && !lab[i]) {
            lab[i] = 1;
            stack[sp++] = i;
        }
    }
    while (sp > 0) {
        int p = stack[--sp], py = p / W, px = p % W, k;
        const int nb[4] = {p - W, p + W, p - 1, p + 1};
        const int ok[4] = {py > 0, py < H - 1, px > 0, px < W - 1};

        for (k = 0; k < 4; k++) {
            if (ok[k] && !out[nb[k]] && !lab[nb[k]]) {
                lab[nb[k]] = 1;
                stack[sp++] = nb[k];
            }
        }
    }
    for (i = 0; i < N; i++) {
        if (!out[i] && !lab[i]) {
            out[i] = 1u;
        }
    }
    free(lab);
    free(size);
    free(stack);
    return n;
}

/* ---------------------------------------------------------------- features */

void eq_sector(const uint8_t *frames, int T, int W, int H, uint8_t *sector, int *top, int *height)
{
    int      N = W * H, i, t, r0 = H, r1 = -1;
    uint8_t *mx = (uint8_t *)calloc((size_t)N, 1u);

    if (mx == NULL) {
        memset(sector, 1, (size_t)N);
        *top = 0;
        *height = H;
        return;
    }
    for (t = 0; t < T; t++) {
        const uint8_t *f = frames + (size_t)t * (size_t)N;

        for (i = 0; i < N; i++) {
            mx[i] = f[i] > mx[i] ? f[i] : mx[i];
        }
    }
    for (i = 0; i < N; i++) {
        mx[i] = mx[i] > 4;
    }
    clean_wh(mx, sector, W, H);
    for (i = 0; i < N; i++) {
        if (sector[i]) {
            r0 = i / W < r0 ? i / W : r0;
            r1 = i / W > r1 ? i / W : r1;
        }
    }
    *top = r1 >= 0 ? r0 : 0;
    *height = r1 >= 0 ? r1 - r0 + 1 : H;
    free(mx);
}

int eq_acc_init(eq_acc_t *a, const uint8_t *sector, int W, int H, int top, int height)
{
    size_t   N = (size_t)W * (size_t)H;
    uint8_t *tmp = (uint8_t *)malloc(N);
    size_t   i;

    memset(a, 0, sizeof(*a));
    a->W = W;
    a->H = H;
    a->top = top;
    a->height = height;
    a->sector = (uint8_t *)malloc(N);
    a->edge = (uint8_t *)malloc(N);
    a->prev_mask = (uint8_t *)malloc(N);
    if (tmp == NULL || a->sector == NULL || a->edge == NULL || a->prev_mask == NULL) {
        free(tmp);
        eq_acc_free(a);
        return -1;
    }
    memcpy(a->sector, sector, N);
    {
        uint8_t *er = (uint8_t *)malloc(N);

        if (er == NULL) {
            free(tmp);
            eq_acc_free(a);
            return -1;
        }
        morph(sector, er, tmp, W, H, 2, 0);
        for (i = 0; i < N; i++) {
            a->edge[i] = sector[i] && !er[i];
        }
        free(er);
    }
    free(tmp);
    return 0;
}

void eq_acc_free(eq_acc_t *a)
{
    free(a->sector);
    free(a->edge);
    free(a->prev_mask);
    a->sector = a->edge = a->prev_mask = NULL;
}

void eq_acc_key(eq_acc_t *a, const uint8_t *frame, const int8_t *q, const uint8_t *m, int ncomp)
{
    int      W = a->W, H = a->H, N = W * H, i, cnt = 0, nb = 0, nbe = 0, rmin = H;
    double   f[7], mr = 0.0, mc = 0.0, sxx = 0.0, sxy = 0.0, syy = 0.0, vx, vy;
    uint8_t *e1 = (uint8_t *)malloc((size_t)N), *e2 = (uint8_t *)malloc((size_t)N), *d2 = (uint8_t *)malloc((size_t)N);
    uint8_t *d6 = (uint8_t *)malloc((size_t)N), *tmp = (uint8_t *)malloc((size_t)N);

    if (e1 == NULL || e2 == NULL || d2 == NULL || d6 == NULL || tmp == NULL) {
        goto out;
    }
    f[5] = ncomp > 1 ? 1.0 : 0.0;                                  /* multi */
    for (i = 0; i < N; i++) {
        cnt += m[i];
    }
    if (cnt < 10) {
        f[0] = 1.0;
        f[1] = 1.0;
        f[2] = 0.0;
        f[3] = 1.0;
        f[4] = 1.0;
        f[6] = 0.0;
    } else {
        double s1lo = 1e300, s1hi = -1e300, s2lo = 1e300, s2hi = -1e300, L, Wd, sa = 0.0, sb = 0.0;
        int    nband = 0, nlow = 0, ncav = 0, nring = 0;

        morph(m, e1, tmp, W, H, 1, 0);
        for (i = 0; i < N; i++) {                                  /* touch: border pixels in the edge band */
            if (m[i] && !e1[i]) {
                nb++;
                nbe += a->edge[i];
            }
            if (m[i]) {
                int r = i / W;

                rmin = r < rmin ? r : rmin;
                mr += r;
                mc += i % W;
            }
        }
        f[0] = (double)nbe / (double)(nb > 1 ? nb : 1);
        f[1] = (double)(rmin - a->top) / (double)a->height;      /* apex_gap */
        mr /= cnt;
        mc /= cnt;
        for (i = 0; i < N; i++) {
            if (m[i]) {
                double y = i / W - mr, x = i % W - mc;

                sxx += y * y;
                sxy += y * x;
                syy += x * x;
            }
        }
        en_dlaev2_vec(sxx, sxy, syy, &vx, &vy);                    /* major axis (y, x) = (vx, vy) */
        for (i = 0; i < N; i++) {
            if (m[i]) {
                double y = i / W - mr, x = i % W - mc, s1 = y * vx + x * vy, s2 = -y * vy + x * vx;

                s1lo = s1 < s1lo ? s1 : s1lo;
                s1hi = s1 > s1hi ? s1 : s1hi;
                s2lo = s2 < s2lo ? s2 : s2lo;
                s2hi = s2 > s2hi ? s2 : s2hi;
            }
        }
        L = s1hi - s1lo + 1.0;
        Wd = s2hi - s2lo + 1.0;
        f[2] = L / a->height;                                       /* length */
        f[3] = Wd / L;                                              /* round */
        morph(m, d2, tmp, W, H, 2, 1);
        morph(m, e2, tmp, W, H, 2, 0);
        morph(m, d6, tmp, W, H, 6, 1);
        for (i = 0; i < N; i++) {
            if (d2[i] && !e2[i]) {                                  /* unc band */
                nband++;
                nlow += q[i] >= -2 && q[i] <= 2;
            }
            if (e2[i]) {                                            /* cavity */
                sa += frame[i];
                ncav++;
            }
            if (d6[i] && !d2[i] && a->sector[i]) {                  /* ring */
                sb += frame[i];
                nring++;
            }
        }
        f[4] = nband ? (double)nlow / nband : 1.0;
        sa = ncav ? sa / ncav : 0.0;
        sb = nring ? sb / nring : 0.0;
        f[6] = (sb - sa) / (sa + sb + 1.0);                         /* contrast */
    }
    for (i = 0; i < 7; i++) {
        a->sum[i] += f[i];
    }
    a->nkey++;
out:
    free(e1);
    free(e2);
    free(d2);
    free(d6);
    free(tmp);
}

void eq_acc_mask(eq_acc_t *a, const uint8_t *m)
{
    int    N = a->W * a->H, i, inter = 0, s = 0;
    double area = 0.0;

    for (i = 0; i < N; i++) {
        area += m[i];
    }
    if (a->nframes > 0) {
        for (i = 0; i < N; i++) {
            inter += m[i] & a->prev_mask[i];
            s += m[i] + a->prev_mask[i];
        }
        a->dice_sum += s ? 2.0 * inter / s : 1.0;
        a->ndice++;
    }
    if (a->nframes > 1) {
        a->rough_sum += fabs(area - 2.0 * a->area_prev + a->area_prev2);
        a->nrough++;
    }
    a->area_prev2 = a->area_prev;
    a->area_prev = area;
    a->area_sum += area;
    memcpy(a->prev_mask, m, (size_t)N);
    a->nframes++;
}

void eq_acc_finish(const eq_acc_t *a, const eq_model_t *md, eq_quality_t *o)
{
    int    i, k = a->nkey > 0 ? a->nkey : 1;
    double mean_area = a->nframes ? a->area_sum / a->nframes : 0.0, contrib[QF_N];

    for (i = 0; i < 7; i++) {
        o->f[i] = a->nkey ? a->sum[i] / k : (i == 2 || i == 6 ? 0.0 : 1.0);
    }
    o->f[7] = a->ndice ? a->dice_sum / a->ndice : 1.0;
    o->f[8] = a->nrough ? (a->rough_sum / a->nrough) / (mean_area > 1.0 ? mean_area : 1.0) : 0.0;
    o->nkey = a->nkey;
    o->score = md->w[0];
    for (i = 0; i < QF_N; i++) {
        contrib[i] = md->w[i + 1] * ((o->f[i] - md->mu[i]) / md->sd[i]);
        o->score += contrib[i];
    }
    o->light = o->score >= md->t_red ? 2 : (o->score >= md->t_green ? 1 : 0);
    o->reason[0] = o->reason[1] = -1;
    for (i = 0; i < QF_N; i++) {                                    /* two largest positive contributions */
        if (contrib[i] <= 0.0) {
            continue;
        }
        if (o->reason[0] < 0 || contrib[i] > contrib[o->reason[0]]) {
            o->reason[1] = o->reason[0];
            o->reason[0] = i;
        } else if (o->reason[1] < 0 || contrib[i] > contrib[o->reason[1]]) {
            o->reason[1] = i;
        }
    }
}

void eq_interval(const eq_model_t *m, double ef, double beat_sd, int nbeats, int fallback, const double *f,
                 eq_interval_t *o)
{
    double x[QS_N], lp = m->b[0];
    int    i;

    x[0] = beat_sd;
    x[1] = 1.0 / (nbeats > 1 ? nbeats : 1);
    x[2] = fallback ? 1.0 : 0.0;
    x[3] = f[4];                                                    /* unc */
    x[4] = f[8];                                                    /* roughness */
    x[5] = f[7];                                                    /* stability */
    x[6] = f[6];                                                    /* contrast */
    x[7] = f[1];                                                    /* apex_gap */
    x[8] = f[5];                                                    /* multi */
    for (i = 0; i < QS_N; i++) {
        lp += m->b[i + 1] * ((x[i] - m->zmu[i]) / m->zsd[i]);
    }
    o->sigma = exp(lp);
    o->lo80 = ef - m->q80 * o->sigma;
    o->hi80 = ef + m->q80 * o->sigma;
    o->lo90 = ef - m->q90 * o->sigma;
    o->hi90 = ef + m->q90 * o->sigma;
    o->review = (o->lo80 < 40.0 && o->hi80 > 40.0) || (o->lo80 < 50.0 && o->hi80 > 50.0);
}

int eq_clean(const uint8_t *m, uint8_t *out, int W, int H)
{
    return clean_wh(m, out, W, H);
}

int eq_window(const uint8_t *frames, int T, int W, int H, const int *sel, int nsel, const int8_t *q,
              const uint8_t *masks, const int32_t *ncomp, const eq_model_t *md, eq_quality_t *o)
{
    size_t   N = (size_t)W * (size_t)H;
    uint8_t *sector = (uint8_t *)malloc(N);
    eq_acc_t a;
    int      top, height, i;

    if (sector == NULL) {
        return -1;
    }
    eq_sector(frames, T, W, H, sector, &top, &height);
    if (eq_acc_init(&a, sector, W, H, top, height) != 0) {
        free(sector);
        return -1;
    }
    for (i = 0; i < nsel; i++) {
        eq_acc_key(&a, frames + (size_t)sel[i] * N, q + (size_t)i * N, masks + (size_t)i * N, ncomp[i]);
        eq_acc_mask(&a, masks + (size_t)i * N);
    }
    eq_acc_finish(&a, md, o);
    eq_acc_free(&a);
    free(sector);
    return 0;
}

void eq_f1_to112(const int8_t *lg128, int8_t *q112)
{
    int y, x;

    for (y = 0; y < 112; y++) {
        const int8_t *row = lg128 + (size_t)((2 * y + 1) * 128 / 224) * 128u;

        for (x = 0; x < 112; x++) {
            q112[y * 112 + x] = row[(2 * x + 1) * 128 / 224];
        }
    }
}

void eq_clip_features(const uint8_t *frames, const int8_t *q, const uint8_t *masks, const uint8_t *is_key,
                      const int32_t *ncomp, int T, int W, int H, double *f)
{
    uint8_t     *sector = (uint8_t *)malloc((size_t)W * (size_t)H);
    eq_acc_t     a;
    eq_model_t   md;
    eq_quality_t o;
    int          top, height, t, i;
    size_t       N = (size_t)W * (size_t)H;

    if (sector == NULL) {
        return;
    }
    eq_sector(frames, T, W, H, sector, &top, &height);
    if (eq_acc_init(&a, sector, W, H, top, height) != 0) {
        free(sector);
        return;
    }
    for (t = 0; t < T; t++) {
        if (is_key[t]) {
            eq_acc_key(&a, frames + t * N, q + t * N, masks + t * N, ncomp[t]);
        }
        eq_acc_mask(&a, masks + t * N);
    }
    memset(&md, 0, sizeof(md));
    for (i = 0; i < QF_N; i++) {
        md.sd[i] = 1.0;
    }
    eq_acc_finish(&a, &md, &o);
    memcpy(f, o.f, sizeof(o.f));
    eq_acc_free(&a);
    free(sector);
}

/* ---------------------------------------------------------------- echo_ef mode-2 parameter extension */

static uint32_t le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static double le64d(const uint8_t *p)
{
    double d;

    memcpy(&d, p, 8u);              /* both the board (aarch64) and the PC are little-endian */
    return d;
}

int eq_parse_ext(const uint8_t *x, uint32_t nbytes, struct kf_cfg *kc, int *flags, double *a, double *b,
                 eq_model_t *md)
{
    if (nbytes < KF_EXT_BYTES || le32(x) != KF_MAGIC) {
        return -1;
    }
    memset(kc, 0, sizeof(*kc));
    kc->kind = (int32_t)le32(x + 4);
    kc->n = (int32_t)le32(x + 8);
    kc->n_max = (int32_t)le32(x + 12);
    kc->n_min = (int32_t)le32(x + 16);
    kc->win = (int32_t)le32(x + 20);
    kc->R = (int32_t)le32(x + 24);
    kc->k = (int32_t)le32(x + 28);
    kc->reg = (int32_t)le32(x + 32);
    kc->pad = (int32_t)le32(x + 36);
    *flags = (int32_t)le32(x + 40);
    kc->res = le64d(x + 48);
    kc->spacing = le64d(x + 56);
    *a = le64d(x + 64);
    *b = le64d(x + 72);
    memcpy(md, x + 80, sizeof(*md));    /* 60 x f64, no padding */
    return 0;
}
