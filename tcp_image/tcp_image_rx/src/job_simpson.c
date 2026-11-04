/* job_simpson.c -- JOB_SIMPSON_EF: F4 biplane Simpson EF (CAMUS) computed on the board.
 *
 * Line-by-line port of tcp_image_ui/simpson_port.py, the library-free twin of ef_simpson.py (CAMUS notebook
 * method): Pillow NEAREST resize, scipy clean, skimage find_contours(seg, 0.5)[0], mitral chord search, apex,
 * 20 disks, volumes, EF -- bit for bit (checked against ef_simpson.volumes on the expert masks).
 *
 * params (32 bytes): f64 spacing 2CH (height, width) mm, f64 spacing 4CH (height, width) mm
 * data: 4 masks in the order 2CH ED, 2CH ES, 4CH ED, 4CH ES, each
 *       u16 src_w, u16 src_h, u16 out_w, u16 out_h (NEAREST resize to out when it differs from src),
 *       u8 clean (largest 4-component + fill holes), u8 0 x3, then src_w * src_h bits (bit k&7 of byte k>>3)
 * result: {"edv","esv","ef","step","masks":[{"diam":[20],"step","base","mid","apex","disks","n_contour"}...]}
 *         geometry in the original (resized-to-out) pixel coordinates; blob "contours": per mask, n x (row, col)
 *         float32 in the same coordinates (for drawing)
 *
 * v5.16 (P1 1.6, tcp_image_ui/cardiac_measure.py): the data may carry 4 more masks in the same format, in the order
 * 2CH ED epicardium, 4CH ED epicardium, 2CH ES left atrium, 4CH ES left atrium; the result then adds
 * "cardiac":{la_truncated, la_raw_ml, la_ml (training-set linear calibration), epi_ml, endo_ml, lv_mass_g, wall_mm,
 * wall_mm_views, errors}. Volumes are cardiac_measure.biplane_volume (not rounded), bit for bit; LA truncated = any LA
 * pixel in the bottom LA_EDGE_ROWS rows of either view -> no volume ("left atrium cut off, not measurable").
 * Measurement aids for review only (no diagnosis); BSA / LAVI / reference ranges are applied on the PC / board UI.
 *
 * part A (10-16, next server version; cardiac_measure.lvidd_view_mm / lvmi_class): "cardiac" also
 * gets lvidd_mm (mean of ED endocardium disks LVIDD_D0..+3, both views averaged), lvidd_mm_views and
 * rwt = 2 * wall_mm / lvidd_mm (a value only: the guideline 0.42 cut-off is not applied, user 10-16 -- our 2D-mask
 * walls put 98% of expert cases above it). Optional params bytes 32..55: f64 height cm, f64 weight kg, u8 sex
 * (0 unknown, 1 F, 2 M), 7 bytes 0; with height and weight > 0 it adds bsa_m2 (Mosteller), lvmi, and "lvmi_class" =
 * {"code":"increased"|"not_increased","lvmi_limit"} with the sex, else null (LVMI > 95 F / 115 M g/m2, ASE 2015
 * linear-method limits; ours come from 2D apical masks). Older clients send 32 bytes: no BSA / class.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "image_proto.h"
#include "job.h"

#define NDISK 20
#define MAXDIM 4096

/* cardiac_measure.py (P1 1.6): LA_EDGE_ROWS, LA_CAL (echo/fit_la_calib.py on the TRAIN split, 192 patients,
 * results/p1_16/la_calib.json), DENSITY (ASE 2015 sec. 4) */
#define LA_EDGE_ROWS 2
#define LA_CAL_A     0.9533886925323705
#define LA_CAL_B     9.024183337520322
#define MYO_DENSITY  1.05
/* part A: LVMI_LIMIT, LVIDD_DISKS = 3..6 */
#define LVMI_LIM_F   95
#define LVMI_LIM_M   115
#define LVIDD_D0     3
#define LVIDD_ND     4

typedef struct {
    double  diam[NDISK], step, back[2];
    double *cr, *cc;                    /* contour (iso pixel coordinates) */
    int     n, bi, bj, mid, apex;
    double  dir[2], normal[2], ends[NDISK][2][2];
} sgeom_t;

/* ---------------------------------------------------------------- Pillow Image.resize(NEAREST) */

static void nearest_index(int n_in, int n_out, int *idx)
{
    double a0 = (double)n_in / (double)n_out, xo = 0.0 + a0 * 0.5;
    int    k;

    for (k = 0; k < n_out; k++) {                     /* ImagingScaleAffine: accumulated, not multiplied */
        int xin = (xo < 0.0) ? -1 : (int)xo;

        idx[k] = xin < 0 ? 0 : (xin >= n_in ? n_in - 1 : xin);
        xo += a0;
    }
}

static uint8_t *resize_nearest(const uint8_t *m, int W, int H, int w, int h)
{
    uint8_t *o = (uint8_t *)malloc((size_t)w * h);
    int     *xi = (int *)malloc((size_t)w * sizeof(int)), *yi = (int *)malloc((size_t)h * sizeof(int)), y, x;

    if (o == NULL || xi == NULL || yi == NULL) {
        free(o);
        o = NULL;
    } else {
        nearest_index(W, w, xi);
        nearest_index(H, h, yi);
        for (y = 0; y < h; y++) {
            for (x = 0; x < w; x++) {
                o[(size_t)y * w + x] = m[(size_t)yi[y] * W + xi[x]];
            }
        }
    }
    free(xi);
    free(yi);
    return o;
}

/* ---------------------------------------------------------------- scipy clean: largest 4-component + fill holes */

static int clean(uint8_t *m, int w, int h)
{
    size_t n = (size_t)w * h, i;
    int   *lab = (int *)malloc(n * sizeof(int)), *st = (int *)malloc(n * sizeof(int)), ncomp = 0, best = -1;
    long   bestn = -1;

    if (lab == NULL || st == NULL) {
        free(lab);
        free(st);
        return -1;
    }
    for (i = 0; i < n; i++) {
        lab[i] = -1;
    }
    for (i = 0; i < n; i++) {                         /* components numbered in raster order of their first pixel */
        int  sp = 0;
        long cnt = 0;

        if (!m[i] || lab[i] >= 0) {
            continue;
        }
        st[sp++] = (int)i;
        lab[i] = ncomp;
        while (sp > 0) {
            int p = st[--sp], y = p / w, x = p % w, k;
            const int nb[4] = {p - w, p + w, p - 1, p + 1};
            const int ok[4] = {y > 0, y < h - 1, x > 0, x < w - 1};

            cnt++;
            for (k = 0; k < 4; k++) {
                if (ok[k] && m[nb[k]] && lab[nb[k]] < 0) {
                    lab[nb[k]] = ncomp;
                    st[sp++] = nb[k];
                }
            }
        }
        if (cnt > bestn) {
            bestn = cnt;
            best = ncomp;
        }
        ncomp++;
    }
    if (ncomp > 1) {
        for (i = 0; i < n; i++) {
            m[i] = (uint8_t)(lab[i] == best);
        }
    }
    {                                                 /* background 4-connected to the border stays background */
        int sp = 0, x, y;

        for (i = 0; i < n; i++) {
            lab[i] = 0;
        }
        for (y = 0; y < h; y++) {
            for (x = 0; x < w; x++) {
                int p = y * w + x;

                if ((y == 0 || y == h - 1 || x == 0 || x == w - 1) && !m[p] && !lab[p]) {
                    lab[p] = 1;
                    st[sp++] = p;
                }
            }
        }
        while (sp > 0) {
            int p = st[--sp], py = p / w, px = p % w, k;
            const int nb[4] = {p - w, p + w, p - 1, p + 1};
            const int ok[4] = {py > 0, py < h - 1, px > 0, px < w - 1};

            for (k = 0; k < 4; k++) {
                if (ok[k] && !m[nb[k]] && !lab[nb[k]]) {
                    lab[nb[k]] = 1;
                    st[sp++] = nb[k];
                }
            }
        }
        for (i = 0; i < n; i++) {
            if (!m[i] && !lab[i]) {
                m[i] = 1u;
            }
        }
    }
    free(lab);
    free(st);
    return ncomp;
}

/* ---------------------------------------------------------------- skimage find_contours(seg, 0.5)[0] */

typedef struct {
    int *pr, *pc, *nxt, n, cap;          /* points (doubled coordinates) as linked nodes */
    int *first, *last;
    char *alive;
    int  nc, ccap;
} asm_t;

static int node(asm_t *a, int r, int c)
{
    if (a->n == a->cap) {
        int  cap = a->cap ? 2 * a->cap : 4096;
        int *pr = (int *)realloc(a->pr, (size_t)cap * sizeof(int)), *pc, *nx;

        if (pr == NULL) return -1;
        a->pr = pr;
        pc = (int *)realloc(a->pc, (size_t)cap * sizeof(int));
        if (pc == NULL) return -1;
        a->pc = pc;
        nx = (int *)realloc(a->nxt, (size_t)cap * sizeof(int));
        if (nx == NULL) return -1;
        a->nxt = nx;
        a->cap = cap;
    }
    a->pr[a->n] = r;
    a->pc[a->n] = c;
    a->nxt[a->n] = -1;
    return a->n++;
}

static int new_contour(asm_t *a)
{
    if (a->nc == a->ccap) {
        int   cap = a->ccap ? 2 * a->ccap : 256;
        int  *f = (int *)realloc(a->first, (size_t)cap * sizeof(int)), *l;
        char *al;

        if (f == NULL) return -1;
        a->first = f;
        l = (int *)realloc(a->last, (size_t)cap * sizeof(int));
        if (l == NULL) return -1;
        a->last = l;
        al = (char *)realloc(a->alive, (size_t)cap);
        if (al == NULL) return -1;
        a->alive = al;
        a->ccap = cap;
    }
    return a->nc++;
}

/* one segment from (ar, ac) to (br, bc), doubled coordinates; starts / ends map a point to a contour or -1 */
static int add_segment(asm_t *a, int *starts, int *ends, int gw, int ar, int ac, int br, int bc)
{
    const int pa = ar * gw + ac, pb = br * gw + bc;
    int       tail = starts[pb], head = ends[pa], n;

    starts[pb] = -1;                                   /* dict.pop */
    ends[pa] = -1;
    if (tail >= 0 && head >= 0) {
        if (tail == head) {                            /* close the contour */
            if ((n = node(a, br, bc)) < 0) return -1;
            a->nxt[a->last[head]] = n;
            a->last[head] = n;
        } else {                                       /* join: sequence = head + tail, the older number survives */
            int keep = tail > head ? head : tail, gone = tail > head ? tail : head, f, l;

            a->nxt[a->last[head]] = a->first[tail];
            f = a->first[head];
            l = a->last[tail];
            if (keep == tail) {
                starts[a->pr[f] * gw + a->pc[f]] = -1;
            }
            a->alive[gone] = 0;
            a->first[keep] = f;
            a->last[keep] = l;
            starts[a->pr[f] * gw + a->pc[f]] = keep;
            ends[a->pr[l] * gw + a->pc[l]] = keep;
        }
    } else if (tail < 0 && head < 0) {
        int n1 = node(a, ar, ac), n2 = node(a, br, bc), k = new_contour(a);

        if (n1 < 0 || n2 < 0 || k < 0) return -1;
        a->nxt[n1] = n2;
        a->first[k] = n1;
        a->last[k] = n2;
        a->alive[k] = 1;
        starts[pa] = k;
        ends[pb] = k;
    } else if (head < 0) {                             /* prepend */
        if ((n = node(a, ar, ac)) < 0) return -1;
        a->nxt[n] = a->first[tail];
        a->first[tail] = n;
        starts[pa] = tail;
    } else {                                           /* append */
        if ((n = node(a, br, bc)) < 0) return -1;
        a->nxt[a->last[head]] = n;
        a->last[head] = n;
        ends[pb] = head;
    }
    return 0;
}

/* -> number of points of the first contour (cr / cc malloc'ed, plain coordinates), 0 none, -1 out of memory */
static int first_contour(const uint8_t *s, int w, int h, double **cr, double **cc)
{
    const int gw = 2 * w + 1;
    size_t    gn = (size_t)(2 * h + 1) * gw, i;
    int      *starts = (int *)malloc(gn * sizeof(int)), *ends = (int *)malloc(gn * sizeof(int));
    asm_t     a;
    int       r0, c0, rc = -1, k, n, cnt;

    memset(&a, 0, sizeof(a));
    *cr = *cc = NULL;
    if (starts == NULL || ends == NULL) {
        goto done;
    }
    for (i = 0; i < gn; i++) {
        starts[i] = ends[i] = -1;
    }
    for (r0 = 0; r0 < h - 1; r0++) {
        for (c0 = 0; c0 < w - 1; c0++) {
            const int ul = s[(size_t)r0 * w + c0] != 0, ur = s[(size_t)r0 * w + c0 + 1] != 0;
            const int ll = s[(size_t)(r0 + 1) * w + c0] != 0, lr = s[(size_t)(r0 + 1) * w + c0 + 1] != 0;
            const int cs = ul + 2 * ur + 4 * ll + 8 * lr, R = 2 * r0, C = 2 * c0;
            /* top (R, C+1), bottom (R+2, C+1), left (R+1, C), right (R+1, C+2) */
            int e = 0;

            switch (cs) {
            case 1: e = add_segment(&a, starts, ends, gw, R, C + 1, R + 1, C); break;
            case 2: e = add_segment(&a, starts, ends, gw, R + 1, C + 2, R, C + 1); break;
            case 3: e = add_segment(&a, starts, ends, gw, R + 1, C + 2, R + 1, C); break;
            case 4: e = add_segment(&a, starts, ends, gw, R + 1, C, R + 2, C + 1); break;
            case 5: e = add_segment(&a, starts, ends, gw, R, C + 1, R + 2, C + 1); break;
            case 6: e = add_segment(&a, starts, ends, gw, R + 1, C + 2, R, C + 1);
                    e |= add_segment(&a, starts, ends, gw, R + 1, C, R + 2, C + 1); break;
            case 7: e = add_segment(&a, starts, ends, gw, R + 1, C + 2, R + 2, C + 1); break;
            case 8: e = add_segment(&a, starts, ends, gw, R + 2, C + 1, R + 1, C + 2); break;
            case 9: e = add_segment(&a, starts, ends, gw, R, C + 1, R + 1, C);
                    e |= add_segment(&a, starts, ends, gw, R + 2, C + 1, R + 1, C + 2); break;
            case 10: e = add_segment(&a, starts, ends, gw, R + 2, C + 1, R, C + 1); break;
            case 11: e = add_segment(&a, starts, ends, gw, R + 2, C + 1, R + 1, C); break;
            case 12: e = add_segment(&a, starts, ends, gw, R + 1, C, R + 1, C + 2); break;
            case 13: e = add_segment(&a, starts, ends, gw, R, C + 1, R + 1, C + 2); break;
            case 14: e = add_segment(&a, starts, ends, gw, R + 1, C, R, C + 1); break;
            default: break;
            }
            if (e) {
                goto done;
            }
        }
    }
    for (k = 0; k < a.nc && !a.alive[k]; k++) {
    }
    if (k == a.nc) {
        rc = 0;
        goto done;
    }
    for (cnt = 0, n = a.first[k]; n != -1; n = (n == a.last[k]) ? -1 : a.nxt[n]) {
        cnt++;
    }
    *cr = (double *)malloc((size_t)cnt * sizeof(double));
    *cc = (double *)malloc((size_t)cnt * sizeof(double));
    if (*cr == NULL || *cc == NULL) {
        free(*cr);
        free(*cc);
        *cr = *cc = NULL;
        goto done;
    }
    for (cnt = 0, n = a.first[k]; n != -1; n = (n == a.last[k]) ? -1 : a.nxt[n]) {
        (*cr)[cnt] = a.pr[n] / 2.0;
        (*cc)[cnt] = a.pc[n] / 2.0;
        cnt++;
    }
    rc = cnt;
done:
    free(starts);
    free(ends);
    free(a.pr);
    free(a.pc);
    free(a.nxt);
    free(a.first);
    free(a.last);
    free(a.alive);
    return rc;
}

/* ---------------------------------------------------------------- ef_simpson.diameters */

static double distance_to_edge(const uint8_t *seg, int w, int h, const double *p, const double *nrm)
{
    double d = 8.0;

    for (;;) {
        int y = (int)nearbyint(p[0] + d * nrm[0]), x = (int)nearbyint(p[1] + d * nrm[1]);

        if (!(y >= 0 && y < h && x >= 0 && x < w) || seg[(size_t)y * w + x] == 0) {
            return d;
        }
        d += 0.5;
    }
}

/* 0 ok; -1 out of memory; -2 empty mask / no contour; -3 no mitral chord */
static int diameters(const uint8_t *mask, int W, int H, const double sp[2], sgeom_t *g)
{
    const double mn = sp[0] < sp[1] ? sp[0] : sp[1], iso = mn;
    int          h = (int)nearbyint((double)H * (sp[0] / mn)), w = (int)nearbyint((double)W * (sp[1] / mn));
    uint8_t     *seg;
    double      *cr, *cc, best = 0.0, axis_len, nn;
    int          n, i, j, k;

    memset(g, 0, sizeof(*g));
    if (h < 2 || w < 2 || h > MAXDIM || w > MAXDIM) {
        return -2;
    }
    seg = resize_nearest(mask, W, H, w, h);           /* to_isotropic */
    if (seg == NULL) {
        return -1;
    }
    n = first_contour(seg, w, h, &cr, &cc);
    if (n <= 0) {
        free(seg);
        return n < 0 ? -1 : -2;
    }
    g->bi = g->bj = -1;
    for (i = 2; i < n; i++) {
        const double ri = cr[i], ci = cc[i];

        for (j = 0; j < i; j++) {
            const double dr = cr[j] - ri, dc = cc[j] - ci;  /* |degrees(arctan2(dr, dc))| <= 45 <=> dc >= |dr| */
            double       nv;
            int          ok = 1;

            if (!(dc >= fabs(dr))) {
                continue;
            }
            nv = sqrt(dr * dr + dc * dc);
            for (k = j + 1; k < i; k++) {
                const double w0 = ri - cr[k], w1 = ci - cc[k];

                if (!(fabs(dr * w1 - dc * w0) / nv <= 8)) {   /* NaN (nv == 0) fails, like numpy */
                    ok = 0;
                    break;
                }
            }
            if (ok) {
                const double dist = sqrt(dr * dr + dc * dc);

                if (best < dist) {
                    best = dist;
                    g->bi = i;
                    g->bj = j;
                }
            }
        }
    }
    if (g->bi < 0) {
        free(seg);
        free(cr);
        free(cc);
        return -3;
    }
    g->mid = (int)(g->bj + nearbyint((g->bi - g->bj) / 2.0));
    g->apex = 0;
    axis_len = -1.0;
    for (k = 0; k < n; k++) {                          /* first maximum */
        const double a = cr[k] - cr[g->mid], b = cc[k] - cc[g->mid], l = sqrt(a * a + b * b);

        if (l > axis_len) {
            axis_len = l;
            g->apex = k;
        }
    }
    g->dir[0] = cr[g->apex] - cr[g->mid];
    g->dir[1] = cc[g->apex] - cc[g->mid];
    nn = sqrt(g->dir[1] * g->dir[1] + g->dir[0] * g->dir[0]);   /* norm of (-dir1, dir0) */
    g->normal[0] = -g->dir[1] / nn;
    g->normal[1] = g->dir[0] / nn;
    for (k = 0; k < NDISK; k++) {
        const double f = (double)k * (1.0 / NDISK);    /* np.linspace(0, 1, 20, endpoint=False) */
        const double p[2] = {cr[g->mid] + g->dir[0] * f, cc[g->mid] + g->dir[1] * f};
        const double mneg[2] = {-g->normal[0], -g->normal[1]};
        const double d1 = distance_to_edge(seg, w, h, p, g->normal), d2 = distance_to_edge(seg, w, h, p, mneg);

        g->diam[k] = (d1 + d2) * iso;
        g->ends[k][0][0] = p[0] + d1 * g->normal[0];
        g->ends[k][0][1] = p[1] + d1 * g->normal[1];
        g->ends[k][1][0] = p[0] - d2 * g->normal[0];
        g->ends[k][1][1] = p[1] - d2 * g->normal[1];
    }
    g->step = axis_len * iso / 20;
    g->back[0] = iso / sp[0];
    g->back[1] = iso / sp[1];
    g->cr = cr;
    g->cc = cc;
    g->n = n;
    free(seg);
    return 0;
}

static long vol_ml(const double *a, const double *b, double step)
{
    double t[NDISK];
    int    k;

    for (k = 0; k < NDISK; k++) {
        t[k] = (a[k] / 1000) * (b[k] / 1000);
    }
    return (long)nearbyint(job_np_sum(t, NDISK) * (step / 1000) * M_PI / 4 * 1e6);
}

/* ---------------------------------------------------------------- P1 1.6: cardiac_measure.py */

/* biplane_volume: np.sum(d2 * d4) * max(s2, s4) * pi / 4 / 1000 (mL, not rounded) */
static double biplane_ml(const sgeom_t *a, const sgeom_t *b)
{
    double t[NDISK], step = a->step >= b->step ? a->step : b->step;
    int    k;

    for (k = 0; k < NDISK; k++) {
        t[k] = a->diam[k] * b->diam[k];
    }
    return job_np_sum(t, NDISK) * step * M_PI / 4.0 / 1000.0;
}

static int la_truncated(const uint8_t *m, int w, int h)
{
    size_t i, n = (size_t)w * h, s = (size_t)(h > LA_EDGE_ROWS ? h - LA_EDGE_ROWS : 0) * w;

    for (i = s; i < n; i++) {
        if (m[i]) {
            return 1;
        }
    }
    return 0;
}

static long long fdiv(long long a, long long b)        /* floor division, b > 0 */
{
    long long q = a / b;

    return (a % b != 0 && a < 0) ? q - 1 : q;
}

/* exact squared Euclidean distance to the nearest 0 pixel (Meijster et al.), scipy distance_transform_edt squared */
static long long *edt_sq(const uint8_t *m, int w, int h)
{
    const long long inf = (long long)w + h + 1;
    long long      *g = (long long *)malloc((size_t)w * h * sizeof(long long));
    long long      *d = (long long *)malloc((size_t)w * h * sizeof(long long));
    int            *s = (int *)malloc((size_t)w * sizeof(int)), *t = (int *)malloc((size_t)w * sizeof(int));
    int             x, y, u, q;

    if (g == NULL || d == NULL || s == NULL || t == NULL) {
        free(g);
        free(d);
        free(s);
        free(t);
        return NULL;
    }
    for (x = 0; x < w; x++) {                          /* phase 1: per column */
        g[x] = m[x] ? inf : 0;
        for (y = 1; y < h; y++) {
            g[(size_t)y * w + x] = m[(size_t)y * w + x] ? g[(size_t)(y - 1) * w + x] + 1 : 0;
        }
        for (y = h - 2; y >= 0; y--) {
            if (g[(size_t)(y + 1) * w + x] < g[(size_t)y * w + x]) {
                g[(size_t)y * w + x] = g[(size_t)(y + 1) * w + x] + 1;
            }
        }
    }
    for (y = 0; y < h; y++) {                          /* phase 2: per row, lower envelope */
        const long long *gy = g + (size_t)y * w;

#define F(xx, i) ((long long)((xx) - (i)) * ((xx) - (i)) + gy[i] * gy[i])
        q = 0;
        s[0] = 0;
        t[0] = 0;
        for (u = 1; u < w; u++) {
            while (q >= 0 && F(t[q], s[q]) > F(t[q], u)) {
                q--;
            }
            if (q < 0) {
                q = 0;
                s[0] = u;
            } else {
                long long ww = 1 + fdiv((long long)u * u - (long long)s[q] * s[q] + gy[u] * gy[u] - gy[s[q]] * gy[s[q]],
                                        2LL * (u - s[q]));

                if (ww < w) {
                    q++;
                    s[q] = u;
                    t[q] = (int)ww;
                }
            }
        }
        for (u = w - 1; u >= 0; u--) {
            d[(size_t)y * w + u] = F(u, s[q]);
            if (u == t[q]) {
                q--;
            }
        }
#undef F
    }
    free(g);
    free(s);
    free(t);
    return d;
}

static int cmp_double(const void *a, const void *b)
{
    const double x = *(const double *)a, y = *(const double *)b;

    return (x > y) - (x < y);
}

/* wall_thickness_mm: median distance (mm) from the endocardial edge points (minus those within 3 mm of the mitral
 * chord) to the outside of the epicardium, both masks at the isotropic spacing. 0 ok (*out set), 1 no value, -1 OOM */
static int wall_view(const uint8_t *endo, const uint8_t *epi, int W, int H, const double sp[2], const sgeom_t *g,
                     double *out)
{
    const double mn = sp[0] < sp[1] ? sp[0] : sp[1];
    const int    h = (int)nearbyint((double)H * (sp[0] / mn)), w = (int)nearbyint((double)W * (sp[1] / mn));
    uint8_t     *ei = resize_nearest(endo, W, H, w, h), *pi = resize_nearest(epi, W, H, w, h);
    long long   *d2 = NULL;
    double      *vals = NULL, y0, x0, y1, x1, v0, v1, L;
    size_t       i, n = (size_t)w * h, nv = 0;
    int          rc = 1, anye = 0, anyp = 0, y, x;

    if (ei == NULL || pi == NULL) {
        rc = -1;
        goto out;
    }
    for (i = 0; i < n; i++) {
        anye |= ei[i] != 0;
        anyp |= pi[i] != 0;
    }
    if (!anye || !anyp) {
        goto out;
    }
    d2 = edt_sq(pi, w, h);
    vals = (double *)malloc(n * sizeof(double) + 1u);
    if (d2 == NULL || vals == NULL) {
        rc = -1;
        goto out;
    }
    /* geo['base'] back to isotropic coordinates exactly as the Python: (contour * back) / back */
    y0 = (g->cr[g->bj] * g->back[0]) / g->back[0];
    x0 = (g->cc[g->bj] * g->back[1]) / g->back[1];
    y1 = (g->cr[g->bi] * g->back[0]) / g->back[0];
    x1 = (g->cc[g->bi] * g->back[1]) / g->back[1];
    v0 = y1 - y0;
    v1 = x1 - x0;
    L = hypot(v0, v1);
    if (L == 0.0) {
        L = 1.0;
    }
    for (y = 0; y < h; y++) {
        for (x = 0; x < w; x++) {
            const size_t q = (size_t)y * w + x;
            double       t, dd;

            /* edge = endo & ~binary_erosion(endo): a 4-neighbour is 0 or outside (border_value 0) */
            if (!ei[q] || (y > 0 && x > 0 && y < h - 1 && x < w - 1 && ei[q - 1] && ei[q + 1] && ei[q - w] && ei[q + w])) {
                continue;
            }
            t = (((double)y - y0) * v0 + ((double)x - x0) * v1) / (L * L);
            t = t < 0.0 ? 0.0 : (t > 1.0 ? 1.0 : t);
            dd = hypot((double)y - (y0 + t * v0), (double)x - (x0 + t * v1)) * mn;
            if (dd > 3.0) {
                vals[nv++] = sqrt((double)d2[q]) * mn;
            }
        }
    }
    if (nv > 0) {
        qsort(vals, nv, sizeof(double), cmp_double);
        *out = (nv & 1u) ? vals[nv / 2] : (vals[nv / 2 - 1] + vals[nv / 2]) / 2.0;
        rc = 0;
    }
out:
    free(ei);
    free(pi);
    free(d2);
    free(vals);
    return rc;
}

/* lvidd_view_mm: disks LVIDD_D0 .. LVIDD_D0 + LVIDD_ND - 1 added left to right, then divided */
static double lvidd_view(const sgeom_t *g)
{
    double s = g->diam[LVIDD_D0];
    int    k;

    for (k = 1; k < LVIDD_ND; k++) {
        s = s + g->diam[LVIDD_D0 + k];
    }
    return s / LVIDD_ND;
}

static void jnum(jbuf_t *b, const char *key, int ok, double v)
{
    if (ok) {
        jb_add(b, ",\"%s\":%.17g", key, v);
    } else {
        jb_add(b, ",\"%s\":null", key);
    }
}

/* ---------------------------------------------------------------- mask upload */

/* one mask record -> *out (original size, cleaned if asked). 0 ok, 1 failed (error RESULT sent) */
static int read_mask(job_t *j, const uint8_t **pp, const uint8_t *end, const double sp[2], const char *name,
                     uint8_t **out, int *pw, int *ph, int *ncomp)
{
    const uint8_t *p = *pp;
    int            sw, sh, ow, oh, cl, y, x;
    size_t         nb;
    uint8_t       *m, *r;
    char           err[160];

    *out = NULL;
    if (end - p < 12) {
        job_fail(j, "truncated mask header");
        return 1;
    }
    sw = proto_get_le16(p);
    sh = proto_get_le16(p + 2);
    ow = proto_get_le16(p + 4);
    oh = proto_get_le16(p + 6);
    cl = p[8];
    p += 12;
    nb = ((size_t)sw * sh + 7u) / 8u;
    if (sw < 2 || sh < 2 || ow < 2 || oh < 2 || sw > MAXDIM || sh > MAXDIM || ow > MAXDIM || oh > MAXDIM ||
        (size_t)(end - p) < nb || !(sp[0] > 0.0) || !(sp[1] > 0.0)) {
        snprintf(err, sizeof(err), "bad mask %s", name);
        job_fail(j, err);
        return 1;
    }
    m = (uint8_t *)malloc((size_t)sw * sh);
    if (m == NULL) {
        job_fail(j, "out of memory");
        return 1;
    }
    for (y = 0; y < sh; y++) {
        for (x = 0; x < sw; x++) {
            size_t q = (size_t)y * sw + x;

            m[q] = (uint8_t)((p[q >> 3] >> (q & 7u)) & 1u);
        }
    }
    p += nb;
    if (ow != sw || oh != sh) {                        /* back to the original size (sequence._mask_original) */
        r = resize_nearest(m, sw, sh, ow, oh);
        free(m);
        m = r;
        if (m == NULL) {
            job_fail(j, "out of memory");
            return 1;
        }
    }
    *ncomp = cl ? clean(m, ow, oh) : -1;
    *pp = p;
    *out = m;
    *pw = ow;
    *ph = oh;
    return 0;
}

/* ---------------------------------------------------------------- the job */

int job_simpson_run(job_t *j)
{
    static const char *const names[4] = {"2CH ED", "2CH ES", "4CH ED", "4CH ES"};
    sgeom_t     g[4];
    double      sp[2][2], step = 0.0;
    const uint8_t *p = j->data, *end = j->data + j->data_bytes;
    char        err[160], *js = NULL, xes[512] = "";
    jbuf_t      b, xe;
    sgeom_t     xg[4];
    uint8_t    *endo[2] = {NULL, NULL}, *xm[4] = {NULL, NULL, NULL, NULL};
    int         ew[2] = {0, 0}, eh[2] = {0, 0}, xw[4], xh[4], xok[4] = {0, 0, 0, 0}, wok[2] = {0, 0};
    int         has_cardiac = 0, la_trunc = 0, sex = 0;
    double      wall[2] = {0.0, 0.0}, height = 0.0, weight = 0.0;
    float      *cblob = NULL;
    size_t      cn = 0;
    long        edv, esv;
    int         k, rc = -1, ncomp[4];

    memset(g, 0, sizeof(g));
    memset(xg, 0, sizeof(xg));
    jb_init(&xe, xes, sizeof(xes));
    if (j->param_bytes < 32u) {
        return job_fail(j, "simpson_ef needs 32 bytes of params");
    }
    for (k = 0; k < 4; k++) {
        memcpy(&sp[k / 2][k % 2], j->param + 8 * k, 8u);
    }
    if (j->param_bytes >= 56u) {                       /* part A: patient height / weight / sex */
        memcpy(&height, j->param + 32, 8u);
        memcpy(&weight, j->param + 40, 8u);
        sex = j->param[48];
    }
    for (k = 0; k < 4; k++) {
        uint8_t *m;
        int      ow, oh, e;

        if (read_mask(j, &p, end, sp[k / 2], names[k], &m, &ow, &oh, &ncomp[k]) != 0) {
            rc = 0;
            goto out;
        }
        e = diameters(m, ow, oh, sp[k / 2], &g[k]);
        if (k == 0 || k == 2) {                        /* ED endocardium: kept for the wall thickness */
            endo[k / 2] = m;
            ew[k / 2] = ow;
            eh[k / 2] = oh;
        } else {
            free(m);
        }
        if (e != 0) {
            snprintf(err, sizeof(err), "%s: %s", names[k],
                     e == -2 ? "empty mask / no contour" : (e == -3 ? "no mitral plane found" : "out of memory"));
            rc = job_fail(j, err);
            goto out;
        }
        if (job_progress(j, "simpson", (uint32_t)k + 1u, 4u, 0) != 0) {
            goto out;
        }
    }
    if (p < end) {                                     /* v5.16: epicardium + left atrium (P1 1.6) */
        static const char *const xn[4] = {"2CH ED epi", "4CH ED epi", "2CH ES LA", "4CH ES LA"};

        for (k = 0; k < 4; k++) {
            int nc;

            if (read_mask(j, &p, end, sp[k % 2], xn[k], &xm[k], &xw[k], &xh[k], &nc) != 0) {
                rc = 0;
                goto out;
            }
        }
        has_cardiac = 1;
        la_trunc = la_truncated(xm[2], xw[2], xh[2]) || la_truncated(xm[3], xw[3], xh[3]);
        for (k = 0; k < 4; k++) {
            int e;

            if (k >= 2 && la_trunc) {
                continue;
            }
            e = diameters(xm[k], xw[k], xh[k], sp[k % 2], &xg[k]);
            xok[k] = e == 0;
            if (e == -1) {
                rc = job_fail(j, "out of memory");
                goto out;
            }
            if (e != 0) {
                jb_add(&xe, "%s\"%s: %s\"", xe.len ? "," : "", xn[k], e == -2 ? "empty mask / no contour" : "no mitral plane found");
            }
        }
        for (k = 0; k < 2; k++) {
            int e = (xw[k] == ew[k] && xh[k] == eh[k]) ? wall_view(endo[k], xm[k], ew[k], eh[k], sp[k], &g[2 * k], &wall[k]) : 1;

            if (e == -1) {
                rc = job_fail(j, "out of memory");
                goto out;
            }
            wok[k] = e == 0;
        }
        if (job_progress(j, "cardiac", 1u, 1u, 0) != 0) {
            goto out;
        }
    }
    for (k = 0; k < 4; k++) {
        step = g[k].step > step ? g[k].step : step;     /* max of the four */
    }
    edv = vol_ml(g[0].diam, g[2].diam, step);
    esv = vol_ml(g[1].diam, g[3].diam, step);
    js = (char *)malloc(1u << 16);
    if (js == NULL) {
        rc = job_fail(j, "out of memory");
        goto out;
    }
    jb_init(&b, js, 1u << 16);
    jb_add(&b, "{\"ok\":true,\"edv\":%ld,\"esv\":%ld,\"ef\":", edv, esv);
    if (edv != 0) {
        jb_add(&b, "%ld", (long)nearbyint((double)(100 * (edv - esv)) / (double)edv));
    } else {
        jb_add(&b, "null");
    }
    jb_add(&b, ",\"step\":%.17g,\"masks\":[", step);
    for (k = 0; k < 4; k++) {
        const sgeom_t *q = &g[k];
        const double   b0 = q->back[0], b1 = q->back[1];
        int            d;

        jb_add(&b, "%s{\"name\":\"%s\",\"ncomp\":%d,\"n_contour\":%d,\"step\":%.17g,\"diam\":[", k ? "," : "", names[k],
               ncomp[k], q->n, q->step);
        for (d = 0; d < NDISK; d++) {
            jb_add(&b, "%s%.17g", d ? "," : "", q->diam[d]);
        }
        jb_add(&b, "],\"base\":[[%.17g,%.17g],[%.17g,%.17g]],\"mid\":[%.17g,%.17g],\"apex\":[%.17g,%.17g],\"disks\":[",
               q->cr[q->bj] * b0, q->cc[q->bj] * b1, q->cr[q->bi] * b0, q->cc[q->bi] * b1, q->cr[q->mid] * b0,
               q->cc[q->mid] * b1, q->cr[q->apex] * b0, q->cc[q->apex] * b1);
        for (d = 0; d < NDISK; d++) {
            jb_add(&b, "%s[[%.9g,%.9g],[%.9g,%.9g]]", d ? "," : "", q->ends[d][0][0] * b0, q->ends[d][0][1] * b1,
                   q->ends[d][1][0] * b0, q->ends[d][1][1] * b1);
        }
        jb_add(&b, "]}");
        cn += (size_t)q->n;
    }
    jb_add(&b, "]");
    if (has_cardiac) {
        const int    epi_ok = xok[0] && xok[1], la_ok = !la_trunc && xok[2] && xok[3];
        const double endo_ml = biplane_ml(&g[0], &g[2]), epi_ml = epi_ok ? biplane_ml(&xg[0], &xg[1]) : 0.0;
        const double la_raw = la_ok ? biplane_ml(&xg[2], &xg[3]) : 0.0;

        jb_add(&b, ",\"cardiac\":{\"la_truncated\":%s", la_trunc ? "true" : "false");
        jnum(&b, "la_raw_ml", la_ok, la_raw);
        jnum(&b, "la_ml", la_ok, LA_CAL_A * la_raw + LA_CAL_B);
        jb_add(&b, ",\"la_cal\":[%.17g,%.17g],\"la_edge_rows\":%d", LA_CAL_A, LA_CAL_B, LA_EDGE_ROWS);
        jnum(&b, "endo_ml", 1, endo_ml);
        jnum(&b, "epi_ml", epi_ok, epi_ml);
        jnum(&b, "lv_mass_g", epi_ok, MYO_DENSITY * (epi_ml - endo_ml));
        jnum(&b, "wall_mm", wok[0] || wok[1], wok[0] && wok[1] ? (wall[0] + wall[1]) / 2.0 : (wok[0] ? wall[0] : wall[1]));
        jb_add(&b, ",\"wall_mm_views\":[");
        for (k = 0; k < 2; k++) {
            if (wok[k]) {
                jb_add(&b, "%s%.17g", k ? "," : "", wall[k]);
            } else {
                jb_add(&b, "%snull", k ? "," : "");
            }
        }
        jb_add(&b, "]");
        {                                              /* part A: LVIDd, RWT (value), BSA, LVMI, LVMI class */
            const double li[2] = {lvidd_view(&g[0]), lvidd_view(&g[2])}, lvidd = (li[0] + li[1]) / 2.0;
            const double wmm = wok[0] && wok[1] ? (wall[0] + wall[1]) / 2.0 : (wok[0] ? wall[0] : wall[1]);
            const int    rwt_ok = (wok[0] || wok[1]) && lvidd > 0.0;
            const double rwt = rwt_ok ? 2.0 * wmm / lvidd : 0.0;

            jnum(&b, "lvidd_mm", 1, lvidd);
            jb_add(&b, ",\"lvidd_mm_views\":[%.17g,%.17g]", li[0], li[1]);
            jnum(&b, "rwt", rwt_ok, rwt);
            if (height > 0.0 && weight > 0.0 && isfinite(height) && isfinite(weight)) {
                const double bsa = sqrt(height * weight / 3600.0), lvmi = MYO_DENSITY * (epi_ml - endo_ml) / bsa;
                const int    lim = sex == 1 ? LVMI_LIM_F : (sex == 2 ? LVMI_LIM_M : 0);

                jnum(&b, "bsa_m2", 1, bsa);
                jnum(&b, "lvmi", epi_ok, lvmi);
                if (epi_ok && lim) {
                    jb_add(&b, ",\"lvmi_class\":{\"code\":\"%s\",\"lvmi_limit\":%d}",
                           lvmi > lim ? "increased" : "not_increased", lim);
                } else {
                    jb_add(&b, ",\"lvmi_class\":null");
                }
            }
        }
        jb_add(&b, ",\"errors\":[%s]}", xes);
    }
    jb_add(&b, ",\"ms\":%.2f}", (double)(job_now_us() - j->t0_us) / 1000.0);
    cblob = (float *)malloc(cn * 2u * sizeof(float) + 1u);
    if (cblob == NULL) {
        rc = job_fail(j, "out of memory");
        goto out;
    }
    cn = 0;
    for (k = 0; k < 4; k++) {
        int t;

        for (t = 0; t < g[k].n; t++) {
            cblob[cn++] = (float)(g[k].cr[t] * g[k].back[0]);
            cblob[cn++] = (float)(g[k].cc[t] * g[k].back[1]);
        }
    }
    if (job_blob(j, "contours", cblob, cn * sizeof(float), 1) != 0) {
        goto out;
    }
    rc = job_result(j, js);
    printf("[job] simpson_ef: EDV %ld ESV %ld ml, %.0f ms\n", edv, esv, (double)(job_now_us() - j->t0_us) / 1000.0);
out:
    for (k = 0; k < 4; k++) {
        free(g[k].cr);
        free(g[k].cc);
        free(xg[k].cr);
        free(xg[k].cc);
        free(xm[k]);
    }
    free(endo[0]);
    free(endo[1]);
    free(cblob);
    free(js);
    return j->failed ? -1 : rc;
}
