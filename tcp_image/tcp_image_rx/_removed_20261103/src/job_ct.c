/* job_ct.c -- JOB_CT_SEG: a whole CT volume segmented on the board (⑤ spleen, two stages).
 *
 * The PC uploads the NIfTI file as it is (.nii or .nii.gz) and gets the volume, masks and timings back.
 * Every step is a port of tcp_image_ui/ct_volume.py (byte-identical masks, same numbers):
 *   PS  gunzip (zlib), header, radiological view (to_radiological), W400/L40 window (float32, round half
 *       to even), 4x4 box average, z3 encoding (slices z-o / z / z+o, o = round(5 mm / dz))
 *   PL  program ct_spleen on every slice (128)
 *   PL  bilinear x4 + threshold on pp_layer (PS exact integer form with flag bit3, or without pp_layer)
 *   PS  largest 3-D 26-connected component (cc3d.c) -> coarse mask, ROI window (256, +-2 o slices)
 *   PS  roi_prior encoding (R slice, G coarse prior, B neighbour mean, 2x2 box)
 *   PL  program ct_spleen_roi on every ROI slice, bilinear x2 on pp_layer; PS paste, largest component, volume
 *
 * A decode thread (gunzip -> window -> view -> box) runs ahead of the PL loop (slice z needs z + o). The pixel
 * post-processing runs on the PL (pp_layer) by default, so the whole pixel path is FPGA logic (user's choice
 * 10-13, for the competition rule that the FPGA carries the key image tasks); it costs PL time (spleen_10
 * coarse pass 2.20 vs 1.84 s, see 进度与交接 §9.54 / §9.57). Flag bit3 moves it to a PS worker thread that
 * overlaps the next PL run; the results are bit-identical either way.
 *
 * params (8 bytes): u8 coarse model, u8 fine model (255 = single stage), i16 window level, u16 window width,
 *                   u8 target mm for z3 (0 = 5), u8 0
 * result "spleen" (v5.15+, ct_volume.spleen_measure, bit-identical): craniocaudal length = slice extent x dz,
 *   width = largest in-plane Feret diameter (pixel centres, mm) over the axial slices, thickness = extent of that
 *   slice perpendicular to the width, splenic index = L x W x T / 1000 (cm^3); thresholds are applied on the PC
 * flags: bit0 logits (blobs "logits_coarse", "logits_fine"), bit1 "mask_coarse", bit2 "mask_final"
 *        (bit-packed z,y,x, bit k&7 of byte k>>3, zlib), bit3 up-sampling on a PS worker instead of pp_layer,
 *        bit4 the encoded inputs for checking ("small" = 4x4 box averages z x 128 x 128, "inputs_fine" = RGB
 *        payloads), bit5 up-sampling on pp_layer (the default since v5.14; kept for compatibility),
 *        bit6 (v5.16) 3-D surface views of the final mask rendered on the board (job_render.c / surf3d.c, the port of
 *        ct_volume.Surface): blob "render3d" + result "surface"; optional params (12 bytes) u8 views (0 = 12 at
 *        30 deg), u8 0, u16 size (0 = 320),
 *        bit7 (v5.16) blob "slices_u8": the windowed slices in the radiological view (nz x 512 x 512, zlib), what the
 *        board's own UI (1.5) displays under the PL overlay
 */
#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <zlib.h>

#include "cc3d.h"
#include "image_proto.h"
#include "job.h"
#include "job_render.h"
#include "pp_ai.h"

#define N      512
#define S      128
#define ROI    256
#define ROI_K  2
#define ROI_ZM 2

#define F_LOGITS      1u
#define F_MASK_COARSE 2u
#define F_MASK_FINAL  4u
#define F_PS_UPSAMPLE 8u
#define F_INPUTS      16u
#define F_PL_UPSAMPLE 32u
#define F_RENDER3D    64u
#define F_SLICES      128u

/* ---------------------------------------------------------------- NIfTI */

typedef struct {
    z_stream       zs;
    int            gz;
    const uint8_t *raw;
    size_t         raw_n, pos;
} reader_t;

static int rd_open(reader_t *r, const uint8_t *p, size_t n)
{
    memset(r, 0, sizeof(*r));
    r->raw = p;
    r->raw_n = n;
    r->gz = (n >= 2u && p[0] == 0x1Fu && p[1] == 0x8Bu);
    if (r->gz) {
        r->zs.next_in = (Bytef *)(uintptr_t)p;
        r->zs.avail_in = (uInt)n;
        if (inflateInit2(&r->zs, 16 + MAX_WBITS) != Z_OK) {
            return -1;
        }
    }
    return 0;
}

static void rd_close(reader_t *r)
{
    if (r->gz) {
        inflateEnd(&r->zs);
    }
}

/* exactly n bytes into dst (dst NULL = skip); -1 if the file ends early or is corrupt */
static int rd_read(reader_t *r, uint8_t *dst, size_t n)
{
    static uint8_t sink[65536];

    if (!r->gz) {
        if (r->pos + n > r->raw_n) {
            return -1;
        }
        if (dst != NULL) {
            memcpy(dst, r->raw + r->pos, n);
        }
        r->pos += n;
        return 0;
    }
    while (n > 0u) {
        size_t chunk = (dst != NULL) ? n : (n < sizeof(sink) ? n : sizeof(sink));
        int    rc;

        if (chunk > 0x40000000u) {
            chunk = 0x40000000u;
        }
        r->zs.next_out = (dst != NULL) ? dst : sink;
        r->zs.avail_out = (uInt)chunk;
        while (r->zs.avail_out > 0u) {
            rc = inflate(&r->zs, Z_NO_FLUSH);
            if (rc == Z_STREAM_END) {
                if (r->zs.avail_in == 0u) {
                    break;
                }
                if (inflateReset(&r->zs) != Z_OK) {         /* multi-member gzip (gzip.decompress reads them all) */
                    return -1;
                }
            } else if (rc != Z_OK) {
                return -1;
            }
        }
        if (r->zs.avail_out > 0u) {
            return -1;
        }
        if (dst != NULL) {
            dst += chunk;
        }
        n -= chunk;
    }
    return 0;
}

static int16_t le16s(const uint8_t *p) { return (int16_t)(p[0] | (p[1] << 8)); }
static int32_t le32s(const uint8_t *p) { return (int32_t)proto_get_le32(p); }
static float   le32f(const uint8_t *p) { uint32_t u = proto_get_le32(p); float f; memcpy(&f, &u, 4u); return f; }

typedef struct {
    int    ni, nj, nk, dtype, bpp;
    long   vox_offset;
    float  slope, inter;
    float  pixdim[8];
    double m[3][3];                    /* direction * spacing, columns = array axes i, j, k */
    int    has_m;
} nii_t;

static int bytes_per_voxel(int dtype)
{
    switch (dtype) {
    case 2: case 256: return 1;
    case 4: case 512: return 2;
    case 8: case 16: case 768: return 4;
    case 64: return 8;
    default: return 0;
    }
}

static const char *nii_header(const uint8_t *h, nii_t *n)
{
    int16_t dim[8], qcode, scode;
    int     i;

    if (le32s(h) != 348) {
        return "not a little-endian NIfTI-1 file";
    }
    for (i = 0; i < 8; i++) {
        dim[i] = le16s(h + 40 + 2 * i);
        n->pixdim[i] = le32f(h + 76 + 4 * i);
    }
    n->dtype = le16s(h + 70);
    n->bpp = bytes_per_voxel(n->dtype);
    if (dim[0] < 3 || n->bpp == 0) {
        return "only 3-D NIfTI with an integer / float32 / float64 data type";
    }
    n->ni = dim[1];
    n->nj = dim[2];
    n->nk = dim[3];
    if (n->ni <= 0 || n->nj <= 0 || n->nk <= 0 || n->nk > 2048 || n->ni > 2048 || n->nj > 2048) {
        return "bad dimensions";
    }
    n->vox_offset = (long)le32f(h + 108);
    n->slope = le32f(h + 112);
    n->inter = le32f(h + 116);
    qcode = le16s(h + 252);
    scode = le16s(h + 254);
    n->has_m = 0;
    if (scode > 0) {
        int r, c;

        for (r = 0; r < 3; r++) {
            for (c = 0; c < 3; c++) {
                n->m[r][c] = (double)le32f(h + 280 + 16 * r + 4 * c);
            }
        }
        n->has_m = 1;
    } else if (qcode > 0) {                                  /* ct_volume._quat_to_matrix */
        double b = le32f(h + 256), c = le32f(h + 260), d = le32f(h + 264), a, q = n->pixdim[0];
        double pix[3] = {n->pixdim[1], n->pixdim[2], n->pixdim[3]};
        double r[3][3];
        int    k, l;

        a = 1.0 - (b * b + c * c + d * d);
        a = sqrt(a > 0.0 ? a : 0.0);
        r[0][0] = a * a + b * b - c * c - d * d; r[0][1] = 2 * (b * c - a * d);       r[0][2] = 2 * (b * d + a * c);
        r[1][0] = 2 * (b * c + a * d);       r[1][1] = a * a + c * c - b * b - d * d; r[1][2] = 2 * (c * d - a * b);
        r[2][0] = 2 * (b * d - a * c);       r[2][1] = 2 * (c * d + a * b);       r[2][2] = a * a + d * d - c * c - b * b;
        pix[2] *= (q != 0.0) ? q : 1.0;
        for (k = 0; k < 3; k++) {
            for (l = 0; l < 3; l++) {
                n->m[k][l] = r[k][l] * pix[l];
            }
        }
        n->has_m = 1;
    }
    if (n->vox_offset < 348) {
        return "bad vox_offset";
    }
    return NULL;
}

static float voxel_f32(const uint8_t *p, int dtype)
{
    switch (dtype) {
    case 2:   return (float)p[0];
    case 256: return (float)(int8_t)p[0];
    case 4:   return (float)le16s(p);
    case 512: return (float)(uint16_t)(p[0] | (p[1] << 8));
    case 8:   return (float)le32s(p);
    case 768: return (float)proto_get_le32(p);
    case 16:  return le32f(p);
    case 64:  { double d; memcpy(&d, p, 8u); return (float)d; }
    default:  return 0.0f;
    }
}

/* ---------------------------------------------------------------- pixel helpers (ct_volume.py) */

/* box(u8, k): k x k mean, rounded, integer */
static void box(const uint8_t *src, int sw, int x0, int y0, int cw, int k, uint8_t *dst)
{
    int ow = cw / k, y, x, a, b;

    for (y = 0; y < ow; y++) {
        for (x = 0; x < ow; x++) {
            unsigned s = 0u;

            for (a = 0; a < k; a++) {
                const uint8_t *row = src + (size_t)(y0 + y * k + a) * sw + x0 + x * k;

                for (b = 0; b < k; b++) {
                    s += row[b];
                }
            }
            dst[y * ow + x] = (uint8_t)((s + (unsigned)(k * k / 2)) / (unsigned)(k * k));
        }
    }
}

typedef struct {
    int i0[N], i1[N], f[N];
    int rows[N * S];                    /* (n*k) x n, scaled by 2k */
} up_scratch_t;

/* upsample_bilinear(q, k) > 0.5, exact integer form: source position (2i + 1 - k) / 2k clamped at 0.
 * Writes (n*k) rows of n*k bytes 0/1 with row stride `stride`. */
static void upsample_ps(const int8_t *q, int n, int k, uint8_t *out, int stride, up_scratch_t *t)
{
    const int m = n * k, d = 2 * k;
    int       i, x, y;

    for (i = 0; i < m; i++) {
        int num = 2 * i + 1 - k;

        if (num < 0) {
            num = 0;
        }
        t->i0[i] = num / d;
        t->f[i] = num % d;
        t->i1[i] = (t->i0[i] + 1 < n) ? t->i0[i] + 1 : n - 1;
    }
    for (y = 0; y < m; y++) {
        const int8_t *a = q + t->i0[y] * n, *b = q + t->i1[y] * n;
        const int     wa = d - t->f[y], wb = t->f[y];
        int          *r = t->rows + y * n;

        for (x = 0; x < n; x++) {
            r[x] = a[x] * wa + b[x] * wb;
        }
    }
    for (y = 0; y < m; y++) {
        const int *r = t->rows + y * n;
        uint8_t   *o = out + (size_t)y * stride;

        for (x = 0; x < m; x++) {
            int v = r[t->i0[x]] * (d - t->f[x]) + r[t->i1[x]] * t->f[x];      /* value * (2k)^2 */

            o[x] = (2 * v > d * d) ? 1u : 0u;
        }
    }
}

/* pp_layer time of the running job (kernel / incl. copies), for the result's "pp_ms"; PL thread only */
static uint64_t g_pp_pl_us, g_pp_total_us;

/* the same on pp_layer (PL, on the calling thread; the PL is then busy for ~1-4 ms). 0 ok. */
static int upsample_pl(const int8_t *q, int n, int k, uint8_t *out, int stride)
{
    static uint8_t bits[N * (N / 8)];
    pp_result_t    r;
    size_t         rb = pp_mask_row_bytes(n * k);
    int            y, x, m = n * k;

    if (!pp_ready() || pp_run(q, n, n, PP_OP_MASK, 0, 0, n, n, k, 0, 1, bits, &r) != 0 || r.status != 0u) {
        return -1;
    }
    g_pp_pl_us += r.pl_us;
    g_pp_total_us += r.total_us;
    for (y = 0; y < m; y++) {
        const uint8_t *b = bits + (size_t)y * rb;
        uint8_t       *o = out + (size_t)y * stride;

        for (x = 0; x < m; x++) {
            o[x] = (uint8_t)((b[x >> 3] >> (x & 7)) & 1u);
        }
    }
    return 0;
}

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

static uint8_t *pack_bits(const uint8_t *m, size_t n)
{
    uint8_t *b = (uint8_t *)calloc(n / 8u + 1u, 1u);
    size_t   i;

    if (b != NULL) {
        for (i = 0; i < n; i++) {
            if (m[i]) {
                b[i >> 3] |= (uint8_t)(1u << (i & 7u));
            }
        }
    }
    return b;
}

static int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }

/* ---------------------------------------------------------------- PS worker: up-sampling off the PL thread */

#define UPQ 32

typedef struct {
    const int8_t *q;
    int           k;
    uint8_t      *out;
    int           stride;
} up_task_t;

typedef struct {
    pthread_t       th;
    pthread_mutex_t mu;
    pthread_cond_t  cv;
    up_task_t       q[UPQ];
    int             head, tail, busy, stop, started;
    double          work_ms;
    up_scratch_t    scratch;
} upw_t;

static void *upw_main(void *arg)
{
    upw_t *w = (upw_t *)arg;

    pthread_mutex_lock(&w->mu);
    for (;;) {
        up_task_t t;
        uint64_t  t0;

        while (w->head == w->tail && !w->stop) {
            pthread_cond_wait(&w->cv, &w->mu);
        }
        if (w->head == w->tail) {
            break;
        }
        t = w->q[w->tail % UPQ];
        w->busy = 1;
        pthread_mutex_unlock(&w->mu);
        t0 = job_now_us();
        upsample_ps(t.q, S, t.k, t.out, t.stride, &w->scratch);
        pthread_mutex_lock(&w->mu);
        w->work_ms += (double)(job_now_us() - t0) / 1000.0;
        w->tail++;
        w->busy = 0;
        pthread_cond_broadcast(&w->cv);
    }
    pthread_mutex_unlock(&w->mu);
    return NULL;
}

static void upw_put(upw_t *w, const int8_t *q, int k, uint8_t *out, int stride)
{
    pthread_mutex_lock(&w->mu);
    while (w->head - w->tail >= UPQ) {
        pthread_cond_wait(&w->cv, &w->mu);
    }
    w->q[w->head % UPQ].q = q;
    w->q[w->head % UPQ].k = k;
    w->q[w->head % UPQ].out = out;
    w->q[w->head % UPQ].stride = stride;
    w->head++;
    pthread_cond_broadcast(&w->cv);
    pthread_mutex_unlock(&w->mu);
}

static void upw_drain(upw_t *w)
{
    pthread_mutex_lock(&w->mu);
    while (w->head != w->tail) {
        pthread_cond_wait(&w->cv, &w->mu);
    }
    pthread_mutex_unlock(&w->mu);
}

/* ---------------------------------------------------------------- decode thread: gunzip -> window -> view -> box */

typedef struct {
    pthread_t       th;
    pthread_mutex_t mu;
    pthread_cond_t  cv;
    reader_t       *rd;
    const nii_t    *nh;
    size_t          st[3];             /* numpy strides of the stored array (k, j, i) */
    int             order[3], fz, fy, fx, nz, scaled, per_slice;
    float           lo, c;
    uint8_t        *vol, *small;
    int             ready;             /* slices of vol / small that are final */
    const char     *err;
    volatile int    abort;
    double          ms;
} dec_t;

/* stored voxels -> window -> uint8 (float32 like ct_volume.window_u8, round half to even) */
static void window_plane(const dec_t *d, const uint8_t *raw, size_t n, uint8_t *dst)
{
    size_t p;

    if (d->nh->dtype == 16 && !d->scaled) {                 /* MSD: float32 HU, slope 1 */
        for (p = 0; p < n; p++) {
            float h, v;

            memcpy(&h, raw + 4u * p, 4u);
            v = rintf((h - d->lo) * d->c);
            dst[p] = (uint8_t)(v < 0.0f ? 0.0f : (v > 255.0f ? 255.0f : v));
        }
        return;
    }
    for (p = 0; p < n; p++) {
        float h = voxel_f32(raw + p * (size_t)d->nh->bpp, d->nh->dtype), v;

        if (d->scaled) {                                    /* built with -ffp-contract=off */
            h = h * d->nh->slope + d->nh->inter;
        }
        v = rintf((h - d->lo) * d->c);
        dst[p] = (uint8_t)(v < 0.0f ? 0.0f : (v > 255.0f ? 255.0f : v));
    }
}

/* one output slice zz of the radiological view from the stored array `base` (to_radiological) */
static void view_slice(const dec_t *d, const uint8_t *base, int zz)
{
    const size_t s1 = d->st[d->order[1]], s2 = d->st[d->order[2]];
    int          yy, xx;

    for (yy = 0; yy < N; yy++) {
        const uint8_t *row = base + (size_t)(d->fy ? N - 1 - yy : yy) * s1;
        uint8_t       *dst = d->vol + ((size_t)zz * N + yy) * N;

        if (s2 == 1u && !d->fx) {
            memcpy(dst, row, N);
        } else if (s2 == 1u) {
            for (xx = 0; xx < N; xx++) {
                dst[xx] = row[N - 1 - xx];
            }
        } else {
            for (xx = 0; xx < N; xx++) {
                dst[xx] = row[(size_t)(d->fx ? N - 1 - xx : xx) * s2];
            }
        }
    }
    box(d->vol + (size_t)zz * N * N, N, 0, 0, N, 4, d->small + (size_t)zz * S * S);
}

static void dec_publish(dec_t *d, int ready, const char *err)
{
    pthread_mutex_lock(&d->mu);
    d->ready = ready;
    if (err != NULL && d->err == NULL) {
        d->err = err;
    }
    pthread_cond_broadcast(&d->cv);
    pthread_mutex_unlock(&d->mu);
}

static void *dec_main(void *arg)
{
    dec_t         *d = (dec_t *)arg;
    const size_t   plane = (size_t)d->nh->ni * d->nh->nj;
    const uint64_t t0 = job_now_us();
    uint8_t       *raw = (uint8_t *)malloc(plane * (size_t)d->nh->bpp);
    uint8_t       *vs = d->per_slice ? (uint8_t *)malloc(plane) : (uint8_t *)malloc(plane * (size_t)d->nh->nk);
    int            k;

    if (raw == NULL || vs == NULL) {
        dec_publish(d, d->ready, "out of memory");
        goto done;
    }
    for (k = 0; k < d->nh->nk && !d->abort; k++) {
        uint8_t *dst = d->per_slice ? vs : vs + (size_t)k * plane;

        if (rd_read(d->rd, raw, plane * (size_t)d->nh->bpp) != 0) {
            dec_publish(d, d->ready, "file ends early or is corrupt");
            goto done;
        }
        window_plane(d, raw, plane, dst);
        if (d->per_slice) {                                 /* stored k is output z: hand it over now */
            view_slice(d, vs, k);
            dec_publish(d, k + 1, NULL);
        }
    }
    if (!d->per_slice && !d->abort) {                       /* axes permuted or z flipped: needs the whole volume */
        int zz;

        for (zz = 0; zz < d->nz; zz++) {
            view_slice(d, vs + (size_t)(d->fz ? d->nz - 1 - zz : zz) * d->st[d->order[0]], zz);
        }
        dec_publish(d, d->nz, NULL);
    }
done:
    free(raw);
    free(vs);
    d->ms = (double)(job_now_us() - t0) / 1000.0;
    return NULL;
}

/* wait until `need` slices are decoded (sending progress meanwhile). 0 ok, -1 decode error / link gone. */
static int dec_wait(job_t *j, dec_t *d, int need, double *wait_ms)
{
    uint64_t t0 = job_now_us();
    int      rc = 0;

    pthread_mutex_lock(&d->mu);
    while (d->ready < need && d->err == NULL) {
        struct timespec ts;

        clock_gettime(CLOCK_REALTIME, &ts);
        ts.tv_nsec += 200000000L;
        if (ts.tv_nsec >= 1000000000L) {
            ts.tv_sec++;
            ts.tv_nsec -= 1000000000L;
        }
        pthread_cond_timedwait(&d->cv, &d->mu, &ts);
        if (d->ready < need && d->err == NULL) {
            int r = d->ready;

            pthread_mutex_unlock(&d->mu);
            if (job_progress(j, "decode", (uint32_t)r, (uint32_t)d->nz, 0) != 0) {
                return -1;
            }
            pthread_mutex_lock(&d->mu);
        }
    }
    if (d->ready < need) {
        rc = -1;
    }
    pthread_mutex_unlock(&d->mu);
    *wait_ms += (double)(job_now_us() - t0) / 1000.0;
    return rc;
}

/* ---------------------------------------------------------------- the job */

typedef struct {
    double header, decode, wait, coarse, lcc0, roi, fine, lcc1, upsample_ps, send, total;
} ct_times_t;

int job_ct_run(job_t *j)
{
    static uint8_t in[S * S * 3];
    static uint8_t tmp[ROI * ROI];
    static upw_t   up;
    static dec_t   dec;
    char           js[3072], err[160], rjs[1024];
    jbuf_t         b;
    reader_t       rd;
    nii_t          nh;
    ct_times_t     tm;
    uint8_t        hdr[348];
    const char    *e;
    uint8_t       *vol = NULL, *small = NULL, *m0 = NULL, *fin = NULL, *inf = NULL;
    int8_t        *lgc = NULL, *lgf = NULL;
    unsigned       mc, mf, target;
    float          level, width;
    int            world[3], sign[3], order[3], nz = 0, o = 1, z, k, rc = -1, use_pl, dec_started = 0, rd_open_ok = 0;
    int            dec_inited = 0;
    int            win_ok = 0, y0 = 0, x0 = 0, z0 = 0, z1 = -1, nf = 0, pl_ups = 0, n_ups = 0;
    double         sp[3], spc[3], ml;
    uint32_t       run_us = 0u, comps0 = 0u, comps1 = 0u;
    long           vox0 = 0, vox1 = 0;
    uint64_t       t;

    memset(&tm, 0, sizeof(tm));
    memset(&nh, 0, sizeof(nh));
    memset(order, 0, sizeof(order));
    memset(spc, 0, sizeof(spc));
    if (j->param_bytes < 8u) {
        return job_fail(j, "ct_seg needs 8 bytes of params");
    }
    g_pp_pl_us = g_pp_total_us = 0u;
    mc = j->param[0];
    mf = j->param[1];
    level = (float)le16s(j->param + 2);
    width = (float)(uint16_t)(j->param[4] | (j->param[5] << 8));
    target = j->param[6] ? j->param[6] : 5u;
    use_pl = (j->flags & F_PS_UPSAMPLE) == 0u && pp_ready();      /* default: PL; bit5 (old "PL") is implied */
    if (width <= 0.0f) {
        return job_fail(j, "bad window width");
    }

    /* PS worker for the up-sampling (one PL user: only this thread touches the PL) */
    if (!up.started) {
        pthread_mutex_init(&up.mu, NULL);
        pthread_cond_init(&up.cv, NULL);
        up.started = 1;
        if (pthread_create(&up.th, NULL, upw_main, &up) != 0) {
            up.started = 0;
        }
    }
    if (!up.started) {
        return job_fail(j, "cannot start the worker thread");
    }
    up.work_ms = 0.0;

    /* ---- 1. header, orientation; then the decode thread runs ahead of the PL ---- */
    t = job_now_us();
    if (rd_open(&rd, j->data, j->data_bytes) != 0) {
        return job_fail(j, "zlib init failed");
    }
    rd_open_ok = 1;
    e = (rd_read(&rd, hdr, sizeof(hdr)) != 0) ? "file too short" : nii_header(hdr, &nh);
    if (e == NULL && rd_read(&rd, NULL, (size_t)(nh.vox_offset - 348)) != 0) {
        e = "file too short";
    }
    if (e == NULL) {
        double m[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
        int    ax, seen = 0;

        if (nh.has_m) {
            memcpy(m, nh.m, sizeof(m));
        }
        for (ax = 0; ax < 3; ax++) {                       /* array axis i/j/k -> world x/y/z */
            int r, best = 0;

            for (r = 1; r < 3; r++) {
                if (fabs(m[r][ax]) > fabs(m[best][ax])) {
                    best = r;
                }
            }
            world[ax] = best;
            sign[ax] = (m[best][ax] < 0.0) ? -1 : 1;
            seen |= 1 << best;
        }
        if (seen != 7) {
            e = "direction matrix is not axis aligned";
        }
    }
    if (e == NULL) {
        int wi[3], shp[3] = {nh.nk, nh.nj, nh.ni};          /* numpy axes 0 = k, 1 = j, 2 = i */

        for (k = 0; k < 3; k++) {
            wi[world[k]] = k;                               /* world.index(w) */
        }
        order[0] = 2 - wi[2];
        order[1] = 2 - wi[1];
        order[2] = 2 - wi[0];
        nz = shp[order[0]];
        if (shp[order[1]] != N || shp[order[2]] != N) {
            snprintf(err, sizeof(err), "only 512x512 slices (this one is %dx%d)", shp[order[1]], shp[order[2]]);
            e = err;
        }
        sp[0] = nh.pixdim[1];
        sp[1] = nh.pixdim[2];
        sp[2] = nh.pixdim[3];
        spc[0] = sp[2 - order[2]];                          /* x, y, z after the transpose */
        spc[1] = sp[2 - order[1]];
        spc[2] = sp[2 - order[0]];
        memset(&dec, 0, sizeof(dec));
        dec.fz = sign[wi[2]] < 0;
        dec.fy = sign[wi[1]] > 0;
        dec.fx = sign[wi[0]] > 0;
    }
    if (e == NULL) {
        vol = (uint8_t *)malloc((size_t)nz * N * N);
        small = (uint8_t *)malloc((size_t)nz * S * S);
        lgc = (int8_t *)malloc((size_t)nz * S * S);
        m0 = (uint8_t *)malloc((size_t)nz * N * N);
        if (vol == NULL || small == NULL || lgc == NULL || m0 == NULL) {
            e = "out of memory";
        }
    }
    tm.header = (double)(job_now_us() - t) / 1000.0;
    if (e != NULL) {
        rc = job_fail(j, e);
        goto out;
    }
    dec.rd = &rd;
    dec.nh = &nh;
    dec.st[0] = (size_t)nh.nj * nh.ni;
    dec.st[1] = (size_t)nh.ni;
    dec.st[2] = 1u;
    memcpy(dec.order, order, sizeof(order));
    dec.nz = nz;
    dec.scaled = !(nh.slope == 0.0f || nh.slope == 1.0f) || nh.inter != 0.0f;
    dec.lo = (float)((double)level - (double)width / 2.0);
    dec.c = (float)(255.0 / (double)width);
    dec.vol = vol;
    dec.small = small;
    dec.per_slice = (order[0] == 0 && !dec.fz);
    pthread_mutex_init(&dec.mu, NULL);
    pthread_cond_init(&dec.cv, NULL);
    dec_inited = 1;
    if (pthread_create(&dec.th, NULL, dec_main, &dec) != 0) {
        rc = job_fail(j, "cannot start the decode thread");
        goto out;
    }
    dec_started = 1;
    o = (int)nearbyint((double)target / spc[2]);            /* Python round(): half to even */
    if (o < 1) {
        o = 1;
    }

    /* ---- 2. coarse: z3 payload -> PL program; x4 bilinear on the PS worker while the PL runs the next slice ---- */
    t = job_now_us();
    for (z = 0; z < nz; z++) {
        const uint8_t *r, *g, *bb;

        if (dec_wait(j, &dec, (z + o + 1 < nz) ? z + o + 1 : nz, &tm.wait) != 0) {
            if (!j->failed) {
                rc = job_fail(j, dec.err != NULL ? dec.err : "decode failed");
            }
            goto out;
        }
        r = small + (size_t)clampi(z - o, 0, nz - 1) * S * S;
        g = small + (size_t)z * S * S;
        bb = small + (size_t)clampi(z + o, 0, nz - 1) * S * S;
        for (k = 0; k < S * S; k++) {
            in[3 * k] = r[k];
            in[3 * k + 1] = g[k];
            in[3 * k + 2] = bb[k];
        }
        if (job_unet(mc, 2, in, lgc + (size_t)z * S * S, &run_us) != 0) {
            rc = job_fail(j, "PL run failed (coarse model)");
            goto out;
        }
        if (use_pl && upsample_pl(lgc + (size_t)z * S * S, S, 4, m0 + (size_t)z * N * N, N) == 0) {
            pl_ups++;
        } else {
            upw_put(&up, lgc + (size_t)z * S * S, 4, m0 + (size_t)z * N * N, N);
        }
        n_ups++;
        if (job_progress(j, "coarse", (uint32_t)z + 1u, (uint32_t)nz, 0) != 0) {
            goto out;
        }
    }
    upw_drain(&up);
    tm.coarse = (double)(job_now_us() - t) / 1000.0;
    t = job_now_us();
    vox0 = cc3d_keep_largest(m0, nz, N, N, &comps0);
    tm.lcc0 = (double)(job_now_us() - t) / 1000.0;
    if (vox0 < 0) {
        rc = job_fail(j, "out of memory (3-D components)");
        goto out;
    }

    /* ---- 3. ROI window + fine stage ---- */
    fin = m0;
    if (mf != 255u && vox0 > 0) {
        int zmin = -1, zmax = -1, rmin = N, rmax = -1, cmin = N, cmax = -1, yy, xx, cy, cx;

        t = job_now_us();
        for (z = 0; z < nz; z++) {
            const uint8_t *p = m0 + (size_t)z * N * N;

            for (yy = 0; yy < N; yy++) {
                const uint8_t *row = p + yy * N;

                if (memchr(row, 1, N) == NULL) {
                    continue;
                }
                for (xx = 0; xx < N; xx++) {
                    if (row[xx]) {
                        zmin = (zmin < 0) ? z : zmin;
                        zmax = z;
                        rmin = yy < rmin ? yy : rmin;
                        rmax = yy > rmax ? yy : rmax;
                        cmin = xx < cmin ? xx : cmin;
                        cmax = xx > cmax ? xx : cmax;
                    }
                }
            }
        }
        cy = (rmin + rmax + 1) / 2;
        cx = (cmin + cmax + 1) / 2;
        y0 = clampi(cy - ROI / 2, 0, N - ROI);
        x0 = clampi(cx - ROI / 2, 0, N - ROI);
        z0 = zmin - ROI_ZM * o > 0 ? zmin - ROI_ZM * o : 0;
        z1 = zmax + ROI_ZM * o < nz - 1 ? zmax + ROI_ZM * o : nz - 1;
        nf = z1 - z0 + 1;
        win_ok = 1;
        lgf = (int8_t *)malloc((size_t)nf * S * S);
        fin = (uint8_t *)calloc((size_t)nz * N * N, 1u);
        inf = (j->flags & F_INPUTS) ? (uint8_t *)malloc((size_t)nf * S * S * 3u) : NULL;
        if (lgf == NULL || fin == NULL || ((j->flags & F_INPUTS) && inf == NULL)) {
            free(fin);
            fin = m0;
            rc = job_fail(j, "out of memory");
            goto out;
        }
        tm.roi = (double)(job_now_us() - t) / 1000.0;
        t = job_now_us();
        for (z = z0; z <= z1; z++) {
            const uint8_t *cur = vol + (size_t)z * N * N;
            const uint8_t *pv = vol + (size_t)clampi(z - o, 0, nz - 1) * N * N;
            const uint8_t *nx = vol + (size_t)clampi(z + o, 0, nz - 1) * N * N;
            const uint8_t *pm = m0 + (size_t)z * N * N;
            int8_t        *lq = lgf + (size_t)(z - z0) * S * S;
            uint8_t       *dst = fin + ((size_t)z * N + y0) * N + x0;
            static uint8_t r2[S * S], b2[S * S];

            box(cur, N, x0, y0, ROI, ROI_K, r2);
            for (yy = 0; yy < ROI; yy++) {                 /* (prev + next + 1) >> 1 at full resolution */
                for (xx = 0; xx < ROI; xx++) {
                    size_t q = (size_t)(y0 + yy) * N + x0 + xx;

                    tmp[yy * ROI + xx] = (uint8_t)(((unsigned)pv[q] + nx[q] + 1u) >> 1);
                }
            }
            box(tmp, ROI, 0, 0, ROI, ROI_K, b2);
            for (yy = 0; yy < S; yy++) {
                for (xx = 0; xx < S; xx++) {
                    size_t q = (size_t)(y0 + 2 * yy) * N + x0 + 2 * xx;
                    int    s = pm[q] + pm[q + 1] + pm[q + N] + pm[q + N + 1];

                    k = yy * S + xx;
                    in[3 * k] = r2[k];
                    in[3 * k + 1] = (s >= 2) ? 255u : 0u;
                    in[3 * k + 2] = b2[k];
                }
            }
            if (inf != NULL) {
                memcpy(inf + (size_t)(z - z0) * S * S * 3u, in, sizeof(in));
            }
            if (job_unet(mf, 2, in, lq, &run_us) != 0) {
                rc = job_fail(j, "PL run failed (fine model)");
                goto out;
            }
            if (use_pl && upsample_pl(lq, S, ROI_K, dst, N) == 0) {
                pl_ups++;
            } else {
                upw_put(&up, lq, ROI_K, dst, N);
            }
            n_ups++;
            if (job_progress(j, "fine", (uint32_t)(z - z0) + 1u, (uint32_t)nf, 0) != 0) {
                goto out;
            }
        }
        upw_drain(&up);
        tm.fine = (double)(job_now_us() - t) / 1000.0;
        t = job_now_us();
        vox1 = cc3d_keep_largest(fin, nz, N, N, &comps1);
        tm.lcc1 = (double)(job_now_us() - t) / 1000.0;
        if (vox1 < 0) {
            rc = job_fail(j, "out of memory (3-D components)");
            goto out;
        }
    } else {
        vox1 = vox0;
        comps1 = comps0;
    }
    tm.upsample_ps = up.work_ms;

    /* ---- 4. results ---- */
    t = job_now_us();
    if (job_progress(j, "send", 0u, 0u, 1) != 0) {
        goto out;
    }
    if (j->flags & F_LOGITS) {
        if (job_blob(j, "logits_coarse", lgc, (size_t)nz * S * S, 1) != 0 ||
            (nf > 0 && job_blob(j, "logits_fine", lgf, (size_t)nf * S * S, 1) != 0)) {
            goto out;
        }
    }
    if (j->flags & F_INPUTS) {
        if (job_blob(j, "small", small, (size_t)nz * S * S, 1) != 0 ||
            (inf != NULL && job_blob(j, "inputs_fine", inf, (size_t)nf * S * S * 3u, 1) != 0)) {
            goto out;
        }
    }
    if (j->flags & (F_MASK_COARSE | F_MASK_FINAL)) {
        const uint8_t *src[2] = {m0, fin};
        const char    *nm[2] = {"mask_coarse", "mask_final"};
        const unsigned fl[2] = {F_MASK_COARSE, F_MASK_FINAL};

        for (k = 0; k < 2; k++) {
            uint8_t *bits;

            if ((j->flags & fl[k]) == 0u) {
                continue;
            }
            bits = pack_bits(src[k], (size_t)nz * N * N);
            if (bits == NULL) {
                rc = job_fail(j, "out of memory");
                goto out;
            }
            if (job_blob(j, nm[k], bits, (size_t)nz * N * N / 8u, 1) != 0) {
                free(bits);
                goto out;
            }
            free(bits);
        }
    }
    if ((j->flags & F_SLICES) && job_blob(j, "slices_u8", vol, (size_t)nz * N * N, 1) != 0) {
        goto out;
    }
    tm.send = (double)(job_now_us() - t) / 1000.0;
    rjs[0] = '\0';
    if (j->flags & F_RENDER3D) {
        jbuf_t rb;
        int    rr;

        jb_init(&rb, rjs, sizeof(rjs));
        rr = job_render3d(j, fin, nz, spc, j->param_bytes >= 12u ? j->param[8] : 0, NULL,
                          j->param_bytes >= 12u ? (j->param[10] | j->param[11] << 8) : 0, 2, &rb);
        if (rr == -1 || j->failed) {
            goto out;
        }
    }
    {
        int zlo = -1, zhi = -1;

        for (z = 0; z < nz; z++) {
            const uint8_t *p = fin + (size_t)z * N * N;

            if (memchr(p, 1, (size_t)N * N) != NULL) {
                zlo = (zlo < 0) ? z : zlo;
                zhi = z;
            }
        }
        ml = (double)vox1 * spc[0] * spc[1] * spc[2] / 1000.0;   /* ct_volume.volume_ml, same order */
        tm.total = (double)(job_now_us() - j->t0_us) / 1000.0;
        jb_init(&b, js, sizeof(js));
        jb_add(&b, "{\"ok\":true,\"nz\":%d,\"spacing\":[%.17g,%.17g,%.17g],\"order\":[%d,%d,%d],\"offset\":%d", nz,
               spc[0], spc[1], spc[2], order[0], order[1], order[2], o);
        jb_add(&b, ",\"dtype\":%d,\"per_slice_decode\":%s,\"models\":[%u,%u],\"voxels_coarse\":%ld,\"comps_coarse\":%u",
               nh.dtype, dec.per_slice ? "true" : "false", mc, mf, vox0, (unsigned)comps0);
        if (win_ok) {
            jb_add(&b, ",\"win\":[%d,%d,%d,%d],\"comps_fine\":%u", y0, x0, z0, z1, (unsigned)comps1);
        } else {
            jb_add(&b, ",\"win\":null");
        }
        jb_add(&b, ",\"voxels\":%ld,\"volume_ml\":%.17g,\"z_range\":", vox1, ml);
        if (zlo >= 0) {
            jb_add(&b, "[%d,%d]", zlo, zhi);
        } else {
            jb_add(&b, "null");
        }
        spleen_json(&b, fin, nz, zlo, zhi, spc);
        jb_add(&b, ",\"slices\":[%d,%d],\"upsample\":{\"pl\":%d,\"all\":%d},\"pl_ms\":%.3f", nz, nf, pl_ups, n_ups,
               run_us / 1000.0);
        job_pl_split_json(&b);
        jb_add(&b, "%s", rjs);
        jb_add(&b, ",\"pp_ms\":{\"pl\":%.3f,\"total\":%.3f}", g_pp_pl_us / 1000.0, g_pp_total_us / 1000.0);
        jb_add(&b, ",\"ms\":{\"header\":%.2f,\"decode\":%.2f,\"decode_wait\":%.2f,\"coarse\":%.2f,\"lcc0\":%.2f,"
                   "\"roi\":%.2f,\"fine\":%.2f,\"lcc1\":%.2f,\"upsample_ps\":%.2f,\"send\":%.2f,\"total\":%.2f}"
                   ",\"upload_bytes\":%u}",
               tm.header, dec.ms, tm.wait, tm.coarse, tm.lcc0, tm.roi, tm.fine, tm.lcc1, tm.upsample_ps, tm.send,
               tm.total, (unsigned)j->data_bytes);
        rc = job_result(j, js);
        printf("[job] ct_seg %d slices (%d fine): %.1f mL, %.0f ms (decode %.0f / waited %.0f, coarse %.0f, fine %.0f, "
               "PL %.0f)\n", nz, nf, ml, tm.total, dec.ms, tm.wait, tm.coarse, tm.fine, run_us / 1000.0);
    }
out:
    if (dec_started) {
        dec.abort = 1;
        pthread_join(dec.th, NULL);
    }
    if (dec_inited) {
        pthread_mutex_destroy(&dec.mu);
        pthread_cond_destroy(&dec.cv);
    }
    upw_drain(&up);                                         /* nothing may still write into fin / m0 */
    if (rd_open_ok) {
        rd_close(&rd);
    }
    free(vol);
    free(small);
    free(lgc);
    free(lgf);
    free(inf);
    if (fin != m0) {
        free(fin);
    }
    free(m0);
    return j->failed ? -1 : rc;
}
