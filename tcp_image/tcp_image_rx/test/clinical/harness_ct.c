#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define N 512
typedef struct { char *mem; size_t cap, len; } jbuf_t;
static void jb_add(jbuf_t *b, const char *fmt, ...) { va_list ap; va_start(ap, fmt);
  b->len += (size_t)vsnprintf(b->mem + b->len, b->cap - b->len, fmt, ap); va_end(ap); }
/* ---- spleen measurements (ct_volume.slice_calipers / spleen_measure, same arithmetic order) ---- */
typedef struct {
    int c, r;
} pt_t;

static int cmp_pt(const void *a, const void *b)
{
    const pt_t *p = (const pt_t *)a, *q = (const pt_t *)b;

    if (p->c != q->c) {
        return p->c < q->c ? -1 : 1;
    }
    return (p->r > q->r) - (p->r < q->r);
}

static long cross_pt(pt_t o, pt_t a, pt_t b)
{
    return (long)(a.c - o.c) * (b.r - o.r) - (long)(a.r - o.r) * (b.c - o.c);
}

/* monotone chain, collinear points dropped, counter-clockwise; returns hull size (h has room for 2n + 1) */
static int hull_pts(const pt_t *p, int n, pt_t *h)
{
    int k = 0, i, lo;

    if (n <= 2) {
        memcpy(h, p, (size_t)n * sizeof(pt_t));
        return n;
    }
    for (i = 0; i < n; i++) {
        while (k >= 2 && cross_pt(h[k - 2], h[k - 1], p[i]) <= 0) {
            k--;
        }
        h[k++] = p[i];
    }
    lo = k;                                   /* lower chain incl. its last point */
    for (i = n - 1; i >= 0; i--) {
        while (k >= lo + 2 && cross_pt(h[k - 2], h[k - 1], p[i]) <= 0) {
            k--;
        }
        h[k++] = p[i];
    }
    /* python: lo[:-1] + up[:-1]; here h = lo + up, up starts with lo's last point */
    memmove(h + lo - 1, h + lo, (size_t)(k - lo) * sizeof(pt_t));
    return k - 2;
}

/* returns 0 for an empty slice; else W, T (mm) and the Feret end points */
static int slice_calipers(const uint8_t *m, double dx, double dy, pt_t *pts, pt_t *h, double *w, double *t, int ends[4])
{
    int    n = 0, r, nh, i, j, bi = 0, bj = 0;
    double best = -1.0;

    for (r = 0; r < N; r++) {
        const uint8_t *row = m + (size_t)r * N;
        int            c0 = -1, c1 = -1, c;

        for (c = 0; c < N; c++) {
            if (row[c]) {
                c0 = c0 < 0 ? c : c0;
                c1 = c;
            }
        }
        if (c0 >= 0) {
            pts[n].c = c0;
            pts[n++].r = r;
            if (c1 != c0) {
                pts[n].c = c1;
                pts[n++].r = r;
            }
        }
    }
    if (n == 0) {
        return 0;
    }
    qsort(pts, (size_t)n, sizeof(pt_t), cmp_pt);
    nh = hull_pts(pts, n, h);
    for (i = 0; i < nh; i++) {
        for (j = i + 1; j < nh; j++) {
            double ex = (double)(h[j].c - h[i].c) * dx, ey = (double)(h[j].r - h[i].r) * dy, d2 = ex * ex + ey * ey;

            if (d2 > best) {
                best = d2;
                bi = i;
                bj = j;
            }
        }
    }
    *w = best > 0.0 ? sqrt(best) : 0.0;
    if (*w > 0.0) {
        double nx = -((double)(h[bj].r - h[bi].r) * dy) / *w, ny = ((double)(h[bj].c - h[bi].c) * dx) / *w;
        double pmin = (double)h[0].c * dx * nx + (double)h[0].r * dy * ny, pmax = pmin;

        for (i = 0; i < nh; i++) {
            double q = (double)h[i].c * dx * nx + (double)h[i].r * dy * ny;

            pmin = q < pmin ? q : pmin;
            pmax = q > pmax ? q : pmax;
        }
        *t = pmax - pmin;
    } else {
        *t = 0.0;
    }
    ends[0] = h[bi].c;
    ends[1] = h[bi].r;
    ends[2] = h[bj].c;
    ends[3] = h[bj].r;
    return 1;
}

/* appends ,"spleen":{...} or ,"spleen":null */
static void spleen_json(jbuf_t *b, const uint8_t *fin, int nz, int zlo, int zhi, const double spc[3])
{
    pt_t  *pts = (pt_t *)malloc(sizeof(pt_t) * 2u * N), *h = (pt_t *)malloc(sizeof(pt_t) * (4u * N + 1u));
    double bw = -1.0, bt = 0.0, len;
    int    z, bz = -1, be[4] = {0, 0, 0, 0};

    if (zlo < 0 || pts == NULL || h == NULL) {
        jb_add(b, ",\"spleen\":null");
        free(pts);
        free(h);
        return;
    }
    (void)nz;
    len = (double)(zhi - zlo + 1) * spc[2];
    for (z = zlo; z <= zhi; z++) {
        double w, t;
        int    e[4];

        if (slice_calipers(fin + (size_t)z * N * N, spc[0], spc[1], pts, h, &w, &t, e) && (bz < 0 || w > bw)) {
            bz = z;
            bw = w;
            bt = t;
            memcpy(be, e, sizeof(be));
        }
    }
    jb_add(b, ",\"spleen\":{\"length_mm\":%.17g,\"width_mm\":%.17g,\"thickness_mm\":%.17g,\"splenic_index\":%.17g,"
              "\"width_slice\":%d,\"width_ends\":[%d,%d,%d,%d]}",
           len, bw, bt, len * bw * bt / 1000.0, bz, be[0], be[1], be[2], be[3]);
    free(pts);
    free(h);
}


int main(int argc, char **argv) {
  FILE *f = fopen(argv[1], "rb"); int nz, z, zlo = -1, zhi = -1; double spc[3]; static char mem[4096]; jbuf_t b;
  uint8_t *m;
  while (fread(&nz, 4, 1, f) == 1) {
    fread(spc, 8, 3, f); m = malloc((size_t)nz * N * N); fread(m, 1, (size_t)nz * N * N, f);
    zlo = zhi = -1;
    for (z = 0; z < nz; z++) if (memchr(m + (size_t)z * N * N, 1, N * N)) { zlo = zlo < 0 ? z : zlo; zhi = z; }
    b.mem = mem; b.cap = sizeof(mem); b.len = 0;
    spleen_json(&b, m, nz, zlo, zhi, spc);
    printf("{%s}\n", mem + 1); free(m);
  }
  return 0;
}
