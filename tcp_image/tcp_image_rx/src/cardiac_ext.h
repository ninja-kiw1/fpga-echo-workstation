/* cardiac_ext.h -- whole-heart extensions of echo_ef modes 2 / 3 (10-25), all from the A4C clip already on the board:
 *  - regional wall motion: 6 A4C segments (HMC-QU order SEG3 basal septal, SEG9 mid septal, SEG14 apical septal,
 *    SEG16 apical lateral, SEG12 mid anterolateral, SEG6 basal anterolateral) from the board's own LV masks at each
 *    beat's ED / ES (zero extra model). Geometry from the ED mask: principal axis (apex = upper end), u = 0 base .. 1
 *    apex, side of the axis through the ED centroid (left = septum: LV on the right of an A4C screen); region = third
 *    of u x side; ES pixels classified in the same ED frame. fac_k = 1 - A_es_k / A_ed_k, averaged over the beats.
 *    z_k = (fac_k - mu_k) / sd_k (mu / sd of non-MI segments, HMC-QU DEV); segment prompt when -z_k >= t_seg;
 *    clip prompt when w0 + w1 (EF - 50) / 10 + w2 mean z + w3 min z >= t_rec (logistic, HMC-QU DEV).
 *    Port of E:/PLD/wallmotion/wm_proto.py (frame_of / regions / clip_features).
 *  - right heart: programs 21 cardiac_rv / 22 cardiac_ra (CardiacUDA) on the beats' ED / ES frames, largest
 *    4-connected component, plausibility as E:/PLD/cardiac_rv/cross_check.py: area >= min_frac x LV area, centroid
 *    left of the LV centroid, share inside the LV mask < max_overlap.
 * Wording: measurement aid / review prompt only, no diagnosis.
 * Params block (little endian), after KF01 (mode 2: byte 688) or LV01 (mode 3: byte 800):
 *   0 u32 "HC01"  4 u8 flags (bit0 wall motion, bit1 right heart)  5 u8 RV program  6 u8 RA program  7 u8 0
 *   8 f64 mu[6]  56 f64 sd[6]  104 f64 t_seg  112 f64 w[4]  144 f64 t_rec  152 f64 min_frac  160 f64 max_overlap
 *   168 f64 0  -> 176 bytes
 */
#ifndef CARDIAC_EXT_H
#define CARDIAC_EXT_H

#include <stdint.h>

#define HC_MAGIC 0x31304348u        /* "HC01" */
#define HC_BYTES 176u
#define WM_SEG   6

typedef struct {
    int    flags, rv_model, ra_model;
    double mu[WM_SEG], sd[WM_SEG], t_seg, w[4], t_rec, min_frac, max_overlap;
} hc_par_t;

typedef struct {
    double fac[WM_SEG], z[WM_SEG], score;
    int    seg[WM_SEG], flag, has_score, nbeats;
} wm_out_t;

typedef struct {
    int    area, lv_area, present, side, ok;
    double overlap, cy, cx;
} rh_part_t;

int  hc_parse(const uint8_t *x, uint32_t nbytes, hc_par_t *p);
/* one beat: 0 and fac[6] (HMC-QU order), -1 when the ED mask has < 50 pixels or no extent */
int  wm_beat(const uint8_t *ed, const uint8_t *es, int w, int h, double fac[WM_SEG]);
/* fac = mean over nbeats beats; ef = calibrated clip EF or NaN (then no clip score) */
void wm_eval(const hc_par_t *p, const double fac[WM_SEG], int nbeats, double ef, wm_out_t *o);
/* largest 4-connected component of in (0 / 1) -> out; returns its area */
int  rh_largest(const uint8_t *in, int w, int h, uint8_t *out);
void rh_check(const uint8_t *part, const uint8_t *lv, int w, int h, const hc_par_t *p, rh_part_t *o);

#endif
