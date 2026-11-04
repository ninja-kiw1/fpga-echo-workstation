/* ai_view.h -- turn the 32x32 U-Net logits into something a human can look at:
 * a 0/255 vessel mask, a red overlay on the preprocessed frame, and a 64-byte
 * statistics block that is sent ahead of the image stream.
 *
 * The image sizes here are fixed at the wire format (512x512 RGB888) while the
 * network output size comes from unet_net.h, so the view layer keeps working if
 * the model resolution changes later.
 */
#ifndef AI_VIEW_H
#define AI_VIEW_H

#include <stdint.h>

#include "unet_ai.h"

/* 64-byte statistics block sent before the image stream when flags bit3 is set. */
#define AI_STAT_BYTES    64u
#define AI_STAT_MAGIC    0x31414953u   /* little-endian bytes: 'S','I','A','1' */
/* v2: the server understands FLAG_ECHO_LOGIT.  The PC reads this field to decide
 * whether a logit image will follow, so an old board never leaves it waiting.
 * v3: the server understands the v5 control header (ROI inference); in ROI mode
 *     "total" is the ROI area and "reserved" = (crop_h << 16) | crop_w.
 * v4: the server understands FLAG_MORPH (64-byte morphology block after this one).
 * v5: model library (aiparam bits[7:5]); "flags" bits[23:16] = index + 1 of the
 *     model that produced this frame (bits[15:0] are still the request flags). */
/* v6: the server understands FLAG_REG (96-byte tile-sum block after the stats / morphology
 *     block); bit FLAG_REG of "flags" is cleared when no tile-sum block follows. */
/* v7: the server understands compact frames (control header v6c "GM", board-side tracking
 *     state, flags b8 / b9). For a compact frame "reserved" = 0x80000000 | bit0 (the prior
 *     came from the board-side state) | bit1 (a state is stored after this frame). */
/* v8: the server understands FLAG_C_PP (PL post-processing, image_proto.h); for a compact frame
 *     "reserved" bit2 = the loaded firmware has pp_layer. */
#define AI_STAT_VERSION  8u

/* 32-byte PL post-processing block (FLAG_C_PP), sent last; then, when mask_bytes > 0, the bit mask
 * (rows of row_bytes, bit j of byte i = pixel 8 i + j) and its CRC16.
 *   u32 magic 'P','P','R','1' | status | count | comps | largest | pl_us | mask_bytes | rows << 16 | row_bytes
 * status: 0 ok; bit0 / bit1 label table overflow; bit2 bad window; bit31 not available on this board;
 *         bit30 | code: driver error (pp_ai.h pp_run return value). count / comps / largest are before CLEAN. */
#define PP_BLOCK_BYTES   32u
#define PP_BLOCK_MAGIC   0x31525050u   /* little-endian bytes: 'P','P','R','1' */
#define PP_STATUS_UNAVAILABLE 0x80000000u
#define PP_STATUS_DRIVER      0x40000000u

/* 96-byte tile-sum block (FLAG_REG, F5 registration): the logit map is cut into
 * REG_GRID x REG_GRID equal tiles and the signed sum of each tile is returned. With the
 * |a - b| weight set (model reg_absdiff) and 16 candidate shifts tiled 4 x 4 in one 512
 * frame, sum[k] is the SAD of candidate k: the PL computes the per-pixel cost, the PS sums.
 *   u32 magic 'R','E','G','1' | version | grid | out_w | out_h | run_us | 2 reserved |
 *   i32 sum[16] (row-major tiles) */
#define REG_BLOCK_BYTES   96u
#define REG_BLOCK_MAGIC   0x31474552u   /* little-endian bytes: 'R','E','G','1' */
#define REG_BLOCK_VERSION 1u
#define REG_GRID          4

/**
 * @brief Box-free nearest-neighbour downsample of a 512x512 RGB888 frame to the
 *        network input size.  Samples the top-left pixel of each block, exactly
 *        like ai/net/gen_small_vectors.py does, so a golden generated from a
 *        512 frame is directly comparable.
 */
void ai_view_downsample_rgb(const uint8_t *src, int sw, int sh,
                            uint8_t *dst, int dw, int dh);

/**
 * @brief Count vessel pixels (network resolution) and 4-connected blobs.
 * @param components [out] optional; number of 4-connected vessel blobs.
 * @return number of vessel pixels in the network resolution.
 */
uint32_t ai_view_count(const int8_t *logits, int lw, int lh, int thr,
                       uint32_t *components);

/**
 * @brief Upsample the logit map into a black/white RGB mask (vessel = white).
 *
 * @param components [out] optional; number of 4-connected vessel blobs.
 * @return the number of vessel pixels in the *network* resolution.
 */
uint32_t ai_view_mask_rgb(const int8_t *logits, int lw, int lh, int thr,
                          uint8_t *rgb_out, int ow, int oh, uint32_t *components);

/**
 * @brief Upsample the mask and paint it red on top of a 512x512 RGB888 frame.
 */
void ai_view_overlay_rgb(const uint8_t *base, const int8_t *logits, int lw, int lh, int thr,
                         uint8_t *rgb_out, int ow, int oh);

/**
 * @brief Upsample the raw logit map into an RGB888 frame for the wire
 *        (FLAG_ECHO_LOGIT).  Every pixel carries logit + LOGIT_IMG_OFFSET on all
 *        three channels; the upsampling is the same nearest-neighbour mapping as
 *        ai_view_mask_rgb(), so thresholding this image on the PC reproduces the
 *        board's mask byte for byte.
 */
void ai_view_logit_rgb(const int8_t *logits, int lw, int lh,
                       uint8_t *rgb_out, int ow, int oh);

/**
 * @brief ROI box (512x512 image coordinates) -> crop window for ROI inference.
 *
 * The box is grown by UNET_ROI_MARGIN on every side that is not an image edge,
 * the start is aligned down to UNET_ROI_ALIGN and the size rounded up to
 * UNET_SIZE_ALIGN (moving the start back if that would leave the image).  The
 * network output inside the box is then identical to the full-frame output.
 * The PC mirrors this in proto.roi_crop(); test/ai_view_dump.c checks they agree.
 */
void ai_view_roi_crop(int rx, int ry, int rw, int rh,
                      int *cx, int *cy, int *cw, int *ch);

/** @brief Copy one RGB888 window (cx, cy, cw x ch) out of a sw-wide frame. */
void ai_view_crop_rgb(const uint8_t *src, int sw, int cx, int cy, int cw, int ch,
                      uint8_t *dst);

/**
 * @brief Paste the ROI part of a crop's logits back into a full-size logit map.
 *
 * full (fw x fh) is set to ROI_OUTSIDE_LOGIT outside the box; roi_out (rw x rh,
 * optional) receives the box alone, for counting vessels / blobs in the ROI.
 */
void ai_view_roi_paste(const int8_t *crop, int cx, int cy, int cw,
                       int rx, int ry, int rw, int rh,
                       int8_t *full, int fw, int fh, int8_t *roi_out);

/**
 * @brief Fill the 64-byte statistics block.
 *
 * Every field is a little-endian u32; magic is written last-visible by the
 * caller (this function writes all of them in order).
 */
void ai_view_fill_stats(uint8_t *blk,
                        uint32_t flags,
                        uint32_t ai_res,
                        uint32_t vessels,
                        uint32_t total,
                        const unet_stats_t *st,
                        uint32_t components,
                        uint32_t proc_min,
                        uint32_t proc_max,
                        uint32_t mode,
                        uint32_t frame_count);

/** @brief Set the last u32 of the stats block (ROI mode: (crop_h << 16) | crop_w). */
void ai_view_set_stats_extra(uint8_t *blk, uint32_t value);

/**
 * @brief 边缘叠加（模式 6）：把"边缘 >= 阈值"的像素在**原图**上画成白色。
 *
 * @param base 原图 RGB888（nx3 字节）
 * @param edge 边缘图 RGB888（PL 模式 2 的输出；三通道相同，取一个即可）
 * @param out  输出 RGB888。**允许 out == edge（就地叠加）** —— 逐像素先读后写，
 *             out != base 时会从 base 拷非边缘像素，所以 base 不能与 out 重叠。
 * @param thr  边缘阈值 0..255
 * @param n    像素个数
 */
void ai_view_edge_overlay(const uint8_t *base, const uint8_t *edge, uint8_t *out,
                          int thr, int n);

/**
 * @brief Fill the 96-byte tile-sum block: sums of the logits over REG_GRID x REG_GRID tiles
 *        (lw, lh must be multiples of REG_GRID; otherwise the block is all zero sums).
 */
void ai_view_fill_reg(uint8_t *blk, const int8_t *logits, int lw, int lh, uint32_t run_us);

#endif /* AI_VIEW_H */
