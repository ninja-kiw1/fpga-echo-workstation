"""Host check of job_echo.c clinical_json vs echo_ws.clinical: build harness from the real source, random beat sets."""
import json, re, subprocess, sys
from pathlib import Path
import numpy as np
here = Path(__file__).resolve().parent
rx = here.parents[1]
sys.path.insert(0, str(rx.parent / "tcp_image_ui"))
import echo_ws
src = (rx / "src" / "job_echo.c").read_text(encoding="utf-8")
fn = src[src.index("/* echo_ws.clinical: measurement aids"):src.index("int job_echo_run(job_t *j)")]
jobc = (rx / "src" / "job.c").read_text(encoding="utf-8")
npsum = jobc[jobc.index("double job_np_sum(const double *a, int n)"):jobc.index("int job_unet(")]
harness = r'''#include <math.h>
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
''' + npsum + fn + r'''
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
'''
(here / "harness.c").write_text(harness, encoding="utf-8")
rng = np.random.default_rng(13)
cases, lines = [], []
for t in range(3000):
    n = int(rng.integers(0, 9))
    fps = float(rng.choice([50.0, 30.0, 44.0, 72.5]))
    ed = np.cumsum(rng.integers(15, 70, n)).tolist()
    beats = [dict(ed=int(e), es=int(e) + int(rng.integers(5, 15)), ef=float(rng.uniform(5, 85))) for e in ed]
    a, b = 0.811509698710793, 10.511287524327033
    ef = float(rng.uniform(20, 75)) if t % 7 else float(rng.choice([40.0, 50.0, 45.0, 34.0, 56.0]))
    cc = dict(review_margin=float(rng.choice([6.0, 6.5])), rr_cv_irregular=float(rng.choice([0.276, 0.225])), min_beats_avg=5)
    cases.append((beats, fps, ef, a, b, cc))
    lines.append("%r %r %r %r %r %r %r %d %s" % (ef, a, b, fps, cc["review_margin"], cc["rr_cv_irregular"], 5.0, n,
                 " ".join("%d %d %r" % (x["ed"], x["es"], x["ef"]) for x in beats)))
(here / "cases.txt").write_text("\n".join(lines) + "\n")
out = subprocess.run(["wsl", "-e", "sh", "-c", "cd /mnt/" + str(here).replace("\\", "/").replace("E:", "e").replace("e:", "e")
                      + " && gcc -std=gnu99 -O2 -ffp-contract=off -Wall harness.c -o /tmp/clin -lm && /tmp/clin < cases.txt"],
                     capture_output=True, text=True, encoding="utf-8", errors="replace")
if out.returncode:
    print(out.stderr); sys.exit(1)
res = [json.loads(l)["clinical"] for l in out.stdout.splitlines()]
bad = 0
for (beats, fps, ef, a, b, cc), cj in zip(cases, res):
    pj = echo_ws.clinical(beats, fps, ef, cal=lambda e: a * e + b, cc=cc)
    pj = json.loads(json.dumps(pj))
    if pj != cj:
        bad += 1
        if bad <= 3:
            print("DIFF", json.dumps(pj), "\n    ", json.dumps(cj))
print("cases", len(res), "different", bad)
print("CLINICAL_HOST_PASS" if bad == 0 and len(res) == len(cases) else "CLINICAL_HOST_FAIL")
