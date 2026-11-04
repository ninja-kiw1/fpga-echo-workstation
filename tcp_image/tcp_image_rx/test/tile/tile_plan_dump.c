/* tile_plan_dump.c -- host harness for src/tile_plan.c (part C): reads "a n full" lines on stdin, prints
 * "k t size own0 own1 ..." per line (k = -1 on error). Used by E:/PLD/hrf/c2_plan_compare.py. */
#include <stdio.h>

#include "tile_plan.h"

int main(void)
{
    static axis_t out[4096];
    int           a, n, full;

    while (scanf("%d %d %d", &a, &n, &full) == 3) {
        int k = tile_plan_axis(a, n, full, out, 4096), i;

        printf("%d", k);
        for (i = 0; i < k; i++) {
            printf(" %d %d %d %d", out[i].t, out[i].size, out[i].own0, out[i].own1);
        }
        printf("\n");
    }
    return 0;
}
