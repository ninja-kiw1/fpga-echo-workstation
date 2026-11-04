/*
 * cpu_unet.c -- CPU baseline: the deployed integer U-Net, computed on the CPU.
 *
 * Same network, same parameters, same arithmetic as the FPGA:
 *   - layer table + packed parameters are linked straight from the board
 *     program's generated unet_net_data.c (fundus) or skin/board/gen (skin);
 *   - int16 activations x int8 weights, INT32 accumulation, ReLU before the
 *     affine where the table says so, INT64 multiplier + offset, signed
 *     round-half-to-even shift, saturation to [qmin, qmax];
 *   - Add: round(a*m0 + b*m1) once; Concat: each branch rounded; MaxPool 2x2.
 * The output must equal the FPGA's logits value for value (checked against a
 * golden file on every run), so the timing compares two implementations of
 * exactly the same computation.
 *
 * Optimisation level is "good plain C": loop order chosen so the innermost
 * loop runs over 8 output channels (auto-vectorised: NEON on the A53, AVX2 on
 * x86 with -march=native), -O3, OpenMP over output rows. No hand intrinsics.
 *
 *   cpu_unet <rgb.raw> <expected.i8> <H> <W> [runs] [--layers]
 *     rgb.raw      H*W*3 uint8, the network input at the run resolution
 *     expected.i8  H*W int8 logits from the FPGA / integer reference
 *   threads: OMP_NUM_THREADS
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef _OPENMP
#include <omp.h>
#endif

#include "unet_net.h"

#define LANES 8
#define MAXC  32

typedef struct {
    int      h, w, c;      /* HWC, c = real channel count */
    int16_t *d;
} tensor_t;

typedef struct {           /* one CONV node, unpacked once at start-up */
    int      taps, cin, cout, coutp;
    int16_t *w;            /* [tap][cin][coutp] */
    int32_t  bias[MAXC];
    int64_t  mult[MAXC], off[MAXC];
    int16_t *pad;          /* blocked NEON path: zero-bordered copy of the input, (H+2)x(W+2)xcin */
} conv_t;

static double now_s(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + 1e-9 * (double)t.tv_nsec;
}

/* signed round to nearest, ties to even (same as round_shift in the references) */
static inline int64_t round_shift(int64_t v, int s)
{
    int64_t mag, q, rem, half;

    if (s == 0) {
        return v;
    }
    mag  = v < 0 ? -v : v;
    q    = mag >> s;
    rem  = mag & ((1LL << s) - 1);
    half = 1LL << (s - 1);
    q   += (rem > half) || (rem == half && (q & 1));
    return v < 0 ? -q : q;
}

static inline int16_t sat(int64_t v, int lo, int hi)
{
    return (int16_t)(v < lo ? lo : (v > hi ? hi : v));
}

static void unpack_conv(const unet_node_t *n, const uint8_t *blob, conv_t *cv)
{
    int taps = n->mode == 0 ? 9 : (n->mode == 1 ? 1 : 4);
    int cig = (n->ca + LANES - 1) / LANES, cog = (n->cb + LANES - 1) / LANES;
    const int64_t *pc;
    int t, og, ig, o, i;

    cv->taps = taps;
    cv->cin = n->ca;
    cv->cout = n->cb;
    cv->coutp = cog * LANES;
    cv->w = calloc((size_t)taps * n->ca * cv->coutp, sizeof(int16_t));
    /* word order: tap, output group, input group, output lane; 8 int8 input lanes per word */
    for (t = 0; t < taps; t++)
        for (og = 0; og < cog; og++)
            for (ig = 0; ig < cig; ig++)
                for (o = 0; o < LANES; o++) {
                    const int8_t *wd = (const int8_t *)(blob + 8u * ((((size_t)t * cog + og) * cig + ig) * LANES + o));
                    for (i = 0; i < LANES; i++) {
                        int ci = ig * LANES + i, co = og * LANES + o;
                        if (ci < n->ca && co < n->cb)
                            cv->w[((size_t)t * n->ca + ci) * cv->coutp + co] = wd[i];
                    }
                }
    pc = (const int64_t *)(blob + 8u * (size_t)taps * cog * cig * LANES);
    for (o = 0; o < MAXC; o++) {
        cv->bias[o] = o < n->cb ? (int32_t)pc[o] : 0;
        cv->mult[o] = o < n->cb ? pc[cog * LANES + o] : 0;
        cv->off[o]  = o < n->cb ? pc[2 * cog * LANES + o] : 0;
    }
}

static inline void requant_store(const unet_node_t *n, const conv_t *cv, const int32_t *acc, int16_t *dst)
{
    int co;

    for (co = 0; co < cv->cout; co++) {
        int64_t a = acc[co];
        if (n->relu && a < 0) {
            a = 0;
        }
        dst[co] = sat(round_shift(a * cv->mult[co] + cv->off[co], n->shift), n->qmin, n->qmax);
    }
}

/* acc[0..coutp) += sum_taps sum_ci xs[t][ci] * ws[t][ci][.] -- plain C, innermost loop 8 lanes wide */
static inline void mac_plain(int32_t *acc, const int16_t *const *xs, const int16_t *const *ws,
                             int ntap, int cin, int coutp)
{
    int t, ci, cb, l;

    for (t = 0; t < ntap; t++)
        for (ci = 0; ci < cin; ci++) {
            int32_t xv = xs[t][ci];
            const int16_t *wr = ws[t] + (size_t)ci * coutp;
            for (cb = 0; cb < coutp; cb += LANES)
                for (l = 0; l < LANES; l++)
                    acc[cb + l] += xv * (int32_t)wr[cb + l];
        }
}

#if defined(__ARM_NEON) && !defined(NO_NEON)
#include <arm_neon.h>
#define IMPL "neon"
/* Same sums with NEON: G groups of 8 output channels kept in 2G int32x4
 * registers for the whole pixel; one SMLAL = 4 int16 x int16 -> int32 MACs. */
#define DEF_MAC(G)                                                                       \
static inline void mac_g##G(int32_t *acc, const int16_t *const *xs, const int16_t *const *ws, \
                            int ntap, int cin)                                            \
{                                                                                         \
    int32x4_t a[2 * G];                                                                   \
    int k, t, ci;                                                                         \
    for (k = 0; k < 2 * G; k++) a[k] = vld1q_s32(acc + 4 * k);                            \
    for (t = 0; t < ntap; t++) {                                                          \
        const int16_t *x = xs[t], *w = ws[t];                                             \
        for (ci = 0; ci < cin; ci++) {                                                    \
            int16_t xv = x[ci];                                                           \
            const int16_t *wr = w + ci * (8 * G);                                         \
            for (k = 0; k < G; k++) {                                                     \
                int16x8_t w8 = vld1q_s16(wr + 8 * k);                                     \
                a[2 * k]     = vmlal_n_s16(a[2 * k], vget_low_s16(w8), xv);               \
                a[2 * k + 1] = vmlal_high_n_s16(a[2 * k + 1], w8, xv);                    \
            }                                                                             \
        }                                                                                 \
    }                                                                                     \
    for (k = 0; k < 2 * G; k++) vst1q_s32(acc + 4 * k, a[k]);                             \
}
DEF_MAC(1)
DEF_MAC(2)
DEF_MAC(4)
static inline void mac(int32_t *acc, const int16_t *const *xs, const int16_t *const *ws,
                       int ntap, int cin, int coutp)
{
    switch (coutp) {
    case 8:  mac_g1(acc, xs, ws, ntap, cin); break;
    case 16: mac_g2(acc, xs, ws, ntap, cin); break;
    case 32: mac_g4(acc, xs, ws, ntap, cin); break;
    default: mac_plain(acc, xs, ws, ntap, cin, coutp);
    }
}

/* Register blocking: P neighbouring output pixels share every weight load.
 * x points at pixel 0 of the block for this tap; pixel p is at x + p*cin.
 * acc is [P][MAXC]. Input must be zero-padded so all taps are valid. */
#define DEF_BLK(G, P)                                                                     \
static inline void blk_g##G##_p##P(int32_t *acc, const int16_t *const *xs,                \
                                   const int16_t *const *ws, int ntap, int cin)           \
{                                                                                         \
    int32x4_t a[P][2 * G];                                                                \
    int k, p, t, ci;                                                                      \
    for (p = 0; p < P; p++)                                                               \
        for (k = 0; k < 2 * G; k++) a[p][k] = vld1q_s32(acc + p * MAXC + 4 * k);          \
    for (t = 0; t < ntap; t++) {                                                          \
        const int16_t *x = xs[t], *w = ws[t];                                             \
        for (ci = 0; ci < cin; ci++) {                                                    \
            const int16_t *wr = w + ci * (8 * G);                                         \
            for (k = 0; k < G; k++) {                                                     \
                int16x8_t w8 = vld1q_s16(wr + 8 * k);                                     \
                int16x4_t wl = vget_low_s16(w8);                                          \
                for (p = 0; p < P; p++) {                                                 \
                    int16_t xv = x[p * cin + ci];                                         \
                    a[p][2 * k]     = vmlal_n_s16(a[p][2 * k], wl, xv);                   \
                    a[p][2 * k + 1] = vmlal_high_n_s16(a[p][2 * k + 1], w8, xv);          \
                }                                                                         \
            }                                                                             \
        }                                                                                 \
    }                                                                                     \
    for (p = 0; p < P; p++)                                                               \
        for (k = 0; k < 2 * G; k++) vst1q_s32(acc + p * MAXC + 4 * k, a[p][k]);           \
}
DEF_BLK(1, 4)
DEF_BLK(2, 4)
DEF_BLK(4, 2)
#define HAVE_BLOCKED 1
static inline int blk_pixels(int coutp) { return coutp == 32 ? 2 : 4; }
static inline void blk(int32_t *acc, const int16_t *const *xs, const int16_t *const *ws,
                       int ntap, int cin, int coutp)
{
    switch (coutp) {
    case 8:  blk_g1_p4(acc, xs, ws, ntap, cin); break;
    case 16: blk_g2_p4(acc, xs, ws, ntap, cin); break;
    default: blk_g4_p2(acc, xs, ws, ntap, cin); break;
    }
}
#else
#define IMPL "plain"
#define mac mac_plain
#endif

#ifdef HAVE_BLOCKED
static int use_blocked = 1;     /* --noblock turns it off */

static void run_conv_blocked(const unet_node_t *n, const conv_t *cv, const tensor_t *in, tensor_t *out)
{
    int H = in->h, W = in->w, cin = cv->cin, coutp = cv->coutp, P = blk_pixels(coutp);
    int Wp = W + 2, y;
    const int16_t *src = in->d;

    if (n->mode == 0) {         /* refresh the interior of the zero-bordered copy */
        #pragma omp parallel for schedule(static)
        for (y = 0; y < H; y++)
            memcpy(cv->pad + ((size_t)(y + 1) * Wp + 1) * cin, in->d + (size_t)y * W * cin,
                   (size_t)W * cin * sizeof(int16_t));
        src = cv->pad;
    }
    #pragma omp parallel for schedule(static)
    for (y = 0; y < H; y++) {
        int32_t acc[4 * MAXC];
        const int16_t *xs[9], *ws[9];
        int x, p, co, t, nt = n->mode == 0 ? 9 : 1;
        for (x = 0; x < W; x += P) {
            for (p = 0; p < P; p++)
                for (co = 0; co < coutp; co++) acc[p * MAXC + co] = cv->bias[co];
            for (t = 0; t < nt; t++) {
                int ky = n->mode == 0 ? t / 3 : 0, kx = n->mode == 0 ? t % 3 : 0;
                int rw = n->mode == 0 ? Wp : W;
                xs[t] = src + ((size_t)(y + ky) * rw + (x + kx)) * cin;
                ws[t] = cv->w + (size_t)t * cin * coutp;
            }
            blk(acc, xs, ws, nt, cin, coutp);
            for (p = 0; p < P; p++)
                requant_store(n, cv, acc + p * MAXC, out->d + ((size_t)y * W + x + p) * cv->cout);
        }
    }
}
#endif

static void run_conv(const unet_node_t *n, const conv_t *cv, const tensor_t *in, tensor_t *out)
{
    int H = in->h, W = in->w, cin = cv->cin, coutp = cv->coutp;
    int y;

#ifdef HAVE_BLOCKED
    if (use_blocked && n->mode != 2 && (coutp == 8 || coutp == 16 || coutp == 32) &&
        W % blk_pixels(coutp) == 0) {
        run_conv_blocked(n, cv, in, out);
        return;
    }
#endif

    if (n->mode == 2) {    /* 2x2 transposed conv, stride 2 */
        #pragma omp parallel for schedule(static)
        for (y = 0; y < H; y++) {
            int32_t acc[MAXC];
            const int16_t *xs[1], *ws[1];
            int x, t, co;
            for (x = 0; x < W; x++)
                for (t = 0; t < 4; t++) {
                    int oy = 2 * y + t / 2, ox = 2 * x + t % 2;
                    for (co = 0; co < coutp; co++) acc[co] = cv->bias[co];
                    xs[0] = in->d + ((size_t)y * W + x) * cin;
                    ws[0] = cv->w + (size_t)t * cin * coutp;
                    mac(acc, xs, ws, 1, cin, coutp);
                    requant_store(n, cv, acc, out->d + ((size_t)oy * out->w + ox) * cv->cout);
                }
        }
        return;
    }
    #pragma omp parallel for schedule(static)
    for (y = 0; y < H; y++) {
        int32_t acc[MAXC];
        const int16_t *xs[9], *ws[9];
        int x, ky, kx, co, nt;
        for (x = 0; x < W; x++) {
            for (co = 0; co < coutp; co++) acc[co] = cv->bias[co];
            nt = 0;
            if (n->mode == 1) {
                xs[0] = in->d + ((size_t)y * W + x) * cin;
                ws[0] = cv->w;
                nt = 1;
            } else {
                for (ky = 0; ky < 3; ky++) {
                    int iy = y + ky - 1;
                    if (iy < 0 || iy >= H) continue;
                    for (kx = 0; kx < 3; kx++) {
                        int ix = x + kx - 1;
                        if (ix < 0 || ix >= W) continue;
                        xs[nt] = in->d + ((size_t)iy * W + ix) * cin;
                        ws[nt] = cv->w + (size_t)(ky * 3 + kx) * cin * coutp;
                        nt++;
                    }
                }
            }
            mac(acc, xs, ws, nt, cin, coutp);
            requant_store(n, cv, acc, out->d + ((size_t)y * W + x) * cv->cout);
        }
    }
}

static void run_elt(const unet_node_t *n, const tensor_t *a, const tensor_t *b, tensor_t *out)
{
    int H = a->h, W = a->w;
    int y;

    #pragma omp parallel for schedule(static)
    for (y = 0; y < out->h; y++) {
        int x, c;
        for (x = 0; x < out->w; x++) {
            int16_t *o = out->d + ((size_t)y * out->w + x) * out->c;
            if (n->mode == 2) {                       /* MaxPool 2x2 */
                const int16_t *p = a->d + ((size_t)(2 * y) * W + 2 * x) * a->c;
                const int16_t *q = p + (size_t)W * a->c;
                for (c = 0; c < a->c; c++) {
                    int16_t m = p[c];
                    if (p[c + a->c] > m) m = p[c + a->c];
                    if (q[c] > m) m = q[c];
                    if (q[c + a->c] > m) m = q[c + a->c];
                    o[c] = m;
                }
            } else {
                const int16_t *p = a->d + ((size_t)y * W + x) * a->c;
                const int16_t *q = b->d + ((size_t)y * W + x) * b->c;
                if (n->mode == 0) {                   /* Add: one rounding */
                    for (c = 0; c < a->c; c++)
                        o[c] = sat(round_shift((int64_t)p[c] * n->m0 + (int64_t)q[c] * n->m1, n->shift),
                                   n->qmin, n->qmax);
                } else {                              /* Concat: each branch rounded */
                    for (c = 0; c < a->c; c++)
                        o[c] = sat(round_shift((int64_t)p[c] * n->m0, n->shift), n->qmin, n->qmax);
                    for (c = 0; c < b->c; c++)
                        o[a->c + c] = sat(round_shift((int64_t)q[c] * n->m1, n->shift), n->qmin, n->qmax);
                }
            }
        }
    }
    (void)H;
}

static int cmp_double(const void *a, const void *b)
{
    double x = *(const double *)a, y = *(const double *)b;
    return x < y ? -1 : (x > y);
}

int main(int argc, char **argv)
{
    static tensor_t ten[UNET_NODES_LEN];
    static conv_t   cvs[UNET_NODES_LEN];
    static double   layer_s[UNET_NODES_LEN];
    int      H, W, runs, show_layers = 0, i, r, threads = 1;
    size_t   npx;
    uint8_t *rgb;
    int8_t  *expect;
    double  *times, macs = 0.0;
    FILE    *f;
    long     mism = 0;

    if (argc < 5) {
        fprintf(stderr, "usage: %s rgb.raw expected.i8 H W [runs] [--layers]\n", argv[0]);
        return 2;
    }
    H = atoi(argv[3]);
    W = atoi(argv[4]);
    runs = argc > 5 && argv[5][0] != '-' ? atoi(argv[5]) : 5;
    for (i = 5; i < argc; i++) {
        if (strcmp(argv[i], "--layers") == 0) show_layers = 1;
#ifdef HAVE_BLOCKED
        if (strcmp(argv[i], "--noblock") == 0) use_blocked = 0;
#endif
    }
    npx = (size_t)H * W;
    rgb = malloc(npx * 3);
    expect = malloc(npx);
    f = fopen(argv[1], "rb");
    if (!f || fread(rgb, 1, npx * 3, f) != npx * 3) { fprintf(stderr, "bad %s\n", argv[1]); return 2; }
    fclose(f);
    f = fopen(argv[2], "rb");
    if (!f || fread(expect, 1, npx, f) != npx) { fprintf(stderr, "bad %s\n", argv[2]); return 2; }
    fclose(f);

    /* shapes: the 512-based table scaled to H x W, exactly as unet_set_size() does */
    for (i = 0; i < (int)UNET_NODES_LEN; i++) {
        const unet_node_t *n = &unet_nodes[i];
        ten[i].h = n->out_h * H / 512;
        ten[i].w = n->out_w * W / 512;
        ten[i].c = n->out_c;
        ten[i].d = calloc((size_t)ten[i].h * ten[i].w * ten[i].c, sizeof(int16_t));
        if (n->kind == UNET_KIND_CONV) {
            unpack_conv(n, unet_params + unet_param_off[i], &cvs[i]);
            if (n->mode == 0)
                cvs[i].pad = calloc((size_t)(ten[n->in0].h + 2) * (ten[n->in0].w + 2) * n->ca, sizeof(int16_t));
            macs += (double)ten[n->in0].h * ten[n->in0].w * cvs[i].taps * n->ca * n->cb;
        }
    }
    for (i = 0; i < (int)npx * 3; i++) {
        ten[0].d[i] = rgb[i];                     /* UINT8 input, scale 1/255 */
    }
#ifdef _OPENMP
    threads = omp_get_max_threads();
#endif
    times = calloc((size_t)runs + 1, sizeof(double));
    for (r = 0; r <= runs; r++) {                 /* run 0 = warm-up, not timed */
        double t0 = now_s();
        for (i = 1; i < (int)UNET_NODES_LEN; i++) {
            const unet_node_t *n = &unet_nodes[i];
            double tl = now_s();
            if (n->kind == UNET_KIND_CONV) {
                run_conv(n, &cvs[i], &ten[n->in0], &ten[i]);
            } else {
                run_elt(n, &ten[n->in0], n->in1 >= 0 ? &ten[n->in1] : NULL, &ten[i]);
            }
            if (r > 0) layer_s[i] += now_s() - tl;
        }
        times[r] = now_s() - t0;
        if (r > 0) printf("RUN %d %.3f\n", r, 1e3 * times[r]);   /* perf_j: per-run latency */
    }
    for (i = 0; i < (int)npx; i++) {
        if (ten[UNET_NODES_LEN - 1].d[i] != expect[i]) mism++;
    }
    qsort(times + 1, (size_t)runs, sizeof(double), cmp_double);
    {
        double sum = 0.0;
        for (r = 1; r <= runs; r++) sum += times[r];
        const char *impl = IMPL;
#ifdef HAVE_BLOCKED
        if (use_blocked) impl = "neon_blocked";
#endif
        printf("RESULT impl=%s res=%dx%d threads=%d runs=%d min_ms=%.2f median_ms=%.2f mean_ms=%.2f "
               "gmac=%.4f gmacps=%.3f mismatches=%ld/%zu %s\n",
               impl, H, W, threads, runs, 1e3 * times[1], 1e3 * times[1 + runs / 2], 1e3 * sum / runs,
               macs / 1e9, macs / 1e9 / times[1], mism, npx, mism == 0 ? "BIT_EXACT" : "MISMATCH");
    }
    if (show_layers) {
        for (i = 1; i < (int)UNET_NODES_LEN; i++) {
            const unet_node_t *n = &unet_nodes[i];
            printf("LAYER %2d kind=%d mode=%d in=%dx%dx%d out_c=%d ms=%.3f\n", i, n->kind, n->mode,
                   ten[n->in0].h, ten[n->in0].w, ten[n->in0].c, n->out_c, 1e3 * layer_s[i] / runs);
        }
    }
    return mism == 0 ? 0 : 1;
}
