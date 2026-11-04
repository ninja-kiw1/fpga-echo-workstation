/* job_render.h -- 3-D surface views on the board (P1 1.4), see job_render.c. */
#ifndef JOB_RENDER_H
#define JOB_RENDER_H

#include <stdint.h>

#include "job.h"

#define RENDER_MAX_ANGLES 36
#define RENDER_MAX_SIZE   512

/**
 * @brief Build the surface of mask [nz][512][512], render n_angles views (n_angles <= 0: 12; angles NULL: evenly spaced, 360 / n_angles apart),
 *        send them as blob "render3d" and append ,"surface":{...} to b.
 * @return 0 ok, -1 link gone, -2 not rendered (the reason is appended as ,"surface":{"error":..}; no RESULT sent).
 */
int job_render3d(job_t *j, const uint8_t *mask, int nz, const double spc[3], int n_angles, const double *angles,
                 int size, int step, jbuf_t *b);

#endif
