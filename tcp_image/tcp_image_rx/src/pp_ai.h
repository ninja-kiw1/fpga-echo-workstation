/* pp_ai.h -- PS driver for pp_layer, the PL post-processing kernel (source ai/pp_hls, bitstream xu2dp_e2b_pp).
 *
 * pp_layer reads int8 logits from the tensor BRAM (copied in as they are, 16 per 128-bit word) and returns
 * the mask pixel count, connected components (4 / 8), the largest component, 4 x 4 tile sums, and optionally
 * the mask as bits: threshold or integer bilinear x2 / x4 up-sampling (CT), keep-largest + fill-holes (echo).
 * Bit-exact with ai_view_count / ai_view_fill_reg / ct_volume.upsample_bilinear / echo_ws.clean
 * (ai/pp_hls/README.md). Runs only between U-Net runs (one PL user at a time, single-threaded server).
 *
 * ⚠ The control registers exist only in bitstreams that contain pp_layer; touching 0xA003_0000 on any other
 *   bitstream hangs the PS for good (handoff §9.6 ③). pp_firmware_ok() checks the loaded firmware's sha256
 *   against the list written at deployment, and pp_init() must only be called when it says yes.
 */
#ifndef PP_AI_H
#define PP_AI_H

#include <stddef.h>
#include <stdint.h>

#define PP_DEF_CTRL      0xA0030000ull
#define PP_FIRMWARE      "/lib/firmware/ai_sys.bin"
#define PP_FIRMWARE_LIST "/lib/firmware/ai_sys_pp.sha256"   /* sha256 lines of bitstreams with pp_layer */

/* op bits, same as ai/pp_hls/src/pp_layer.hpp (PP_FIRST / PP_LAST are set by the driver) */
#define PP_OP_MASK   1u
#define PP_OP_CLEAN  2u
#define PP_OP_CONN8  4u
#define PP_OP_TILES  8u

typedef struct {
    uint32_t count;        /* mask pixels (before CLEAN) */
    uint32_t comps;        /* connected components */
    uint32_t largest;      /* pixels of the largest component */
    uint32_t status;       /* bit0 / bit1: label table overflow (results invalid), bit2: bad config */
    int32_t  tiles[16];    /* PP_OP_TILES: 4 x 4 tile sums of the raw logits */
    uint32_t calls;        /* kernel calls (bands) */
    uint32_t pl_us;        /* time the kernel ran (start .. done) */
    uint32_t total_us;     /* incl. copying logits in and results / mask out */
} pp_result_t;

/** @brief 1 if the loaded firmware is listed in PP_FIRMWARE_LIST (uses sha256sum), else 0. */
int pp_firmware_ok(void);

/** @brief Map the control registers (needs unet_init() for the shared BRAM). 0 ok, -1 failure. */
int  pp_init(uint64_t ctrl_base);
int  pp_ready(void);
void pp_close(void);

/** @brief Bytes per mask row for an output width ow (rows are padded to whole 128-bit words). */
size_t pp_mask_row_bytes(int ow);

/**
 * @brief Post-process one logit map q (h x w int8, row-major, w a multiple of 16).
 *
 * Window (x0, y0, cw, ch) in logit pixels, up-sampling k in {1, 2, 4} (k > 1: mask = bilinear > tn / 2,
 * otherwise logit >= thr). With PP_OP_MASK, mask receives ch*k rows of pp_mask_row_bytes(cw*k) bytes
 * (bit j of byte i = pixel 8 i + j). Frames whose logits and results do not fit the BRAM at once (512 x 512)
 * are sent in row bands; that needs k == 1 and no PP_OP_CLEAN.
 * @return 0 ok; -1 not ready / bad arguments; -2 kernel timeout; -3 kernel reported bad config.
 */
int pp_run(const int8_t *q, int h, int w, unsigned op, int x0, int y0, int cw, int ch, int k, int thr, int tn,
           uint8_t *mask, pp_result_t *res);

#endif /* PP_AI_H */
