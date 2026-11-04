"""Parse J3(b) logs -> results/j3b.json (+ table on stdout). Usage: parse_j3b.py [-o OUT.json] LOG...
(-o: write a fresh OUT.json instead of merging into results/j3b.json; also keeps PL ap/put/get medians)"""
import json, re, sys
import numpy as np
from pathlib import Path

args = sys.argv[1:]
dst_arg = None
if args[:1] == ["-o"]:
    dst_arg, args = args[1], args[2:]
out = {}
for logf in args:
    cur = None
    for line in open(logf, encoding="utf-8"):
        m = re.match(r"== cond=(\w+) impl=(\w+)", line)
        if m:
            cur = (m.group(1), m.group(2)); out.setdefault(f"{cur[0]}/{cur[1]}", {"ms": []}); continue
        if cur is None:
            continue
        k = f"{cur[0]}/{cur[1]}"
        if line.startswith("SAMPLE"):
            s = json.loads(line[7:])
            if s["sample"] >= 1:                      # sample 0 = cold run
                out[k]["ms"].append(s["run_us"] / 1000)
                for f in ("ap", "put", "get"):
                    out[k].setdefault(f, []).append(s[f + "_us"] / 1000)
        elif line.startswith("RUN "):
            out[k]["ms"].append(float(line.split()[2]))
        elif "STRICT_NET_PASS" in line or "BIT_EXACT" in line:
            out[k]["exact"] = True
out = {k: v for k, v in out.items() if v["ms"]}       # chrt -f runs were refused -> no samples
for k, v in out.items():
    a = np.array(v["ms"])
    v.update(n=len(a), p50=float(np.percentile(a, 50)), p99=float(np.percentile(a, 99)),
             max=float(a.max()), min=float(a.min()), mean=float(a.mean()), std=float(a.std()))
    if dst_arg:
        for f in ("ap", "put", "get"):
            if f in v:
                v[f + "_p50"] = float(np.median(v.pop(f)))
    else:
        for f in ("ap", "put", "get"):
            v.pop(f, None)
    print(f"{k:22s} n={v['n']:4d} P50 {v['p50']:8.2f}  P99 {v['p99']:8.2f}  max {v['max']:8.2f}  "
          f"min {v['min']:8.2f}  std {v['std']:6.2f}  exact={v.get('exact', False)}")
dst = Path(dst_arg or "results/j3b.json")
old = json.loads(dst.read_text()) if dst.exists() and not dst_arg else {}
old.update(out)
dst.write_text(json.dumps(old, indent=1))
