/* surf3d.c -- 3-D surface reconstruction + rendering on the PS (see surf3d.h).
 *
 * Every step follows the numpy / scipy code it replaces (ct_volume.Surface): scipy's 1-D correlations run in
 * double on lines and store float32 between axes, np.gradient and the normals are float32, positions float64.
 * libm exp / sqrt / pow may differ from numpy's in the last bit, so the acceptance is by tolerance.
 */
#include "surf3d.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define GAUSS_SIGMA  1.2
#define GAUSS_RADIUS 5               /* int(4.0 * sigma + 0.5) */

static uint64_t now_us(void)
{
    struct timespec ts;

    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)(ts.tv_nsec / 1000);
}

/* scipy 'reflect' (d c b a | a b c d | d c b a) */
static int reflect(int i, int n)
{
    if (n == 1) {
        return 0;
    }
    while (i < 0 || i >= n) {
        i = (i < 0) ? -i - 1 : 2 * n - i - 1;
    }
    return i;
}

/* gaussian_filter1d along one axis of a float32 volume, in place (scipy correlate1d, symmetric branch) */
static int gauss_axis(float *v, const int dims[3], int axis, const double *fw /* centre at [0], [1..R] */)
{
    const int n = dims[axis];
    const size_t stride = axis == 0 ? (size_t)dims[1] * dims[2] : (axis == 1 ? (size_t)dims[2] : 1u);
    const int o1 = axis == 0 ? dims[1] : dims[0], o2 = axis == 2 ? dims[1] : dims[2];
    double *line = malloc(sizeof(double) * (size_t)(n + 2 * GAUSS_RADIUS));
    int     a, b, i, j;

    if (line == NULL) {
        return -1;
    }
    for (a = 0; a < o1; a++) {
        for (b = 0; b < o2; b++) {
            size_t base;
            double *il = line + GAUSS_RADIUS;

            if (axis == 0) {
                base = (size_t)a * dims[2] + (size_t)b;
            } else if (axis == 1) {
                base = (size_t)a * dims[1] * dims[2] + (size_t)b;
            } else {
                base = ((size_t)a * dims[1] + (size_t)b) * dims[2];
            }
            for (i = -GAUSS_RADIUS; i < n + GAUSS_RADIUS; i++) {
                il[i] = (double)v[base + (size_t)reflect(i, n) * stride];
            }
            for (i = 0; i < n; i++) {
                double t = il[i] * fw[0];

                for (j = GAUSS_RADIUS; j >= 1; j--) {        /* jj = -size1 .. -1 */
                    t += (il[i - j] + il[i + j]) * fw[j];
                }
                v[base + (size_t)i * stride] = (float)t;
            }
        }
    }
    free(line);
    return 0;
}

/* np.gradient along an axis at (z, r, c): float32 central differences, one-sided at the ends */
static float grad_at(const float *g, const int dims[3], int axis, int z, int r, int c)
{
    const int n = dims[axis], i = axis == 0 ? z : (axis == 1 ? r : c);
    const size_t stride = axis == 0 ? (size_t)dims[1] * dims[2] : (axis == 1 ? (size_t)dims[2] : 1u);
    const size_t p = ((size_t)z * dims[1] + (size_t)r) * dims[2] + (size_t)c;

    if (n < 2) {
        return 0.0f;
    }
    if (i == 0) {
        return g[p + stride] - g[p];
    }
    if (i == n - 1) {
        return g[p] - g[p - stride];
    }
    return (float)(g[p + stride] - g[p - stride]) / 2.0f;
}

void surf3d_free(surf3d_t *s)
{
    free(s->X);
    free(s->Y);
    free(s->Z);
    free(s->nx);
    free(s->ny);
    free(s->nz);
    s->X = s->Y = s->Z = NULL;
    s->nx = s->ny = s->nz = NULL;
    s->n = 0;
    s->empty = 1;
}

int surf3d_build(const uint8_t *mask, int nz, int nr, int nc, const double spacing[3], int step, surf3d_t *s)
{
    const uint64_t t0 = now_us();
    const int      R = (nr + step - 1) / step, C = (nc + step - 1) / step;
    int            zmin = nz, zmax = -1, rmin = R, rmax = -1, cmin = C, cmax = -1;
    int            z, r, c, z0, z1, r0, r1, c0, c1, dz, dr, dc, oz, dims[3], k;
    double         sx, sy, zf, zoom, fw[GAUSS_RADIUS + 1], wsum, mx, my, mz, ext2;
    float         *f = NULL;
    uint8_t       *in = NULL;
    size_t         nvox, i;
    long           n;

    memset(s, 0, sizeof(*s));
    s->empty = 1;
    for (z = 0; z < nz; z++) {
        for (r = 0; r < R; r++) {
            const uint8_t *row = mask + ((size_t)z * nr + (size_t)r * step) * nc;

            for (c = 0; c < C; c++) {
                if (row[(size_t)c * step]) {
                    zmin = z < zmin ? z : zmin;
                    zmax = z > zmax ? z : zmax;
                    rmin = r < rmin ? r : rmin;
                    rmax = r > rmax ? r : rmax;
                    cmin = c < cmin ? c : cmin;
                    cmax = c > cmax ? c : cmax;
                }
            }
        }
    }
    if (zmax < 0) {
        s->build_us = (uint32_t)(now_us() - t0);
        return 0;
    }
    z0 = zmin - 1 > 0 ? zmin - 1 : 0;
    z1 = zmax + 2 < nz ? zmax + 2 : nz;
    r0 = rmin - 3 > 0 ? rmin - 3 : 0;
    r1 = rmax + 4 < R ? rmax + 4 : R;
    c0 = cmin - 3 > 0 ? cmin - 3 : 0;
    c1 = cmax + 4 < C ? cmax + 4 : C;
    dz = z1 - z0;
    dr = r1 - r0;
    dc = c1 - c0;
    sx = spacing[0] * step;
    sy = spacing[1] * step;
    zf = spacing[2] / sx;
    if (zf < 1.0) {
        zf = 1.0;
    }
    oz = (int)nearbyint((double)dz * zf);                   /* int(round(ii * jj)), round half to even */
    if (oz < 1) {
        oz = 1;
    }
    dims[0] = oz;
    dims[1] = dr;
    dims[2] = dc;
    nvox = (size_t)oz * dr * dc;
    in = malloc((size_t)dz * dr * dc);
    f = malloc(sizeof(float) * nvox);
    if (in == NULL || f == NULL) {
        free(in);
        free(f);
        return -1;
    }
    for (z = 0; z < dz; z++) {
        for (r = 0; r < dr; r++) {
            for (c = 0; c < dc; c++) {
                in[((size_t)z * dr + r) * dc + c] =
                    mask[((size_t)(z0 + z) * nr + (size_t)(r0 + r) * step) * nc + (size_t)(c0 + c) * step] ? 1u : 0u;
            }
        }
    }
    /* ndimage.zoom(order=1) along z: input coordinate k * (dz - 1) / (oz - 1) */
    zoom = oz > 1 ? (double)(dz - 1) / (double)(oz - 1) : 1.0;
    for (k = 0; k < oz; k++) {
        const double cc = (double)k * zoom;
        int          i0 = (int)floor(cc);
        double       t = cc - (double)i0;
        const size_t plane = (size_t)dr * dc;

        if (i0 >= dz - 1) {
            i0 = dz - 1;
            t = 0.0;
        }
        for (i = 0; i < plane; i++) {
            const double a = in[(size_t)i0 * plane + i];
            const double b = (i0 + 1 < dz) ? in[(size_t)(i0 + 1) * plane + i] : 0.0;

            f[(size_t)k * plane + i] = (float)(a * (1.0 - t) + b * t);
        }
    }
    free(in);
    s->sz = spacing[2] * (double)dz / (double)oz;
    /* gaussian kernel: exp(-0.5 x^2 / sigma^2), normalised */
    wsum = 0.0;
    for (k = -GAUSS_RADIUS; k <= GAUSS_RADIUS; k++) {
        wsum += exp(-0.5 / (GAUSS_SIGMA * GAUSS_SIGMA) * (double)(k * k));
    }
    for (k = 0; k <= GAUSS_RADIUS; k++) {
        fw[k] = exp(-0.5 / (GAUSS_SIGMA * GAUSS_SIGMA) * (double)(k * k)) / wsum;
    }
    for (k = 0; k < 3; k++) {
        if (gauss_axis(f, dims, k, fw) != 0) {
            free(f);
            return -1;
        }
    }
    s->dims[0] = dims[0];
    s->dims[1] = dims[1];
    s->dims[2] = dims[2];
    /* surface = (g > 0.5) & ~erosion(g > 0.5), cross structure, border = 0 */
    n = 0;
    for (i = 0; i < nvox; i++) {
        s->inside += f[i] > 0.5f;
    }
#define IN(zz, rr, cc) ((zz) >= 0 && (zz) < oz && (rr) >= 0 && (rr) < dr && (cc) >= 0 && (cc) < dc && \
                        f[((size_t)(zz) * dr + (size_t)(rr)) * dc + (size_t)(cc)] > 0.5f)
    for (z = 0; z < oz; z++) {
        for (r = 0; r < dr; r++) {
            for (c = 0; c < dc; c++) {
                if (IN(z, r, c) && !(IN(z - 1, r, c) && IN(z + 1, r, c) && IN(z, r - 1, c) && IN(z, r + 1, c) &&
                                     IN(z, r, c - 1) && IN(z, r, c + 1))) {
                    n++;
                }
            }
        }
    }
    if (s->inside == 0 || n == 0) {
        free(f);
        s->build_us = (uint32_t)(now_us() - t0);
        return 0;
    }
    s->X = malloc(sizeof(double) * (size_t)n);
    s->Y = malloc(sizeof(double) * (size_t)n);
    s->Z = malloc(sizeof(double) * (size_t)n);
    s->nx = malloc(sizeof(float) * (size_t)n);
    s->ny = malloc(sizeof(float) * (size_t)n);
    s->nz = malloc(sizeof(float) * (size_t)n);
    if (!s->X || !s->Y || !s->Z || !s->nx || !s->ny || !s->nz) {
        free(f);
        surf3d_free(s);
        return -1;
    }
    {
        const float fsx = (float)sx, fsy = (float)sy, fsz = (float)s->sz;
        long        j = 0;

        for (z = 0; z < oz; z++) {
            for (r = 0; r < dr; r++) {
                for (c = 0; c < dc; c++) {
                    float gz, gr, gc, a, b, e, nn;

                    if (!(IN(z, r, c) && !(IN(z - 1, r, c) && IN(z + 1, r, c) && IN(z, r - 1, c) &&
                                           IN(z, r + 1, c) && IN(z, r, c - 1) && IN(z, r, c + 1)))) {
                        continue;
                    }
                    gz = grad_at(f, dims, 0, z, r, c);
                    gr = grad_at(f, dims, 1, z, r, c);
                    gc = grad_at(f, dims, 2, z, r, c);
                    a = gc / fsx;
                    b = -gr / fsy;
                    e = -gz / fsz;
                    nn = sqrtf(a * a + b * b + e * e) + 1e-9f;
                    s->nx[j] = a / nn;
                    s->ny[j] = b / nn;
                    s->nz[j] = e / nn;
                    s->X[j] = -((double)c * sx);
                    s->Y[j] = (double)r * sy;
                    s->Z[j] = (double)z * s->sz;
                    j++;
                }
            }
        }
    }
#undef IN
    free(f);
    s->n = (int)n;
    mx = my = mz = 0.0;
    for (k = 0; k < s->n; k++) {
        mx += s->X[k];
        my += s->Y[k];
        mz += s->Z[k];
    }
    mx /= (double)n;
    my /= (double)n;
    mz /= (double)n;
    ext2 = 0.0;
    for (k = 0; k < s->n; k++) {
        double q;

        s->X[k] -= mx;
        s->Y[k] -= my;
        s->Z[k] -= mz;
        q = s->X[k] * s->X[k] + s->Y[k] * s->Y[k] + s->Z[k] * s->Z[k];
        ext2 = q > ext2 ? q : ext2;
    }
    s->ext = sqrt(ext2) > 1.0 ? sqrt(ext2) : 1.0;
    s->vox = sx > sy ? sx : sy;
    s->vox = s->sz > s->vox ? s->sz : s->vox;
    s->empty = 0;
    s->build_us = (uint32_t)(now_us() - t0);
    return 0;
}

void surf3d_render(const surf3d_t *s, double angle_deg, int size, const uint8_t color[3], uint8_t *rgb)
{
    const size_t npx = (size_t)size * size;
    double       t, ct, st, L[3], ln, scale, near, far;
    int          rad, du, dv, k;
    double      *dep;
    int32_t     *who;
    double      *u, *d;
    int32_t     *pu, *pv;

    memset(rgb, 24, npx * 3u);
    if (s->empty || s->n == 0) {
        return;
    }
    t = angle_deg * (M_PI / 180.0);
    ct = cos(t);
    st = sin(t);
    L[0] = -0.35;
    L[1] = 0.45;
    L[2] = -1.0;
    ln = sqrt(L[0] * L[0] + L[1] * L[1] + L[2] * L[2]);
    L[0] /= ln;
    L[1] /= ln;
    L[2] /= ln;
    scale = (double)size * 0.46 / s->ext;
    rad = (int)ceil(s->vox * scale / 2.0);
    if (rad < 1) {
        rad = 1;
    }
    u = malloc(sizeof(double) * (size_t)s->n);
    d = malloc(sizeof(double) * (size_t)s->n);
    pu = malloc(sizeof(int32_t) * (size_t)s->n);
    pv = malloc(sizeof(int32_t) * (size_t)s->n);
    dep = malloc(sizeof(double) * npx);
    who = malloc(sizeof(int32_t) * npx);
    if (!u || !d || !pu || !pv || !dep || !who) {
        goto out;
    }
    near = INFINITY;
    far = -INFINITY;
    for (k = 0; k < s->n; k++) {
        u[k] = s->X[k] * ct - s->Y[k] * st;
        d[k] = s->X[k] * st + s->Y[k] * ct;
        pu[k] = (int32_t)nearbyint(u[k] * scale + (double)size / 2.0);
        pv[k] = (int32_t)nearbyint(-s->Z[k] * scale + (double)size / 2.0);
        near = d[k] < near ? d[k] : near;
        far = d[k] > far ? d[k] : far;
    }
    for (k = 0; k < (int)npx; k++) {
        who[k] = -1;
    }
    /* same order as the Python concatenation (du, dv, point) + stable sort by (pixel, depth): first wins ties */
    for (du = -rad; du <= rad; du++) {
        for (dv = -rad; dv <= rad; dv++) {
            for (k = 0; k < s->n; k++) {
                const int uu = pu[k] + du, vv = pv[k] + dv;
                size_t    p;

                if (uu < 0 || uu >= size || vv < 0 || vv >= size) {
                    continue;
                }
                p = (size_t)vv * size + (size_t)uu;
                if (who[p] < 0 || d[k] < dep[p]) {
                    who[p] = k;
                    dep[p] = d[k];
                }
            }
        }
    }
    {
        const double span = (far - near) > 1e-6 ? (far - near) : 1e-6;
        size_t       p;

        for (p = 0; p < npx; p++) {
            const int k2 = who[p];
            float     nu, nd, sp;
            double    diff, lum;
            int       ch;

            if (k2 < 0) {
                continue;
            }
            nu = s->nx[k2] * (float)ct - s->ny[k2] * (float)st;
            nd = s->nx[k2] * (float)st + s->ny[k2] * (float)ct;
            diff = (double)nu * L[0] + (double)s->nz[k2] * L[1] + (double)nd * L[2];
            diff = diff < 0.0 ? 0.0 : (diff > 1.0 ? 1.0 : diff);
            sp = -nd < 0.0f ? 0.0f : (-nd > 1.0f ? 1.0f : -nd);
            sp = powf(sp, 24.0f);
            lum = (0.16 + 0.84 * diff) * (1.0 - 0.3 * (d[k2] - near) / span);
            for (ch = 0; ch < 3; ch++) {
                double v = lum * (double)color[ch] + (double)sp * 90.0;

                v = v < 0.0 ? 0.0 : (v > 255.0 ? 255.0 : v);
                rgb[p * 3u + (size_t)ch] = (uint8_t)v;
            }
        }
    }
out:
    free(u);
    free(d);
    free(pu);
    free(pv);
    free(dep);
    free(who);
}
