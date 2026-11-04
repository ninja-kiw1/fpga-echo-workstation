import json, re, statistics as st, sys
cur = None; d = {}
for line in open("results/j1_ab.log", encoding="utf-8"):
    m = re.match(r"== round=(\d) variant=(\w+) case=(\w+)", line)
    if m: cur = (m.group(2), m.group(3)); continue
    if line.startswith("SAMPLE") and cur:
        s = json.loads(line[7:])
        if s["sample"] >= 1:  # drop cold sample 0
            d.setdefault(cur, []).append(s)
out = {}
for case in ["fundus512", "skin128", "echo128", "fundus32"]:
    for v in ["base", "j1"]:
        L = d.get((v, case), [])
        r = {k: st.median([x[k] for x in L]) for k in ["run_us", "ap_us", "put_us", "get_us"]}
        r["n"] = len(L); r["run_min"] = min(x["run_us"] for x in L); r["run_max"] = max(x["run_us"] for x in L)
        out[f"{case}/{v}"] = r
        print(f"{case:10s} {v:5s} n={r['n']:2d} run med {r['run_us']/1000:8.2f} ms [{r['run_min']/1000:.2f}-{r['run_max']/1000:.2f}] ap {r['ap_us']/1000:7.2f} put {r['put_us']/1000:6.2f} get {r['get_us']/1000:6.2f}")
    b, j = out[f"{case}/base"]["run_us"], out[f"{case}/j1"]["run_us"]
    print(f"   -> saving {(b-j)/1000:.2f} ms ({100*(b-j)/b:.1f}%)")
json.dump(out, open("results/j1_ab.json", "w"), indent=1)
