/* echo_num.h -- echo numbers shared by the board server and the host reference DLL (10-24).
 *
 * Moved verbatim from job_echo.c (clean, volume, beats) and job.c (numpy pairwise sum) so that the keyframe
 * scheduler (kf.c) and the quality / interval code (echo_quality.c) use the very same functions, and so that the
 * same C is compiled into the PC DLL the Python evaluation calls (E:/PLD/keyframe/echo_core.py): the host
 * reference and the board are one source. Numeric files are built with -ffp-contract=off (makefile).
 */
#ifndef ECHO_NUM_H
#define ECHO_NUM_H

#include <stdint.h>

#define EN_N    112                 /* EchoNet grid (program 16 crops the 128 logits back to 112) */
#define EN_MAXF 4096

typedef struct {
    int    ed, es;
    double v_ed, v_es, ef;
} en_beat_t;

typedef struct {
    double    ef, ef_sd, hr, cycle_s;
    int       has_hr, has_sd, fallback, nbeats;
    en_beat_t beats[EN_MAXF / 2];
} en_result_t;

/* beats.py parameters (beats_config_*.json "cfg") */
typedef struct {
    unsigned smooth, agg;           /* agg: 0 median, 1 mean */
    double   dist_s, prom, win;
} en_cfg_t;

/** numpy's add.reduce on a contiguous float64 array (pairwise summation), bit for bit. */
double job_np_sum(const double *a, int n);

/** echo_ws.clean on 112 x 112: largest 4-connected component + fill holes; returns the input's component count. */
int en_clean112(const uint8_t *m, uint8_t *out);
int en_cmp_d(const void *a, const void *b);
/** np.percentile(v, q) (linear); np.median. */
double en_percentile(const double *v, int n, double q);
double en_median(const double *v, int n);
void en_dlaev2_vec(double a, double b, double c, double *cs1, double *sn1);
/** volume.volume_pca on a 112 x 112 mask (principal axis, 20 disks, pixel units). */
double en_volume_pca(const uint8_t *m);
/** scipy.signal.find_peaks(x, distance=dist, prominence=pmin) -> peak indices in out, returns the count. */
int en_find_peaks(const double *x, int n, int dist, double pmin, int *out);
/** beats.analyze(vol, fps, step, cfg). */
void en_analyze(const double *vol, int n, double fps, int step, const en_cfg_t *c, en_result_t *r);
/** en_analyze for the host DLL (ctypes): head[8] = ef, ef_sd, hr, cycle_s, has_hr, has_sd, fallback, nbeats;
 *  beats[nbeats][5] = ed, es, v_ed, v_es, ef. Returns nbeats. */
int en_analyze_flat(const double *vol, int n, double fps, int step, unsigned smooth, unsigned agg, double dist_s,
                    double prom, double win, double *head, double *beats);

#endif /* ECHO_NUM_H */
