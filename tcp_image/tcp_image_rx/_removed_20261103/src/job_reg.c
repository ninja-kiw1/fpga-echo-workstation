/* job_reg.c -- JOB_REG_SEARCH: F5 frame registration searched entirely on the board.
 *
 * Port of tcp_image_ui/registration.py (same candidates, same tie-break, same numbers):
 * 16 candidate shifts of B are tiled 4 x 4 into one 512 frame (R = A, G = shifted B, B = 0), the PL runs
 * program reg_absdiff (logit ~ |R - G| / 2), the PS sums each tile and divides by the overlap (MAD),
 * and the search (three-step 4/2/1, or full +-radius) loops on the PS. The PC sends two frames and gets
 * (dx, dy) back instead of driving 3 round trips.
 *
 * params (8 bytes): u8 mode (0 three-step, 1 full), u8 radius (full search, <= 15), u8 model (reg_absdiff),
 *                   u8 engine (v5.16: 0 = this U-Net path, 1 = the r2 PL engine vis_layer when the board has it,
 *                   else this path; "engine" in the result says which ran), u8 steps[4] (three-step step sizes,
 *                   0-terminated; all 0 = 4, 2, 1)
 *   optional (v5.16, PL engine only): u8 subpel (0..2: then 1/2 and 1/4 pixel refinement), u8 size (0 = 128, 1 = 256)
 * data: A then B, 128 x 128 grey each (registration.down128 of the 512 frames), or 256 x 256 with size 1
 * flags: bit0 = also return the cost of every evaluated candidate (blob "costs": i8 dx, i8 dy, f64 MAD; with the
 *        PL engine also "costs_q": i8 qx, i8 qy, f64 MAD for every candidate in quarter pixels)
 *
 * PL engine (vis_layer, src/vis_ai.c): shift, quarter-pixel interpolation, cost and the search all on the PL, with
 * the reg_absdiff logit table, so the integer results are the same numbers as this U-Net path (MAD = SAD / overlap,
 * the PL compares S1 * O2 < S2 * O1, which orders exactly as the doubles; ai/vis_hls/README.md).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ai_view.h"
#include "image_proto.h"
#include "job.h"
#include "unet_ai.h"
#include "vis_ai.h"

#define T     128
#define W     512
#define GRID  4
#define MAXR  15
#define SIDE  (2 * MAXR + 1)

typedef struct {
    const uint8_t *a, *b;
    unsigned       model;
    double         cost[SIDE][SIDE];   /* NaN-free: valid[] says what was computed */
    uint8_t        valid[SIDE][SIDE];
    uint32_t       frames, run_us;
    uint8_t        rgb[W * W * 3];
    int8_t         lg[W * W];
} reg_t;

static int overlap(int dx, int dy)
{
    int ox = T - abs(dx), oy = T - abs(dy);

    return (ox > 0 ? ox : 0) * (oy > 0 ? oy : 0);
}

/* tile k: R = A, G = B shifted by (dx, dy) with A outside the overlap; B channel 0 */
static void put_tile(reg_t *g, int k, int dx, int dy)
{
    int ty = (k / GRID) * T, tx = (k % GRID) * T, y, x;

    for (y = 0; y < T; y++) {
        uint8_t *o = g->rgb + ((size_t)(ty + y) * W + tx) * 3u;
        int      sy = y - dy;

        for (x = 0; x < T; x++) {
            int sx = x - dx;

            o[3 * x] = g->a[y * T + x];
            o[3 * x + 1] = (sy >= 0 && sy < T && sx >= 0 && sx < T) ? g->b[sy * T + sx] : g->a[y * T + x];
            o[3 * x + 2] = 0u;
        }
    }
}

/* evaluate the candidates not computed yet, 16 per PL run (registration.Registrar.evaluate) */
static int evaluate(job_t *j, reg_t *g, const int (*c)[2], int n)
{
    int todo[SIDE * SIDE][2], nt = 0, i, k;

    for (i = 0; i < n; i++) {
        int dup = 0;

        if (g->valid[c[i][1] + MAXR][c[i][0] + MAXR]) {
            continue;
        }
        for (k = 0; k < nt; k++) {
            dup |= (todo[k][0] == c[i][0] && todo[k][1] == c[i][1]);
        }
        if (!dup) {
            todo[nt][0] = c[i][0];
            todo[nt][1] = c[i][1];
            nt++;
        }
    }
    for (i = 0; i < nt; i += GRID * GRID) {
        uint8_t blk[REG_BLOCK_BYTES];
        int     m = (nt - i < GRID * GRID) ? nt - i : GRID * GRID;

        for (k = 0; k < GRID * GRID; k++) {     /* missing tiles: A vs A (difference 0) */
            put_tile(g, k, k < m ? todo[i + k][0] : 0, k < m ? todo[i + k][1] : 0);
            if (k >= m) {
                int ty = (k / GRID) * T, tx = (k % GRID) * T, y, x;

                for (y = 0; y < T; y++) {
                    for (x = 0; x < T; x++) {
                        g->rgb[((size_t)(ty + y) * W + tx + x) * 3u + 1u] = g->a[y * T + x];
                    }
                }
            }
        }
        if (job_unet(g->model, 0, g->rgb, g->lg, &g->run_us) != 0) {
            return -1;
        }
        g->frames++;
        ai_view_fill_reg(blk, g->lg, W, W, 0u);
        for (k = 0; k < m; k++) {
            int32_t s = (int32_t)proto_get_le32(blk + 32u + 4u * (unsigned)k);
            int     dx = todo[i + k][0], dy = todo[i + k][1];

            g->cost[dy + MAXR][dx + MAXR] = (double)s / (double)overlap(dx, dy);
            g->valid[dy + MAXR][dx + MAXR] = 1u;
        }
        if (job_progress(j, "search", g->frames, 0u, 0) != 0) {
            return -1;
        }
    }
    return 0;
}

/* lexicographic (cost, |dx| + |dy|, dx, dy) minimum, as registration.Registrar.best */
static int best(job_t *j, reg_t *g, const int (*c)[2], int n, int *bx, int *by)
{
    int i, k = 0;

    if (evaluate(j, g, c, n) != 0) {
        return -1;
    }
    for (i = 1; i < n; i++) {
        double ci = g->cost[c[i][1] + MAXR][c[i][0] + MAXR], ck = g->cost[c[k][1] + MAXR][c[k][0] + MAXR];
        int    li = abs(c[i][0]) + abs(c[i][1]), lk = abs(c[k][0]) + abs(c[k][1]);

        if (ci < ck || (ci == ck && (li < lk || (li == lk && (c[i][0] < c[k][0] ||
                                                             (c[i][0] == c[k][0] && c[i][1] < c[k][1])))))) {
            k = i;
        }
    }
    *bx = c[k][0];
    *by = c[k][1];
    return 0;
}

static int cmp_q(const void *a, const void *b)
{
    const vis_rec_t *x = (const vis_rec_t *)a, *y = (const vis_rec_t *)b;

    return x->qy != y->qy ? x->qy - y->qy : x->qx - y->qx;
}

/* the r2 PL engine: everything on vis_layer, results in the same form as the U-Net path */
static int reg_vis(job_t *j, int n, int mode, int radius, const int *steps, int ns, int subpel)
{
    static vis_reg_result_t r;
    static vis_rec_t        sorted[VIS_MAX_RECORDS];
    static uint8_t          tab[VIS_MAX_RECORDS * 10];
    uint8_t                 st[4] = {0, 0, 0, 0};
    char                    js[1024];
    jbuf_t                  b;
    double                  mad;
    int                     i, k, nint;
    size_t                  nb;

    for (i = 0; i < ns && i < 4; i++) {
        st[i] = (uint8_t)steps[i];
    }
    k = vis_reg(j->data, j->data + (size_t)n * n, n, mode, radius, st, subpel, vis_absdiff_table, &r);
    if (k != 0) {
        return job_fail(j, k == -3 ? "PL engine rejected the search (radius / steps out of range)" : "PL engine failed");
    }
    memcpy(sorted, r.rec, sizeof(vis_rec_t) * (size_t)r.nrec);
    qsort(sorted, (size_t)r.nrec, sizeof(vis_rec_t), cmp_q);
    if (j->flags & 1u) {
        for (k = 0; k < 2; k++) {                  /* "costs": integer candidates in pixels; "costs_q": all */
            nb = 0u;
            for (i = 0; i < r.nrec; i++) {
                const vis_rec_t *c = &sorted[i];

                if (i > 0 && c->qx == sorted[i - 1].qx && c->qy == sorted[i - 1].qy) {
                    continue;
                }
                if (k == 0 && ((c->qx & 3) || (c->qy & 3))) {
                    continue;
                }
                mad = (double)c->sad / (double)c->overlap;
                tab[nb] = (uint8_t)(int8_t)(k == 0 ? c->qx / 4 : c->qx);
                tab[nb + 1u] = (uint8_t)(int8_t)(k == 0 ? c->qy / 4 : c->qy);
                memcpy(tab + nb + 2u, &mad, 8u);           /* aarch64: little-endian IEEE */
                nb += 10u;
            }
            if ((k == 0 || subpel > 0) && job_blob(j, k == 0 ? "costs" : "costs_q", tab, nb, 0) != 0) {
                return -1;
            }
        }
    }
    nint = mode == 0 ? ns + 1 : 2;
    mad = (double)r.sad / (double)r.overlap;
    jb_init(&b, js, sizeof(js));
    jb_add(&b, "{\"ok\":true,\"engine\":\"vis\",\"size\":%d,\"dx\":%d,\"dy\":%d,\"qx\":%d,\"qy\":%d,\"mad\":%.17g,"
               "\"frames\":0,\"evals\":%d,\"pl_ms\":%.3f,\"total_ms\":%.3f,\"path\":[",
           n, r.qx / 4, r.qy / 4, r.qx, r.qy, mad, r.nrec, r.pl_us / 1000.0, (double)(job_now_us() - j->t0_us) / 1000.0);
    for (i = 0; i < nint && mode == 0; i++) {
        jb_add(&b, "%s[%d,%d]", i ? "," : "", r.path[i][0] / 4, r.path[i][1] / 4);
    }
    jb_add(&b, "],\"path_q\":[");
    for (i = 0; i < r.npath; i++) {
        jb_add(&b, "%s[%d,%d]", i ? "," : "", r.path[i][0], r.path[i][1]);
    }
    jb_add(&b, "]}");
    return job_result(j, js);
}

int job_reg_run(job_t *j)
{
    static reg_t g;
    char         js[1024];
    jbuf_t       b;
    int          mode, radius, steps[4] = {4, 2, 1, 0}, ns = 3, cx = 0, cy = 0, path[5][2], np = 1, i, dx, dy;
    int          cand[SIDE * SIDE][2], nc;

    int          engine, subpel, n;

    if (j->param_bytes < 8u) {
        return job_fail(j, "reg_search needs 8 bytes of params");
    }
    mode = j->param[0];
    radius = j->param[1];
    engine = j->param[3];
    subpel = j->param_bytes >= 9u ? j->param[8] : 0;
    n = (j->param_bytes >= 10u && j->param[9] == 1u) ? 256 : 128;
    if (j->param[4] != 0u) {
        for (ns = 0; ns < 4 && j->param[4 + ns] != 0u; ns++) {
            steps[ns] = j->param[4 + ns];
        }
    }
    if (j->data_bytes != 2u * (uint32_t)n * (uint32_t)n) {
        return job_fail(j, "reg_search needs two grey frames of the given size (128 x 128, or 256 x 256 with size 1)");
    }
    if (engine == 1 && vis_ready()) {
        return reg_vis(j, n, mode, radius, steps, ns, subpel);
    }
    if (n != T || subpel != 0) {
        return job_fail(j, "256 / sub-pixel registration needs the PL engine (r2 firmware with vis_layer)");
    }
    memset(&g, 0, sizeof(g));
    g.model = j->param[2];
    g.a = j->data;
    g.b = j->data + T * T;
    if (mode > 1 || radius > MAXR || (mode == 1 && radius < 1)) {
        return job_fail(j, "bad mode / radius");
    }
    path[0][0] = path[0][1] = 0;
    if (mode == 0) {
        for (i = 0; i < ns; i++) {
            int s = steps[i], u, v;

            nc = 0;
            for (v = -1; v <= 1; v++) {
                for (u = -1; u <= 1; u++) {
                    cand[nc][0] = cx + u * s;
                    cand[nc][1] = cy + v * s;
                    nc++;
                }
            }
            for (u = 0; u < nc; u++) {
                if (abs(cand[u][0]) > MAXR || abs(cand[u][1]) > MAXR) {
                    return job_fail(j, "three-step search left +-15");
                }
            }
            if (best(j, &g, (const int (*)[2])cand, nc, &cx, &cy) != 0) {
                return j->failed ? -1 : job_fail(j, "PL run failed");
            }
            if (np < 5) {
                path[np][0] = cx;
                path[np][1] = cy;
                np++;
            }
        }
    } else {
        nc = 0;
        for (dy = -radius; dy <= radius; dy++) {
            for (dx = -radius; dx <= radius; dx++) {
                cand[nc][0] = dx;
                cand[nc][1] = dy;
                nc++;
            }
        }
        if (best(j, &g, (const int (*)[2])cand, nc, &cx, &cy) != 0) {
            return j->failed ? -1 : job_fail(j, "PL run failed");
        }
    }
    if (j->flags & 1u) {
        static uint8_t tab[SIDE * SIDE * 10];
        size_t         n = 0u;

        for (dy = -MAXR; dy <= MAXR; dy++) {
            for (dx = -MAXR; dx <= MAXR; dx++) {
                if (g.valid[dy + MAXR][dx + MAXR]) {
                    tab[n] = (uint8_t)(int8_t)dx;
                    tab[n + 1u] = (uint8_t)(int8_t)dy;
                    memcpy(tab + n + 2u, &g.cost[dy + MAXR][dx + MAXR], 8u);   /* aarch64: little-endian IEEE */
                    n += 10u;
                }
            }
        }
        if (job_blob(j, "costs", tab, n, 0) != 0) {
            return -1;
        }
    }
    jb_init(&b, js, sizeof(js));
    jb_add(&b, "{\"ok\":true,\"engine\":\"unet\",\"engine_requested\":%d,\"dx\":%d,\"dy\":%d,\"mad\":%.17g,\"frames\":%u,"
               "\"pl_ms\":%.3f,\"total_ms\":%.3f,\"path\":[",
           engine, cx, cy, g.cost[cy + MAXR][cx + MAXR], (unsigned)g.frames, g.run_us / 1000.0,
           (double)(job_now_us() - j->t0_us) / 1000.0);
    for (i = 0; i < np && mode == 0; i++) {
        jb_add(&b, "%s[%d,%d]", i ? "," : "", path[i][0], path[i][1]);
    }
    jb_add(&b, "]}");
    return job_result(j, js);
}
