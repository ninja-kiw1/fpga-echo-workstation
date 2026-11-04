/* tile_plan.h -- tile planner of JOB_TILE (part C, v5.20); = tcp_image_ui/tile_engine.plan_axis, checked by
 * E:/PLD/hrf/c2_plan_compare.py through test/tile/tile_plan_dump.c.
 *
 * One axis: box [a, a + n) inside [0, full) (full a multiple of 8). When one tile is enough (box + 24 px margin,
 * start floored to 4, length rounded up to 8, moved back inside) it is the old ROI crop window (ai_view_roi_crop).
 * Otherwise tiles of 512 start at floor8(a - 24) every TILE_PLAN_STEP = 464 px; the last one is only as long as
 * needed. Own regions [own0, own1) are contiguous and tile the box; every own pixel has >= 24 real neighbours inside
 * its tile on every side that is not an image edge, so tiled == whole-image inference bit for bit.
 */
#ifndef TILE_PLAN_H
#define TILE_PLAN_H

#include "unet_ai.h"

#define TILE_PLAN_MAX  512
#define TILE_PLAN_STEP (TILE_PLAN_MAX - 2 * (int)UNET_ROI_MARGIN)

typedef struct {
    int t, size, own0, own1;
} axis_t;

/** @brief Plan one axis into out[0..cap). @return the number of tiles, -1 on bad input / more than cap. */
int tile_plan_axis(int a, int n, int full, axis_t *out, int cap);

#endif /* TILE_PLAN_H */
