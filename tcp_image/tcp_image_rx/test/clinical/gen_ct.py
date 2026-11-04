"""Host check of job_ct.c spleen_json vs ct_volume.spleen_measure on random multi-blob volumes."""
import json, subprocess, sys
from pathlib import Path
import numpy as np
here = Path(__file__).resolve().parent
rx = here.parents[1]
sys.path.insert(0, str(rx.parent / "tcp_image_ui"))
import ct_volume as cv
src = (rx / "src" / "job_ct.c").read_text(encoding="utf-8")
fn = src[src.index("/* ---- spleen measurements"):src.index("static uint8_t *pack_bits(")]
harness = r'''#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define N 512
typedef struct { char *mem; size_t cap, len; } jbuf_t;
static void jb_add(jbuf_t *b, const char *fmt, ...) { va_list ap; va_start(ap, fmt);
  b->len += (size_t)vsnprintf(b->mem + b->len, b->cap - b->len, fmt, ap); va_end(ap); }
''' + fn + r'''
int main(int argc, char **argv) {
  FILE *f = fopen(argv[1], "rb"); int nz, z, zlo = -1, zhi = -1; double spc[3]; static char mem[4096]; jbuf_t b;
  uint8_t *m;
  while (fread(&nz, 4, 1, f) == 1) {
    fread(spc, 8, 3, f); m = malloc((size_t)nz * N * N); fread(m, 1, (size_t)nz * N * N, f);
    zlo = zhi = -1;
    for (z = 0; z < nz; z++) if (memchr(m + (size_t)z * N * N, 1, N * N)) { zlo = zlo < 0 ? z : zlo; zhi = z; }
    b.mem = mem; b.cap = sizeof(mem); b.len = 0;
    spleen_json(&b, m, nz, zlo, zhi, spc);
    printf("{%s}\n", mem + 1); free(m);
  }
  return 0;
}
'''
(here / "harness_ct.c").write_text(harness, encoding="utf-8")
rng = np.random.default_rng(7)
cases = []
with open(here / "ct_cases.bin", "wb") as f:
    for t in range(60):
        nz = int(rng.integers(3, 12))
        spc = (float(rng.uniform(0.6, 1.0)), float(rng.uniform(0.6, 1.0)), float(rng.choice([1.5, 2.5, 5.0, 7.0])))
        m = np.zeros((nz, 512, 512), np.uint8)
        if t % 10:
            for _ in range(int(rng.integers(1, 4))):
                z0, z1 = sorted(rng.integers(0, nz, 2)); cy, cx = rng.integers(100, 400, 2)
                a, bb, ang = rng.uniform(1, 90), rng.uniform(1, 40), rng.uniform(0, np.pi)
                yy, xx = np.mgrid[0:512, 0:512]
                u = (xx - cx) * np.cos(ang) + (yy - cy) * np.sin(ang); v = -(xx - cx) * np.sin(ang) + (yy - cy) * np.cos(ang)
                e = (u / a) ** 2 + (v / bb) ** 2 <= 1
                m[z0:z1 + 1] |= e.astype(np.uint8)
            if t % 7 == 3:
                m[nz // 2, 10, 10] = 1          # single-pixel speck
        f.write(np.int32(nz).tobytes()); f.write(np.array(spc, np.float64).tobytes()); f.write(m.tobytes())
        cases.append((m.astype(bool), spc))
wpath = "/mnt/" + str(here).replace("\\", "/").replace("E:", "e")
out = subprocess.run(["wsl", "-e", "sh", "-c", f"cd {wpath} && gcc -std=gnu99 -O2 -ffp-contract=off -Wall -Wextra harness_ct.c -o /tmp/ctm -lm && /tmp/ctm ct_cases.bin"],
                     capture_output=True, text=True, encoding="utf-8", errors="replace")
if out.returncode:
    print(out.stderr); sys.exit(1)
if out.stderr.strip():
    print("WARN", out.stderr)
res = [json.loads(l)["spleen"] for l in out.stdout.splitlines()]
bad = 0
for (m, spc), cj in zip(cases, res):
    pm = cv.spleen_measure(m, spc)
    if pm is not None:
        pm = {k: pm[k] for k in ("length_mm", "width_mm", "thickness_mm", "splenic_index", "width_slice", "width_ends")}
    if pm != cj:
        bad += 1
        print("DIFF", pm, cj)
print("cases", len(res), "different", bad)
print("SPLEEN_HOST_PASS" if bad == 0 and len(res) == len(cases) else "SPLEEN_HOST_FAIL")
