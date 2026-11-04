r"""v5.26 CANDIDATE board runs (copied from board_acceptance_v525): the v5.25 cases below unchanged (chain 0: must equal
the v5.25 results bit for bit apart from timing) + C1 = CAMUS_scan_demo_z3.y8 (the 10-31 simulated scan, CAMUS VAL,
fixed crop 128 48 384) with the CAMUS chain exactly as the UI sends it (jobs.load_roll_camus(): F1 every 5 frames,
window 50 / every 10, view every 25 / median of 3, view_t 0.219). The v5.25 notes follow.

v5.25 candidate (rolling quality light + view while scanning, RQ01 block of echo_ef mode 3): board runs.

Runs on whatever server answers on 192.168.1.254:5060 (the candidate under devrun.py start). Each case = one live
run with frames112 + trace + per-frame "live" blobs; everything is saved to results/<case>.npz / .json for
host_check.py (skin venv: program 16 integer model + RV integer reference + echo_core.dll):
  L0  demo source 0X100009310A3BD7FC_z4.y8, keyframe schedule, fixed crop, HC01, NO RQ01  (= v5.24 request)
  L1  same + RQ01 win 50 / every 5 / view every 25 / median of 3 / view_t 0.27         (proposed default)
  L2  same + RQ01 view every 10 / median of 5                                            (heavier view, timing only)
  V1  ES000108_2CH_then_4CH.y8 (HMC-QU, same patient 2CH 4 s then 4CH 6 s), fixed 4CH crop, RQ01 as L1
view_t 0.27 = CAMUS TRAIN-best RV / LV threshold on the program-16 chain (E:/PLD/quality/scan_sim/restd.py; VAL acc 0.84).

    C:\Users\21888\anaconda3\envs\srtp-vision\python.exe -I run_board.py
"""
import json
import os
import struct
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PLD = HERE.parent
UI = PLD / 'tcp_image' / 'tcp_image_ui'
sys.path.insert(0, str(UI))
sys.path.insert(0, str(PLD / 'live'))
import echo_ws  # noqa: E402
import imagelink  # noqa: E402
import jobs  # noqa: E402
import proto  # noqa: E402

HOST = '192.168.1.254'
OUT = HERE / 'results'
MODEL = next(m for m in proto.MODELS if m['name'] == 'echonet_lv')
DEMO = '0X100009310A3BD7FC_z4.y8'
CAMUS = 'CAMUS_scan_demo_z3.y8'
SEARCH = 'CAMUS_view_search_z3.y8'                     # 11-02: one patient, simulated view search
VIEW_T = 0.27


def rq_block(win=50, every=5, view_every=25, view_n=3, rv_model=0, view_t=VIEW_T):
    b = struct.pack('<IHHHHidII', 0x31305152, win, every, view_every, view_n, rv_model, float(view_t), 0, 0)
    assert len(b) == 32
    return b


def live_run(link, conf, kf, heart, name, crop, rq):
    params = jobs.echo_live_params(jobs.echo_params(conf, MODEL['scale'], 16, False, 0, 50.0), kf, name, 'file', crop,
                                   every=1, out_flags=jobs.LIVE_FRAMES112 | jobs.LIVE_TRACE | jobs.LIVE_FRAME)
    params += jobs.heart_block(heart)
    if rq is not None:
        params += rq
    live = []

    def blob(n, d):
        if n == 'live':
            u = jobs.unpack_live(d)
            u['roll'] = (d[5], d[6], struct.unpack_from('<f', d, 44)[0], d[7])   # v5.26: + hold
            live.append(u)
            return True
        return False
    link.connect()
    t0 = time.perf_counter()
    r = jobs.run(link, jobs.JOB_ECHO_EF, jobs.ECHO_MASKS, params, b'', None, on_blob=blob)
    r['pc_wall_s'] = time.perf_counter() - t0
    return r, live


def save(case, r, live, meta):
    res, bl = r['result'], r['blobs']
    n = res.get('n_frames', 0)
    arrays = dict(frames112=np.frombuffer(bl['frames112'], np.uint8).reshape(-1, 112, 112),
                  trace=np.frombuffer(bl['trace'], np.uint32).reshape(-1, 7),
                  masks=np.unpackbits(np.frombuffer(bl['masks'], np.uint8), bitorder='little')[:n * 112 * 112]
                  .reshape(n, 112, 112).astype(bool),
                  live_t=np.array([d['t'] for d in live]), live_roll=np.array([d['roll'] for d in live], np.float64),
                  live_vol=np.array([d['vol'] for d in live]),
                  live_masks=np.stack([np.unpackbits(np.frombuffer(d['mask'], np.uint8), bitorder='little')[:112 * 112]
                                       .reshape(112, 112).astype(bool) for d in live]))
    if 'rh_masks' in bl:
        arrays['rh_masks'] = np.frombuffer(bl['rh_masks'], np.uint8)
    np.savez_compressed(OUT / f'{case}.npz', **arrays)
    (OUT / f'{case}.json').write_text(json.dumps(dict(meta, result=res, pc_wall_s=r['pc_wall_s']), indent=1,
                                                 ensure_ascii=False, default=str), encoding='utf-8')


def main():
    OUT.mkdir(exist_ok=True)
    import avi_to_y8
    avi_to_y8.HERE = str(PLD / 'board_acceptance_v525')    # upload() reads <HERE>/src/<name> (v5.25 test file)
    avi_to_y8.upload(['ES000108_2CH_then_4CH.y8'])
    idx = json.loads((PLD / 'live' / 'src' / 'index.json').read_text(encoding='utf-8'))
    vs = json.loads((PLD / 'board_acceptance_v525' / 'src' / 'view_src.json').read_text(encoding='utf-8'))
    conf = echo_ws.load_config()
    kf = jobs.load_kf_config('keyframe')
    heart = jobs.load_heart_config()
    link = imagelink.ImageLink(HOST, 5060, timeout=30.0)
    caps = jobs.caps(link)
    print('caps', caps.get('server'), 'live_roll', caps.get('live_roll'), 'live_roll_camus', caps.get('live_roll_camus'),
          flush=True)
    (OUT / 'caps.json').write_text(json.dumps(caps, indent=1), encoding='utf-8')
    m, cm, sm = idx[DEMO], idx[CAMUS], idx[SEARCH]
    nohold = jobs.load_roll_camus()
    chain1 = dict(jobs.load_roll_camus(), chain=1)
    nohold['camus'] = dict(nohold['camus'], hold_thr=0.0)
    demo_crop = (m['x'], m['y'], m['side'])
    cases = [('L0', DEMO, demo_crop, None, {}), ('L1', DEMO, demo_crop, rq_block(), {}),
             ('L2', DEMO, demo_crop, rq_block(view_every=10, view_n=5), {}),
             ('V1', vs['name'], tuple(vs['crop']), rq_block(), dict(n_2ch=vs['n_2ch'])),
             ('C1', CAMUS, (cm['x'], cm['y'], cm['side']), jobs.roll_block(**jobs.load_roll_camus()), {}),
             ('C0', CAMUS, (cm['x'], cm['y'], cm['side']), jobs.roll_block(**nohold), {}),   # hold off = try 3
             ('S1', SEARCH, (sm['x'], sm['y'], sm['side']), jobs.roll_block(**jobs.load_roll_camus()), {}),   # view search
             ('C9', CAMUS, (cm['x'], cm['y'], cm['side']), jobs.roll_block(**chain1), {})]   # chain 1 = active 2b0bc6a3
    cam = jobs.load_roll_camus()['camus']
    for case, name, crop, rq, extra in cases:
        k_ = dict(kf, a=cam['ef_a'], b=cam['ef_b']) if rq is not None and struct.unpack_from('<I', rq, 24)[0] == 2 else kf
        r, live = live_run(link, conf, k_, heart, name, crop, rq)
        res = r['result']
        if not res.get('ok'):
            print(case, 'FAIL', res.get('error'), flush=True)
            (OUT / f'{case}.json').write_text(json.dumps(dict(result=res), indent=1), encoding='utf-8')
            continue
        meta = dict(case=case, source=name, crop=crop, fps=res['fps'], rq=None if rq is None else rq.hex(),
                    kf_config='keyframe', **extra)
        save(case, r, live, meta)
        st, ro = res['stream'], res.get('rolling')
        print('%s %-28s %d frames  EF %.1f  light %s  latency p50 %.1f p99 %.1f max %.1f ms%s' % (
            case, name, res['n_frames'], res['ef'] or -1, res['quality']['light'], st['latency_ms']['p50'],
            st['latency_ms']['p99'], st['latency_ms']['max'],
            '' if ro is None else '  chain %d, rolling %d updates (%.1f / max %.1f ms, worker), %d views (%.1f / max %.1f ms)' % (
                ro.get('chain', 0), len(ro['updates']), ro['quality_ms_mean'], ro['quality_ms_max'], len(ro['views']), ro['view_ms_mean'],
                ro['view_ms_max'])), flush=True)
    print('RUN_BOARD_DONE')


if __name__ == '__main__':
    main()
