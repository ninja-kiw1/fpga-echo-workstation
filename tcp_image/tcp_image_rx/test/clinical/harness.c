#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define MAXF 2048
typedef struct { char *mem; size_t cap, len; } jbuf_t;
static void jb_add(jbuf_t *b, const char *fmt, ...) { va_list ap; va_start(ap, fmt);
  b->len += (size_t)vsnprintf(b->mem + b->len, b->cap - b->len, fmt, ap); va_end(ap); }
typedef struct { int ed, es; double v_ed, v_es, ef; } beat_t;
typedef struct { double ef, ef_sd, hr, cycle_s; int has_hr, has_sd, fallback, nbeats; beat_t beats[MAXF / 2]; } result_t;
typedef struct { double review_margin, rr_cv_irr, min_beats; } echo_par_t;
double job_np_sum(const double *a, int n)
{
    double r[8], res;
    int    i, j;

    if (n < 8) {
        res = 0.0;                                   /* numpy starts from -0.0 + a[0]...: same value */
        for (i = 0; i < n; i++) {
            res += a[i];
        }
        return res;
    }
    if (n <= 128) {
        for (j = 0; j < 8; j++) {
            r[j] = a[j];
        }
        for (i = 8; i < n - (n % 8); i += 8) {
            for (j = 0; j < 8; j++) {
                r[j] += a[i + j];
            }
        }
        res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        for (; i < n; i++) {
            res += a[i];
        }
        return res;
    }
    {
        int n2 = n / 2;

        n2 -= n2 % 8;
        return job_np_sum(a, n2) + job_np_sum(a + n2, n - n2);
    }
}

/* echo_ws.clinical: measurement aids only (no diagnosis); appends ,"clinical":{...} */
static void clinical_json(jbuf_t *b, const result_t *r, double ef, double a, double bb, double fps,
                          const echo_par_t *c)
{
    double e[MAXF / 2], rr[MAXF / 2], x[MAXF / 2], mean, sd, emax, emin;
    int    n = r->nbeats, i;

    jb_add(b, ",\"clinical\":{\"band\":");
    if (isnan(ef)) {
        jb_add(b, "null,\"review\":false,\"nearest_bound\":null");
    } else {
        double d0 = fabs(ef - 40.0), d1 = fabs(ef - 50.0);
        int    band = ef <= 40.0 ? 0 : (ef < 50.0 ? 1 : 2);
        double dmin = d1 < d0 ? d1 : d0;

        jb_add(b, "%d,\"review\":%s,\"nearest_bound\":%.17g", band, dmin < c->review_margin ? "true" : "false",
               d1 < d0 ? 50.0 : 40.0);
    }
    jb_add(b, ",\"margin\":%.17g,\"n_beats\":%d,\"beat_ef\":[", c->review_margin, n);
    for (i = 0; i < n; i++) {
        e[i] = a * r->beats[i].ef + bb;
        jb_add(b, "%s%.17g", i ? "," : "", e[i]);
    }
    jb_add(b, "]");
    if (n > 0) {
        mean = job_np_sum(e, n) / n;
        emax = emin = e[0];
        for (i = 0; i < n; i++) {
            x[i] = (e[i] - mean) * (e[i] - mean);
            emax = e[i] > emax ? e[i] : emax;
            emin = e[i] < emin ? e[i] : emin;
        }
        sd = sqrt(job_np_sum(x, n) / n);
        jb_add(b, ",\"beat_sd\":%.17g,\"beat_range\":%.17g", sd, emax - emin);
    } else {
        jb_add(b, ",\"beat_sd\":null,\"beat_range\":null");
    }
    jb_add(b, ",\"rr_s\":[");
    for (i = 0; n >= 2 && fps > 0.0 && i + 1 < n; i++) {
        rr[i] = ((double)r->beats[i + 1].ed - (double)r->beats[i].ed) / fps;
        jb_add(b, "%s%.17g", i ? "," : "", rr[i]);
    }
    jb_add(b, "]");
    if (n >= 2 && fps > 0.0) {
        int m = n - 1;

        mean = job_np_sum(rr, m) / m;
        jb_add(b, ",\"rr_mean_s\":%.17g", mean);
        if (m >= 2) {
            double step = 0.0, best = 0.0, cv;
            int    k = 0;

            for (i = 0; i < m; i++) {
                x[i] = (rr[i] - mean) * (rr[i] - mean);
            }
            cv = sqrt(job_np_sum(x, m) / m) / mean;
            for (i = 0; i + 1 < m; i++) {
                double dd = fabs(rr[i + 1] - rr[i]);

                step = dd > step ? dd : step;
                if (i == 0 || dd < best) {
                    best = dd;
                    k = i;
                }
            }
            jb_add(b, ",\"rr_cv\":%.17g,\"rr_max_step_ms\":%.17g,\"irregular\":%s,\"index_beat\":{\"k\":%d,"
                      "\"ed\":%d,\"es\":%d,\"ef\":%.17g,\"rr_diff_ms\":%.17g}",
                   cv, step * 1000.0, cv > c->rr_cv_irr ? "true" : "false", k + 2, r->beats[k + 2].ed,
                   r->beats[k + 2].es, e[k + 2], best * 1000.0);
        } else {
            jb_add(b, ",\"rr_cv\":null,\"rr_max_step_ms\":null,\"irregular\":null,\"index_beat\":null");
        }
    } else {
        jb_add(b, ",\"rr_mean_s\":null,\"rr_cv\":null,\"rr_max_step_ms\":null,\"irregular\":null,\"index_beat\":null");
    }
    jb_add(b, ",\"few_beats\":%s,\"rr_cv_irregular\":%.17g,\"min_beats_avg\":%d}",
           (double)n < c->min_beats ? "true" : "false", c->rr_cv_irr, (int)c->min_beats);
}


int main(void) {
  static result_t r; static char mem[1 << 20]; jbuf_t b; echo_par_t c; double ef, a, bb, fps; int n, i;
  while (scanf("%lf %lf %lf %lf %lf %lf %lf %d", &ef, &a, &bb, &fps, &c.review_margin, &c.rr_cv_irr, &c.min_beats, &n) == 8) {
    r.nbeats = n;
    for (i = 0; i < n; i++) scanf("%d %d %lf", &r.beats[i].ed, &r.beats[i].es, &r.beats[i].ef);
    b.mem = mem; b.cap = sizeof(mem); b.len = 0; mem[0] = 0;
    clinical_json(&b, &r, ef, a, bb, fps, &c);
    printf("{%s}\n", mem + 1);
  }
  return 0;
}
