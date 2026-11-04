/* vessel_morph.c -- see vessel_morph.h */
#include "vessel_morph.h"

#include <math.h>
#include <string.h>

#define VM_N        (VM_MAX_W * VM_MAX_H)
#define VM_SQRT2    1.4142135623730951

static uint8_t g_bin[VM_N];      /* cleaned vessel mask, then reused */
static uint8_t g_skel[VM_N];     /* skeleton (1 = on) */
static uint8_t g_cls[VM_N];      /* 1 endpoint, 2 branch */
static uint8_t g_flag[VM_N];     /* scratch: visited / box occupancy */
static int32_t g_lab[VM_N];      /* segment / cluster labels */
static int32_t g_list[VM_N];     /* BFS queue and collected pixels */

/* neighbour order N, NE, E, SE, S, SW, W, NW (P2..P9 in Zhang-Suen terms) */
static const int k_dx[8] = { 0, 1, 1, 1, 0, -1, -1, -1 };
static const int k_dy[8] = { -1, -1, 0, 1, 1, 1, 0, -1 };

static inline int at(const uint8_t *b, int w, int h, int x, int y)
{
    return (x >= 0 && y >= 0 && x < w && y < h && b[(y * w) + x] != 0u) ? 1 : 0;
}

static inline uint32_t rnd(double v)
{
    return (v <= 0.0) ? 0u : (uint32_t)(v + 0.5);
}

static inline void put_le32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
    p[2] = (uint8_t)((v >> 16) & 0xFFu);
    p[3] = (uint8_t)((v >> 24) & 0xFFu);
}

void vm_fov_from_rgb(const uint8_t *rgb, int sw, int x0, int y0, int rw, int rh,
                     int w, int h, uint8_t *fov)
{
    int gy, gx;

    for (gy = 0; gy < h; gy++) {
        int sy = y0 + ((gy * rh) / h);
        for (gx = 0; gx < w; gx++) {
            int            sx = x0 + ((gx * rw) / w);
            const uint8_t *p  = rgb + (((size_t)sy * (size_t)sw) + (size_t)sx) * 3u;
            uint32_t       lum = (77u * p[0] + 150u * p[1] + 29u * p[2]) >> 8;

            fov[(gy * w) + gx] = (lum > VM_FOV_THR) ? 1u : 0u;
        }
    }
}

/* Collect the 8-connected component of `in` that contains `seed` into g_list,
 * marking g_flag.  Returns the pixel count. */
static int grab_component(const uint8_t *in, int w, int h, int seed)
{
    int head = 0, tail = 0;

    g_flag[seed] = 1u;
    g_list[tail++] = seed;
    while (head < tail) {
        int c = g_list[head++];
        int cx = c % w, cy = c / w, k;

        for (k = 0; k < 8; k++) {
            int nx = cx + k_dx[k], ny = cy + k_dy[k], q;

            if (nx < 0 || ny < 0 || nx >= w || ny >= h) {
                continue;
            }
            q = (ny * w) + nx;
            if (in[q] != 0u && g_flag[q] == 0u) {
                g_flag[q] = 1u;
                g_list[tail++] = q;
            }
        }
    }
    return tail;
}

/* One Zhang-Suen sub-iteration; returns the number of pixels removed. */
static int zs_pass(uint8_t *b, int w, int h, int step)
{
    int n = 0, i, x, y;

    for (y = 0; y < h; y++) {
        for (x = 0; x < w; x++) {
            int p[8], k, bsum = 0, a = 0;

            if (b[(y * w) + x] == 0u) {
                continue;
            }
            for (k = 0; k < 8; k++) {
                p[k] = at(b, w, h, x + k_dx[k], y + k_dy[k]);
                bsum += p[k];
            }
            if (bsum < 2 || bsum > 6) {
                continue;
            }
            for (k = 0; k < 8; k++) {
                a += (p[k] == 0 && p[(k + 1) & 7] == 1) ? 1 : 0;
            }
            if (a != 1) {
                continue;
            }
            /* p[0]=P2(N) p[2]=P4(E) p[4]=P6(S) p[6]=P8(W) */
            if (step == 0) {
                if ((p[0] && p[2] && p[4]) || (p[2] && p[4] && p[6])) {
                    continue;
                }
            } else {
                if ((p[0] && p[2] && p[6]) || (p[0] && p[4] && p[6])) {
                    continue;
                }
            }
            g_list[n++] = (y * w) + x;
        }
    }
    for (i = 0; i < n; i++) {
        b[g_list[i]] = 0u;
    }
    return n;
}

/* Links among pixels where lab == want (lab == NULL: skel != 0). */
static void count_links(int w, int h, const int32_t *pix, int npix, const int32_t *lab,
                        int32_t want, uint32_t *n_orth, uint32_t *n_diag)
{
    int i;

    *n_orth = 0u;
    *n_diag = 0u;
    for (i = 0; i < npix; i++) {
        int c = pix[i], x = c % w, y = c / w;
#define ON(xx, yy) ((xx) >= 0 && (yy) >= 0 && (xx) < w && (yy) < h && \
                    (lab != NULL ? lab[((yy) * w) + (xx)] == want : g_skel[((yy) * w) + (xx)] != 0u))
        int e = ON(x + 1, y), s = ON(x, y + 1), wst = ON(x - 1, y);

        *n_orth += (uint32_t)(e + s);
        if (ON(x + 1, y + 1) && !e && !s) {
            (*n_diag)++;
        }
        if (ON(x - 1, y + 1) && !wst && !s) {
            (*n_diag)++;
        }
#undef ON
    }
}

/* res512 = the grid's width in full-frame terms (512 at full resolution and for
 * ROI crops, 128 for the 128 setting).  It scales the noise / segment limits. */
int vm_analyze(const uint8_t *mask, const uint8_t *fov, int w, int h, int res512,
               uint8_t *skel, vm_result_t *r)
{
    const int n = w * h;
    int       min_blob, min_seg, i, x, y, k, npix = 0;
    uint32_t  n_o, n_d;
    double    length, sum_arc = 0.0, sum_chord = 0.0;
    uint32_t  ves_q[4] = { 0u, 0u, 0u, 0u }, fov_q[4] = { 0u, 0u, 0u, 0u };
    int32_t   next_label = 0;

    memset(r, 0, sizeof(*r));
    if (w < 1 || h < 1 || w > VM_MAX_W || h > VM_MAX_H) {
        return -1;
    }
    r->w = (uint32_t)w;
    r->h = (uint32_t)h;
    min_blob = (VM_MIN_BLOB_512 * res512 * res512) / (VM_MAX_W * VM_MAX_W);
    if (min_blob < 1) {
        min_blob = 1;
    }
    min_seg = (VM_MIN_SEG_512 * res512) / VM_MAX_W;
    if (min_seg < 2) {
        min_seg = 2;
    }

    /* 1) binary mask without small blobs */
    for (i = 0; i < n; i++) {
        g_bin[i] = (mask[i] != 0u) ? 1u : 0u;
        g_flag[i] = 0u;
    }
    for (i = 0; i < n; i++) {
        if (g_bin[i] != 0u && g_flag[i] == 0u) {
            int cnt = grab_component(g_bin, w, h, i), j;

            if (cnt < min_blob) {
                for (j = 0; j < cnt; j++) {
                    g_bin[g_list[j]] = 0u;
                }
            }
        }
    }
    for (i = 0; i < n; i++) {
        r->vessel_px += g_bin[i];
    }

    /* 2) Zhang-Suen thinning */
    memcpy(g_skel, g_bin, (size_t)n);
    for (;;) {                         /* two sub-iterations, in this order */
        int removed = zs_pass(g_skel, w, h, 0);

        removed += zs_pass(g_skel, w, h, 1);
        if (removed == 0) {
            break;
        }
    }

    /* 3) crossing number */
    for (i = 0; i < n; i++) {
        g_cls[i] = 0u;
    }
    for (y = 0; y < h; y++) {
        for (x = 0; x < w; x++) {
            int c = (y * w) + x, cn = 0, p[8];

            if (g_skel[c] == 0u) {
                continue;
            }
            g_list[npix++] = c;
            for (k = 0; k < 8; k++) {
                p[k] = at(g_skel, w, h, x + k_dx[k], y + k_dy[k]);
            }
            for (k = 0; k < 8; k++) {
                cn += (p[k] != p[(k + 1) & 7]) ? 1 : 0;
            }
            cn /= 2;
            if (cn == 1) {
                g_cls[c] = 1u;
                r->endpoints++;
            } else if (cn >= 3) {
                g_cls[c] = 2u;
            }
        }
    }

    /* 4) skeleton length (g_list holds the skeleton pixels in raster order) */
    count_links(w, h, g_list, npix, NULL, 0, &n_o, &n_d);
    length = (double)n_o + ((double)n_d * VM_SQRT2);
    r->length_x10 = rnd(length * 10.0);
    if (length > 0.0) {
        r->caliber_x100 = rnd(((double)r->vessel_px / length) * 100.0);
    }

    /* branch points: 8-connected clusters of branch pixels */
    for (i = 0; i < n; i++) {
        g_flag[i] = 0u;
        g_lab[i] = 0;                  /* reused below as "branch pixel" mask */
    }
    {
        static uint8_t is_branch[VM_N];

        for (i = 0; i < n; i++) {
            is_branch[i] = (g_cls[i] == 2u) ? 1u : 0u;
        }
        for (i = 0; i < n; i++) {
            if (is_branch[i] != 0u && g_flag[i] == 0u) {
                (void)grab_component(is_branch, w, h, i);
                r->branches++;
            }
        }
    }

    /* 5) segments = skeleton minus branch pixels and their 8 neighbours (with
     *    only the branch pixel removed, the arms still touch diagonally) */
    {
        static uint8_t seg[VM_N];

        for (y = 0; y < h; y++) {
            for (x = 0; x < w; x++) {
                int c = (y * w) + x, near = (g_cls[c] == 2u) ? 1 : 0;

                for (k = 0; k < 8 && !near; k++) {
                    int nx = x + k_dx[k], ny = y + k_dy[k];

                    near = (nx >= 0 && ny >= 0 && nx < w && ny < h &&
                            g_cls[(ny * w) + nx] == 2u) ? 1 : 0;
                }
                seg[c] = (g_skel[c] != 0u && !near) ? 1u : 0u;
                g_flag[c] = 0u;
            }
        }
        for (i = 0; i < n; i++) {
            int cnt, j, ends = 0, ex[2] = { 0, 0 }, ey[2] = { 0, 0 };
            double arc;

            if (seg[i] == 0u || g_flag[i] != 0u) {
                continue;
            }
            cnt = grab_component(seg, w, h, i);
            next_label++;
            r->segments++;
            for (j = 0; j < cnt; j++) {
                g_lab[g_list[j]] = next_label;
            }
            for (j = 0; j < cnt; j++) {
                int c = g_list[j], cx = c % w, cy = c / w, nb = 0;

                for (k = 0; k < 8; k++) {
                    int nx = cx + k_dx[k], ny = cy + k_dy[k];

                    if (nx >= 0 && ny >= 0 && nx < w && ny < h &&
                        g_lab[(ny * w) + nx] == next_label) {
                        nb++;
                    }
                }
                if (nb == 1) {
                    if (ends < 2) {
                        ex[ends] = cx;
                        ey[ends] = cy;
                    }
                    ends++;
                }
            }
            count_links(w, h, g_list, cnt, g_lab, next_label, &n_o, &n_d);
            arc = (double)n_o + ((double)n_d * VM_SQRT2);
            if (ends == 2 && arc >= (double)min_seg) {
                double dx = (double)(ex[0] - ex[1]), dy = (double)(ey[0] - ey[1]);
                double chord = sqrt((dx * dx) + (dy * dy));

                if (chord > 0.0) {
                    sum_arc   += arc;
                    sum_chord += chord;
                }
            }
        }
    }
    if (sum_chord > 0.0) {
        r->tortuosity_x1000 = rnd((sum_arc / sum_chord) * 1000.0);
    }

    /* 6) fractal dimension by box counting */
    {
        double sx = 0.0, sy = 0.0, sxx = 0.0, sxy = 0.0;
        int    m = 0, ok = 1, s;
        int    side = (w < h) ? w : h;

        for (s = 2; s * 4 <= side; s *= 2) {
            int bw = (w + s - 1) / s, bh = (h + s - 1) / s, cnt = 0;

            memset(g_flag, 0, (size_t)(bw * bh));
            for (y = 0; y < h; y++) {
                for (x = 0; x < w; x++) {
                    if (g_bin[(y * w) + x] != 0u) {
                        int b = ((y / s) * bw) + (x / s);

                        if (g_flag[b] == 0u) {
                            g_flag[b] = 1u;
                            cnt++;
                        }
                    }
                }
            }
            if (cnt == 0) {
                ok = 0;
                break;
            }
            sx  += log((double)s);
            sy  += log((double)cnt);
            sxx += log((double)s) * log((double)s);
            sxy += log((double)s) * log((double)cnt);
            m++;
        }
        if (ok && m >= 3) {
            double den = ((double)m * sxx) - (sx * sx);

            if (den > 0.0) {
                r->fractal_x1000 = rnd((-(((double)m * sxy) - (sx * sy)) / den) * 1000.0);
            }
        }
    }

    /* 7) density inside the field of view */
    for (y = 0; y < h; y++) {
        for (x = 0; x < w; x++) {
            int c = (y * w) + x;
            int q = ((y >= h / 2) ? 2 : 0) + ((x >= w / 2) ? 1 : 0);

            if (fov == NULL || fov[c] != 0u) {
                fov_q[q]++;
                ves_q[q] += g_bin[c];
            }
        }
    }
    {
        uint32_t fv = 0u, vv = 0u;

        for (k = 0; k < 4; k++) {
            fv += fov_q[k];
            vv += ves_q[k];
            r->quad_x1000[k] = (fov_q[k] > 0u)
                               ? rnd(((double)ves_q[k] / (double)fov_q[k]) * 1000.0) : 0u;
        }
        r->fov_px = fv;
        r->density_x1000 = (fv > 0u) ? rnd(((double)vv / (double)fv) * 1000.0) : 0u;
    }

    if (skel != NULL) {
        for (i = 0; i < n; i++) {
            if (g_skel[i] != 0u) {
                skel[i] = (g_cls[i] == 2u) ? VM_BRANCH : (g_cls[i] == 1u) ? VM_END : VM_SKEL;
            } else {
                skel[i] = (g_bin[i] != 0u) ? VM_VESSEL : VM_BG;
            }
        }
    }
    return 0;
}

void vm_fill_block(uint8_t *blk, const vm_result_t *r)
{
    put_le32(blk +  0u, VM_BLOCK_MAGIC);
    put_le32(blk +  4u, VM_BLOCK_VERSION);
    put_le32(blk +  8u, (r->w & 0xFFFFu) | (r->h << 16));
    put_le32(blk + 12u, r->vessel_px);
    put_le32(blk + 16u, r->fov_px);
    put_le32(blk + 20u, r->length_x10);
    put_le32(blk + 24u, r->caliber_x100);
    put_le32(blk + 28u, r->branches);
    put_le32(blk + 32u, r->endpoints);
    put_le32(blk + 36u, r->segments);
    put_le32(blk + 40u, r->tortuosity_x1000);
    put_le32(blk + 44u, r->fractal_x1000);
    put_le32(blk + 48u, r->density_x1000);
    put_le32(blk + 52u, (r->quad_x1000[0] & 0xFFFFu) | (r->quad_x1000[1] << 16));
    put_le32(blk + 56u, (r->quad_x1000[2] & 0xFFFFu) | (r->quad_x1000[3] << 16));
    put_le32(blk + 60u, r->run_us);
}

static void dot(uint8_t *out, int cx, int cy, uint8_t r, uint8_t g, uint8_t b)
{
    int x, y;

    for (y = cy - 1; y <= cy + 1; y++) {
        for (x = cx - 1; x <= cx + 1; x++) {
            if (x >= 0 && y >= 0 && x < VM_MAX_W && y < VM_MAX_H) {
                uint8_t *d = out + (((size_t)y * VM_MAX_W) + (size_t)x) * 3u;
                d[0] = r;
                d[1] = g;
                d[2] = b;
            }
        }
    }
}

void vm_draw(const uint8_t *base, const uint8_t *skel, int w, int h,
             int x0, int y0, int rw, int rh, uint8_t *out)
{
    int X, Y, gx, gy, pass;

    memcpy(out, base, (size_t)VM_MAX_W * VM_MAX_H * 3u);
    for (Y = y0; Y < y0 + rh; Y++) {
        gy = ((Y - y0) * h) / rh;
        for (X = x0; X < x0 + rw; X++) {
            uint8_t  code = skel[(gy * w) + (((X - x0) * w) / rw)];
            uint8_t *d = out + (((size_t)Y * VM_MAX_W) + (size_t)X) * 3u;

            if (code == VM_VESSEL) {          /* vessel body: half-transparent red */
                d[0] = (uint8_t)((d[0] + 255u) / 2u);
                d[1] = (uint8_t)(d[1] / 2u);
                d[2] = (uint8_t)(d[2] / 2u);
            } else if (code != VM_BG) {       /* skeleton: white */
                d[0] = d[1] = d[2] = 255u;
            }
        }
    }
    for (pass = 0; pass < 2; pass++) {        /* endpoints first, branch points on top */
        uint8_t want = (pass == 0) ? VM_END : VM_BRANCH;

        for (gy = 0; gy < h; gy++) {
            for (gx = 0; gx < w; gx++) {
                if (skel[(gy * w) + gx] == want) {
                    int cx = x0 + (((2 * gx) + 1) * rw) / (2 * w);
                    int cy = y0 + (((2 * gy) + 1) * rh) / (2 * h);

                    if (want == VM_END) {
                        dot(out, cx, cy, 0u, 230u, 255u);
                    } else {
                        dot(out, cx, cy, 255u, 40u, 40u);
                    }
                }
            }
        }
    }
}
