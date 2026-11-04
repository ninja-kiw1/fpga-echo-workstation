/* ai_view.c -- see ai_view.h */
#include "ai_view.h"
#include "image_proto.h"       /* LOGIT_IMG_OFFSET */

#include <string.h>

static inline void put_le32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
    p[2] = (uint8_t)((v >> 16) & 0xFFu);
    p[3] = (uint8_t)((v >> 24) & 0xFFu);
}

void ai_view_downsample_rgb(const uint8_t *src, int sw, int sh,
                            uint8_t *dst, int dw, int dh)
{
    int y, x;

    for (y = 0; y < dh; y++) {
        int sy = (y * sh) / dh;
        for (x = 0; x < dw; x++) {
            int sx = (x * sw) / dw;
            const uint8_t *s = src + (((size_t)sy * (size_t)sw) + (size_t)sx) * 3u;
            uint8_t       *d = dst + (((size_t)y * (size_t)dw) + (size_t)x) * 3u;
            d[0] = s[0];
            d[1] = s[1];
            d[2] = s[2];
        }
    }
}

/* 4-connected blob count on the network-resolution threshold map. */
uint32_t ai_view_count(const int8_t *logits, int lw, int lh, int thr,
                       uint32_t *components)
{
    /* A 512x512 frame has indices up to 262143, beyond uint16_t. */
    static uint32_t stack[(size_t)UNET_OUT_H * UNET_OUT_W];
    uint32_t comps = 0;
    uint32_t vessels = 0;
    int      n = lw * lh;
    int      i;

    if (n > (int)(sizeof(stack) / sizeof(stack[0]))) {
        if (components != NULL) { *components = 0u; }
        return 0u;
    }

    for (i = 0; i < n; i++) {
        if (logits[i] >= (int8_t)thr) {
            vessels++;
        }
    }

    {
        static uint8_t mark[(size_t)UNET_OUT_H * UNET_OUT_W];
        int sp;

        memset(mark, 0, sizeof(mark));
        for (i = 0; i < n; i++) {
            if (mark[i] != 0u || logits[i] < (int8_t)thr) {
                continue;
            }
            comps++;
            sp = 0;
            stack[sp++] = (uint32_t)i;
            mark[i] = 1u;
            while (sp > 0) {
                int c = stack[--sp];
                int cy = c / lw;
                int cx = c - (cy * lw);
                int nb[4];
                int k;

                nb[0] = cx > 0        ? c - 1  : -1;
                nb[1] = cx < lw - 1   ? c + 1  : -1;
                nb[2] = cy > 0        ? c - lw : -1;
                nb[3] = cy < lh - 1   ? c + lw : -1;
                for (k = 0; k < 4; k++) {
                    int q = nb[k];
                    if (q >= 0 && mark[q] == 0u && logits[q] >= (int8_t)thr) {
                        mark[q] = 1u;
                        stack[sp++] = (uint32_t)q;
                    }
                }
            }
        }
    }

    if (components != NULL) {
        *components = comps;
    }
    return vessels;
}

uint32_t ai_view_mask_rgb(const int8_t *logits, int lw, int lh, int thr,
                          uint8_t *rgb_out, int ow, int oh, uint32_t *components)
{
    uint32_t vessels = ai_view_count(logits, lw, lh, thr, components);
    int      y, x;

    for (y = 0; y < oh; y++) {
        int ly = (y * lh) / oh;
        for (x = 0; x < ow; x++) {
            int     lx = (x * lw) / ow;
            uint8_t v = (logits[(ly * lw) + lx] >= (int8_t)thr) ? 255u : 0u;
            uint8_t *d = rgb_out + (((size_t)y * (size_t)ow) + (size_t)x) * 3u;
            d[0] = v;
            d[1] = v;
            d[2] = v;
        }
    }
    return vessels;
}

void ai_view_overlay_rgb(const uint8_t *base, const int8_t *logits, int lw, int lh, int thr,
                         uint8_t *rgb_out, int ow, int oh)
{
    int y, x;

    for (y = 0; y < oh; y++) {
        int ly = (y * lh) / oh;
        for (x = 0; x < ow; x++) {
            int      lx = (x * lw) / ow;
            size_t   off = (((size_t)y * (size_t)ow) + (size_t)x) * 3u;
            uint8_t *d = rgb_out + off;

            if (logits[(ly * lw) + lx] >= (int8_t)thr) {
                d[0] = 255u;      /* vessel: paint it red */
                d[1] = 0u;
                d[2] = 0u;
            } else if (base != NULL) {
                d[0] = base[off + 0u];
                d[1] = base[off + 1u];
                d[2] = base[off + 2u];
            } else {
                d[0] = d[1] = d[2] = 0u;
            }
        }
    }
}

void ai_view_logit_rgb(const int8_t *logits, int lw, int lh,
                       uint8_t *rgb_out, int ow, int oh)
{
    int y, x;

    for (y = 0; y < oh; y++) {
        int ly = (y * lh) / oh;
        for (x = 0; x < ow; x++) {
            int      lx = (x * lw) / ow;
            uint8_t  v  = (uint8_t)((int)logits[(ly * lw) + lx] + LOGIT_IMG_OFFSET);
            uint8_t *d  = rgb_out + (((size_t)y * (size_t)ow) + (size_t)x) * 3u;
            d[0] = v;
            d[1] = v;
            d[2] = v;
        }
    }
}

/* One axis of ai_view_roi_crop(): [r, r+len) inside [0, full). */
static void roi_axis(int r, int len, int full, int *c0, int *clen)
{
    int lo = r - (int)UNET_ROI_MARGIN;
    int hi = r + len + (int)UNET_ROI_MARGIN;
    int s, n;

    if (lo < 0) {
        lo = 0;                          /* image edge: zero padding is the same as full-frame */
    }
    if (hi > full) {
        hi = full;
    }
    s = (lo / (int)UNET_ROI_ALIGN) * (int)UNET_ROI_ALIGN;
    n = ((hi - s + (int)UNET_SIZE_ALIGN - 1) / (int)UNET_SIZE_ALIGN) * (int)UNET_SIZE_ALIGN;
    if (s + n > full) {
        s = full - n;                    /* full is a multiple of 8, so s stays 4-aligned */
    }
    *c0   = s;
    *clen = n;
}

void ai_view_roi_crop(int rx, int ry, int rw, int rh,
                      int *cx, int *cy, int *cw, int *ch)
{
    roi_axis(rx, rw, (int)UNET_IN_W, cx, cw);
    roi_axis(ry, rh, (int)UNET_IN_H, cy, ch);
}

void ai_view_crop_rgb(const uint8_t *src, int sw, int cx, int cy, int cw, int ch,
                      uint8_t *dst)
{
    int y;

    for (y = 0; y < ch; y++) {
        memcpy(dst + (size_t)y * (size_t)cw * 3u,
               src + (((size_t)(cy + y) * (size_t)sw) + (size_t)cx) * 3u,
               (size_t)cw * 3u);
    }
}

void ai_view_roi_paste(const int8_t *crop, int cx, int cy, int cw,
                       int rx, int ry, int rw, int rh,
                       int8_t *full, int fw, int fh, int8_t *roi_out)
{
    int y;

    memset(full, (int)(uint8_t)ROI_OUTSIDE_LOGIT, (size_t)fw * (size_t)fh);
    for (y = 0; y < rh; y++) {
        const int8_t *s = crop + ((size_t)(ry - cy + y) * (size_t)cw) + (size_t)(rx - cx);

        memcpy(full + ((size_t)(ry + y) * (size_t)fw) + (size_t)rx, s, (size_t)rw);
        if (roi_out != NULL) {
            memcpy(roi_out + (size_t)y * (size_t)rw, s, (size_t)rw);
        }
    }
}

void ai_view_edge_overlay(const uint8_t *base, const uint8_t *edge, uint8_t *out,
                          int thr, int n)
{
    int i;

    for (i = 0; i < n; i++) {
        const size_t k = (size_t)i * 3u;

        if (edge[k] >= (uint8_t)thr) {
            out[k] = 255u;                     /* 强边缘: 画白 */
            out[k + 1u] = 255u;
            out[k + 2u] = 255u;
        } else if (out != base) {
            out[k] = base[k];                  /* 其余保持原图 */
            out[k + 1u] = base[k + 1u];
            out[k + 2u] = base[k + 2u];
        }
    }
}

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
                        uint32_t frame_count)
{
    uint32_t permille = (total > 0u) ? ((vessels * 1000u) / total) : 0u;

    put_le32(blk +  0u, AI_STAT_MAGIC);
    put_le32(blk +  4u, AI_STAT_VERSION);
    put_le32(blk +  8u, flags);
    put_le32(blk + 12u, ai_res);
    put_le32(blk + 16u, vessels);
    put_le32(blk + 20u, total);
    put_le32(blk + 24u, permille);
    put_le32(blk + 28u, (uint32_t)(int32_t)((st != NULL) ? st->logit_min : 0));
    put_le32(blk + 32u, (uint32_t)(int32_t)((st != NULL) ? st->logit_max : 0));
    put_le32(blk + 36u, (st != NULL) ? st->run_us : 0u);
    put_le32(blk + 40u, components);
    put_le32(blk + 44u, proc_min);
    put_le32(blk + 48u, proc_max);
    put_le32(blk + 52u, mode);
    put_le32(blk + 56u, frame_count);
    put_le32(blk + 60u, 0u);
}

void ai_view_set_stats_extra(uint8_t *blk, uint32_t value)
{
    put_le32(blk + 60u, value);
}
