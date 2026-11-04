/* strain.h -- part E: myocardial speckle tracking -> GLS-like curve (PS side; job_strain.c runs it on the board).
 *
 * Bit-for-bit C twin of E:/PLD/strain/step3/strain_port.py, which in turn equals E:/PLD/strain/step2/track_val.py
 * (final config final_t512_r6_k9_v_s5) on all 100 CAMUS VAL sequences (strain/step3/results/compare_port.json):
 *   lv, epi 128 x 128 masks -> clean (largest 4-component + fill holes) -> x4 -> myo = epi & ~lv -> largest
 *   8-component -> Zhang thinning (skimage 0.25 LUT) -> longest path (Dijkstra twice) -> 5-point moving average +
 *   3 mm resampling -> Q8 -> per frame pair bm_track_line (512, R 6, k 9, V fit) -> per frame 5-point moving average +
 *   length -> GLS-like(t) = L(t) / L(0) - 1. TRAIN offset STRAIN_OFFSET_PP for the ES value.
 * Floating point: build with -ffp-contract=off (no fused multiply-add), every sum in the numpy order.
 * Exploratory, assisted measurement only: VAL MAE 3.1 percentage points after the offset (strain/step2).
 */
#ifndef STRAIN_H
#define STRAIN_H

#include <stdint.h>

#include "bm_sad.h"

#define STRAIN_W      512           /* tracking tier (frames 512 x 512 u8) */
#define STRAIN_M      128           /* mask tier */
#define STRAIN_R      6
#define STRAIN_K      9
#define STRAIN_STEP_MM 3.0
#define STRAIN_SMOOTH 5
#define STRAIN_NMAX   BM_NMAX
/* -(TRAIN raw bias) of strain/step2/results/calib_train.json (800 TRAIN sequences, sha256 below) */
#define STRAIN_OFFSET_PP (-3.8059230431912217)
#define STRAIN_CALIB_SHA "5aed7a6e76d7fbe9"   /* sha256 prefix of calib_train.json */

enum { STRAIN_OK = 0, STRAIN_E_NOMYO = -1, STRAIN_E_SHORT = -2, STRAIN_E_MEM = -3, STRAIN_E_ARG = -4 };

typedef struct {
    /* ED line */
    int      n;                          /* tracked points (STRAIN_SMOOTH .. STRAIN_NMAX) */
    double   pts[STRAIN_NMAX][2];        /* resampled mid-wall points (y, x), 512 px */
    int32_t  q8[STRAIN_NMAX][2];         /* rint(256 pts) (y, x) */
    int      path_len;                   /* longest skeleton path b -> a */
    int32_t *path;                       /* (y, x) pairs, STRAIN_W * STRAIN_W max */
    uint8_t *lv, *epi;                   /* cleaned 128 masks (0 / 1) */
    uint8_t *myo;                        /* 512 x 512: largest 8-component of the myocardium (0 / 1) */
    int      ncomp_lv, ncomp_epi, ncomp_myo, skel_px;
    /* scratch */
    int32_t *lab, *stack;
    uint8_t *sk0, *sk1;
    double  *dist, *wtmp;
    int32_t *pred, *node, *nidx;
    uint8_t *done;
} strain_ws_t;

int  strain_ws_init(strain_ws_t *w);
void strain_ws_free(strain_ws_t *w);

/** @brief Largest 4-connected component + fill holes (= ef_simpson.clean), in place on a w x h 0/1 mask.
 *  @return number of components before (0: empty, mask unchanged). */
int strain_clean(uint8_t *m, int w, int h, int32_t *lab, int32_t *stack);

/** @brief ED mid-wall line from the 128 masks (do_clean: clean them first; else they are used as given).
 *  sx, sy: mm per 512 px (x, y). @return STRAIN_OK or STRAIN_E_*. */
int strain_ed_line(strain_ws_t *w, const uint8_t *lv128, const uint8_t *epi128, int do_clean, double sx, double sy);

/** @brief Length (mm) of n tracked Q8 points (y, x): 5-point moving average ('valid'), numpy pairwise sum. */
double strain_line_length(const int32_t (*q8)[2], int n, double sx, double sy, double *tmp);

/** @brief One frame step: positions q8 (y, x) tracked from frame A to frame B (512 x 512), anchor = current.
 *  opts: BM_OPT_NEON on the board (BM_OPT_VFIT is always added). @return BM_EDGE points, < 0 on bad arguments. */
int strain_track_step(const uint8_t *A, const uint8_t *B, int n, int32_t (*q8)[2], int opts);

/** @brief numpy add.reduce on a float64 array (pairwise summation), bit for bit. */
double strain_np_sum(const double *a, int n);

#endif /* STRAIN_H */
