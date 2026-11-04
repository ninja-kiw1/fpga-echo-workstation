/* Part E step 2: PS SAD block matching for speckle tracking (16 x 16 template, full search +-R).
 *
 * Convention (same as strain/step1/tb_bm.cpp): the template T is the 16 x 16 block of frame A with top-left (tx, ty);
 * candidate (dx, dy) compares T with frame B at (tx + dx, ty + dy). tb_bm.cpp works in quarter pixels with
 * q = -4 d (its G(p) = B(p - q / 4)), so the raw-SAD argmin there is, for d:
 *     smaller SAD, then smaller |dx| + |dy|, then larger dx, then larger dy        (= its smaller |q|1, qx, qy).
 * bm_argmin() uses exactly this total order. Sub-pixel: separable parabola through the argmin and its two
 * neighbours on each axis, integer arithmetic in 1/256 px (identical on every platform). Option BM_OPT_VFIT uses the
 * equiangular (V) fit instead, which suits the V-shaped SAD minimum better (parabola: up to 0.13 px bias towards
 * the integer on CAMUS VAL frames, exp/subpix_bias.py; V fit 2-3 x less).
 *
 * All kernels require the whole search window inside B: tx - R >= 0, ty - R >= 0, tx + 16 + R <= w, ty + 16 + R <= h
 * (bm_track_points() checks this and flags the point instead).
 */
#ifndef BM_SAD_H
#define BM_SAD_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BM_T 16          /* template size */
#define BM_RMAX 15       /* largest search radius */

/* tab[(dy + R) * (2R + 1) + (dx + R)] = SAD(T, B at (dx, dy)).  a / b point at (tx, ty) in A / B. */
void bm_sad_table_c(const uint8_t *a, int as, const uint8_t *b, int bs, int R, uint32_t *tab);
#if defined(__aarch64__)
void bm_sad_table_neon(const uint8_t *a, int as, const uint8_t *b, int bs, int R, uint32_t *tab);
void bm_sad_table_neon2(const uint8_t *a, int as, const uint8_t *b, int bs, int R, uint32_t *tab);  /* row reuse */
#endif

typedef struct {
    int dx, dy;          /* integer argmin */
    uint32_t sad;        /* its SAD */
    int fx, fy;          /* sub-pixel offset, 1/256 px, in [-128, 128] (0 when the argmin is on the border) */
    int flags;           /* BM_BORDER_X / BM_BORDER_Y: argmin on the search border (no parabola on that axis) */
} bm_best_t;

#define BM_BORDER_X 1
#define BM_BORDER_Y 2
#define BM_EDGE 4        /* bm_track_points: search window leaves the image, point not moved */

#define BM_OPT_NEON 1    /* trackers: NEON kernel bm_sad_table_neon2 (board 10-17: 97 vs 183 cycles / candidate) */
#define BM_OPT_VFIT 2    /* equiangular sub-pixel fit instead of the parabola */

void bm_argmin(const uint32_t *tab, int R, bm_best_t *out);               /* parabola */
void bm_argmin_opt(const uint32_t *tab, int R, int opts, bm_best_t *out);

/* Frame-to-frame tracking of n points. Positions are block centres in 1/256 px (x_q8, y_q8 in / out);
 * the template is the 16 x 16 block of A whose top-left is round(pos) - 8. Out: new positions, best SAD, flags.
 * opts: BM_OPT_*. Returns the number of BM_EDGE points. */
int bm_track_points(const uint8_t *A, const uint8_t *B, int w, int h, int stride, int n, int R, int opts,
                    int32_t *x_q8, int32_t *y_q8, uint32_t *sad, int32_t *flags);

/* Anchored tracking step for n points along a line (n <= BM_NMAX), with SAD aggregation.
 * Template of point i: the 16 x 16 block of the anchor frame A whose top-left is round(anchor_i) - 8; search window
 * in B centred on the integer offset o_i = round(pred_i - anchor_i) (pred = x_q8 / y_q8 on input). The SAD tables of
 * the k line neighbours (i - k/2 .. i + k/2) that have the same o_i are summed before the argmin, so one estimate uses
 * k blocks (k = 1: none). Output x_q8 / y_q8 = anchor + 256 o + argmin + sub-pixel. Points whose template or window
 * leaves the image keep their prediction and get BM_EDGE. With A = previous frame, anchor = pred and k = 1 this is
 * exactly bm_track_points(). Returns the number of BM_EDGE points, -1 on bad arguments. */
#define BM_NMAX 512
int bm_track_line(const uint8_t *A, const uint8_t *B, int w, int h, int stride, int n, int R, int k, int opts,
                  const int32_t *ax_q8, const int32_t *ay_q8, int32_t *x_q8, int32_t *y_q8, uint32_t *sad,
                  int32_t *flags);

#ifdef __cplusplus
}
#endif

#endif
