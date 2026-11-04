/* cc3d.h -- largest 26-connected component of a 3-D mask (CT whole-volume post-processing on the PS).
 *
 * Same result as tcp_image_ui/ct_volume.largest_cc3d (scipy.ndimage.label with a 3x3x3 structure, then
 * the label with the most voxels; ties go to the component whose first voxel in raster order comes first,
 * which is scipy's label order). Works on runs, so memory grows with the number of runs, not voxels.
 */
#ifndef CC3D_H
#define CC3D_H

#include <stdint.h>

/**
 * @brief Keep only the largest 26-connected component of m (nz x h x w, values 0 / 1), in place.
 *        With 0 or 1 components m is left as it is.
 * @param ncomp  number of components found (may be NULL)
 * @return voxels in the mask afterwards, -1 out of memory (m unchanged).
 */
long cc3d_keep_largest(uint8_t *m, int nz, int h, int w, uint32_t *ncomp);

#endif /* CC3D_H */
