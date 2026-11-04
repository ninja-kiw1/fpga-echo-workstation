/* vessel_morph.h -- vessel morphology metrics on a binary vessel mask.
 *
 * Pipeline (all integer except the final ratios):
 *   1. drop 8-connected blobs smaller than VM_MIN_BLOB_512 (scaled to the grid)
 *   2. Zhang-Suen thinning -> one-pixel skeleton
 *   3. crossing number on the skeleton: CN == 1 endpoint, CN >= 3 branch pixel;
 *      8-connected clusters of branch pixels count as one branch point
 *   4. length = orthogonal links + sqrt(2) x diagonal links (a diagonal link is
 *      skipped when an orthogonal path already joins the two pixels)
 *   5. segments = skeleton minus branch pixels and their 8 neighbours;
 *      tortuosity = sum(arc) / sum(chord) over segments with two ends and
 *      arc >= VM_MIN_SEG_512 (scaled)
 *   6. fractal dimension by box counting (box 2, 4, ... <= min(w, h) / 4)
 *   7. vessel density inside the fundus field of view, overall and per quadrant
 *
 * The same algorithm, written independently in NumPy, is in
 * ai/net/vessel_morph_ref.py; test/vessel_morph_dump.c + that script check that
 * the two agree on real masks.  Every 3x3 step here is a streaming window
 * operation, so this is also the reference for a later PL (HLS) version.
 *
 * Not reentrant: uses static work buffers sized for 512 x 512.
 */
#ifndef VESSEL_MORPH_H
#define VESSEL_MORPH_H

#include <stdint.h>

#define VM_MAX_W          512
#define VM_MAX_H          512
#define VM_FOV_THR        20    /* luminance (77R+150G+29B)>>8 > 20 = inside the field of view */
#define VM_MIN_BLOB_512   20    /* blobs below 20 px (at 512 x 512) are treated as noise */
#define VM_MIN_SEG_512    10    /* segments shorter than 10 px (at 512) skip the tortuosity */

/* skeleton map codes written by vm_analyze() */
#define VM_BG       0u
#define VM_VESSEL   1u          /* vessel pixel that is not on the skeleton */
#define VM_SKEL     2u
#define VM_BRANCH   3u
#define VM_END      4u

/* 64-byte metrics block sent after the stats block when flags b6 is set */
#define VM_BLOCK_BYTES    64u
#define VM_BLOCK_MAGIC    0x3150524Du   /* little-endian bytes 'M','R','P','1' */
#define VM_BLOCK_VERSION  1u

typedef struct {
    uint32_t w, h;               /* analysis grid */
    uint32_t vessel_px;          /* after dropping small blobs */
    uint32_t fov_px;
    uint32_t length_x10;         /* skeleton length, grid px x 10 */
    uint32_t caliber_x100;       /* vessel_px / length, grid px x 100 */
    uint32_t branches;
    uint32_t endpoints;
    uint32_t segments;
    uint32_t tortuosity_x1000;
    uint32_t fractal_x1000;
    uint32_t density_x1000;      /* vessel px inside FOV / FOV px */
    uint32_t quad_x1000[4];      /* top-left, top-right, bottom-left, bottom-right */
    uint32_t run_us;             /* filled in by the caller */
} vm_result_t;

/**
 * @brief Field-of-view mask for an analysis grid of w x h covering the region
 *        (x0, y0, rw, rh) of an sw-wide RGB888 frame (nearest sampling, the
 *        same mapping as the mask upsampling in ai_view.c).
 */
void vm_fov_from_rgb(const uint8_t *rgb, int sw, int x0, int y0, int rw, int rh,
                     int w, int h, uint8_t *fov);

/**
 * @param mask    w*h bytes, nonzero = vessel
 * @param fov     w*h bytes, nonzero = inside the field of view; NULL = everything
 * @param res512  the grid's width in full-frame terms: 512 at full resolution and
 *                for ROI crops, 128 for the 128 setting; scales the noise / segment limits
 * @param skel    [out, optional] w*h skeleton map (VM_* codes)
 * @return 0 ok; -1 if the grid is larger than VM_MAX_W x VM_MAX_H.
 */
int vm_analyze(const uint8_t *mask, const uint8_t *fov, int w, int h, int res512,
               uint8_t *skel, vm_result_t *r);

void vm_fill_block(uint8_t *blk, const vm_result_t *r);

/**
 * @brief Draw the skeleton view on a 512x512 RGB888 copy of base: vessels tinted
 *        red, skeleton white, branch points red and endpoints cyan (3x3 dots).
 *        The w x h grid maps onto the region (x0, y0, rw, rh) of the frame.
 */
void vm_draw(const uint8_t *base, const uint8_t *skel, int w, int h,
             int x0, int y0, int rw, int rh, uint8_t *out);

#endif /* VESSEL_MORPH_H */
