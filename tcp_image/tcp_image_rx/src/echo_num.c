/* echo_num.c -- see echo_num.h. Everything below is the code that was in job_echo.c (clean_ps .. analyze) and
 * job.c (job_np_sum) until v5.20, moved without changes except the en_ names and the en_cfg_t parameter block. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE                 /* M_PI (the makefile passes -D_GNU_SOURCE; the host DLL build may not) */
#endif
#include <float.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "echo_num.h"

#define NDISK 20

/* numpy's pairwise summation (add.reduce on a contiguous float64 array) */
double job_np_sum(const double *a, int n)
{
    double r[8], res;
    int    i, j;

    if (n < 8) {
        res = 0.0;                                   /* numpy starts from -0.0 + a[0]...: same value */
        for (i = 0; i < n; i++) {
            res += a[i];
        }
        return res;
    }
    if (n <= 128) {
        for (j = 0; j < 8; j++) {
            r[j] = a[j];
        }
        for (i = 8; i < n - (n % 8); i += 8) {
            for (j = 0; j < 8; j++) {
                r[j] += a[i + j];
            }
        }
        res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        for (; i < n; i++) {
            res += a[i];
        }
        return res;
    }
    {
        int n2 = n / 2;

        n2 -= n2 % 8;
        return job_np_sum(a, n2) + job_np_sum(a + n2, n - n2);
    }
}

/* ---------------------------------------------------------------- clean on the PS (echo_ws.clean) */

/* 4-connected labelling with union-find on pixels; keeps the largest component (ties: first in raster
 * order, as scipy's label numbering), then fills holes (background not 4-connected to the border).
 * Returns the number of components of the input mask. */
int en_clean112(const uint8_t *m, uint8_t *out)
{
    static int lab[EN_N * EN_N], size[EN_N * EN_N];
    static int stack[EN_N * EN_N];
    int        i, n = 0, best = -1, x, y, sp = 0;

    for (i = 0; i < EN_N * EN_N; i++) {
        lab[i] = -1;
    }
    for (i = 0; i < EN_N * EN_N; i++) {               /* flood fill per component, raster order of first pixel */
        if (!m[i] || lab[i] >= 0) {
            continue;
        }
        size[n] = 0;
        stack[sp++] = i;
        lab[i] = n;
        while (sp > 0) {
            int p = stack[--sp], py = p / EN_N, px = p % EN_N, k;
            const int nb[4] = {p - EN_N, p + EN_N, p - 1, p + 1};
            const int ok[4] = {py > 0, py < EN_N - 1, px > 0, px < EN_N - 1};

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
    for (i = 0; i < EN_N * EN_N; i++) {
        out[i] = (uint8_t)(n > 1 ? lab[i] == best : m[i] != 0u);
    }
    /* fill holes: background reachable from the border (4-connected) stays background */
    for (i = 0; i < EN_N * EN_N; i++) {
        lab[i] = 0;                                  /* 1 = outside */
    }
    for (y = 0; y < EN_N; y++) {
        for (x = 0; x < EN_N; x++) {
            int p = y * EN_N + x;

            if ((y == 0 || y == EN_N - 1 || x == 0 || x == EN_N - 1) && !out[p] && !lab[p]) {
                lab[p] = 1;
                stack[sp++] = p;
            }
        }
    }
    while (sp > 0) {
        int p = stack[--sp], py = p / EN_N, px = p % EN_N, k;
        const int nb[4] = {p - EN_N, p + EN_N, p - 1, p + 1};
        const int ok[4] = {py > 0, py < EN_N - 1, px > 0, px < EN_N - 1};

        for (k = 0; k < 4; k++) {
            if (ok[k] && !out[nb[k]] && !lab[nb[k]]) {
                lab[nb[k]] = 1;
                stack[sp++] = nb[k];
            }
        }
    }
    for (i = 0; i < EN_N * EN_N; i++) {
        if (!out[i] && !lab[i]) {
            out[i] = 1u;
        }
    }
    return n;
}

/* ---------------------------------------------------------------- numbers (echo_ws.py) */

int en_cmp_d(const void *a, const void *b)
{
    double x = *(const double *)a, y = *(const double *)b;

    return (x > y) - (x < y);
}

/* np.percentile(v, q), method linear (numpy >= 1.22: lerp from the nearer end) */
double en_percentile(const double *v, int n, double q)
{
    double *s = (double *)malloc((size_t)n * sizeof(double)), vi, g, a, b, d, r;
    int     lo, hi;

    if (s == NULL) {
        return 0.0;
    }
    memcpy(s, v, (size_t)n * sizeof(double));
    qsort(s, (size_t)n, sizeof(double), en_cmp_d);
    vi = (double)(n - 1) * (q / 100.0);
    lo = (int)floor(vi);
    hi = lo + 1;
    if (vi >= (double)(n - 1)) {
        lo = hi = n - 1;
    }
    if (vi < 0.0) {
        lo = hi = 0;
    }
    g = vi - floor(vi);
    a = s[lo];
    b = s[hi];
    d = b - a;
    r = (g >= 0.5) ? b - d * (1.0 - g) : a + d * g;
    free(s);
    return r;
}

double en_median(const double *v, int n)
{
    double *s = (double *)malloc((size_t)n * sizeof(double)), r;

    if (s == NULL || n == 0) {
        free(s);
        return NAN;
    }
    memcpy(s, v, (size_t)n * sizeof(double));
    qsort(s, (size_t)n, sizeof(double), en_cmp_d);
    r = (n % 2) ? s[n / 2] : (s[n / 2 - 1] + s[n / 2]) / 2.0;
    free(s);
    return r;
}

/* LAPACK dlaev2: unit eigenvector (cs1, sn1) of [[a b][b c]] for the eigenvalue of larger absolute value */
void en_dlaev2_vec(double a, double b, double c, double *cs1, double *sn1)
{
    double sm = a + c, df = a - c, adf = fabs(df), tb = b + b, ab = fabs(tb), rt, cs, ct, tn;
    int    sgn1, sgn2;

    if (adf > ab) {
        rt = adf * sqrt(1.0 + (ab / adf) * (ab / adf));
    } else if (adf < ab) {
        rt = ab * sqrt(1.0 + (adf / ab) * (adf / ab));
    } else {
        rt = ab * sqrt(2.0);
    }
    sgn1 = (sm < 0.0) ? -1 : 1;
    if (df >= 0.0) {
        cs = df + rt;
        sgn2 = 1;
    } else {
        cs = df - rt;
        sgn2 = -1;
    }
    if (fabs(cs) > ab) {
        ct = -tb / cs;
        *sn1 = 1.0 / sqrt(1.0 + ct * ct);
        *cs1 = ct * *sn1;
    } else if (ab == 0.0) {
        *cs1 = 1.0;
        *sn1 = 0.0;
    } else {
        tn = -cs / tb;
        *cs1 = 1.0 / sqrt(1.0 + tn * tn);
        *sn1 = tn * *cs1;
    }
    if (sgn1 == sgn2) {
        tn = *cs1;
        *cs1 = -*sn1;
        *sn1 = tn;
    }
}

/* echo_ws.en_volume_pca: principal axis (larger eigenvalue of the 2x2 scatter), 20 disks, pixel units */
double en_volume_pca(const uint8_t *m)
{
    static double rr[EN_N * EN_N], cc[EN_N * EN_N];
    double        mr = 0.0, mc = 0.0, sxx = 0.0, sxy = 0.0, syy = 0.0, vx, vy, lo = 1e300, hi = -1e300, h;
    double        width[NDISK], terms[NDISK];
    int           n = 0, i, cnt[NDISK];

    for (i = 0; i < EN_N * EN_N; i++) {
        if (m[i]) {
            rr[n] = (double)(i / EN_N);
            cc[n] = (double)(i % EN_N);
            n++;
        }
    }
    if (n < 3) {
        return 0.0;
    }
    for (i = 0; i < n; i++) {                       /* integer sums: exact in double */
        mr += rr[i];
        mc += cc[i];
    }
    mr /= n;
    mc /= n;
    for (i = 0; i < n; i++) {
        double a = rr[i] - mr, b = cc[i] - mc;

        sxx += a * a;
        sxy += a * b;
        syy += b * b;
    }
    /* principal axis exactly as numpy.linalg.eigh (LAPACK dsyevd -> dsteqr) picks it for a 2x2 matrix:
     * negligible off-diagonal -> axis vector, else the (cs1, sn1) of dlaev2 -- including its sign. The sign
     * matters: pixels exactly midway between the two extreme pixels sit on the middle disk edge, and the side
     * they fall on depends on the direction (and on last-bit rounding). */
    if (fabs(sxy) <= sqrt(fabs(sxx)) * sqrt(fabs(syy)) * (DBL_EPSILON / 2)) {
        vx = (sxx > syy) ? 1.0 : 0.0;
        vy = (sxx > syy) ? 0.0 : 1.0;
    } else {
        en_dlaev2_vec(sxx, sxy, syy, &vx, &vy);
    }
    for (i = 0; i < n; i++) {
        double s = fma(rr[i] - mr, vx, (cc[i] - mc) * vy);    /* = OpenBLAS dgemv for q @ v (checked 300/300) */

        rr[i] = s;
        lo = s < lo ? s : lo;
        hi = s > hi ? s : hi;
    }
    lo -= 0.5;
    hi += 0.5;
    h = (hi - lo) / NDISK;
    memset(cnt, 0, sizeof(cnt));
    for (i = 0; i < n; i++) {
        int k = (int)((rr[i] - lo) / h);

        cnt[k < NDISK - 1 ? k : NDISK - 1]++;
    }
    for (i = 0; i < NDISK; i++) {
        width[i] = (double)cnt[i] / h;
        terms[i] = M_PI / 4 * (width[i] * width[i]) * h;
    }
    return job_np_sum(terms, NDISK);
}

/* scipy.signal.en_find_peaks(x, distance=dist, prominence=pmin) */
int en_find_peaks(const double *x, int n, int dist, double pmin, int *out)
{
    int   *pk = (int *)malloc((size_t)(n + 1) * sizeof(int)), *ord = NULL, np = 0, i, k, nk = 0;
    char  *keep = NULL;

    if (pk == NULL) {
        return 0;
    }
    i = 1;
    while (i < n - 1) {                              /* _local_maxima_1d: plateaus -> middle */
        if (x[i - 1] < x[i]) {
            int ia = i + 1;

            while (ia < n - 1 && x[ia] == x[i]) {
                ia++;
            }
            if (x[ia] < x[i]) {
                pk[np++] = (i + ia - 1) / 2;
                i = ia;
            }
        }
        i++;
    }
    keep = (char *)malloc((size_t)np + 1u);
    ord = (int *)malloc((size_t)(np + 1) * sizeof(int));
    if (keep == NULL || ord == NULL) {
        free(pk);
        free(keep);
        free(ord);
        return 0;
    }
    for (i = 0; i < np; i++) {                       /* _select_by_peak_distance: highest first */
        keep[i] = 1;
        ord[i] = i;
    }
    for (i = 1; i < np; i++) {                       /* stable insertion sort by height (numpy argsort: ties rare) */
        int t = ord[i], j = i - 1;

        while (j >= 0 && x[pk[ord[j]]] > x[pk[t]]) {
            ord[j + 1] = ord[j];
            j--;
        }
        ord[j + 1] = t;
    }
    for (i = np - 1; i >= 0; i--) {
        int j = ord[i];

        if (!keep[j]) {
            continue;
        }
        for (k = j - 1; k >= 0 && pk[j] - pk[k] < dist; k--) {
            keep[k] = 0;
        }
        for (k = j + 1; k < np && pk[k] - pk[j] < dist; k++) {
            keep[k] = 0;
        }
    }
    for (i = 0; i < np; i++) {                       /* _peak_prominences (no wlen), keep prominence >= pmin */
        int    p = pk[i], j;
        double lmin = x[p], rmin = x[p], prom;

        if (!keep[i]) {
            continue;
        }
        for (j = p; j >= 0 && x[j] <= x[p]; j--) {
            if (x[j] < lmin) {
                lmin = x[j];
            }
        }
        for (j = p; j < n && x[j] <= x[p]; j++) {
            if (x[j] < rmin) {
                rmin = x[j];
            }
        }
        prom = x[p] - (lmin > rmin ? lmin : rmin);
        if (prom >= pmin) {
            out[nk++] = p;
        }
    }
    free(pk);
    free(keep);
    free(ord);
    return nk;
}

/* echo_ws.analyze (beats.py) */
void en_analyze(const double *vol, int n, double fps, int step, const en_cfg_t *c, en_result_t *r)
{
    double *v = (double *)malloc((size_t)(n + 1) * sizeof(double)), fe = fps / step, vmax = -1e300, cycle;
    int    *tr = (int *)malloc((size_t)(n + 1) * sizeof(int)), nt, i, w;

    memset(r, 0, sizeof(*r));
    if (v == NULL || tr == NULL) {
        free(v);
        free(tr);
        r->ef = NAN;
        r->fallback = 1;
        return;
    }
    if ((int)c->smooth > 1 && n >= (int)c->smooth) {  /* scipy.signal.medfilt: zero padded */
        int     k = (int)c->smooth, hk = k / 2;
        double *win = (double *)malloc((size_t)k * sizeof(double));

        for (i = 0; i < n && win != NULL; i++) {
            int j;

            for (j = 0; j < k; j++) {
                int t = i - hk + j;

                win[j] = (t >= 0 && t < n) ? vol[t] : 0.0;
            }
            qsort(win, (size_t)k, sizeof(double), en_cmp_d);
            v[i] = win[hk];
        }
        free(win);
    } else {
        memcpy(v, vol, (size_t)n * sizeof(double));
    }
    for (i = 0; i < n; i++) {
        vmax = v[i] > vmax ? v[i] : vmax;
    }
    if (n < 3 || vmax <= 0.0) {
        r->ef = NAN;
        r->fallback = 1;
        free(v);
        free(tr);
        return;
    }
    {
        double rng = en_percentile(v, n, 95.0) - en_percentile(v, n, 5.0), *neg, pm;
        int    dist = (int)nearbyint(c->dist_s * fe);

        neg = (double *)malloc((size_t)n * sizeof(double));
        for (i = 0; i < n && neg != NULL; i++) {
            neg[i] = -v[i];
        }
        pm = c->prom * rng;
        nt = (neg != NULL) ? en_find_peaks(neg, n, dist > 1 ? dist : 1, pm > 1e-9 ? pm : 1e-9, tr) : 0;
        free(neg);
    }
    if (nt >= 2) {
        double *d = (double *)malloc((size_t)nt * sizeof(double));

        for (i = 0; i + 1 < nt && d != NULL; i++) {
            d[i] = (double)(tr[i + 1] - tr[i]);
        }
        cycle = d != NULL ? en_median(d, nt - 1) : 0.8 * fe;
        free(d);
        r->cycle_s = cycle / fe;
        r->hr = 60.0 / r->cycle_s;
        r->has_hr = 1;
    } else {
        cycle = 0.8 * fe;
    }
    w = (int)nearbyint(c->win * cycle);
    w = w > 2 ? w : 2;
    for (i = 0; i < nt; i++) {
        int t = tr[i], a = t - w > 0 ? t - w : 0, e, j;

        if (t - a < 2) {
            continue;
        }
        e = a;
        for (j = a + 1; j < t; j++) {
            if (v[j] > v[e]) {
                e = j;
            }
        }
        if (v[e] <= v[t]) {
            continue;
        }
        r->beats[r->nbeats].ed = e * step;
        r->beats[r->nbeats].es = t * step;
        r->beats[r->nbeats].v_ed = v[e];
        r->beats[r->nbeats].v_es = v[t];
        r->beats[r->nbeats].ef = 100 * (v[e] - v[t]) / v[e];
        r->nbeats++;
    }
    if (r->nbeats > 0) {
        double efs[EN_MAXF / 2], dev[EN_MAXF / 2], mean;

        for (i = 0; i < r->nbeats; i++) {
            efs[i] = r->beats[i].ef;
        }
        mean = job_np_sum(efs, r->nbeats) / r->nbeats;
        r->ef = (c->agg == 1u) ? mean : en_median(efs, r->nbeats);
        for (i = 0; i < r->nbeats; i++) {
            double x = efs[i] - mean;

            dev[i] = x * x;
        }
        r->ef_sd = sqrt(job_np_sum(dev, r->nbeats) / r->nbeats);
        r->has_sd = 1;
    } else {
        double vmin = 1e300;

        for (i = 0; i < n; i++) {
            vmin = v[i] < vmin ? v[i] : vmin;
        }
        r->ef = 100 * (vmax - vmin) / vmax;
        r->fallback = 1;
    }
    free(v);
    free(tr);
}

int en_analyze_flat(const double *vol, int n, double fps, int step, unsigned smooth, unsigned agg, double dist_s,
                    double prom, double win, double *head, double *beats)
{
    en_cfg_t     c;
    en_result_t *r = (en_result_t *)malloc(sizeof(en_result_t));
    int          i, nb;

    if (r == NULL) {
        return -1;
    }
    c.smooth = smooth;
    c.agg = agg;
    c.dist_s = dist_s;
    c.prom = prom;
    c.win = win;
    en_analyze(vol, n, fps, step, &c, r);
    head[0] = r->ef;
    head[1] = r->ef_sd;
    head[2] = r->hr;
    head[3] = r->cycle_s;
    head[4] = r->has_hr;
    head[5] = r->has_sd;
    head[6] = r->fallback;
    head[7] = r->nbeats;
    for (i = 0; i < r->nbeats; i++) {
        beats[5 * i + 0] = r->beats[i].ed;
        beats[5 * i + 1] = r->beats[i].es;
        beats[5 * i + 2] = r->beats[i].v_ed;
        beats[5 * i + 3] = r->beats[i].v_es;
        beats[5 * i + 4] = r->beats[i].ef;
    }
    nb = r->nbeats;
    free(r);
    return nb;
}
