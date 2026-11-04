/* surf3d.h -- 3-D surface reconstruction + rendering of a binary volume on the PS (P1 1.4).
 *
 * Port of tcp_image_ui/ct_volume.py Surface / render_surface (float: accepted by tolerance, not bit for bit):
 *   build : mask[:, ::step, ::step] -> crop (1 slice / 3 px margin) -> linear zoom along z to ~cubic voxels
 *           (scipy ndimage.zoom order 1) -> gaussian_filter sigma 1.2 (radius 5, reflect) -> > 0.5 ->
 *           surface = mask & ~binary_erosion (6-neighbourhood, outside = 0) -> normals = -grad (np.gradient)
 *   render: rotate about the body axis by angle, orthographic projection, z-buffer of point splats,
 *           Lambert + specular + depth cue (same constants as the Python).
 * mask is [nz][nr][nc] (z, row front->back, col right->left: the radiological view of job_ct.c),
 * spacing = (dx col, dy row, dz slice) in mm.
 */
#ifndef SURF3D_H
#define SURF3D_H

#include <stdint.h>

typedef struct {
    int    empty;
    int    n;                       /* surface points */
    double *X, *Y, *Z;              /* centred positions (mm) */
    float *nx, *ny, *nz;            /* unit normals */
    double ext, vox;
    /* statistics for the acceptance (compare with Python) */
    int    dims[3];                 /* smoothed volume size (z, r, c) */
    long   inside;                  /* voxels with g > 0.5 */
    double sz;                      /* z voxel size after the zoom (mm) */
    uint32_t build_us;
} surf3d_t;

int  surf3d_build(const uint8_t *mask, int nz, int nr, int nc, const double spacing[3], int step, surf3d_t *s);
void surf3d_render(const surf3d_t *s, double angle_deg, int size, const uint8_t color[3], uint8_t *rgb);
void surf3d_free(surf3d_t *s);

#endif
