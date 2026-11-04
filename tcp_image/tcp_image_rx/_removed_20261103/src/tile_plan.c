/* tile_plan.c -- tile planner of JOB_TILE (see tile_plan.h). Pure C, no hardware: compiled on the PC too. */
#include "tile_plan.h"

static int ceil_to(int v, int a) { return (v + a - 1) / a * a; }

int tile_plan_axis(int a, int n, int full, axis_t *out, int cap)
{
    const int m = (int)UNET_ROI_MARGIN;
    int       lo, hi, s, size, t, own0, k = 0;

    if (a < 0 || n < 1 || a + n > full || full % (int)UNET_SIZE_ALIGN != 0 || cap < 1) {
        return -1;
    }
    lo = (a - m > 0) ? a - m : 0;
    hi = (a + n + m < full) ? a + n + m : full;
    s = lo / (int)UNET_ROI_ALIGN * (int)UNET_ROI_ALIGN;
    size = ceil_to(hi - s, (int)UNET_SIZE_ALIGN);
    if (size <= TILE_PLAN_MAX) {
        if (s + size > full) {
            s = full - size;
        }
        out[0].t = s;
        out[0].size = size;
        out[0].own0 = a;
        out[0].own1 = a + n;
        return 1;
    }
    t = lo / (int)UNET_SIZE_ALIGN * (int)UNET_SIZE_ALIGN;
    own0 = a;
    for (;;) {
        int core_end, own1;

        size = ceil_to(hi - t, (int)UNET_SIZE_ALIGN);
        if (size > TILE_PLAN_MAX) {
            size = TILE_PLAN_MAX;
        }
        if (t + size > full) {
            t = full - size;
        }
        core_end = t + size - ((t + size < full) ? m : 0);
        own1 = (core_end < a + n) ? core_end : a + n;
        if (k >= cap) {
            return -1;
        }
        out[k].t = t;
        out[k].size = size;
        out[k].own0 = own0;
        out[k].own1 = own1;
        k++;
        if (own1 >= a + n) {
            return k;
        }
        own0 = own1;
        t += TILE_PLAN_STEP;
    }
}

