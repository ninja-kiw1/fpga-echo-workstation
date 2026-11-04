/* live_crop.h -- live echo entry (10-25): find the moving image on a captured screen and bring it to 112 x 112.
 *
 * A capture card delivers the whole screen of the ultrasound machine (or of a laptop playing a clip): menus, text,
 * patient name, and somewhere the moving B-mode sector. The board keeps only the moving part:
 *   detect   per pixel max - min over the first K frames > thr -> 4 x 4 cells (any active pixel) -> largest
 *            8-connected group of cells -> its box, made square (side = max(w, h), centred, clamped to the frame)
 *   crop     from then on only that square is read from each captured frame; the rest of the frame is never copied
 *            out of the capture buffer (privacy drop at the entry), the K full frames used for detection are freed
 *   resize   exact box filter (area average) from S x S to 112 x 112 in integers: source pixel j covers
 *            [112 j, 112 (j + 1)), output pixel i covers [S i, S (i + 1)) in units of 1 / 112 px; out = round(sum of
 *            overlap_y * overlap_x * p / S^2), half up. For S = 112 m this is the plain mean of m x m pixels, so a
 *            clip shown at an integer zoom comes back bit for bit.
 * Also built into the host DLL (E:/PLD/keyframe/build_core.sh) so the PC can replay the same entry.
 */
#ifndef LIVE_CROP_H
#define LIVE_CROP_H

#include <stdint.h>

#define LC_OUT 112

typedef struct {
    int x, y, side;       /* square in the captured frame */
    int bx, by, bw, bh;   /* box of the moving cells before squaring */
    int cells;            /* moving cells in the kept group */
} lc_box_t;

/** Moving square from K frames (W x H, 8-bit). 0 ok, -1 nothing moves / out of memory. */
int lc_detect(const uint8_t *const *frames, int K, int W, int H, int thr, lc_box_t *box);

/** Crop the square of `box` out of one W x H frame (row stride `stride` bytes, pixel step `step` bytes: 1 for Y8,
 *  2 for the Y of YUYV) and resize it into out[112 * 112]. `tmp` holds side * 112 uint32 (lc_tmp_words). */
void lc_crop_resize(const uint8_t *frame, int stride, int step, const lc_box_t *box, uint8_t *out, uint32_t *tmp);

/** v5.26: the same box filter to N x N (lc_crop_resize = N 112; the CAMUS quality chain takes N 128: a 128 frame shown
 *  at an integer zoom comes back bit for bit). `tmp` holds side * N uint32 (lc_tmp_words_n). Needs N <= side. */
void lc_crop_resize_n(const uint8_t *frame, int stride, int step, const lc_box_t *box, uint8_t *out, uint32_t *tmp,
                      int N);

static inline int lc_tmp_words(int side) { return side * LC_OUT; }
static inline int lc_tmp_words_n(int side, int N) { return side * N; }

#endif /* LIVE_CROP_H */
