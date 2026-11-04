/* strain.c -- part E: ED mid-wall line + speckle tracking -> GLS-like curve. See strain.h.
 * Every step mirrors E:/PLD/strain/step3/strain_port.py line for line (operation order included). */
#include "strain.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#define NPIX (STRAIN_W * STRAIN_W)

int strain_ws_init(strain_ws_t *w)
{
    memset(w, 0, sizeof(*w));
    w->path = (int32_t *)malloc(sizeof(int32_t) * 2u * NPIX);
    w->lv = (uint8_t *)malloc(STRAIN_M * STRAIN_M);
    w->epi = (uint8_t *)malloc(STRAIN_M * STRAIN_M);
    w->myo = (uint8_t *)malloc(NPIX);
    w->lab = (int32_t *)malloc(sizeof(int32_t) * NPIX);
    w->stack = (int32_t *)malloc(sizeof(int32_t) * NPIX);
    w->sk0 = (uint8_t *)malloc((STRAIN_W + 2) * (STRAIN_W + 2));
    w->sk1 = (uint8_t *)malloc((STRAIN_W + 2) * (STRAIN_W + 2));
    w->dist = (double *)malloc(sizeof(double) * NPIX);
    w->wtmp = (double *)malloc(sizeof(double) * NPIX);
    w->pred = (int32_t *)malloc(sizeof(int32_t) * NPIX);
    w->node = (int32_t *)malloc(sizeof(int32_t) * NPIX);
    w->nidx = (int32_t *)malloc(sizeof(int32_t) * NPIX);
    w->done = (uint8_t *)malloc(NPIX);
    if (!w->path || !w->lv || !w->epi || !w->myo || !w->lab || !w->stack || !w->sk0 || !w->sk1 || !w->dist ||
        !w->wtmp || !w->pred || !w->node || !w->nidx || !w->done) {
        strain_ws_free(w);
        return STRAIN_E_MEM;
    }
    return STRAIN_OK;
}

void strain_ws_free(strain_ws_t *w)
{
    free(w->path);
    free(w->lv);
    free(w->epi);
    free(w->myo);
    free(w->lab);
    free(w->stack);
    free(w->sk0);
    free(w->sk1);
    free(w->dist);
    free(w->wtmp);
    free(w->pred);
    free(w->node);
    free(w->nidx);
    free(w->done);
    memset(w, 0, sizeof(*w));
}

/* ---------------------------------------------------------------- masks */
/* components in raster order of their first pixel (scipy label order); lab[i] = 1-based label; returns the count and
 * the first largest label in *best */
static int label(const uint8_t *m, int w, int h, int conn8, int32_t *lab, int32_t *stack, int *best)
{
    int n = 0, bsize = 0;

    memset(lab, 0, sizeof(int32_t) * (size_t)w * h);
    *best = 0;
    for (int i = 0; i < w * h; i++) {
        if (!m[i] || lab[i]) {
            continue;
        }
        int sp = 0, size = 0;
        n++;
        lab[i] = n;
        stack[sp++] = i;
        while (sp > 0) {
            const int p = stack[--sp], y = p / w, x = p % w;
            size++;
            for (int dy = -1; dy <= 1; dy++) {
                for (int dx = -1; dx <= 1; dx++) {
                    if ((dy == 0 && dx == 0) || (!conn8 && dy != 0 && dx != 0)) {
                        continue;
                    }
                    const int yy = y + dy, xx = x + dx;
                    if (yy >= 0 && yy < h && xx >= 0 && xx < w && m[yy * w + xx] && !lab[yy * w + xx]) {
                        lab[yy * w + xx] = n;
                        stack[sp++] = yy * w + xx;
                    }
                }
            }
        }
        if (size > bsize) {               /* np.argmax: first largest */
            bsize = size;
            *best = n;
        }
    }
    return n;
}

int strain_clean(uint8_t *m, int w, int h, int32_t *lab, int32_t *stack)
{
    int best, sp = 0;
    const int n = label(m, w, h, 0, lab, stack, &best);

    if (n == 0) {
        return 0;
    }
    /* keep the largest; then background 4-connected to the border stays background, the rest is filled */
    for (int i = 0; i < w * h; i++) {
        m[i] = (uint8_t)(lab[i] == best);
        lab[i] = m[i];                    /* lab reused as "seen" */
    }
    for (int i = 0; i < w * h; i++) {
        const int y = i / w, x = i % w;
        if ((y == 0 || y == h - 1 || x == 0 || x == w - 1) && !lab[i]) {
            lab[i] = 1;
            stack[sp++] = i;
        }
    }
    while (sp > 0) {
        const int p = stack[--sp], y = p / w, x = p % w;
        const int nb[4][2] = {{y - 1, x}, {y + 1, x}, {y, x - 1}, {y, x + 1}};
        lab[p] = 2;                       /* outside */
        for (int k = 0; k < 4; k++) {
            const int yy = nb[k][0], xx = nb[k][1];
            if (yy >= 0 && yy < h && xx >= 0 && xx < w && !lab[yy * w + xx]) {
                lab[yy * w + xx] = 1;
                stack[sp++] = yy * w + xx;
            }
        }
    }
    for (int i = 0; i < w * h; i++) {
        m[i] = (uint8_t)(lab[i] != 2);
    }
    return n;
}

/* ---------------------------------------------------------------- Zhang thinning (skimage 0.25 _fast_skeletonize) */
/* weights NW 1, N 2, NE 4, E 8, SE 16, S 32, SW 64, W 128; pass 1 removes 1 and 3, pass 2 removes 2 and 3 */
static const uint8_t LUT[256] = {
    0, 0, 0, 1, 0, 0, 1, 3, 0, 0, 3, 1, 1, 0, 1, 3, 0, 0, 0, 0, 0, 0, 0, 0, 2, 0, 2, 0, 3, 0, 3, 3,
    0, 0, 0, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 0, 0, 0, 3, 0, 2, 2,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    2, 0, 0, 0, 0, 0, 0, 0, 2, 0, 0, 0, 2, 0, 0, 0, 3, 0, 0, 0, 0, 0, 0, 0, 3, 0, 0, 0, 3, 0, 2, 0,
    0, 0, 3, 1, 0, 0, 1, 3, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1,
    3, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    2, 3, 1, 3, 0, 0, 1, 3, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    2, 3, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 3, 3, 0, 1, 0, 0, 0, 0, 2, 2, 0, 0, 2, 0, 0, 0};

/* thin the 0/1 mask m (STRAIN_W^2) in place; work on its bounding box with a 1-pixel zero border (pixels outside the
 * box are 0, so this equals skimage's whole-image pass) */
static int skeletonize(uint8_t *m, uint8_t *s, uint8_t *c)
{
    int y0 = STRAIN_W, y1 = -1, x0 = STRAIN_W, x1 = -1, removed = 1, px = 0;

    for (int y = 0; y < STRAIN_W; y++) {
        for (int x = 0; x < STRAIN_W; x++) {
            if (m[y * STRAIN_W + x]) {
                y0 = y < y0 ? y : y0;
                y1 = y > y1 ? y : y1;
                x0 = x < x0 ? x : x0;
                x1 = x > x1 ? x : x1;
            }
        }
    }
    if (y1 < 0) {
        return 0;
    }
    const int bh = y1 - y0 + 3, bw = x1 - x0 + 3;
    memset(s, 0, (size_t)bh * bw);
    for (int y = y0; y <= y1; y++) {
        memcpy(s + (y - y0 + 1) * bw + 1, m + y * STRAIN_W + x0, (size_t)(x1 - x0 + 1));
    }
    memcpy(c, s, (size_t)bh * bw);
    while (removed) {
        removed = 0;
        for (int pass = 0; pass < 2; pass++) {
            for (int r = 1; r < bh - 1; r++) {
                for (int q = 1; q < bw - 1; q++) {
                    const uint8_t *p = s + r * bw + q;
                    if (!*p) {
                        continue;
                    }
                    const uint8_t v = LUT[p[-bw - 1] + 2 * p[-bw] + 4 * p[-bw + 1] + 8 * p[1] + 16 * p[bw + 1] +
                                          32 * p[bw] + 64 * p[bw - 1] + 128 * p[-1]];
                    if (v == 3 || (v == 1 && pass == 0) || (v == 2 && pass == 1)) {
                        c[r * bw + q] = 0;
                        removed = 1;
                    }
                }
            }
            memcpy(s, c, (size_t)bh * bw);
        }
    }
    memset(m, 0, NPIX);
    for (int y = y0; y <= y1; y++) {
        for (int x = x0; x <= x1; x++) {
            m[y * STRAIN_W + x] = s[(y - y0 + 1) * bw + (x - x0 + 1)];
            px += m[y * STRAIN_W + x];
        }
    }
    return px;
}

/* ---------------------------------------------------------------- longest path */
/* Dijkstra over the skeleton nodes: pop order = smallest (distance, node index) (heapq with (d, v) tuples), relax
 * on strict improvement. O(n^2) selection: n is a few hundred to ~2000 skeleton pixels. */
static void dijkstra(strain_ws_t *w, int n, int src, double sx, double sy, double diag)
{
    for (int i = 0; i < n; i++) {
        w->dist[i] = INFINITY;
        w->pred[i] = -1;
        w->done[i] = 0;
    }
    w->dist[src] = 0.0;
    for (;;) {
        int u = -1;
        for (int i = 0; i < n; i++) {
            if (!w->done[i] && w->dist[i] != INFINITY && (u < 0 || w->dist[i] < w->dist[u])) {
                u = i;                    /* ties: first (smallest) index */
            }
        }
        if (u < 0) {
            break;
        }
        w->done[u] = 1;
        const int p = w->node[u], y = p / STRAIN_W, x = p % STRAIN_W;
        for (int dy = -1; dy <= 1; dy++) {
            for (int dx = -1; dx <= 1; dx++) {
                const int yy = y + dy, xx = x + dx;
                if ((dy == 0 && dx == 0) || yy < 0 || yy >= STRAIN_W || xx < 0 || xx >= STRAIN_W) {
                    continue;
                }
                const int v = w->nidx[yy * STRAIN_W + xx];
                if (v < 0 || w->done[v]) {
                    continue;
                }
                const double nd = w->dist[u] + ((dy != 0 && dx != 0) ? diag : (dy != 0 ? sy : sx));
                if (nd < w->dist[v]) {
                    w->dist[v] = nd;
                    w->pred[v] = u;
                }
            }
        }
    }
}

static int argmax_first(const double *d, int n)
{
    double best = -INFINITY;
    int    bi = 0;
    for (int i = 0; i < n; i++) {
        const double x = d[i] == INFINITY ? -1.0 : d[i];
        if (x > best) {
            best = x;
            bi = i;
        }
    }
    return bi;
}

/* ---------------------------------------------------------------- resampling helpers (numpy order) */
static void movavg(const double *v, int n, int pad, double *out)
{
    const int h = STRAIN_SMOOTH / 2, m = pad ? n : n - STRAIN_SMOOTH + 1;
    for (int i = 0; i < m; i++) {
        double s = 0.0;
        for (int j = 0; j < STRAIN_SMOOTH; j++) {
            int k = pad ? i + j - h : i + j;
            k = k < 0 ? 0 : (k >= n ? n - 1 : k);
            s = s + v[k] * 0.2;
        }
        out[i] = s;
    }
}

static double floordiv(double a, double b)        /* np.floor_divide (npy_divmod) */
{
    const double mod = fmod(a, b);
    double div = (a - mod) / b;
    if (mod != 0.0 && ((b < 0) != (mod < 0))) {
        div -= 1.0;
    }
    if (div != 0.0) {
        double fl = floor(div);
        if (div - fl > 0.5) {
            fl += 1.0;
        }
        return fl;
    }
    return copysign(0.0, a / b);
}

static double interp(double x, const double *xp, const double *fp, int n)   /* np.interp */
{
    if (x > xp[n - 1]) {
        return fp[n - 1];
    }
    if (x < xp[0]) {
        return fp[0];
    }
    int lo = 0, hi = n;
    while (lo < hi) {
        const int mid = (lo + hi) >> 1;
        if (x >= xp[mid]) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    const int j = lo - 1;
    if (j == n - 1 || xp[j] == x) {
        return fp[j];
    }
    const double slope = (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j]);
    return slope * (x - xp[j]) + fp[j];
}

double strain_np_sum(const double *a, int n)
{
    if (n < 8) {
        double r = -0.0;
        for (int i = 0; i < n; i++) {
            r = r + a[i];
        }
        return r;
    }
    if (n <= 128) {
        double r[8], res;
        int    i;
        for (i = 0; i < 8; i++) {
            r[i] = a[i];
        }
        for (i = 8; i < n - (n % 8); i += 8) {
            for (int j = 0; j < 8; j++) {
                r[j] = r[j] + a[i + j];
            }
        }
        res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        for (; i < n; i++) {
            res = res + a[i];
        }
        return res;
    }
    int n2 = n / 2;
    n2 -= n2 % 8;
    return strain_np_sum(a, n2) + strain_np_sum(a + n2, n - n2);
}

static double seglen(double y0, double x0, double y1, double x1, double sx, double sy)
{
    const double dy = (y1 - y0) * sy, dx = (x1 - x0) * sx;
    return sqrt(dy * dy + dx * dx);
}

/* ---------------------------------------------------------------- ED line */
int strain_ed_line(strain_ws_t *w, const uint8_t *lv128, const uint8_t *epi128, int do_clean, double sx, double sy)
{
    int best, n = 0;

    if (sx <= 0.0 || sy <= 0.0) {
        return STRAIN_E_ARG;
    }
    memcpy(w->lv, lv128, STRAIN_M * STRAIN_M);
    memcpy(w->epi, epi128, STRAIN_M * STRAIN_M);
    w->ncomp_lv = w->ncomp_epi = -1;
    if (do_clean) {
        w->ncomp_lv = strain_clean(w->lv, STRAIN_M, STRAIN_M, w->lab, w->stack);
        w->ncomp_epi = strain_clean(w->epi, STRAIN_M, STRAIN_M, w->lab, w->stack);
    }
    for (int y = 0; y < STRAIN_W; y++) {
        for (int x = 0; x < STRAIN_W; x++) {
            const int k = (y >> 2) * STRAIN_M + (x >> 2);
            w->myo[y * STRAIN_W + x] = (uint8_t)(w->epi[k] && !w->lv[k]);
        }
    }
    w->ncomp_myo = label(w->myo, STRAIN_W, STRAIN_W, 1, w->lab, w->stack, &best);
    if (w->ncomp_myo == 0) {
        return STRAIN_E_NOMYO;
    }
    for (int i = 0; i < NPIX; i++) {
        w->myo[i] = (uint8_t)(w->lab[i] == best);
    }
    /* skeleton into sk (a copy: myo is kept for the result) */
    uint8_t *skel = w->done;                  /* NPIX bytes, free until dijkstra */
    memcpy(skel, w->myo, NPIX);
    w->skel_px = skeletonize(skel, w->sk0, w->sk1);
    for (int i = 0; i < NPIX; i++) {
        w->nidx[i] = -1;
        if (skel[i]) {
            w->node[n] = i;
            w->nidx[i] = n++;
        }
    }
    if (n < 3) {
        return STRAIN_E_SHORT;
    }
    const double diag = sqrt(sx * sx + sy * sy);
    dijkstra(w, n, 0, sx, sy, diag);
    const int a = argmax_first(w->dist, n);
    dijkstra(w, n, a, sx, sy, diag);
    const int b = argmax_first(w->dist, n);
    int len = 0;
    for (int v = b;; v = w->pred[v]) {
        w->path[2 * len] = w->node[v] / STRAIN_W;
        w->path[2 * len + 1] = w->node[v] % STRAIN_W;
        len++;
        if (v == a) {
            break;
        }
    }
    w->path_len = len;
    if (3 * len > NPIX) {
        return STRAIN_E_SHORT;
    }
    /* 5-point moving average (edge padding) when the path is longer than 5, arc length, linspace, interp */
    double *ys = w->dist, *xs = w->dist + len, *ys2 = w->wtmp, *xs2 = w->wtmp + len, *s = w->dist + 2 * len;
    for (int i = 0; i < len; i++) {
        ys[i] = (double)w->path[2 * i];
        xs[i] = (double)w->path[2 * i + 1];
    }
    if (len > STRAIN_SMOOTH) {
        movavg(ys, len, 1, ys2);
        movavg(xs, len, 1, xs2);
    } else {
        memcpy(ys2, ys, sizeof(double) * len);
        memcpy(xs2, xs, sizeof(double) * len);
    }
    s[0] = 0.0;
    for (int i = 0; i < len - 1; i++) {
        s[i + 1] = s[i] + seglen(ys2[i], xs2[i], ys2[i + 1], xs2[i + 1], sx, sy);
    }
    const double fl = floordiv(s[len - 1], STRAIN_STEP_MM);
    int np_ = (int)fl + 1;
    np_ = np_ < 2 ? 2 : np_;
    if (np_ < STRAIN_SMOOTH || np_ > STRAIN_NMAX) {
        return STRAIN_E_SHORT;
    }
    const double step = s[len - 1] / (double)(np_ - 1);
    for (int i = 0; i < np_; i++) {
        const double t = (i == np_ - 1) ? s[len - 1] : (double)i * step + 0.0;
        w->pts[i][0] = interp(t, s, ys2, len);
        w->pts[i][1] = interp(t, s, xs2, len);
        w->q8[i][0] = (int32_t)nearbyint(w->pts[i][0] * 256.0);
        w->q8[i][1] = (int32_t)nearbyint(w->pts[i][1] * 256.0);
    }
    w->n = np_;
    return STRAIN_OK;
}

double strain_line_length(const int32_t (*q8)[2], int n, double sx, double sy, double *tmp)
{
    double *ys = tmp, *xs = tmp + n, *ya = tmp + 2 * n, *xa = tmp + 3 * n, *sg = tmp + 4 * n;
    const int m = n - STRAIN_SMOOTH + 1;
    for (int i = 0; i < n; i++) {
        ys[i] = q8[i][0] / 256.0;
        xs[i] = q8[i][1] / 256.0;
    }
    movavg(ys, n, 0, ya);
    movavg(xs, n, 0, xa);
    for (int i = 0; i < m - 1; i++) {
        sg[i] = seglen(ya[i], xa[i], ya[i + 1], xa[i + 1], sx, sy);
    }
    return strain_np_sum(sg, m - 1);
}

int strain_track_step(const uint8_t *A, const uint8_t *B, int n, int32_t (*q8)[2], int opts)
{
    int32_t  ax[STRAIN_NMAX], ay[STRAIN_NMAX], xq[STRAIN_NMAX], yq[STRAIN_NMAX], fl[STRAIN_NMAX];
    uint32_t sad[STRAIN_NMAX];

    if (n < 1 || n > STRAIN_NMAX) {
        return -1;
    }
    for (int i = 0; i < n; i++) {
        ay[i] = yq[i] = q8[i][0];
        ax[i] = xq[i] = q8[i][1];
    }
    const int rc = bm_track_line(A, B, STRAIN_W, STRAIN_W, STRAIN_W, n, STRAIN_R, STRAIN_K, opts | BM_OPT_VFIT, ax, ay,
                                 xq, yq, sad, fl);
    if (rc < 0) {
        return rc;
    }
    for (int i = 0; i < n; i++) {
        q8[i][0] = yq[i];
        q8[i][1] = xq[i];
    }
    return rc;
}
