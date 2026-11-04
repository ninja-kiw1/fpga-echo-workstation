"""Real-board acceptance of the CAMUS LV model in the v5 model library, plus a
model-switching stress test. Only talks TCP to 192.168.1.254:5060.

  1. golden validation frame: all 128x128 logits == integer reference;
  2. all 200 official validation frames: logits == integer reference, and
     Dice recomputed from the BOARD's logits == the offline quantized Dice;
  3. switching: fundus / skin / echo frames interleaved (60 frames), every
     frame equal to its own model's reference -- no parameter bleed-over.
Input: the grey 512x512 host stretch replicated to R=G=B (what the UI sends).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe verify_board_echo.py
"""
import json
import sys
import time
from datetime import datetime, timezone

import numpy as np

from common import PLD, RESULTS, ROOT, board_upsample, grouped, load_cache, seg_metrics

sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))
import confidence  # noqa: E402
import imagelink   # noqa: E402
import proto       # noqa: E402

FLAGS = proto.FLAG_STATS | proto.FLAG_ECHO_LOGIT | proto.FLAG_ECHO_MASK
ECHO, SKIN, FUNDUS = 2, 1, 0
BASE = PLD / 'tcp_image' / 'baseline' / 'vectors'


def main():
    link = imagelink.ImageLink('192.168.1.254', 5060, timeout=20)
    report = dict(utc=datetime.now(timezone.utc).isoformat(), checks=[])
    ok = True

    def exchange(rgb512, model, res, thr):
        imgs, times, rows, st = link.round_trip(np.ascontiguousarray(rgb512, np.uint8).tobytes(), mode=0,
                                                flags=FLAGS, aiparam=proto.make_aiparam(0, thr, res, model))
        assert st is not None and st['version'] >= 5 and st['ai_us'] > 0, st
        assert st['model'] == model, f"board used model {st['model']}, asked {model}"
        assert st['ai_res'] == res
        assert all(r['crc_errors'] == 0 and r['seq_errors'] == 0 and r['rows_ok'] == 512 for r in rows.values())
        k = 512 // res
        plane = np.frombuffer(confidence.logit_plane(imgs['logit']), np.uint8).reshape(512, 512)
        lg = plane[::k, ::k].astype(np.int64) - proto.LOGIT_IMG_OFFSET
        assert np.array_equal(plane.astype(np.int64) - proto.LOGIT_IMG_OFFSET,
                              np.repeat(np.repeat(lg, k, 0), k, 1))
        mask = np.frombuffer(imgs['mask'], np.uint8).reshape(512, 512, 3)[:, :, 0] > 0
        assert np.array_equal(mask, np.repeat(np.repeat(lg >= thr, k, 0), k, 1))
        return lg, st, times

    def check(name, cond, **info):
        report['checks'].append(dict(name=name, pass_=bool(cond), **info))
        print(('PASS ' if cond else 'FAIL ') + name, json.dumps(info)[:300], flush=True)
        return cond

    qdir = RESULTS / 'quant' / 'lv_r128'
    summ = json.loads((qdir / 'summary.json').read_text(encoding='utf-8'))
    x, _, cells, rows, cols, meta = load_cache('val')
    rgb = lambda i: np.repeat(np.asarray(x[i])[:, :, None], 3, axis=2)
    ref = lambda i: np.load(qdir / 'outputs' / f'val_{i:03d}.npy').astype(np.int64)

    g = summ['golden_val_index']
    lg, st, tm = exchange(rgb(g), ECHO, 128, 1)
    ok &= check('golden_val_frame', np.array_equal(lg, ref(g)), frame=g, mismatches=int((lg != ref(g)).sum()),
                ai_us=st['ai_us'], total_ms=round(tm['total_ms'], 1))

    bad, per, ai, t0 = [], [], [], time.time()
    for i in range(len(meta)):
        lg, st, _ = exchange(rgb(i), ECHO, 128, 1)
        if not np.array_equal(lg, ref(i)):
            bad.append(i)
        per.append(seg_metrics(board_upsample(lg >= 1, 2), cells[i], rows[i], cols[i]))
        ai.append(st['ai_us'] / 1000)
    board_dice = grouped(per, meta)
    ok &= check('val_200_frames', not bad and abs(board_dice['all'] - summ['val_quantized']['all']) < 1e-12,
                mismatching=bad[:10], board_dice=board_dice['all'], offline_dice=summ['val_quantized']['all'],
                good_medium=board_dice['good+medium'], ai_ms_mean=float(np.mean(ai)), ai_ms_max=float(np.max(ai)),
                seconds=round(time.time() - t0, 1))

    # switching stress: three models interleaved, each frame vs its own reference
    fundus512 = np.fromfile(BASE / 'fundus_512.rgb', np.uint8).reshape(512, 512, 3)
    fundus128_ref = np.fromfile(BASE / 'fundus_128.i8', np.int8).astype(np.int64).reshape(128, 128)
    skin512 = np.fromfile(BASE / 'skin_512.rgb', np.uint8).reshape(512, 512, 3)
    skin128_ref = np.fromfile(BASE / 'skin_128.i8', np.int8).astype(np.int64).reshape(128, 128)
    cases = [('fundus', fundus512, FUNDUS, -3, fundus128_ref), ('skin', skin512, SKIN, 1, skin128_ref),
             ('echo', rgb(g), ECHO, 1, ref(g))]
    order = [cases[j % 3] for j in range(60)]
    np.random.default_rng(7).shuffle(order)
    wrong = []
    for n, (name, img, model, thr, expect) in enumerate(order):
        lg, st, _ = exchange(img, model, 128, thr)
        if not np.array_equal(lg, expect):
            wrong.append((n, name))
    ok &= check('model_switching_60_frames', not wrong, wrong=wrong[:10],
                sequence=''.join(c[0][0] for c in order))
    report['pass'] = bool(ok)
    out = ROOT / 'board_check'
    out.mkdir(exist_ok=True)
    (out / 'acceptance_v5.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('ECHO_BOARD_ACCEPTANCE_PASS' if ok else 'ECHO_BOARD_ACCEPTANCE_FAIL', flush=True)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
