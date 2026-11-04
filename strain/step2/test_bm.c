/* Part E step 2: bit-exact test of the PS block-matching kernel (bm_sad.c) against the step-1 reference
 * (strain/step1/tb_bm.cpp: ref_sad() and its raw-SAD argmin), on the step-1 CAMUS VAL blocks (dump.py) + random data.
 * On AArch64 both NEON kernels are checked against the C kernel and the reference as well (board run).
 *
 *   test_bm <step1 data dir>     -> prints BM_PS_BIT_EXACT or BM_PS_FAIL
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "bm_sad.h"

#define T BM_T

/* tb_bm.cpp ref_sad(), verbatim except that the window margin M is a parameter (tb_bm.cpp: M = 8) */
static int ceil4(int q) { return -((-q) >> 2); }
static unsigned ref_sad(const unsigned char *t, const unsigned char *w, int W, int M, int qx, int qy)
{
    const int cy = ceil4(qy), cx = ceil4(qx), fy = (-qy) & 3, fx = (-qx) & 3;
    unsigned s = 0;
    for (int y = 0; y < T; y++)
        for (int x = 0; x < T; x++) {
            const int sy = y + M - cy, sx = x + M - cx;
            const int b00 = w[sy * W + sx], b01 = w[sy * W + sx + 1];
            const int b10 = w[(sy + 1) * W + sx], b11 = w[(sy + 1) * W + sx + 1];
            const int h0 = b00 * 4 + fx * (b01 - b00), h1 = b10 * 4 + fx * (b11 - b10);
            const int g = (h0 * 4 + fy * (h1 - h0) + 8) >> 4;
            const int d = t[y * T + x] - g;
            s += d < 0 ? -d : d;
        }
    return s;
}

static long n_cand, n_bad, n_best, n_best_bad;

/* check one block: template at (tx, ty) of a, window around it in b (both n x n, stride n), radius R.
 * Reference = tb_bm.cpp formula at q = -4 d and its argmin loop (in q). Returns the kernel's best. */
static bm_best_t check_block(const unsigned char *a, const unsigned char *b, int n, int tx, int ty, int R)
{
    const int M = R + 1, W = T + 2 * M, nc = 2 * R + 1;
    static unsigned char t[T * T], w[(T + 2 * (BM_RMAX + 1)) * (T + 2 * (BM_RMAX + 1))];
    static uint32_t tab[(2 * BM_RMAX + 1) * (2 * BM_RMAX + 1)], tab2[(2 * BM_RMAX + 1) * (2 * BM_RMAX + 1)],
                    tab3[(2 * BM_RMAX + 1) * (2 * BM_RMAX + 1)];
    for (int y = 0; y < T; y++)
        for (int x = 0; x < T; x++) t[y * T + x] = a[(ty + y) * n + tx + x];
    /* the reference reads one column / row past the window at fx = 0 (weight 0); keep it inside the image */
    for (int y = 0; y < W; y++)
        for (int x = 0; x < W; x++) {
            const int yy = ty - M + y, xx = tx - M + x;
            w[y * W + x] = (yy >= 0 && yy < n && xx >= 0 && xx < n) ? b[yy * n + xx] : 0;
        }
    bm_sad_table_c(a + ty * n + tx, n, b + ty * n + tx, n, R, tab);
#if defined(__aarch64__)
    bm_sad_table_neon(a + ty * n + tx, n, b + ty * n + tx, n, R, tab2);
    bm_sad_table_neon2(a + ty * n + tx, n, b + ty * n + tx, n, R, tab3);
#else
    memcpy(tab2, tab, sizeof(uint32_t) * nc * nc);
    memcpy(tab3, tab, sizeof(uint32_t) * nc * nc);
#endif
    unsigned bs = 0xFFFFFFFFu;
    int bqx = 0, bqy = 0;
    for (int dy = -R; dy <= R; dy++)
        for (int dx = -R; dx <= R; dx++) {
            const int qx = -4 * dx, qy = -4 * dy;
            const unsigned want = ref_sad(t, w, W, M, qx, qy), s = tab[(dy + R) * nc + dx + R];
            n_cand++;
            if (s != want || tab2[(dy + R) * nc + dx + R] != want || tab3[(dy + R) * nc + dx + R] != want) {
                if (n_bad < 10)
                    printf("FAIL blk (%d,%d) R %d d(%d,%d): C %u NEON %u ref %u\n", tx, ty, R, dx, dy, s,
                           tab2[(dy + R) * nc + dx + R], want);
                n_bad++;
            }
            /* tb_bm.cpp argmin, verbatim */
            const int ma = abs(qx) + abs(qy), mb = abs(bqx) + abs(bqy);
            if (want < bs || (want == bs && (ma < mb || (ma == mb && (qx < bqx || (qx == bqx && qy < bqy)))))) {
                bs = want;
                bqx = qx;
                bqy = qy;
            }
        }
    bm_best_t o, o2;
    bm_argmin(tab, R, &o);
    bm_argmin(tab2, R, &o2);
    n_best++;
    if (o.dx != -bqx / 4 || o.dy != -bqy / 4 || o.sad != bs || memcmp(&o, &o2, sizeof o)) {
        if (n_best_bad < 10) printf("FAIL best blk (%d,%d) R %d: kernel (%d,%d) %u ref q(%d,%d) %u\n", tx, ty, R, o.dx, o.dy, o.sad, bqx, bqy, bs);
        n_best_bad++;
    }
    return o;
}

static int read_raw(const char *p, unsigned char *v, size_t n)
{
    FILE *f = fopen(p, "rb");
    if (!f) return 0;
    const int ok = fread(v, 1, n, f) == n;
    fclose(f);
    return ok;
}

static uint32_t rng = 12345u;
static uint32_t rnd(void) { rng = rng * 1664525u + 1013904223u; return rng >> 8; }

int main(int argc, char **argv)
{
    const char *dir = argc > 1 ? argv[1] : "../step1/data/";
    char path[512], name[64], fa[128], fb[128];
    snprintf(path, sizeof path, "%s/pairs.txt", dir);
    FILE *pl = fopen(path, "r");
    if (!pl) { printf("no %s\n", path); return 2; }
    static unsigned char a[512 * 512], b[512 * 512], a1[512 * 512], b1[512 * 512];
    static const int radii[] = {4, 6, 7, 8};
    long blocks = 0, s1_n = 0, s1_hit = 0, s1_hit_sub = 0;
    int n, K;
    while (fscanf(pl, "%63s %d %127s %127s %d", name, &n, fa, fb, &K) == 5) {
        snprintf(path, sizeof path, "%s/%s", dir, fa);
        if (!read_raw(path, a, (size_t)n * n)) return 3;
        snprintf(path, sizeof path, "%s/%s", dir, fb);
        if (!read_raw(path, b, (size_t)n * n)) return 3;
        for (int i = 0; i < n * n; i++) { a1[i] = a[i] >> 1; b1[i] = b[i] >> 1; }
        const int s1 = name[1] == '1';
        for (int k = 0; k < K; k++) {
            int cx, cy;
            if (fscanf(pl, "%d %d", &cx, &cy) != 2) return 4;
            const int tx = cx - T / 2, ty = cy - T / 2;
            blocks++;
            for (int ri = 0; ri < 4; ri++) {
                const int R = radii[ri];
                if (tx - R < 0 || ty - R < 0 || tx + T + R > n || ty + T + R > n) continue;
                /* the step-1 PL data path (pixels >> 1) and full 8-bit pixels */
                check_block(a1, b1, n, tx, ty, R);
                const bm_best_t o = check_block(a, b, n, tx, ty, R);
                /* set 1: B(y, x) = A(y + 2, x - 3) -> d = (+3, -2), SAD 0 */
                if (s1 && R == 6) {
                    s1_n++;
                    s1_hit += o.dx == 3 && o.dy == -2 && o.sad == 0;
                    s1_hit_sub += o.dx == 3 && o.dy == -2 && o.fx == 0 && o.fy == 0;
                }
            }
        }
    }
    fclose(pl);
    const long real_cand = n_cand;
    /* random images / positions / radii 1..15, incl. constant regions (ties) and saturated pixels */
    for (int it = 0; it < 400; it++) {
        const int nn = 64 + (int)(rnd() % 3) * 32, mode = (int)(rnd() % 5);   /* 4: a = 255, b = 0 -> SAD 65280 (u16 limit) */
        for (int i = 0; i < nn * nn; i++) {
            const unsigned r = rnd();
            a[i] = mode == 4 ? 255 : mode == 0 ? (unsigned char)r : mode == 1 ? (unsigned char)((r & 1) * 255) : mode == 2 ? (unsigned char)(r % 3) : (unsigned char)(255 - (r & 7));
            b[i] = mode == 4 ? 0 : mode == 0 ? (unsigned char)(r >> 8) : mode == 1 ? (unsigned char)(((r >> 9) & 1) * 255) : mode == 2 ? (unsigned char)((r >> 8) % 3) : (unsigned char)(255 - ((r >> 8) & 7));
        }
        const int R = 1 + (int)(rnd() % BM_RMAX);
        const int span = nn - T - 2 * R;
        if (span <= 0) continue;
        const int tx = R + (int)(rnd() % span), ty = R + (int)(rnd() % span);
        check_block(a, b, nn, tx, ty, R);
    }
    /* sub-pixel fits on 3 x 3 tables with a strict minimum in the centre, vs the double-precision formulas */
    long fit_n = 0, fit_bad = 0;
    for (int it = 0; it < 200000; it++) {
        uint32_t tab[9];
        const uint32_t c = rnd() % 60000;
        for (int i = 0; i < 9; i++) tab[i] = c + 1 + rnd() % (it & 1 ? 20 : 5000);
        tab[4] = c;
        for (int v = 0; v < 2; v++) {
            bm_best_t o;
            bm_argmin_opt(tab, 1, v ? BM_OPT_VFIT : 0, &o);
            const double l = tab[3], r = tab[5], u = tab[1], d = tab[7];
            const double ex = v ? (l - r) / (2 * ((l > r ? l : r) - c)) : (l - r) / (2 * (l + r - 2.0 * c));
            const double ey = v ? (u - d) / (2 * ((u > d ? u : d) - c)) : (u - d) / (2 * (u + d - 2.0 * c));
            const double wx = ex * 256 < 0 ? -(double)(long)(-ex * 256 + 0.5) : (double)(long)(ex * 256 + 0.5);
            const double wy = ey * 256 < 0 ? -(double)(long)(-ey * 256 + 0.5) : (double)(long)(ey * 256 + 0.5);
            fit_n++;
            /* exact rational rounding vs double: allow a 1/256 difference only at an exact .5 tie */
            if (o.dx != 0 || o.dy != 0 || (o.fx != wx && fabs(o.fx - ex * 256) != 0.5) || (o.fy != wy && fabs(o.fy - ey * 256) != 0.5)) {
                if (fit_bad < 5) printf("FAIL fit %d: (%d,%d) vs (%.3f,%.3f)\n", v, o.fx, o.fy, ex * 256, ey * 256);
                fit_bad++;
            }
        }
    }
    printf("sub-pixel fits (parabola, V) vs double formula: %ld, mismatches %ld\n", fit_n, fit_bad);
    n_bad += fit_bad;
    /* bm_track_line with anchor = prediction, k = 1 == bm_track_points; k = 9 C vs NEON (board) */
    snprintf(path, sizeof path, "%s/s1_0_a.raw", dir);
    read_raw(path, a, 512 * 512);
    snprintf(path, sizeof path, "%s/s1_0_b.raw", dir);
    read_raw(path, b, 512 * 512);
    {
        static int32_t ax[BM_NMAX], ay[BM_NMAX], x0[BM_NMAX], y0[BM_NMAX], x1[BM_NMAX], y1[BM_NMAX], f0[BM_NMAX], f1[BM_NMAX];
        static uint32_t s0[BM_NMAX], s1v[BM_NMAX];
        long line_bad = 0;
        for (int opt = 0; opt <= BM_OPT_VFIT; opt += BM_OPT_VFIT) {
            for (int i = 0; i < 300; i++) {   /* a line through the frame plus random points (some at the edge) */
                ax[i] = x0[i] = x1[i] = i < 150 ? (100 + 2 * i) * 256 + (int)(rnd() % 256) : (int)(rnd() % (512 * 256));
                ay[i] = y0[i] = y1[i] = i < 150 ? (120 + i) * 256 + (int)(rnd() % 256) : (int)(rnd() % (512 * 256));
            }
            bm_track_points(a, b, 512, 512, 512, 300, 6, opt, x0, y0, s0, f0);
            bm_track_line(a, b, 512, 512, 512, 300, 6, 1, opt, ax, ay, x1, y1, s1v, f1);
            line_bad += memcmp(x0, x1, 300 * 4) || memcmp(y0, y1, 300 * 4) || memcmp(s0, s1v, 300 * 4) || memcmp(f0, f1, 300 * 4);
#if defined(__aarch64__)
            for (int i = 0; i < 300; i++) {   /* prediction = anchor + up to +-6 px */
                x0[i] = x1[i] = ax[i] + (int)(rnd() % 3073) - 1536;
                y0[i] = y1[i] = ay[i] + (int)(rnd() % 3073) - 1536;
            }
            bm_track_line(a, b, 512, 512, 512, 300, 6, 9, opt, ax, ay, x0, y0, s0, f0);
            bm_track_line(a, b, 512, 512, 512, 300, 6, 9, opt | BM_OPT_NEON, ax, ay, x1, y1, s1v, f1);
            line_bad += memcmp(x0, x1, 300 * 4) || memcmp(y0, y1, 300 * 4) || memcmp(s0, s1v, 300 * 4) || memcmp(f0, f1, 300 * 4);
#endif
        }
        printf("bm_track_line (k 1 == bm_track_points%s): mismatches %ld\n",
#if defined(__aarch64__)
               "; k 9 C == NEON",
#else
               "",
#endif
               line_bad);
        n_bad += line_bad;
    }
#if defined(__aarch64__)
    /* whole tracking step, C vs NEON (positions, SAD, flags), on set-1 frame 0 */
    {
        for (int opt = 0; opt <= BM_OPT_VFIT; opt += BM_OPT_VFIT) {
            int32_t x0[300], y0[300], x1[300], y1[300], f0[300], f1[300];
            uint32_t s0[300], s1v[300];
            for (int i = 0; i < 300; i++) { x0[i] = x1[i] = (int)(rnd() % (512 * 256)); y0[i] = y1[i] = (int)(rnd() % (512 * 256)); }
            bm_track_points(a, b, 512, 512, 512, 300, 8, opt, x0, y0, s0, f0);
            bm_track_points(a, b, 512, 512, 512, 300, 8, opt | BM_OPT_NEON, x1, y1, s1v, f1);
            if (memcmp(x0, x1, sizeof x0) || memcmp(y0, y1, sizeof y0) || memcmp(s0, s1v, sizeof s0) || memcmp(f0, f1, sizeof f0)) {
                printf("FAIL track C != NEON (opts %d)\n", opt);
                n_bad++;
            }
        }
    }
    const char *arch = "aarch64: C + NEON";
#else
    const char *arch = "host: C";
#endif
    printf("%s; step-1 blocks %ld, candidates %ld (CAMUS VAL) + %ld (random), SAD mismatches %ld; argmin %ld, mismatches %ld\n",
           arch, blocks, real_cand, n_cand - real_cand, n_bad, n_best, n_best_bad);
    printf("set 1 known shift (+3,-2) at R 6: integer %ld / %ld, sub-pixel offset 0: %ld / %ld\n", s1_hit, s1_n, s1_hit_sub, s1_n);
    const int ok = n_bad == 0 && n_best_bad == 0 && s1_hit == s1_n && s1_n == 120;
    printf(ok ? "BM_PS_BIT_EXACT\n" : "BM_PS_FAIL\n");
    return !ok;
}
