"""Fixed recording-level split of HMC-QU A4C, written BEFORE any feature or model output is looked at:
DEV 60 % (thresholds, rules, every choice) / TEST 40 % (evaluated once at the end, lock file first).
Stratified by recording MI status (any of the 6 segments MI). One A4C recording per patient in HMC-QU.
"""
import hashlib
import json

import numpy as np

L = json.load(open('results/labels_a4c.json'))['rows']
rng = np.random.default_rng(20261025)
split = {}
for mi in (0, 1):
    names = sorted(r['name'] for r in L if any(r['seg']) == bool(mi))
    perm = rng.permutation(len(names))
    n_test = int(round(0.4 * len(names)))
    for j, i in enumerate(perm):
        split[names[i]] = 'test' if j < n_test else 'dev'
out = dict(seed=20261025, rule='stratified by recording MI, 40 % test', split=split,
           counts={s: dict(n=sum(v == s for v in split.values()),
                           mi=sum(v == s and any(r['seg']) for r in L for k, v in [(r['name'], split[r['name']])]))
                   for s in ('dev', 'test')})
s = json.dumps(out, indent=1, sort_keys=True)
open('results/split.json', 'w').write(s)
open('results/test_lock.json', 'w').write(json.dumps(dict(
    split_sha256=hashlib.sha256(s.encode()).hexdigest(), test_used=False,
    rule='TEST recordings are only evaluated once by final_test.py, which refuses to run twice'), indent=1))
print(out['counts'])
