/* vis_ai.h -- PS driver for vis_layer, the r2 PL registration engine + graphics overlay (source ai/vis_hls,
 * bitstream xu2dp_e2b_pp_r2; bit-level spec ai/vis_hls/visref.py).
 *
 * REG: two grey frames (128 or 256) -> shift (integer or 1/4 pixel, integer bilinear), cost table, three-step /
 *      full search and sub-pixel refinement, all on the PL; returns the best shift, the path and every evaluated
 *      (shift, SAD, overlap). With the reg_absdiff table the costs equal today's F5 path bit for bit
 *      (MAD = SAD / overlap as a double; the PL compares S1 * O2 < S2 * O1, which orders exactly as the doubles).
 * OVL: overlay of an output frame (<= 512 wide, a multiple of 16): base none / grey / RGB / heat (256-entry lut),
 *      fill (alpha in 16ths, 16 = paint), uncertain band, 4-neighbour contour, from int8 logits (nearest) or a
 *      bit mask; = ai_view_overlay_rgb / confidence.overlay_rgb / uncertain_rgb / heat_rgb at alpha 16.
 *      The driver splits the frame into row bands that fit the tensor BRAM.
 * Runs only between U-Net runs (one PL user at a time) and the CPU does not touch the BRAM while it runs.
 *
 * ⚠ 0xA004_0000 exists only in bitstreams with vis_layer; touching it on any other bitstream hangs the PS for
 *   good. vis_firmware_ok() checks the loaded firmware's sha256 against VIS_FIRMWARE_LIST (written at
 *   deployment); vis_init() must only be called when it says yes.
 */
#ifndef VIS_AI_H
#define VIS_AI_H

#include <stddef.h>
#include <stdint.h>

#define VIS_DEF_CTRL      0xA0040000ull
#define VIS_FIRMWARE      "/lib/firmware/ai_sys.bin"
#define VIS_FIRMWARE_LIST "/lib/firmware/ai_sys_vis.sha256"   /* sha256 lines of bitstreams with vis_layer */

#define VIS_MAX_RECORDS 3985        /* full +-31 (63^2) + 16 sub-pixel */

/* overlay flags (= ai/vis_hls/src/vis_layer.hpp) */
#define VIS_BASE_NONE        0u
#define VIS_BASE_GREY        1u
#define VIS_BASE_RGB         2u
#define VIS_BASE_HEAT        3u
#define VIS_F_HAS_LOGIT      8u
#define VIS_F_HAS_BITS       16u
#define VIS_F_FILL_FROM_BITS 32u
#define VIS_F_FILL           64u
#define VIS_F_UNSURE         128u
#define VIS_F_CONTOUR        256u

typedef struct {
    int8_t   qx, qy;        /* quarter pixels */
    uint32_t sad, overlap;
} vis_rec_t;

typedef struct {
    int       qx, qy;        /* best shift, quarter pixels (G(y, x) = B(y - qy / 4, x - qx / 4)) */
    uint32_t  sad, overlap;
    int       npath;
    int8_t    path[8][2];
    int       nrec;
    vis_rec_t rec[VIS_MAX_RECORDS];
    uint32_t  pl_us, total_us;
} vis_reg_result_t;

typedef struct {
    int      ow, oh, flags;
    int      lw, lh;         /* logits (VIS_F_HAS_LOGIT), lw a multiple of 16, <= ow; lh <= oh */
    int      mw, mh;         /* bit mask (VIS_F_HAS_BITS): rows of ceil(mw / 128) * 16 bytes, bit j = pixel j */
    int      thr, band;      /* logit mask >= thr; uncertain |logit - thr| <= band */
    int      fill_a, unsure_a;                 /* alpha in 16ths (0..16) */
    uint8_t  fill[3], unsure[3], contour[3];
} vis_ovl_t;

int  vis_firmware_ok(void);
int  vis_init(uint64_t ctrl_base);     /* needs unet_init() (shared tensor BRAM); 0 ok */
int  vis_ready(void);
void vis_close(void);

/**
 * @brief Registration on the PL. a, b: n x n grey (n = 128 / 256). mode 0 three-step (steps[], grid pixels,
 *        0-terminated, sum <= 15 / 31), mode 1 full +-radius; subpel 0..2; tab: 256-entry cost table.
 * @return 0 ok; -1 not ready / bad arguments; -2 timeout; -3 the kernel rejected the descriptor.
 */
int vis_reg(const uint8_t *a, const uint8_t *b, int n, int mode, int radius, const uint8_t steps[4], int subpel,
            const uint8_t tab[256], vis_reg_result_t *res);

/**
 * @brief Overlay into rgb (oh x ow x 3). base: grey (oh x ow) or RGB (oh x ow x 3) per the base kind; logits:
 *        lh x lw int8; bits: mh rows of packed bits; lut: 256 x RGB for VIS_BASE_HEAT (index = logit + 128).
 * @return 0 ok; -1 not ready / bad arguments; -2 timeout; -3 the kernel rejected the descriptor.
 */
int vis_overlay(const vis_ovl_t *c, const uint8_t *base, const int8_t *logits, const uint8_t *bits,
                const uint8_t lut[256][3], uint8_t *rgb, uint32_t *pl_us, uint32_t *calls);

/** @brief Tests only (test/vis_board_test.c): start the kernel on a descriptor already in the BRAM at desc_word
 *         (the whole BRAM was written by the caller). 0 ok (*ret = kernel status), -1 not ready, -2 timeout. */
int vis_run_raw(uint32_t desc_word, uint32_t *ret, uint32_t *pl_us);

/** @brief The reg_absdiff (model 5) logit as a function of |R - G| (ai/vis_hls/absdiff_table.json). */
extern const uint8_t vis_absdiff_table[256];

#endif /* VIS_AI_H */
