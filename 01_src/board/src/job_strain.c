/* job_strain.c -- JOB_STRAIN (7): part E myocardial speckle tracking -> GLS-like curve, a whole view on the board.
 *
 * Exploratory, assisted measurement only (no diagnosis, no relative-decline threshold): on CAMUS VAL the ES value is
 * off by MAE 3.1 percentage points after the TRAIN offset (E:/PLD/strain/e_step2_ps_block_matching_gls_prototype_2026-10-17.md);
 * poor-quality images basically fail.
 *
 * params (32 bytes, little endian): f64 sx, f64 sy (mm per 512-grid pixel, x / y: original size x spacing / 512),
 *         u32 lv model (0 = 2, F1 echo_lv), u32 epi model (0 = 18, camus_epi), u32 0, u32 0
 * data:   T frames of 512 x 512 u8 (the PC's 512 frame, R channel = host LANCZOS stretch), frame 0 = ED, last = ES
 *         (a CAMUS half sequence), 2 <= T <= STRAIN_MAXF
 * flags:  bit0 F_PSCLEAN  clean (largest 4-component + fill holes) on the PS instead of the PL pp_layer (default PL,
 *                         decision 10-13 §9.57; the PS clean always runs too as a cross-check: "clean_pl_eq_ps")
 *         bit1 F_CKERNEL  plain C SAD kernel instead of NEON neon2 (timing comparison; results are identical)
 *         bit2 F_LOGITS   also return blob "logits" (2 x 128 x 128 int8: lv, epi) for the board check
 * chain:  ED frame [::4, ::4] grey -> PL: lv model, epi model (128 tier) -> logit >= 1 + clean (PL pp_layer) ->
 *         strain.c on the PS: mid-wall line, per frame pair bm_track_line (512, +-6, k 9, V fit), lengths, curve
 *         (bit for bit = strain/step3/strain_port.py = strain/step2/track_val.py final config)
 * result: {"ok","frames","n_points","path_len","skel_px","ncomp":{lv,epi,myo},"clean":"pl"|"ps","clean_pl_eq_ps",
 *          "gls_pct":[T] (100 * (L(t) / L(0) - 1)),"length_mm":[T],"es_raw_pct","es_cal_pct","offset_pct",
 *          "peak_raw_pct","edge_points","kernel","calib","ms":{...},"pl_split",...}
 * blobs:  "points" i32 [T][n][2] (y, x) Q8 in the 512 grid; "line" i32 [path_len][2] (y, x) 512 grid;
 *         "myo" 512 x 512 bits (bit k&7 of byte k>>3); optional "logits"
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "job.h"
#include "pp_ai.h"
#include "strain.h"

#define STRAIN_MAXF 128
#define F_PSCLEAN   1u
#define F_CKERNEL   2u
#define F_LOGITS    4u

static double f64le(const uint8_t *p)
{
    double   d;
    uint64_t u = 0u;

    for (int i = 7; i >= 0; i--) {
        u = (u << 8) | p[i];
    }
    memcpy(&d, &u, sizeof(d));
    return d;
}

static uint32_t u32le(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* run one 128 model on the ED grey input, threshold >= 1 and clean on the PL (unless ps); mask 0/1.
 * Returns 0 ok, -1 PL failure; *pl_cleaned = 1 when the PL clean was used */
static int seg128(unsigned model, const uint8_t *in, int8_t *lg, uint8_t *mask, int ps, int *pl_cleaned,
                  int *ncomp, uint32_t *unet_us, double *pp_ms)
{
    static uint8_t bits[STRAIN_M * 16];   /* pp_mask_row_bytes(128) = 16 */
    pp_result_t    pr;

    if (job_unet(model, 2, in, lg, unet_us) != 0) {
        return -1;
    }
    *pl_cleaned = 0;
    if (!ps && pp_ready() && pp_run(lg, STRAIN_M, STRAIN_M, PP_OP_MASK | PP_OP_CLEAN, 0, 0, STRAIN_M, STRAIN_M, 1, 1, 1,
                                   bits, &pr) == 0 && pr.status == 0u) {
        const size_t rb = pp_mask_row_bytes(STRAIN_M);
        for (int y = 0; y < STRAIN_M; y++) {
            for (int x = 0; x < STRAIN_M; x++) {
                mask[y * STRAIN_M + x] = (uint8_t)((bits[y * rb + (x >> 3)] >> (x & 7)) & 1u);
            }
        }
        *pl_cleaned = 1;
        *ncomp = (int)pr.comps;
        *pp_ms += pr.total_us / 1000.0;
    }
    return 0;
}

static void json_array(jbuf_t *b, const char *name, const double *v, int n, double scale)
{
    jb_add(b, ",\"%s\":[", name);
    for (int i = 0; i < n; i++) {
        jb_add(b, "%s%.17g", i ? "," : "", v[i] * scale);
    }
    jb_add(b, "]");
}

int job_strain_run(job_t *j)
{
    static int8_t  lg[2][STRAIN_M * STRAIN_M];
    static uint8_t in[STRAIN_M * STRAIN_M * 3];
    static uint8_t msk[2][STRAIN_M * STRAIN_M], ps[2][STRAIN_M * STRAIN_M];
    static int32_t lab[STRAIN_M * STRAIN_M], stk[STRAIN_M * STRAIN_M];
    strain_ws_t    w;
    const size_t   FB = (size_t)STRAIN_W * STRAIN_W;
    double         sx, sy, pp_ms = 0.0, *L = NULL, *g = NULL, *tmp = NULL, peak;
    int32_t (*q)[2] = NULL;
    uint8_t       *mbits = NULL;
    char          *js = NULL;
    unsigned       model[2];
    uint32_t       unet_us = 0u;
    int            T, rc, edges = 0, pl_clean[2], ncomp_pl[2] = {-1, -1}, use_ps, eq = 1, opts;
    uint64_t       t0 = job_now_us(), t_seg, t_line, t_track, t_len;
    jbuf_t         b;

    if (j->param_bytes < 32u) {
        return job_fail(j, "strain: 32 bytes of params expected (sx, sy, lv model, epi model)");
    }
    sx = f64le(j->param);
    sy = f64le(j->param + 8);
    model[0] = u32le(j->param + 16) ? u32le(j->param + 16) : 2u;
    model[1] = u32le(j->param + 20) ? u32le(j->param + 20) : 18u;
    T = (int)(j->data_bytes / FB);
    if (!(sx > 0.0) || !(sy > 0.0) || T < 2 || T > STRAIN_MAXF || (size_t)T * FB != j->data_bytes) {
        return job_fail(j, "strain: sx, sy > 0 and 2..128 frames of 512 x 512 bytes expected");
    }
    if (strain_ws_init(&w) != STRAIN_OK) {
        return job_fail(j, "strain: out of memory");
    }
    use_ps = (j->flags & F_PSCLEAN) != 0u;
    opts = (j->flags & F_CKERNEL) ? 0 : BM_OPT_NEON;

    /* ---- 1. ED segmentation on the PL (grey -> 3 channels, as the v6c grey path) ---- */
    job_progress(j, "segment", 0u, 2u, 1);
    for (int y = 0; y < STRAIN_M; y++) {
        for (int x = 0; x < STRAIN_M; x++) {
            const uint8_t v = j->data[(size_t)(4 * y) * STRAIN_W + 4 * x];
            uint8_t      *o = in + 3 * (y * STRAIN_M + x);
            o[0] = o[1] = o[2] = v;
        }
    }
    for (int k = 0; k < 2; k++) {
        if (seg128(model[k], in, lg[k], msk[k], use_ps, &pl_clean[k], &ncomp_pl[k], &unet_us, &pp_ms) != 0) {
            strain_ws_free(&w);
            return job_fail(j, k ? "strain: PL run failed (epicardium model; program package installed?)"
                                 : "strain: PL run failed (LV model)");
        }
        for (int i = 0; i < STRAIN_M * STRAIN_M; i++) {      /* PS clean: fallback and cross-check */
            ps[k][i] = (uint8_t)(lg[k][i] >= 1);
        }
        const int nc = strain_clean(ps[k], STRAIN_M, STRAIN_M, lab, stk);
        if (pl_clean[k]) {
            eq &= memcmp(ps[k], msk[k], sizeof(ps[k])) == 0;
        } else {
            memcpy(msk[k], ps[k], sizeof(ps[k]));
            ncomp_pl[k] = nc;
        }
    }
    t_seg = job_now_us();

    /* ---- 2. mid-wall line (masks already clean) ---- */
    rc = strain_ed_line(&w, msk[0], msk[1], 0, sx, sy);
    t_line = job_now_us();
    if (rc != STRAIN_OK) {
        strain_ws_free(&w);
        return job_fail(j, rc == STRAIN_E_NOMYO ? "strain: no myocardium found at ED (epicardium minus LV is empty)"
                                                : "strain: mid-wall line too short to track");
    }

    /* ---- 3. tracking (PS, NEON) ---- */
    const int n = w.n;
    q = (int32_t (*)[2])malloc(sizeof(int32_t) * 2u * (size_t)n * T);
    L = (double *)malloc(sizeof(double) * T);
    g = (double *)malloc(sizeof(double) * T);
    tmp = (double *)malloc(sizeof(double) * 5u * STRAIN_NMAX);
    mbits = (uint8_t *)calloc(FB / 8u, 1u);
    js = (char *)malloc(32768u);
    if (!q || !L || !g || !tmp || !mbits || !js) {
        rc = job_fail(j, "strain: out of memory");
        goto out;
    }
    memcpy(q, w.q8, sizeof(int32_t) * 2u * n);
    for (int t = 0; t + 1 < T; t++) {
        memcpy(q + (size_t)(t + 1) * n, q + (size_t)t * n, sizeof(int32_t) * 2u * n);
        const int e = strain_track_step(j->data + (size_t)t * FB, j->data + (size_t)(t + 1) * FB, n,
                                        q + (size_t)(t + 1) * n, opts);
        if (e < 0) {
            rc = job_fail(j, "strain: tracking failed");
            goto out;
        }
        edges += e;
        if (job_progress(j, "track", (uint32_t)(t + 1), (uint32_t)(T - 1), 0) != 0) {
            rc = -1;
            goto out;
        }
    }
    t_track = job_now_us();
    for (int t = 0; t < T; t++) {
        L[t] = strain_line_length((const int32_t (*)[2])(q + (size_t)t * n), n, sx, sy, tmp);
    }
    peak = 0.0;
    for (int t = 0; t < T; t++) {
        g[t] = L[t] / L[0] - 1.0;
        peak = (t == 0 || g[t] < peak) ? g[t] : peak;
    }
    t_len = job_now_us();

    /* ---- 4. blobs + result ---- */
    for (size_t i = 0; i < FB; i++) {
        if (w.myo[i]) {
            mbits[i >> 3] |= (uint8_t)(1u << (i & 7u));
        }
    }
    if (job_blob(j, "points", q, sizeof(int32_t) * 2u * (size_t)n * T, 1) != 0 ||
        job_blob(j, "line", w.path, sizeof(int32_t) * 2u * (size_t)w.path_len, 1) != 0 ||
        job_blob(j, "myo", mbits, FB / 8u, 1) != 0 ||
        ((j->flags & F_LOGITS) && job_blob(j, "logits", lg, sizeof(lg), 0) != 0)) {
        rc = -1;
        goto out;
    }
    jb_init(&b, js, 32768u);
    jb_add(&b, "{\"ok\":true,\"job\":\"strain\",\"frames\":%d,\"n_points\":%d,\"path_len\":%d,\"skel_px\":%d", T, n,
           w.path_len, w.skel_px);
    jb_add(&b, ",\"models\":[%u,%u],\"ncomp\":{\"lv\":%d,\"epi\":%d,\"myo\":%d}", model[0], model[1], ncomp_pl[0],
           ncomp_pl[1], w.ncomp_myo);
    jb_add(&b, ",\"clean\":\"%s\",\"clean_pl_eq_ps\":%s", (pl_clean[0] && pl_clean[1]) ? "pl" : "ps",
           (pl_clean[0] || pl_clean[1]) ? (eq ? "true" : "false") : "null");
    json_array(&b, "gls_pct", g, T, 100.0);
    json_array(&b, "length_mm", L, T, 1.0);
    jb_add(&b, ",\"es_raw_pct\":%.17g,\"es_cal_pct\":%.17g,\"offset_pct\":%.17g,\"peak_raw_pct\":%.17g",
           g[T - 1] * 100.0, g[T - 1] * 100.0 + STRAIN_OFFSET_PP, STRAIN_OFFSET_PP, peak * 100.0);
    jb_add(&b, ",\"edge_points\":%d,\"kernel\":\"%s\",\"track\":{\"tier\":%d,\"R\":%d,\"k\":%d,\"fit\":\"v\","
               "\"step_mm\":%.1f,\"smooth\":%d}", edges, opts ? "neon2" : "c", STRAIN_W, STRAIN_R, STRAIN_K,
           STRAIN_STEP_MM, STRAIN_SMOOTH);
    jb_add(&b, ",\"calib\":\"TRAIN offset, strain/step2/results/calib_train.json %s (800 sequences)\"",
           STRAIN_CALIB_SHA);
    jb_add(&b, ",\"ms\":{\"segment\":%.3f,\"unet\":%.3f,\"pp\":%.3f,\"line\":%.3f,\"track\":%.3f,"
               "\"track_per_pair\":%.4f,\"length\":%.3f,\"total\":%.3f}",
           (t_seg - t0) / 1000.0, unet_us / 1000.0, pp_ms, (t_line - t_seg) / 1000.0, (t_track - t_line) / 1000.0,
           (t_track - t_line) / 1000.0 / (T - 1), (t_len - t_track) / 1000.0, (job_now_us() - t0) / 1000.0);
    job_pl_split_json(&b);
    jb_add(&b, ",\"note\":\"exploratory assisted measurement (GLS-like mid-wall length change); CAMUS VAL MAE 3.1 "
               "percentage points after the TRAIN offset; unreliable on poor images; not a diagnosis\"}");
    rc = job_result(j, js);
out:
    free(q);
    free(L);
    free(g);
    free(tmp);
    free(mbits);
    free(js);
    strain_ws_free(&w);
    return rc;
}
