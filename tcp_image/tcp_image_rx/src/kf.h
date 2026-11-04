/* kf.h -- innovation 1: phase-adaptive keyframe inference (10-24). Port of E:/PLD/keyframe/kf.py.
 *
 * The PL runs the LV program only on keyframes; on the other frames the PS carries the LV contour with the SAD
 * block matcher of part E (bm_sad.c, bm_track_line: one 16 x 16 block per contour point, full search +-R, V fit,
 * SAD tables of k contour neighbours summed, chain mode = template from the previous frame), then a circular moving
 * average of the displacements over `reg` points, and fills the polygon back into a mask.
 *
 * Contour: walk the outer border of the (cleaned, single 4-connected component, no holes) mask along the pixel edges
 * with the foreground on the right (4-connected foreground: at a diagonal pair the walk turns away), one vertex at
 * the middle of every pixel edge -- the same points as the 0.5 iso-line of the binary mask -- then points every
 * `spacing` px by arc length (n = max(8, round(L / spacing))), starting at the top-most point.
 * Fill: pixel centres inside the polygon (even-odd rule, half-open in y).
 *
 * Schedule (causal, decided before the frame is inferred / tracked):
 *   KF_FULL      every frame a keyframe              KF_HOLD   keyframe every n, copies in between (lower bound)
 *   KF_FIXED     keyframe every n, tracking between  KF_ADAPT  tracking; keyframe when n_max frames since the last,
 *                or (after n_min) the mean block residual 1 - alpha (alpha = 1 - SAD / (255 * 256 * k)) of the last
 *                tracked frame exceeds res x that of the first tracked frame after the keyframe, or the frame is within
 *                +-win of a predicted ED / ES (extremes of the area curve confirmed 2 frames later, next = last + last
 *                spacing of the same kind; distance 0.3 s, prominence 0.2 x (P95 - P5) of the curve so far)
 * The host evaluation (E:/PLD/keyframe/echonet_c.py) runs this very file through echo_core.dll.
 */
#ifndef KF_H
#define KF_H

#include <stdint.h>

#define KF_MAXPTS 480        /* contour points (BM_NMAX 512 minus the wrap padding) */

enum { KF_FULL = 0, KF_HOLD = 1, KF_FIXED = 2, KF_ADAPT = 3 };
enum { KF_TRACK = 0, KF_START = 1, KF_LOST = 2, KF_EVERY = 3, KF_INTERVAL = 4, KF_RESIDUAL = 5, KF_PHASE = 6 };

typedef struct kf_cfg {
    int    kind, n, n_max, n_min, win;   /* win < 0: no phase forcing */
    double res;                          /* <= 0: no residual trigger */
    int    R, k, reg, pad;
    double spacing;
} kf_cfg_t;

typedef struct {
    kf_cfg_t c;
    int      W, H, PW, PH;               /* frame size, padded size */
    double   fps;
    int      t, kt, npts, base_set, hist_cap;
    double   base, last_res;
    double   py[KF_MAXPTS], px[KF_MAXPTS];   /* current contour, padded coordinates */
    uint8_t *prev, *cur;                 /* padded frames */
    uint8_t *last_mask;                  /* W x H, HOLD */
    double  *area;                       /* area history (phase prediction) */
} kf_t;

int  kf_init(kf_t *s, const kf_cfg_t *c, int W, int H, double fps, int max_frames);
void kf_free(kf_t *s);
/** Decision for the next frame (index s->t): KF_TRACK or a keyframe reason (KF_START .. KF_PHASE). */
int  kf_decide(kf_t *s);
/** Keyframe: the PL's cleaned mask (W x H, 0 / 1) of `frame` (W x H grey). Copies it to `mask_out`. */
void kf_key(kf_t *s, const uint8_t *frame, const uint8_t *mask, int reason, uint8_t *mask_out);
/** Non-keyframe: track the contour into `frame`, fill `mask_out`. Returns the mean block residual (or -1 HOLD). */
double kf_track(kf_t *s, const uint8_t *frame, uint8_t *mask_out);

/** Building blocks (also exported for the host checks). Contour of a W x H mask -> points (y, x) every `spacing`
 *  px, at most maxp; returns the count (0: empty mask). */
int  kf_contour(const uint8_t *m, int W, int H, double spacing, double *py, double *px, int maxp);
/** Polygon fill (pixel centres inside, even-odd) into a W x H mask. */
void kf_fill(const double *py, const double *px, int n, int W, int H, uint8_t *m);

/** Host DLL: run a whole sequence. frames / key_masks: T x H x W (key_masks = the PL's cleaned masks, used only on
 *  keyframes); out masks T x H x W, reason[T], resid[T]. Returns the number of keyframes. */
int  kf_run_sequence(const kf_cfg_t *c, const uint8_t *frames, const uint8_t *key_masks, int T, int W, int H,
                     double fps, uint8_t *masks, int32_t *reason, double *resid);

#endif /* KF_H */
