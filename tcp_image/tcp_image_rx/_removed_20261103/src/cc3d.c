/* cc3d.c -- largest 26-connected component of a 3-D mask, run based union-find. See cc3d.h. */
#include "cc3d.h"

#include <stdlib.h>
#include <string.h>

typedef struct {
    int32_t x0, x1;         /* [x0, x1) */
} run_t;

static uint32_t find(uint32_t *p, uint32_t a)
{
    uint32_t r = a;

    while (p[r] != r) {
        r = p[r];
    }
    while (p[a] != r) {      /* path compression */
        uint32_t n = p[a];

        p[a] = r;
        a = n;
    }
    return r;
}

static void unite(uint32_t *p, uint32_t a, uint32_t b)
{
    a = find(p, a);
    b = find(p, b);
    if (a < b) {
        p[b] = a;            /* the root is always the component's first run in raster order */
    } else if (b < a) {
        p[a] = b;
    }
}

/* union run r with every run of row [s, e) that touches it with 8-neighbourhood in x (|dx| <= 1) */
static void link_row(uint32_t *p, const run_t *runs, uint32_t r, uint32_t s, uint32_t e)
{
    uint32_t k;

    for (k = s; k < e; k++) {
        if (runs[k].x0 > runs[r].x1) {
            break;
        }
        if (runs[k].x1 >= runs[r].x0) {
            unite(p, r, k);
        }
    }
}

long cc3d_keep_largest(uint8_t *m, int nz, int h, int w, uint32_t *ncomp)
{
    const size_t rows = (size_t)nz * (size_t)h;
    uint32_t    *row0 = (uint32_t *)malloc((rows + 1u) * sizeof(uint32_t));
    run_t       *runs = NULL;
    uint32_t    *par = NULL, *size = NULL;
    size_t       nr = 0u, cap = 4096u, r;
    uint32_t     comps = 0u, best = 0u;
    long         kept = 0;
    int          z, y, x;

    if (ncomp != NULL) {
        *ncomp = 0u;
    }
    runs = (run_t *)malloc(cap * sizeof(run_t));
    if (row0 == NULL || runs == NULL) {
        free(row0);
        free(runs);
        return -1;
    }
    for (r = 0u; r < rows; r++) {                   /* runs in raster order */
        const uint8_t *row = m + r * (size_t)w;

        row0[r] = (uint32_t)nr;
        for (x = 0; x < w;) {
            int s;

            if (row[x] == 0u) {
                x++;
                continue;
            }
            s = x;
            while (x < w && row[x] != 0u) {
                x++;
            }
            if (nr == cap) {
                run_t *g = (run_t *)realloc(runs, 2u * cap * sizeof(run_t));

                if (g == NULL) {
                    free(row0);
                    free(runs);
                    return -1;
                }
                runs = g;
                cap *= 2u;
            }
            runs[nr].x0 = s;
            runs[nr].x1 = x;
            nr++;
        }
    }
    row0[rows] = (uint32_t)nr;
    par = (uint32_t *)malloc((nr + 1u) * sizeof(uint32_t));
    size = (uint32_t *)calloc(nr + 1u, sizeof(uint32_t));
    if (par == NULL || size == NULL) {
        free(row0);
        free(runs);
        free(par);
        free(size);
        return -1;
    }
    for (r = 0u; r < nr; r++) {
        par[r] = (uint32_t)r;
    }
    /* neighbours already visited in raster order: (z, y-1), (z-1, y-1 .. y+1) */
    for (z = 0; z < nz; z++) {
        for (y = 0; y < h; y++) {
            size_t   cur = (size_t)z * h + y;
            uint32_t k;

            for (k = row0[cur]; k < row0[cur + 1u]; k++) {
                int dy;

                if (y > 0) {
                    link_row(par, runs, k, row0[cur - 1u], row0[cur]);
                }
                if (z > 0) {
                    for (dy = -1; dy <= 1; dy++) {
                        size_t pr;

                        if (y + dy < 0 || y + dy >= h) {
                            continue;
                        }
                        pr = (size_t)(z - 1) * h + (size_t)(y + dy);
                        link_row(par, runs, k, row0[pr], row0[pr + 1u]);
                    }
                }
            }
        }
    }
    for (r = 0u; r < nr; r++) {
        uint32_t root = find(par, (uint32_t)r);

        if (root == r) {
            comps++;
        }
        size[root] += (uint32_t)(runs[r].x1 - runs[r].x0);
    }
    for (r = 0u; r < nr; r++) {                      /* largest; ties -> first in raster order */
        if (par[r] == r && size[r] > size[best]) {
            best = (uint32_t)r;
        }
    }
    if (comps > 1u) {
        for (r = 0u; r < rows; r++) {
            uint32_t k;

            for (k = row0[r]; k < row0[r + 1u]; k++) {
                if (par[k] != best) {               /* after the counting pass every run points at its root */
                    memset(m + r * (size_t)w + runs[k].x0, 0, (size_t)(runs[k].x1 - runs[k].x0));
                }
            }
        }
        kept = (long)size[best];
    } else {
        kept = (nr > 0u) ? (long)size[best] : 0;
    }
    if (ncomp != NULL) {
        *ncomp = comps;
    }
    free(row0);
    free(runs);
    free(par);
    free(size);
    return kept;
}
