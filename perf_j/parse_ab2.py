"""Parse an A/B log (j1_v517_ab.sh format: '== round=R variant=V case=C' + SAMPLE lines) -> JSON of medians in ms.
Usage: parse_ab2.py LOG OUT.json   (sample 0 of every group = cold run, dropped)"""
import json, re, statistics as st, sys
d, cur, exact = {}, None, {}
for line in open(sys.argv[1], encoding="utf-8"):
    m = re.match(r"== round=(\d) variant=(\w+) case=(\w+)", line)
    if m:
        cur = f"{m.group(2)}/{m.group(3)}"; continue
    if cur and line.startswith("SAMPLE"):
        s = json.loads(line[7:])
        if s["sample"] >= 1:
            d.setdefault(cur, []).append(s)
    elif cur and "STRICT_NET_PASS" in line:
        exact[cur] = exact.get(cur, 0) + 1
out = {}
for k in sorted(d):
    L = d[k]
    r = {f: st.median([x[f] for x in L]) / 1000 for f in ("run_us", "ap_us", "put_us", "get_us")}
    r.update(n=len(L), run_min=min(x["run_us"] for x in L) / 1000, run_max=max(x["run_us"] for x in L) / 1000,
             strict_pass=exact.get(k, 0))
    out[k] = r
    print(f"{k:18s} n={r['n']:2d} run {r['run_us']:8.2f} ms [{r['run_min']:.2f}-{r['run_max']:.2f}] "
          f"ap {r['ap_us']:7.2f} put {r['put_us']:6.2f} get {r['get_us']:6.2f}  pass={r['strict_pass']}")
open(sys.argv[2], "w").write(json.dumps(out, indent=1))
