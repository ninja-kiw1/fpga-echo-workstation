/* job_tile.c -- JOB_TILE (v5.20, part C): exact tiled inference of a box on an image of any size.
 *
 * The level image (a pyramid level of a large fundus image, made on the PC: original scaled with Pillow LANCZOS,
 * padded with black to multiples of 8) lives in a file under /data (the board only crops it, pread per row) or is
 * uploaded as a region of the level. The box is cut into tiles of at most 512 x 512 (start 4-aligned, size a multiple
 * of 8, UNET_ROI_MARGIN = 24 real neighbouring pixels on every side that is not an image edge; the deepest effect of a
 * crop edge is 22 px, ai/net/roi_exactness.py). Each tile keeps only its own region (own regions tile the box), so
 * the stitched result is bit for bit the inference of the whole level at once (E:/PLD/hrf/c1_exactness.py). When one
 * tile is enough the tile is the old ROI crop window (ai_view_roi_crop): a box on a 512 frame = the v5 ROI frame.
 * The same planner is tcp_image_ui/tile_engine.py (plan_axis); E:/PLD/hrf/c2_plan_compare.py checks the two.
 *
 * Per tile (in raster order or the order the PC sends, e.g. most uncertain first): crop -> U-Net on the PL
 * (unet_set_size) -> pp_layer on the own region (count, connected components, mask bits) -> vis_layer overlay of
 * the own region (base = level RGB, vessels painted red = ai_view_overlay_rgb, optional uncertain band). Tiles whose
 * own region has no field-of-view pixel ((77R + 150G + 29B) >> 8 > 20, = vessel_morph VM_FOV_THR) are skipped.
 *
 * params (little endian): u32 source (0 = data holds region pixels, 1 = file), u32 model, i32 thr, i32 band,
 *   u32 level_w, u32 level_h, i32 box x, y, w, h, i32 region x, y, w, h (source 0), u32 n_order, u32 path_len,
 *   then u16 order[n_order] (a permutation of the tile indices; 0 = raster order), then the path (source 1).
 * flags: TILE_LOGITS 1, TILE_MASK 2, TILE_OVERLAY 4, TILE_NO_SKIP 8, TILE_PS_POST 16 (count / overlay on the PS).
 * reply: one blob "tile" per tile (skipped ones too) = 72-byte header (u32 index, pos, i32 tx, ty, tw, th, ox, oy,
 *   ow, oh, u32 state (bit0 skipped), run_us, crop_us, post_us, ovl_us, vessels, comps (4-connected, own region), 0)
 *   + own-region logits (ow * oh)
 *   + mask bits (oh rows of pp_mask_row_bytes(ow16)) + overlay (ow * oh * 3), each only if asked; then the result.
 * File: 32-byte header "PLDL" (u32 0x4C444C50), u32 version 1, u32 w, u32 h, u32 3, 12 bytes 0, then RGB rows.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "ai_view.h"
#include "image_proto.h"
#include "job.h"
#include "pp_ai.h"
#include "progpkg.h"
#include "tile_plan.h"
#include "unet_ai.h"
#include "vis_ai.h"

#define TILE_LOGITS   1u
#define TILE_MASK     2u
#define TILE_OVERLAY  4u
#define TILE_NO_SKIP  8u
#define TILE_PS_POST  16u

#define TILE_MAX      TILE_PLAN_MAX
#define TILE_MAX_N    4096
#define TILE_MAX_SIDE 16384
#define TILE_HDR      72u
#define LEVEL_MAGIC   0x4C444C50u
#define LEVEL_HDR     32
#define FOV_THR       20

static int32_t le32(const uint8_t *p)
{
    return (int32_t)((uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24);
}

static void put32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static int ceil_to(int v, int a) { return (v + a - 1) / a * a; }

/* level pixels: either the uploaded region or the file (pread per row) */
typedef struct {
    int            fd;
    const uint8_t *reg;
    int            rx, ry, rw, rh, lw;
} src_t;

static int src_crop(const src_t *s, int x, int y, int w, int h, uint8_t *dst)
{
    int r;

    if (s->reg != NULL) {
        if (x < s->rx || y < s->ry || x + w > s->rx + s->rw || y + h > s->ry + s->rh) {
            return -1;
        }
        for (r = 0; r < h; r++) {
            memcpy(dst + (size_t)r * w * 3u, s->reg + ((size_t)(y - s->ry + r) * s->rw + (size_t)(x - s->rx)) * 3u,
                   (size_t)w * 3u);
        }
        return 0;
    }
    for (r = 0; r < h; r++) {
        const off_t  off = (off_t)LEVEL_HDR + ((off_t)(y + r) * s->lw + x) * 3;
        const size_t n = (size_t)w * 3u;

        if (pread(s->fd, dst + (size_t)r * n, n, off) != (ssize_t)n) {
            return -1;
        }
    }
    return 0;
}

static int has_fov(const uint8_t *rgb, int tw, int x0, int y0, int w, int h)
{
    int x, y;

    for (y = y0; y < y0 + h; y++) {
        const uint8_t *p = rgb + ((size_t)y * tw + x0) * 3u;

        for (x = 0; x < w; x++, p += 3) {
            if (((77 * p[0] + 150 * p[1] + 29 * p[2]) >> 8) > FOV_THR) {
                return 1;
            }
        }
    }
    return 0;
}

static int bad_path(const char *p)
{
    return strncmp(p, "/data/", 6) != 0 || strstr(p, "..") != NULL || strlen(p) < 8u;
}

int job_tile_run(job_t *j)
{
    const uint8_t *p = j->param;
    uint32_t       source, model, lw, lh, n_order, path_len, nax, nay, ntile, k;
    int32_t        thr, band, bx, by, bw, bh;
    axis_t        *ax = NULL, *ay = NULL;
    uint16_t      *order = NULL;
    uint8_t       *seen = NULL, *rgb = NULL, *pad_rgb = NULL, *ovl = NULL, *blob = NULL, *mask = NULL;
    int8_t        *lg = NULL, *own = NULL;
    char           path[256], js[1536], err[160];
    jbuf_t         b;
    src_t          src;
    const unet_model_t *um;
    uint64_t       t_job = job_now_us(), unet_us = 0u, crop_us = 0u, post_us = 0u, ovl_us = 0u, pp_pl_us = 0u,
                   vis_pl_us = 0u, comp_px = 0u, own_px = 0u, box_px = 0u, vessels = 0u;
    uint32_t       run = 0u, skipped = 0u, max_tile_us = 0u;
    int            rc = 0, post_ps = 0, ovl_ps = 0, i;

    memset(&src, 0, sizeof(src));
    src.fd = -1;
    if (j->param_bytes < 64u) {
        return job_fail(j, "tile: needs 64 bytes of params");
    }
    source = (uint32_t)le32(p);
    model = (uint32_t)le32(p + 4);
    thr = le32(p + 8);
    band = le32(p + 12);
    lw = (uint32_t)le32(p + 16);
    lh = (uint32_t)le32(p + 20);
    bx = le32(p + 24);
    by = le32(p + 28);
    bw = le32(p + 32);
    bh = le32(p + 36);
    n_order = (uint32_t)le32(p + 56);
    path_len = (uint32_t)le32(p + 60);
    if (source > 1u || thr < -128 || thr > 127 || band < 0 || band > 127 || lw < 8u || lh < 8u ||
        lw > TILE_MAX_SIDE || lh > TILE_MAX_SIDE || lw % 8u || lh % 8u || bx < 0 || by < 0 || bw < 1 || bh < 1 ||
        (uint32_t)(bx + bw) > lw || (uint32_t)(by + bh) > lh || n_order > TILE_MAX_N || path_len > 255u ||
        64u + 2u * n_order + path_len != j->param_bytes || (source == 1u) != (path_len > 0u)) {
        return job_fail(j, "tile: bad params (level a multiple of 8, box inside, order / path lengths)");
    }
    um = prog_model(model);
    if (um == NULL || um->roi_ok == 0u) {
        return job_fail(j, "tile: unknown model or a model trained below 512 (no exact crops)");
    }
    nax = (uint32_t)(lw + TILE_PLAN_STEP - 1) / TILE_PLAN_STEP + 2u;
    nay = (uint32_t)(lh + TILE_PLAN_STEP - 1) / TILE_PLAN_STEP + 2u;
    ax = (axis_t *)calloc(nax, sizeof(axis_t));
    ay = (axis_t *)calloc(nay, sizeof(axis_t));
    if (ax == NULL || ay == NULL) {
        free(ax);
        free(ay);
        return job_fail(j, "tile: out of memory");
    }
    i = tile_plan_axis(bx, bw, (int)lw, ax, (int)nax);
    k = (uint32_t)tile_plan_axis(by, bh, (int)lh, ay, (int)nay);
    if (i < 1 || (int)k < 1 || (uint32_t)i * k > TILE_MAX_N) {
        free(ax);
        free(ay);
        return job_fail(j, "tile: planning failed");
    }
    nax = (uint32_t)i;
    nay = k;
    ntile = nax * nay;
    if (n_order != 0u && n_order != ntile) {
        snprintf(err, sizeof(err), "tile: order has %u entries, the plan has %u tiles", (unsigned)n_order,
                 (unsigned)ntile);
        free(ax);
        free(ay);
        return job_fail(j, err);
    }
    order = (uint16_t *)malloc(sizeof(uint16_t) * ntile);
    seen = (uint8_t *)calloc(ntile, 1u);
    rgb = (uint8_t *)malloc((size_t)TILE_MAX * TILE_MAX * 3u);
    pad_rgb = (uint8_t *)malloc((size_t)TILE_MAX * TILE_MAX * 3u);
    ovl = (uint8_t *)malloc((size_t)TILE_MAX * TILE_MAX * 3u);
    lg = (int8_t *)malloc((size_t)TILE_MAX * TILE_MAX);
    own = (int8_t *)malloc((size_t)TILE_MAX * TILE_MAX);
    mask = (uint8_t *)malloc((size_t)TILE_MAX * pp_mask_row_bytes(TILE_MAX));
    blob = (uint8_t *)malloc(TILE_HDR + (size_t)TILE_MAX * TILE_MAX * 4u + (size_t)TILE_MAX * pp_mask_row_bytes(TILE_MAX));
    if (order == NULL || seen == NULL || rgb == NULL || pad_rgb == NULL || ovl == NULL || lg == NULL || own == NULL ||
        mask == NULL || blob == NULL) {
        rc = job_fail(j, "tile: out of memory");
        goto done;
    }
    for (k = 0u; k < ntile; k++) {
        order[k] = (uint16_t)k;
        if (n_order != 0u) {
            order[k] = (uint16_t)((uint32_t)p[64u + 2u * k] | (uint32_t)p[65u + 2u * k] << 8);
            if (order[k] >= ntile || seen[order[k]]) {
                rc = job_fail(j, "tile: order is not a permutation of the tiles");
                goto done;
            }
            seen[order[k]] = 1u;
        }
    }
    src.lw = (int)lw;
    if (source == 1u) {
        uint8_t hdr[LEVEL_HDR];

        memcpy(path, p + 64u + 2u * n_order, path_len);
        path[path_len] = '\0';
        if (bad_path(path)) {
            rc = job_fail(j, "tile: path must be under /data/ (no ..)");
            goto done;
        }
        src.fd = open(path, O_RDONLY);
        if (src.fd < 0 || pread(src.fd, hdr, sizeof(hdr), 0) != (ssize_t)sizeof(hdr) ||
            (uint32_t)le32(hdr) != LEVEL_MAGIC || le32(hdr + 4) != 1 || (uint32_t)le32(hdr + 8) != lw ||
            (uint32_t)le32(hdr + 12) != lh || le32(hdr + 16) != 3) {
            rc = job_fail(j, "tile: cannot open the level file or its header does not match level_w / level_h");
            goto done;
        }
    } else {
        src.rx = le32(p + 40);
        src.ry = le32(p + 44);
        src.rw = le32(p + 48);
        src.rh = le32(p + 52);
        if (src.rx < 0 || src.ry < 0 || src.rw < 1 || src.rh < 1 || (uint32_t)(src.rx + src.rw) > lw ||
            (uint32_t)(src.ry + src.rh) > lh || (uint64_t)src.rw * (uint64_t)src.rh * 3u != j->data_bytes) {
            rc = job_fail(j, "tile: region outside the level or data size != region w * h * 3");
            goto done;
        }
        src.reg = j->data;
    }
    post_ps = (j->flags & TILE_PS_POST) != 0u || !pp_ready();
    ovl_ps = (j->flags & TILE_PS_POST) != 0u || !vis_ready();

    for (k = 0u; k < ntile && rc == 0; k++) {
        const uint32_t idx = order[k];
        const axis_t  *X = &ax[idx % nax], *Y = &ay[idx / nax];
        const int      tx = X->t, ty = Y->t, tw = X->size, th = Y->size;
        const int      ox = X->own0, oy = Y->own0, ow = X->own1 - X->own0, oh = Y->own1 - Y->own0;
        const int      ow16 = ceil_to(ow, 16);
        uint32_t       state = 0u, run_us = 0u, c_us, p_us = 0u, o_us = 0u, nv = 0u, nc = 0u;
        uint64_t       t0 = job_now_us();
        size_t         bn = TILE_HDR;
        int            r;

        box_px += (uint64_t)ow * (uint64_t)oh;
        if (src_crop(&src, tx, ty, tw, th, rgb) != 0) {
            rc = job_fail(j, "tile: reading the tile failed (region too small / file short)");
            break;
        }
        c_us = (uint32_t)(job_now_us() - t0);
        if ((j->flags & TILE_NO_SKIP) == 0u && !has_fov(rgb, tw, ox - tx, oy - ty, ow, oh)) {
            state |= 1u;
            skipped++;
        } else {
            uint64_t t1 = job_now_us();
            unet_stats_t st;

            if (prog_select(model) != 0 || unet_set_size(th, tw) != 0 || unet_run(rgb, lg, &st) != 0) {
                rc = job_fail(j, "tile: U-Net run failed");
                break;
            }
            job_unet_account(&st);
            run_us = st.run_us;
            run++;
            comp_px += (uint64_t)tw * (uint64_t)th;
            own_px += (uint64_t)ow * (uint64_t)oh;
            /* own region, padded to 16 columns with -128 (outside) for pp_layer / vis_layer */
            for (r = 0; r < oh; r++) {
                memcpy(own + (size_t)r * ow16, lg + (size_t)(oy - ty + r) * tw + (ox - tx), (size_t)ow);
                memset(own + (size_t)r * ow16 + ow, (int)(uint8_t)ROI_OUTSIDE_LOGIT, (size_t)(ow16 - ow));
            }
            t1 = job_now_us();
            if (post_ps) {
                int8_t *tmp = lg;            /* compact copy for ai_view_count (no padding) */

                for (r = 0; r < oh; r++) {
                    memmove(tmp + (size_t)r * ow, own + (size_t)r * ow16, (size_t)ow);
                }
                nv = ai_view_count(tmp, ow, oh, thr, &nc);
                if (j->flags & TILE_MASK) {
                    const size_t mrow = pp_mask_row_bytes(ow16);
                    int          x;

                    memset(mask, 0, (size_t)oh * mrow);
                    for (r = 0; r < oh; r++) {
                        for (x = 0; x < ow; x++) {
                            if (own[(size_t)r * ow16 + x] >= thr) {
                                mask[(size_t)r * mrow + (x >> 3)] |= (uint8_t)(1u << (x & 7));
                            }
                        }
                    }
                }
            } else {
                pp_result_t pr;

                if (pp_run(own, oh, ow16, (j->flags & TILE_MASK) ? PP_OP_MASK : 0u, 0, 0, ow, oh, 1, thr, 0, mask,
                           &pr) != 0 || (pr.status & 3u) != 0u) {
                    rc = job_fail(j, "tile: pp_layer failed (or its label table overflowed)");
                    break;
                }
                nv = pr.count;
                nc = pr.comps;
                pp_pl_us += pr.pl_us;
            }
            p_us = (uint32_t)(job_now_us() - t1);
            vessels += nv;
            if (j->flags & TILE_OVERLAY) {
                vis_ovl_t c;
                uint32_t  pl = 0u, calls = 0u;

                t1 = job_now_us();
                for (r = 0; r < oh; r++) {
                    memcpy(pad_rgb + (size_t)r * ow16 * 3u, rgb + ((size_t)(oy - ty + r) * tw + (ox - tx)) * 3u,
                           (size_t)ow * 3u);
                    memset(pad_rgb + ((size_t)r * ow16 + ow) * 3u, 0, (size_t)(ow16 - ow) * 3u);
                }
                memset(&c, 0, sizeof(c));
                c.ow = c.lw = ow16;
                c.oh = c.lh = oh;
                c.flags = (int)(VIS_BASE_RGB | VIS_F_HAS_LOGIT | VIS_F_FILL | (band > 0 ? VIS_F_UNSURE : 0u));
                c.thr = thr;
                c.band = band;
                c.fill_a = 16;
                c.unsure_a = 16;
                c.fill[0] = 255u;
                c.unsure[0] = 255u;
                c.unsure[1] = 200u;
                if (job_overlay_compose(&c, pad_rgb, own, NULL, NULL, ovl, ovl_ps, &pl, &calls) != 0) {
                    rc = job_fail(j, "tile: overlay failed");
                    break;
                }
                vis_pl_us += pl;
                o_us = (uint32_t)(job_now_us() - t1);
            }
        }
        /* blob: header + own logits + mask + overlay */
        put32(blob + 0, idx);
        put32(blob + 4, k);
        put32(blob + 8, (uint32_t)tx);
        put32(blob + 12, (uint32_t)ty);
        put32(blob + 16, (uint32_t)tw);
        put32(blob + 20, (uint32_t)th);
        put32(blob + 24, (uint32_t)ox);
        put32(blob + 28, (uint32_t)oy);
        put32(blob + 32, (uint32_t)ow);
        put32(blob + 36, (uint32_t)oh);
        put32(blob + 40, state);
        put32(blob + 44, run_us);
        put32(blob + 48, c_us);
        put32(blob + 52, p_us);
        put32(blob + 56, o_us);
        put32(blob + 60, nv);
        put32(blob + 64, nc);
        put32(blob + 68, 0u);
        if ((state & 1u) == 0u) {
            if (j->flags & TILE_LOGITS) {
                for (r = 0; r < oh; r++) {
                    memcpy(blob + bn, own + (size_t)r * ow16, (size_t)ow);
                    bn += (size_t)ow;
                }
            }
            if (j->flags & TILE_MASK) {
                const size_t mb = (size_t)oh * pp_mask_row_bytes(ow16);

                memcpy(blob + bn, mask, mb);
                bn += mb;
            }
            if (j->flags & TILE_OVERLAY) {
                for (r = 0; r < oh; r++) {
                    memcpy(blob + bn, ovl + (size_t)r * ow16 * 3u, (size_t)ow * 3u);
                    bn += (size_t)ow * 3u;
                }
            }
        }
        crop_us += c_us;
        unet_us += run_us;
        post_us += p_us;
        ovl_us += o_us;
        {
            const uint32_t tile_us = (uint32_t)(job_now_us() - t0);

            if ((state & 1u) == 0u && tile_us > max_tile_us) {
                max_tile_us = tile_us;
            }
        }
        if (job_blob(j, "tile", blob, bn, 0) != 0) {
            rc = -1;
            break;
        }
        {
            char pj[160];

            snprintf(pj, sizeof(pj), "{\"stage\":\"tile\",\"done\":%u,\"total\":%u,\"index\":%u}",
                     (unsigned)(k + 1u), (unsigned)ntile, (unsigned)idx);
            if (job_progress_json(j, pj) != 0) {
                rc = -1;
                break;
            }
        }
    }
    if (rc == 0) {
        const double ms = (double)(job_now_us() - t_job) / 1000.0;

        jb_init(&b, js, sizeof(js));
        jb_add(&b, "{\"ok\":true,\"level\":[%u,%u],\"box\":[%d,%d,%d,%d],\"tiles\":%u,\"grid\":[%u,%u],\"run\":%u,"
                   "\"skipped\":%u", (unsigned)lw, (unsigned)lh, (int)bx, (int)by, (int)bw, (int)bh,
               (unsigned)ntile, (unsigned)nax, (unsigned)nay, (unsigned)run, (unsigned)skipped);
        jb_add(&b, ",\"computed_px\":%llu,\"own_px\":%llu,\"box_px\":%llu,\"halo_overhead\":%.4f,\"useful_ratio\":%.4f",
               (unsigned long long)comp_px, (unsigned long long)own_px, (unsigned long long)box_px,
               own_px ? (double)comp_px / (double)own_px - 1.0 : 0.0, comp_px ? (double)own_px / (double)comp_px : 0.0);
        jb_add(&b, ",\"vessels\":%llu,\"model\":%u,\"thr\":%d,\"band\":%d,\"source\":\"%s\",\"post\":\"%s\",\"overlay\":\"%s\"",
               (unsigned long long)vessels, (unsigned)model, (int)thr, (int)band, source ? "file" : "upload",
               post_ps ? "ps" : "pp", (j->flags & TILE_OVERLAY) ? (ovl_ps ? "ps" : "vis") : "none");
        jb_add(&b, ",\"ms\":%.3f,\"unet_ms\":%.3f,\"crop_ms\":%.3f,\"post_ms\":%.3f,\"ovl_ms\":%.3f,\"pp_pl_ms\":%.3f,"
                   "\"vis_pl_ms\":%.3f,\"max_tile_ms\":%.3f", ms, unet_us / 1000.0, crop_us / 1000.0, post_us / 1000.0,
               ovl_us / 1000.0, pp_pl_us / 1000.0, vis_pl_us / 1000.0, max_tile_us / 1000.0);
        job_pl_split_json(&b);
        jb_add(&b, "}");
        rc = job_result(j, js);
    }
done:
    (void)unet_set_shift(0);              /* leave the engine at the full 512 frame */
    if (src.fd >= 0) {
        close(src.fd);
    }
    free(ax);
    free(ay);
    free(order);
    free(seen);
    free(rgb);
    free(pad_rgb);
    free(ovl);
    free(lg);
    free(own);
    free(mask);
    free(blob);
    return rc;
}
