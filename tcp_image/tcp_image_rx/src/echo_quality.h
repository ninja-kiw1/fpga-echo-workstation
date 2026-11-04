/* echo_quality.h -- innovations 2 (zero-extra-model quality light) and 3 (split-conformal EF interval), 10-24.
 * Port of E:/PLD/quality/features.py, quality_camus.py (score), conformal_ef.py (sigma, interval, review).
 *
 * Features (per clip; the per-frame ones averaged over the frames the PL segmented = the keyframes):
 *   0 touch     LV border share within 2 px of the sector edge         5 multi     >1 component before clean
 *   1 apex_gap  (LV top - sector top) / sector height                   6 contrast  (ring - cavity) / (ring + cavity + 1)
 *   2 length    LV long-axis extent / sector height                     7 stability mean Dice of consecutive LV masks
 *   3 round     LV short / long axis extent                             8 roughness mean |2nd diff of LV area| / mean area
 *   4 unc       share of the +-2 px band around the LV border with |q| <= 2
 * Sector: largest 4-connected component of (max over all frames > 4), holes filled (the live-mode auto-detect).
 * Score = w0 + sum w_i (x_i - mu_i) / sd_i (logistic weights fitted on CAMUS TRAIN; mu / sd of the program's own
 * TRAIN data); light 2 red (>= t_red), 1 yellow (>= t_green), 0 green. Reasons = the two largest positive w_i z_i.
 * Interval: sigma = exp(b0 + sum b_j z_j) over [beat EF SD (calibrated), 1 / beats, fallback, unc, roughness,
 * stability, contrast, apex_gap, multi] standardised with the TRAIN-subset mu / sd; half width = q_p sigma (q from
 * VAL, split conformal); "review" when the 80 % interval contains 40 or 50 (ESC 2021 band edges) strictly inside.
 * Wording: measurement aid / review prompt only, no diagnosis.
 */
#ifndef ECHO_QUALITY_H
#define ECHO_QUALITY_H

#include <stdint.h>

#define QF_N 9
#define QS_N 9

typedef struct {
    double w[QF_N + 1], mu[QF_N], sd[QF_N], t_green, t_red;     /* quality score (w[0] = bias) */
    double b[QS_N + 1], zmu[QS_N], zsd[QS_N], q80, q90;         /* sigma model + conformal quantiles */
} eq_model_t;

typedef struct {
    double f[QF_N];              /* clip features (order above) */
    double score;
    int    light, reason[2];     /* reason: feature index or -1 */
    int    nkey;
} eq_quality_t;

typedef struct {
    double sigma, lo80, hi80, lo90, hi90;
    int    review;
} eq_interval_t;

/** Sector of a clip: frames T x H x W -> sector mask H x W; returns its top row and height. */
void eq_sector(const uint8_t *frames, int T, int W, int H, uint8_t *sector, int *top, int *height);

/** Accumulator of the per-frame features over a clip. */
typedef struct {
    int     W, H, top, height, nkey, nframes;
    double  sum[7];              /* touch, apex_gap, length, round, unc, multi, contrast */
    double  dice_sum, area_prev, area_prev2, rough_sum, area_sum;
    int     ndice, nrough;
    uint8_t *sector, *edge, *prev_mask;
} eq_acc_t;

int  eq_acc_init(eq_acc_t *a, const uint8_t *sector, int W, int H, int top, int height);
void eq_acc_free(eq_acc_t *a);
/** A PL frame: grey frame, its logits q (H x W, int8, the board decision is q >= 1), the cleaned mask and the
 *  component count of the raw mask. */
void eq_acc_key(eq_acc_t *a, const uint8_t *frame, const int8_t *q, const uint8_t *mask, int ncomp);
/** Every frame (keyframe or tracked), in order: the final LV mask (stability / roughness). */
void eq_acc_mask(eq_acc_t *a, const uint8_t *mask);
void eq_acc_finish(const eq_acc_t *a, const eq_model_t *m, eq_quality_t *out);

/** Interval from the calibrated EF, the beat EF SD (calibrated units, 0 if < 2 beats), beats, fallback and the
 *  clip features. */
void eq_interval(const eq_model_t *m, double ef, double beat_sd, int nbeats, int fallback, const double *f,
                 eq_interval_t *out);

/** echo_ef mode-2 parameter extension "KF01" (job_echo.c): 560 bytes at x (layout there) -> schedule, flags,
 *  EF calibration a / b, models. Returns 0, or -1 (short / bad magic). Shared by the server and the host DLL so the
 *  PC packing (tcp_image_ui/jobs.echo_kf_params) is checked against this very parser. */
#define KF_EXT_BYTES 560u
#define KF_MAGIC     0x3130464Bu       /* "KF01" */
struct kf_cfg;
int eq_parse_ext(const uint8_t *x, uint32_t nbytes, struct kf_cfg *kc, int *flags, double *a, double *b,
                 eq_model_t *md);

/** Host DLL: features of a whole clip at once (frames, logits only on keyframes, final masks, is_key, ncomp).
 *  Writes f[QF_N]. */
void eq_clip_features(const uint8_t *frames, const int8_t *q, const uint8_t *masks, const uint8_t *is_key,
                      const int32_t *ncomp, int T, int W, int H, double *f);

/** v5.26: largest 4-connected component + fill holes of any W x H mask (the board decision); returns the component
 *  count before cleaning. */
int eq_clean(const uint8_t *m, uint8_t *out, int W, int H);

/** v5.26 CAMUS live chain (E:/PLD/quality/live_camus): quality of a window of T frames where only nsel of them were
 *  segmented (F1 every k-th frame). Sector from all T frames; frame sel[i] (index in the window) has logits q[i],
 *  cleaned mask masks[i] and component count ncomp[i] (all contiguous, in order); per-frame features and stability /
 *  roughness over those nsel frames only; score / light with md. Shared by the server's quality thread and the host
 *  DLL. 0 ok, -1 out of memory. */
int eq_window(const uint8_t *frames, int T, int W, int H, const int *sel, int nsel, const int8_t *q,
              const uint8_t *masks, const int32_t *ncomp, const eq_model_t *md, eq_quality_t *o);

/** v5.26 chain 2 (11-02): F1's 128 x 128 logits -> the live engine's 112 x 112 logits by nearest sampling
 *  (q112[y][x] = lg128[(2 y + 1) * 128 / 224][(2 x + 1) * 128 / 224]). */
void eq_f1_to112(const int8_t *lg128, int8_t *q112);

#endif /* ECHO_QUALITY_H */
