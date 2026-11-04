# -*- coding: utf-8 -*-
"""Part D board check (run AFTER the user installs programs_d/fundus_disc.upk (19) + fundus_cup.upk (20)).

Needs server >= v5.9 (program packages, compact frames with extra programs); no server change, no bitstream change.
For every Chakshu VAL image (E:/PLD/fundus_cdr, the integer reference recorded by eval_val.py --name <name>):
  A  stage 1: payload1 = box4(img512) -> model0 = 19                       -> int8 logits == cases.npz q1
  B  stage 2: payload2 (window from cdr_ref on the BOARD q1) -> model0 = 19, extra (20, src 0, dst 0, thr 1)
                                                                            -> logits == cases.npz qd / qc
  C  vCDR from the board logits (cdr_ref.measure, == src/fundus_cdr.c by test/fundus_cdr/compare.py)
     == the VAL vCDR of eval_val (per_image.json), every image
  D  timing per case (two round trips)
Prints FUNDUS_CDR_BOARD_PASS; writes board_check_v5/fundus_cdr_board.json. Measurement aid only, no diagnosis.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe verify_board_fundus_cdr.py --eval-name final
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
FC = HERE.parent.parent / 'fundus_cdr'
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(FC))

import numpy as np                  # noqa: E402

import cdr_ref                      # noqa: E402
import imagelink                    # noqa: E402
import proto                        # noqa: E402
from common import RESULTS, load_cache   # noqa: E402

F = proto.FLAG_STATS | proto.FLAG_C_LOGIT
ID_DISC, ID_CUP = 19, 20


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--eval-name', required=True)
    ap.add_argument('--host', default='192.168.1.254')
    ap.add_argument('--port', type=int, default=5060)
    a = ap.parse_args()
    val = load_cache('chaksu_val')
    cases = np.load(RESULTS / 'eval' / a.eval_name / 'cases.npz')
    per = json.loads((RESULTS / 'eval' / a.eval_name / 'per_image.json').read_text(encoding='utf-8'))
    link = imagelink.ImageLink(a.host, a.port, timeout=60)
    bad = dict(q1=[], qd=[], qc=[], vcdr=[])
    times = []
    for i, m in enumerate(val['meta']):
        img = np.asarray(val['img512'][i])
        t0 = time.perf_counter()
        p1 = cdr_ref.payload1(img)
        _, lg, _, _ = link.v6c_round_trip(p1.tobytes(), 128, kind=proto.V6C_KIND_RGB, model0=ID_DISC, thr=1, flags=F)
        q1 = np.frombuffer(lg, np.int8).reshape(128, 128)
        if not np.array_equal(q1, cases['q1'][i]):
            bad['q1'].append(m['name'])
        loc = cdr_ref.locate(q1)
        if loc is None:
            if per[i]['ok']:
                bad['vcdr'].append(m['name'])
            continue
        side = cdr_ref.side_for(loc['size'])
        y0, x0 = cdr_ref.window(loc['cy'], loc['cx'], side)
        p2 = cdr_ref.payload2(img, y0, x0, side)
        _, lg, _, _ = link.v6c_round_trip(p2.tobytes(), 128, kind=proto.V6C_KIND_RGB, model0=ID_DISC, thr=1, flags=F,
                                          extra=[(ID_CUP, 0, 0, 1)])
        qd, qc = (np.frombuffer(x, np.int8).reshape(128, 128) for x in lg)
        times.append((time.perf_counter() - t0) * 1000)
        if not np.array_equal(qd, cases['qd'][i]):
            bad['qd'].append(m['name'])
        if not np.array_equal(qc, cases['qc'][i]):
            bad['qc'].append(m['name'])
        r = cdr_ref.measure(qd, qc, side)
        if r['vcdr'] != per[i].get('vcdr'):
            bad['vcdr'].append(m['name'])
    link.close()
    ok = not any(bad.values())
    rep = dict(utc=datetime.now(timezone.utc).isoformat(), eval_name=a.eval_name, images=len(val['meta']),
               mismatches=bad, ms_per_case=dict(mean=float(np.mean(times)), max=float(np.max(times))), passed=ok)
    out = HERE / 'board_check_v5' / 'fundus_cdr_board.json'
    out.write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(rep, ensure_ascii=False)[:600])
    print('FUNDUS_CDR_BOARD_PASS' if ok else 'FUNDUS_CDR_BOARD_FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
