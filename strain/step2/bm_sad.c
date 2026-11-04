/* Part E step 2: PS SAD block matching (plain C + AArch64 NEON). See bm_sad.h for the conventions. */
#include "bm_sad.h"

#include <string.h>

#if defined(__aarch64__)
#include <arm_neon.h>
#endif

void bm_sad_table_c(const uint8_t *a, int as, const uint8_t *b, int bs, int R, uint32_t *tab)
{
    const int n = 2 * R + 1;
    for (int dy = -R; dy <= R; dy++)
        for (int dx = -R; dx <= R; dx++) {
            const uint8_t *bb = b + dy * bs + dx;
            uint32_t s = 0;
            for (int y = 0; y < BM_T; y++)
                for (int x = 0; x < BM_T; x++) {
                    const int d = (int)a[y * as + x] - (int)bb[y * bs + x];
                    s += (uint32_t)(d < 0 ? -d : d);
                }
            tab[(dy + R) * n + dx + R] = s;
        }
}

#if defined(__aarch64__)
/* 16 template rows stay in registers; 4 candidates (adjacent dx) per pass give 4 independent accumulator chains.
 * Each u16 lane of an accumulator sums 2 pixels x 16 rows (<= 8160); three pairwise adds then give the per-candidate
 * total, at most 256 x 255 = 65280 < 65536, so u16 never overflows. */
#define BM_ROWS(X) X(0) X(1) X(2) X(3) X(4) X(5) X(6) X(7) X(8) X(9) X(10) X(11) X(12) X(13) X(14) X(15)

void bm_sad_table_neon(const uint8_t *a, int as, const uint8_t *b, int bs, int R, uint32_t *tab)
{
    const int n = 2 * R + 1;
#define LT(y) const uint8x16_t t##y = vld1q_u8(a + (y) * as);
    BM_ROWS(LT)
#undef LT
    for (int dy = -R; dy <= R; dy++) {
        const uint8_t *row = b + dy * bs;
        uint32_t *out = tab + (dy + R) * n + R;
        int dx = -R;
        for (; dx + 3 <= R; dx += 4) {
            const uint8_t *p = row + dx;
            uint16x8_t c0 = vdupq_n_u16(0), c1 = c0, c2 = c0, c3 = c0;
#define ACC4(y)                                                                    \
    {                                                                              \
        const uint8_t *q = p + (y) * bs;                                           \
        c0 = vpadalq_u8(c0, vabdq_u8(t##y, vld1q_u8(q)));                           \
        c1 = vpadalq_u8(c1, vabdq_u8(t##y, vld1q_u8(q + 1)));                       \
        c2 = vpadalq_u8(c2, vabdq_u8(t##y, vld1q_u8(q + 2)));                       \
        c3 = vpadalq_u8(c3, vabdq_u8(t##y, vld1q_u8(q + 3)));                       \
    }
            BM_ROWS(ACC4)
#undef ACC4
            uint16x8_t s = vpaddq_u16(vpaddq_u16(c0, c1), vpaddq_u16(c2, c3));
            s = vpaddq_u16(s, s);
            vst1q_u32(out + dx, vmovl_u16(vget_low_u16(s)));
        }
        for (; dx <= R; dx++) {
            const uint8_t *p = row + dx;
            uint16x8_t c0 = vdupq_n_u16(0);
#define ACC1(y) c0 = vpadalq_u8(c0, vabdq_u8(t##y, vld1q_u8(p + (y) * bs)));
            BM_ROWS(ACC1)
#undef ACC1
            out[dx] = vaddlvq_u16(c0);
        }
    }
}
/* Variant 2: 4 vertically adjacent candidates (dy .. dy + 3, same dx) share each B row load: 19 loads per 4
 * candidates instead of 64 (the 16-byte loads are unaligned for 15 of 16 dx). Same arithmetic as variant 1. */
void bm_sad_table_neon2(const uint8_t *a, int as, const uint8_t *b, int bs, int R, uint32_t *tab)
{
    const int n = 2 * R + 1;
#define LT(y) const uint8x16_t t##y = vld1q_u8(a + (y) * as);
    BM_ROWS(LT)
#undef LT
    for (int dx = -R; dx <= R; dx++) {
        const uint8_t *col = b + dx;
        uint32_t *out = tab + R * n + dx + R;
        int dy = -R;
        for (; dy + 3 <= R; dy += 4) {
            const uint8_t *p = col + dy * bs;
            uint16x8_t c0 = vdupq_n_u16(0), c1 = c0, c2 = c0, c3 = c0;
            uint8x16_t r;
            r = vld1q_u8(p + 0 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t0, r));
            r = vld1q_u8(p + 1 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t1, r)); c1 = vpadalq_u8(c1, vabdq_u8(t0, r));
            r = vld1q_u8(p + 2 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t2, r)); c1 = vpadalq_u8(c1, vabdq_u8(t1, r)); c2 = vpadalq_u8(c2, vabdq_u8(t0, r));
            r = vld1q_u8(p + 3 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t3, r)); c1 = vpadalq_u8(c1, vabdq_u8(t2, r)); c2 = vpadalq_u8(c2, vabdq_u8(t1, r)); c3 = vpadalq_u8(c3, vabdq_u8(t0, r));
            r = vld1q_u8(p + 4 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t4, r)); c1 = vpadalq_u8(c1, vabdq_u8(t3, r)); c2 = vpadalq_u8(c2, vabdq_u8(t2, r)); c3 = vpadalq_u8(c3, vabdq_u8(t1, r));
            r = vld1q_u8(p + 5 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t5, r)); c1 = vpadalq_u8(c1, vabdq_u8(t4, r)); c2 = vpadalq_u8(c2, vabdq_u8(t3, r)); c3 = vpadalq_u8(c3, vabdq_u8(t2, r));
            r = vld1q_u8(p + 6 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t6, r)); c1 = vpadalq_u8(c1, vabdq_u8(t5, r)); c2 = vpadalq_u8(c2, vabdq_u8(t4, r)); c3 = vpadalq_u8(c3, vabdq_u8(t3, r));
            r = vld1q_u8(p + 7 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t7, r)); c1 = vpadalq_u8(c1, vabdq_u8(t6, r)); c2 = vpadalq_u8(c2, vabdq_u8(t5, r)); c3 = vpadalq_u8(c3, vabdq_u8(t4, r));
            r = vld1q_u8(p + 8 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t8, r)); c1 = vpadalq_u8(c1, vabdq_u8(t7, r)); c2 = vpadalq_u8(c2, vabdq_u8(t6, r)); c3 = vpadalq_u8(c3, vabdq_u8(t5, r));
            r = vld1q_u8(p + 9 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t9, r)); c1 = vpadalq_u8(c1, vabdq_u8(t8, r)); c2 = vpadalq_u8(c2, vabdq_u8(t7, r)); c3 = vpadalq_u8(c3, vabdq_u8(t6, r));
            r = vld1q_u8(p + 10 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t10, r)); c1 = vpadalq_u8(c1, vabdq_u8(t9, r)); c2 = vpadalq_u8(c2, vabdq_u8(t8, r)); c3 = vpadalq_u8(c3, vabdq_u8(t7, r));
            r = vld1q_u8(p + 11 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t11, r)); c1 = vpadalq_u8(c1, vabdq_u8(t10, r)); c2 = vpadalq_u8(c2, vabdq_u8(t9, r)); c3 = vpadalq_u8(c3, vabdq_u8(t8, r));
            r = vld1q_u8(p + 12 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t12, r)); c1 = vpadalq_u8(c1, vabdq_u8(t11, r)); c2 = vpadalq_u8(c2, vabdq_u8(t10, r)); c3 = vpadalq_u8(c3, vabdq_u8(t9, r));
            r = vld1q_u8(p + 13 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t13, r)); c1 = vpadalq_u8(c1, vabdq_u8(t12, r)); c2 = vpadalq_u8(c2, vabdq_u8(t11, r)); c3 = vpadalq_u8(c3, vabdq_u8(t10, r));
            r = vld1q_u8(p + 14 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t14, r)); c1 = vpadalq_u8(c1, vabdq_u8(t13, r)); c2 = vpadalq_u8(c2, vabdq_u8(t12, r)); c3 = vpadalq_u8(c3, vabdq_u8(t11, r));
            r = vld1q_u8(p + 15 * bs); c0 = vpadalq_u8(c0, vabdq_u8(t15, r)); c1 = vpadalq_u8(c1, vabdq_u8(t14, r)); c2 = vpadalq_u8(c2, vabdq_u8(t13, r)); c3 = vpadalq_u8(c3, vabdq_u8(t12, r));
            r = vld1q_u8(p + 16 * bs); c1 = vpadalq_u8(c1, vabdq_u8(t15, r)); c2 = vpadalq_u8(c2, vabdq_u8(t14, r)); c3 = vpadalq_u8(c3, vabdq_u8(t13, r));
            r = vld1q_u8(p + 17 * bs); c2 = vpadalq_u8(c2, vabdq_u8(t15, r)); c3 = vpadalq_u8(c3, vabdq_u8(t14, r));
            r = vld1q_u8(p + 18 * bs); c3 = vpadalq_u8(c3, vabdq_u8(t15, r));
            uint16x8_t s = vpaddq_u16(vpaddq_u16(c0, c1), vpaddq_u16(c2, c3));
            s = vpaddq_u16(s, s);
            out[dy * n] = vgetq_lane_u16(s, 0);
            out[(dy + 1) * n] = vgetq_lane_u16(s, 1);
            out[(dy + 2) * n] = vgetq_lane_u16(s, 2);
            out[(dy + 3) * n] = vgetq_lane_u16(s, 3);
        }
        for (; dy <= R; dy++) {
            const uint8_t *p = col + dy * bs;
            uint16x8_t c0 = vdupq_n_u16(0);
#define ACC1(y) c0 = vpadalq_u8(c0, vabdq_u8(t##y, vld1q_u8(p + (y) * bs)));
            BM_ROWS(ACC1)
#undef ACC1
            out[dy * n] = vaddlvq_u16(c0);
        }
    }
}
#endif

/* tb_bm.cpp order in d = -q / 4: smaller SAD, smaller |dx| + |dy|, larger dx, larger dy */
static int better(uint32_t s, int dx, int dy, uint32_t bs, int bdx, int bdy)
{
    if (s != bs) return s < bs;
    const int ma = (dx < 0 ? -dx : dx) + (dy < 0 ? -dy : dy), mb = (bdx < 0 ? -bdx : bdx) + (bdy < 0 ? -bdy : bdy);
    if (ma != mb) return ma < mb;
    if (dx != bdx) return dx > bdx;
    return dy > bdy;
}

/* parabola vertex through (-1, l), (0, c), (1, r) in 1/256, rounded half away from zero; c is the minimum */
static int parab_q8(uint32_t l, uint32_t c, uint32_t r)
{
    const int64_t den = (int64_t)l + r - 2 * (int64_t)c;
    if (den <= 0) return 0;
    const int64_t num = ((int64_t)l - (int64_t)r) * 128;
    int64_t f = num >= 0 ? (num + den / 2) / den : -((-num + den / 2) / den);
    if (f > 128) f = 128;
    if (f < -128) f = -128;
    return (int)f;
}

/* equiangular fit: two lines of equal and opposite slope through the three points; same rounding as parab_q8 */
static int vfit_q8(uint32_t l, uint32_t c, uint32_t r)
{
    const int64_t den = (int64_t)(l > r ? l : r) - (int64_t)c;
    if (den <= 0) return 0;
    const int64_t num = ((int64_t)l - (int64_t)r) * 128;
    int64_t f = num >= 0 ? (num + den / 2) / den : -((-num + den / 2) / den);
    if (f > 128) f = 128;
    if (f < -128) f = -128;
    return (int)f;
}

void bm_argmin(const uint32_t *tab, int R, bm_best_t *o) { bm_argmin_opt(tab, R, 0, o); }

void bm_argmin_opt(const uint32_t *tab, int R, int opts, bm_best_t *o)
{
    int (*fit)(uint32_t, uint32_t, uint32_t) = (opts & BM_OPT_VFIT) ? vfit_q8 : parab_q8;
    const int n = 2 * R + 1;
    uint32_t bs = tab[0];
    for (int i = 1; i < n * n; i++)
        if (tab[i] < bs) bs = tab[i];
    int bdx = R + 1, bdy = 0;      /* sentinel: loses every tie-break against a real candidate */
    for (int dy = -R; dy <= R; dy++)
        for (int dx = -R; dx <= R; dx++) {
            const uint32_t s = tab[(dy + R) * n + dx + R];
            if (s == bs && (bdx > R || better(s, dx, dy, bs, bdx, bdy))) {
                bs = s;
                bdx = dx;
                bdy = dy;
            }
        }
    o->dx = bdx;
    o->dy = bdy;
    o->sad = bs;
    o->fx = o->fy = o->flags = 0;
    const uint32_t *c = tab + (bdy + R) * n + bdx + R;
    if (bdx == -R || bdx == R)
        o->flags |= BM_BORDER_X;
    else
        o->fx = fit(c[-1], c[0], c[1]);
    if (bdy == -R || bdy == R)
        o->flags |= BM_BORDER_Y;
    else
        o->fy = fit(c[-n], c[0], c[n]);
}

int bm_track_points(const uint8_t *A, const uint8_t *B, int w, int h, int stride, int n, int R, int opts,
                    int32_t *x_q8, int32_t *y_q8, uint32_t *sad, int32_t *flags)
{
    uint32_t tab[(2 * BM_RMAX + 1) * (2 * BM_RMAX + 1)];
    int edge = 0;
    (void)opts;
    if (R < 1 || R > BM_RMAX) return -1;
    for (int i = 0; i < n; i++) {
        const int tx = ((x_q8[i] + 128) >> 8) - BM_T / 2, ty = ((y_q8[i] + 128) >> 8) - BM_T / 2;
        if (tx - R < 0 || ty - R < 0 || tx + BM_T + R > w || ty + BM_T + R > h) {
            sad[i] = 0;
            flags[i] = BM_EDGE;
            edge++;
            continue;
        }
        const uint8_t *a = A + ty * stride + tx, *b = B + ty * stride + tx;
#if defined(__aarch64__)
        if (opts & BM_OPT_NEON)
            bm_sad_table_neon2(a, stride, b, stride, R, tab);
        else
#endif
            bm_sad_table_c(a, stride, b, stride, R, tab);
        bm_best_t o;
        bm_argmin_opt(tab, R, opts, &o);
        x_q8[i] += o.dx * 256 + o.fx;
        y_q8[i] += o.dy * 256 + o.fy;
        sad[i] = o.sad;
        flags[i] = o.flags;
    }
    return edge;
}

int bm_track_line(const uint8_t *A, const uint8_t *B, int w, int h, int stride, int n, int R, int k, int opts,
                  const int32_t *ax_q8, const int32_t *ay_q8, int32_t *x_q8, int32_t *y_q8, uint32_t *sad,
                  int32_t *flags)
{
    enum { NC = (2 * BM_RMAX + 1) * (2 * BM_RMAX + 1) };
    static uint32_t tabs[BM_NMAX][NC];
    int ox[BM_NMAX], oy[BM_NMAX], ok[BM_NMAX];
    const int nc = (2 * R + 1) * (2 * R + 1);
    int edge = 0;
    if (R < 1 || R > BM_RMAX || n < 0 || n > BM_NMAX || k < 1) return -1;
    (void)opts;
    for (int i = 0; i < n; i++) {
        const int tx = ((ax_q8[i] + 128) >> 8) - BM_T / 2, ty = ((ay_q8[i] + 128) >> 8) - BM_T / 2;
        ox[i] = (x_q8[i] - ax_q8[i] + 128) >> 8;
        oy[i] = (y_q8[i] - ay_q8[i] + 128) >> 8;
        const int bx = tx + ox[i], by = ty + oy[i];
        ok[i] = tx >= 0 && ty >= 0 && tx + BM_T <= w && ty + BM_T <= h &&
                bx - R >= 0 && by - R >= 0 && bx + BM_T + R <= w && by + BM_T + R <= h;
        if (!ok[i]) continue;
        const uint8_t *a = A + ty * stride + tx, *b = B + by * stride + bx;
#if defined(__aarch64__)
        if (opts & BM_OPT_NEON)
            bm_sad_table_neon2(a, stride, b, stride, R, tabs[i]);
        else
#endif
            bm_sad_table_c(a, stride, b, stride, R, tabs[i]);
    }
    for (int i = 0; i < n; i++) {
        if (!ok[i]) {
            sad[i] = 0;
            flags[i] = BM_EDGE;
            edge++;
            continue;
        }
        uint32_t s[NC];
        memcpy(s, tabs[i], sizeof(uint32_t) * nc);
        const int lo = i - k / 2 < 0 ? 0 : i - k / 2, hi = i + k / 2 >= n ? n - 1 : i + k / 2;
        for (int j = lo; j <= hi; j++)
            if (j != i && ok[j] && ox[j] == ox[i] && oy[j] == oy[i])
                for (int c = 0; c < nc; c++) s[c] += tabs[j][c];
        bm_best_t o;
        bm_argmin_opt(s, R, opts, &o);
        x_q8[i] = ax_q8[i] + (ox[i] + o.dx) * 256 + o.fx;
        y_q8[i] = ay_q8[i] + (oy[i] + o.dy) * 256 + o.fy;
        sad[i] = o.sad;
        flags[i] = o.flags;
    }
    return edge;
}
