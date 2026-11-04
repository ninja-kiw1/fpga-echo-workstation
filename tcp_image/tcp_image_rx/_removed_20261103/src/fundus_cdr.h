/* fundus_cdr.h -- part D: optic disc / cup around two PL programs and the vertical cup-to-disc ratio on the PS.
 *
 * Same integer rules as E:/PLD/fundus_cdr/cdr_ref.py (checked bit for bit by test/fundus_cdr/compare.py):
 *   stage 1  payload1 = 4 x 4 box mean of the 512 x 512 RGB image (round half up)  -> program fundus_disc
 *   locate   largest 8-connected component of (logit >= thr) on the 128 grid -> bounding-box centre / size (512 px)
 *   window   side x side window of the 512 image centred there, clamped inside the image;
 *            side = fcdr_side_for(size) = 128 k, smallest k in 1..4 with side >= RATIO * size
 *   stage 2  payload2 = k x k box mean of the window (k = side / 128)         -> fundus_disc, fundus_cup
 *   masks    bilinear x k of the logits (half-pixel centres, edge clamp, integer weights), keep value >= thr - 1/2;
 *            disc = fill_holes(largest_cc(disc)); cup = fill_holes(largest_cc(cup AND disc))
 *   vCDR     rows spanned by the cup / rows spanned by the disc (a vertical ratio: the host's anisotropic
 *            stretch to 512 x 512 does not change it)
 * Connectivity: components 8-connected, numbered in raster order of their first pixel, ties -> lower number;
 * holes = background not 4-connected to the border.
 * Measurement aid only (辅助测量与复核): no glaucoma decision is made here.
 */
#ifndef FUNDUS_CDR_H
#define FUNDUS_CDR_H

#include <stdint.h>

#define FCDR_PAY         128   /* program payload side (512 >> 2) */
#define FCDR_RATIO_NUM   2     /* stage-2 window side >= NUM / DEN * stage-1 disc size */
#define FCDR_RATIO_DEN   1
#define FCDR_MIN_DISC_PX 4     /* stage-1 component smaller than this = disc not found */

typedef struct {
    int cy, cx;         /* bounding-box centre, 512 grid (rounded: 2 * (r0 + r1 + 1)) */
    int size;           /* 4 * max(bbox height, bbox width) on the 128 grid */
    int area;           /* pixels of the component on the 128 grid */
    int r0, r1, c0, c1; /* bounding box on the 128 grid */
} fcdr_loc_t;

typedef struct {
    int    disc_h, cup_h;        /* rows spanned in the window (512-grid pixels); 0 = empty */
    int    disc_top, cup_top;    /* first row in the window, -1 = empty */
    long   disc_area, cup_area;  /* window pixels */
    double vcdr;                 /* cup_h / disc_h; valid only when disc_h > 0 */
} fcdr_result_t;

/** @brief k x k box mean (round half up) of the cw x cw window at (x0, y0) of an RGB image with sw pixels per row
 *         -> (cw / k)^2 RGB pixels. k = 1 copies. */
void fcdr_box_rgb(const uint8_t *src, int sw, int x0, int y0, int cw, int k, uint8_t *dst);

/** @brief Keep the largest 8-connected component of m (h x w, 0 / 1) in place.
 *  @return pixels kept (0 = empty), -1 out of memory (m unchanged). */
long fcdr_largest_cc(uint8_t *m, int h, int w);

/** @brief Fill holes of m in place. @return 0, -1 out of memory. */
int fcdr_fill_holes(uint8_t *m, int h, int w);

/** @brief Bilinear x k of n x n int8 logits into out (nk x nk, 0 / 1): value >= thr - 1/2 (k = 1: q >= thr). */
void fcdr_up_mask(const int8_t *q, int n, int k, int thr, uint8_t *out);

/** @brief Stage-1 logits (128 x 128) -> disc location. @return 1 found, 0 not found, -1 out of memory. */
int fcdr_locate(const int8_t *q1, int thr, fcdr_loc_t *loc);

/** @brief Stage-2 window side (128, 256, 384 or 512) for a stage-1 disc size in 512-grid pixels. */
int fcdr_side_for(int size);

/** @brief Top-left of the side x side window centred on (cy, cx), clamped inside 512 x 512. */
void fcdr_window(int cy, int cx, int side, int *y0, int *x0);

/**
 * @brief Stage-2 logits (128 x 128 each) -> cleaned masks + vertical diameters + vCDR.
 * @param disc, cup  side x side outputs (0 / 1); may be NULL
 * @return 0 ok (r->disc_h may still be 0), -1 out of memory / bad side.
 */
int fcdr_measure(const int8_t *qd, const int8_t *qc, int side, int thr_d, int thr_c, fcdr_result_t *r,
                 uint8_t *disc, uint8_t *cup);

#endif /* FUNDUS_CDR_H */
