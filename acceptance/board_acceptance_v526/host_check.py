r"""v5.26 candidate: host check of the board runs saved by run_board.py. L0 / L1 / L2 / V1 as in v5.25 (below) and also
identical to today's v5.25 runs (board_acceptance_v525/results_kernel_rollback_20261102: every field except timing, the
same rolling updates / views). C1 (RQ01 chain 1, CAMUS):
  frames      the source .y8 on the PC through echo_core.lc_crop_resize(_n) -> 112 frames == the board's frames112;
              128 frames for the chain
  engine      as below (program 16, keyframes, masks)
  rolling     F1 (lv_r128 integer, int8_gpu) on every f1_every-th 128 frame, eq_clean; every update [t, light, score]
              == echo_core.eq_window over t-win+1..t with the CAMUS model (jobs.load_roll_camus()): light equal,
              f32(score) equal; update count
  views       at F1 frames t > 0, t % view_every == 0: RV (rv_pl1_5 integer on the 128 frame itself, int8_gpu, checked
              against lwnet run_integer on the first one) largest 4-connected area, F1 LV area, median rule
  blobs       as below
v5.25 notes:

v5.25 candidate: host check of the board runs saved by run_board.py (rolling quality light + view, RQ01).

Per case:
  engine      board masks / keyframes == host echo_core.propagate on the board's 112 frames with program 16's
              integer logits (int8_gpu.IntegerModel, bit-exact with the PL) -- the mode-2 engine is unchanged
  rolling     every update [t, light, score]: host echo_core.clip_features on frames t-win+1..t (board masks / keys,
              host logits + component counts) -> KF01 model score / light; light equal, f32(score) equal
  views       every [t, rv, lv, view]: lv == board mask area of frame t; rv == host RV integer reference (lwnet
              run_integer, rv_pl1_5 = program 21, 112 padded to 128, R = G = B, logit >= 1, largest 4-connected);
              view == median rule over the last view_n ratios
  blobs       every "live" blob carries the latest update / view at its frame
  L0 vs L1    everything except "rolling" / timing identical (RQ01 changes nothing else)
Writes results/host_check.json; prints HOST_CHECK_PASS / FAIL.

    E:\PLD\skin\.venv\Scripts\python.exe -I host_check.py
"""
import json
import os
import struct
import sys
from pathlib import Path

import numpy as np
import torch
from scipy import ndimage

HERE = Path(__file__).resolve().parent
PLD = HERE.parent
os.environ['PATH'] = r'C:\msys64\mingw64\bin;' + os.environ['PATH']
sys.path.insert(0, str(PLD / 'keyframe'))
sys.path.insert(0, str(PLD / 'echonet'))
sys.path.insert(0, str(PLD / 'lwnet'))
sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))
import echo_core  # noqa: E402
from frame_ops import video_inputs  # noqa: E402
from int8_gpu import IntegerModel  # noqa: E402
from int8_reference import run_integer  # noqa: E402
import jobs  # noqa: E402

OUT = HERE / 'results'
V525 = PLD / 'board_acceptance_v525' / 'results_kernel_rollback_20261102'
NEED3 = '--before-need3' not in sys.argv     # 11-02 later: CAMUS views decided only once view_n ratios are in
PAD = 8
SKIP = ('mode', 'stream', 'rolling', 'upload_bytes', 'ms', 'pl_ms', 'pl_split')


def program16():
    qd = PLD / 'echonet' / 'results' / 'quant' / 't3'
    plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qd / 'parameters.npz', allow_pickle=False) as z:
        return IntegerModel(plan, {k: z[k] for k in z.files}, 'cuda')


def rv_reference():
    qd = PLD / 'cardiac_rv' / 'results' / 'quant' / 'rv_pl1_5'
    plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
    npz = np.load(qd / 'parameters.npz')
    arrays = {k: npz[k] for k in npz.files}

    def area(fr112):
        x = np.pad(fr112, PAD)
        rgb = torch.from_numpy(np.repeat(x[None], 3, 0)).unsqueeze(0)
        with torch.inference_mode():
            lg = run_integer(plan, arrays, rgb)[0][0, 0].numpy()[PAD:PAD + 112, PAD:PAD + 112]
        m = lg >= 1
        lab, n = ndimage.label(m)
        return int(np.bincount(lab.ravel())[1:].max()) if n else 0
    return area


def score_light(model, f):
    s = model['w'][0]
    for i, k in enumerate(echo_core.QF):                 # same order as eq_acc_finish
        s += model['w'][i + 1] * ((f[k] - model['mu'][i]) / model['sd'][i])
    return s, 2 if s >= model['t_red'] else (1 if s >= model['t_green'] else 0)


def core(res):
    r = {k: v for k, v in res.items() if k not in SKIP}
    r['keyframe'] = {k: v for k, v in r['keyframe'].items() if k not in ('pl_ms_per_key', 'track_ms_per_frame', 'track_ms_max')}
    if 'right_heart' in r:
        r['right_heart'] = {k: v for k, v in r['right_heart'].items() if k != 'pl_ms'}
    return r


def integer128(qd):
    plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qd / 'parameters.npz', allow_pickle=False) as zz:
        arrays = {k: zz[k] for k in zz.files}
    gm = IntegerModel(plan, arrays, 'cuda')

    def run(x128):
        out = []
        with torch.no_grad():
            for i in range(0, len(x128), 64):
                xb = torch.from_numpy(np.repeat(np.ascontiguousarray(x128[i:i + 64])[:, None], 3, 1))
                out.append(gm(xb)[:, 0].to(torch.int8).cpu().numpy())
        return np.concatenate(out)
    return run, plan, arrays


def check_camus(meta, res, z, v, bm, ck, out):
    ro, n = res['rolling'], res['n_frames']
    cam = jobs.load_roll_camus()
    k, win, every = ro['f1_every'], ro['win'], ro['every']
    x, y, side = meta['crop']
    raw = np.memmap(PLD / 'live' / 'src' / meta['source'], np.uint8, 'r', 32)
    W, H = struct.unpack_from('<II', open(PLD / 'live' / 'src' / meta['source'], 'rb').read(12), 4)
    raw = raw.reshape(-1, H, W)[:n]
    box = echo_core.LcBox(x=x, y=y, side=side)
    f112 = np.stack([echo_core.lc_crop_resize(fr, box) for fr in raw])
    f128 = np.stack([echo_core.lc_crop_resize_n(fr, box, 128) for fr in raw])
    ck['frames112_from_source'] = bool(np.array_equal(f112, v))
    # judging frames: v5.26 hold replayed from the events (lock timing comes from the board; every unlock must be the
    # first frame after the lock whose 1 s mean image moved more than the threshold, integers as on the board)
    ho = ro.get('hold')
    jud, hold_ok, unlock_d = np.ones(n, bool), True, []
    if ho:
        ev = {e[0]: e for e in ho['events']}
        c = np.cumsum(np.concatenate([np.zeros((1, 112, 112), np.int64), f112.astype(np.int64)]), 0)
        S = lambda t: c[t + 1] - c[max(t + 1 - ho['window'], 0)]          # noqa: E731
        holding, js, R = False, 0, None
        for t in range(n):
            if holding:
                d = float(np.abs(S(t) - R).sum()) / (ho['window'] * 112 * 112)
                moved = d > ho['thr']
                hold_ok &= moved == (t in ev and ev[t][1] == 0)
                if moved:
                    hold_ok &= ev[t][2] == int(round(d * 1000))
                    unlock_d.append(round(d, 3))
                    holding, js = False, t + 1
            jud[t] = not holding and t >= js
            if t in ev and ev[t][1] == 1:
                hold_ok &= bool(jud[t])
                holding, R = True, S(t)
        hold_ok &= int((~jud).sum()) == ho['held_frames']
        ck['hold_events_replayed'] = bool(hold_ok)
    f1, _, _ = integer128(PLD / 'echo' / 'results' / 'quant' / 'lv_r128')
    rvm, rplan, rarr = integer128(PLD / 'cardiac_rv' / 'results' / 'quant' / 'rv_pl1_5')
    ts = np.array([t for t in range(0, n, k) if jud[t]])
    slot = {int(t): i for i, t in enumerate(ts)}
    q = f1(f128[ts])
    cm, nc = zip(*[echo_core.eq_clean(qq >= 1) for qq in q])
    cm, nc = np.stack(cm), np.array(nc, np.int32)
    em = echo_core.EqModel()
    em.w[:] = [float(a) for a in cam['camus']['w']]
    em.mu[:] = [float(a) for a in cam['camus']['mu']]
    em.sd[:] = [float(a) for a in cam['camus']['sd']]
    em.t_green, em.t_red = float(cam['camus']['t_green']), float(cam['camus']['t_red'])
    want_upd = [t for t in range(win - 1, n, every) if jud[t + 1 - win:t + 1].all()]
    upd_ok, light_seq = [], []
    for t, lt, sc in ro['updates']:
        a = t - win + 1
        js_ = [slot[tt] for tt in range(a, t + 1) if tt in slot]
        o = echo_core.eq_window(f128[a:t + 1], [int(ts[j]) - a for j in js_], q[js_], cm[js_], nc[js_], em)
        upd_ok.append(o['light'] == lt and float(np.float32(o['score'])) == float(np.float32(sc)))
        light_seq.append((t, lt, sc))
    ck['rolling_bit_exact'] = all(upd_ok) and [u[0] for u in ro['updates']] == want_upd
    if ho:                                       # every lock: 3 equal lights of that judging before it
        starts = [0] + [e[0] + 1 for e in ho['events'] if e[1] == 0]
        locks = [e[0] for e in ho['events'] if e[1] == 1]
        lk_ok = True
        for tl in locks:
            s0 = max(s_ for s_ in starts if s_ <= tl)
            ls = [lt for t, lt, _ in light_seq if s0 <= t <= tl]
            lk_ok &= any(ls[i] == ls[i + 1] == ls[i + 2] for i in range(len(ls) - 2))
        ck['locks_after_3_equal_lights'] = bool(lk_ok)
    # RV reference: int8_gpu on the 128 frame, the first one also through lwnet run_integer (CPU)
    vt = [row[0] for row in ro['views']]
    rl = rvm(f128[vt]) if vt else np.zeros((0, 128, 128), np.int8)
    if vt:
        with torch.inference_mode():
            ref = run_integer(rplan, rarr, torch.from_numpy(np.repeat(f128[vt[0]][None], 3, 0)).unsqueeze(0))[0][0, 0].numpy()
        ck['rv_gpu_equals_run_integer'] = bool(np.array_equal(ref.astype(np.int8), rl[0]))
    resets = sorted(e[0] for e in (ho or {}).get('events', []) if e[1] == 0)
    view_ok, ratios, views, last_reset = [], [], [], -1
    for i, (t, rv, lv, vw, td) in enumerate(ro['views']):
        r_ = max([rt for rt in resets if rt < td], default=-1)
        if r_ != last_reset:                         # the probe moved: view history starts again
            ratios, last_reset = [], r_
        lab, nl = ndimage.label(rl[i] >= 1)
        hrv = int(np.bincount(lab.ravel())[1:].max()) if nl else 0
        hlv = int(cm[slot[t]].sum()) if t in slot else -1
        ratios.append(hrv / max(hlv, 1))
        hv = 0 if len(ratios) < ro['view_n'] and NEED3 else (2 if float(np.median(ratios[-ro['view_n']:])) >= ro['view_t'] else 1)
        view_ok.append(hrv == rv and hlv == lv and hv == vw and t < td < n and bool(jud[t]) and bool(jud[td]))
        views.append((td, vw, hrv / max(hlv, 1)))
    want = [t for t in range(ro['view_every'], n, ro['view_every']) if jud[t]]
    got = [row[0] for row in ro['views']]
    ck['views_bit_exact'] = all(view_ok) and all(g in want for g in got) and got == sorted(got)
    td = np.array([row[4] for row in ro['views']])
    trk = np.array([c_ == '0' for c_ in res['key']])
    ck['view_on_tracked_frame'] = bool(all(d % k == 0 or (trk[d] and trk[d - 1] and d - t0 >= 2)
                                          for d, t0 in zip(td, got)))   # forced on the next F1 frame only
    # live blobs: light / view / hold state at each frame
    hold_at = ~jud.copy()
    for e in (ho or {}).get('events', []):
        if e[1] == 1:
            hold_at[e[0]] = True                     # the lock frame's blob already says "holding"
        else:
            hold_at[e[0]] = False                    # the unlock frame's blob says "judging again"
    marks = sorted([(td_, vw) for td_, vw, _ in views] + [(rt, 0) for rt in resets])
    lr, vi, last, blob_ok, lag = z['live_roll'], 0, -1, True, []
    for kk, t in enumerate(z['live_t']):
        while vi < len(marks) and marks[vi][0] <= t:
            vi += 1
        blob_ok &= int(lr[kk, 1]) == (marks[vi - 1][1] if vi else 0)
        if lr.shape[1] > 3 and ho:
            blob_ok &= int(lr[kk, 3]) == int(hold_at[t])
        if int(lr[kk, 0]) == 0:
            blob_ok &= last < 0
            continue
        cand = [i for i in range(max(last, 0), len(light_seq)) if light_seq[i][0] <= t and light_seq[i][1] + 1 == int(lr[kk, 0])
                and float(np.float32(light_seq[i][2])) == float(np.float32(lr[kk, 2]))]
        blob_ok &= bool(cand)
        if cand:
            last = cand[0]
            lag.append(int(t - light_seq[last][0]))
    ck['live_blobs_carry_state'] = bool(blob_ok)
    ck['f1_runs'] = ro['f1_runs'] == len(ts)
    segs = json.loads((PLD / 'live' / 'src' / 'index.json').read_text(encoding='utf-8'))[meta['source']].get('segments', [])
    by_seg = [dict(s, lights=np.bincount([l for t, l, _ in light_seq if s['start'] <= t < s['start'] + s['n']],
                                         minlength=3).tolist(),
                   views=[vw for t, vw, _ in views if s['start'] <= t < s['start'] + s['n']],
                   held=int((~jud[s['start']:s['start'] + s['n']]).sum())) for s in segs]
    out.update(checks=ck, rolling=dict(chain=1, updates=len(ro['updates']), views=len(ro['views']),
                                       quality_ms=ro['quality_ms_mean'], quality_ms_max=ro['quality_ms_max'],
                                       view_ms=ro['view_ms_mean'], f1_ms=ro['f1_ms_mean'], f1_ms_max=ro['f1_ms_max'],
                                       f1_runs=ro['f1_runs'], blob_lag_frames_max=max(lag) if lag else None,
                                       lights=[l for _, l, _ in light_seq], view_seq=[vw for _, vw, _ in views],
                                       ratios=[round(r, 3) for _, _, r in views], by_segment=by_seg,
                                       hold=None if not ho else dict(events=ho['events'], held_frames=ho['held_frames'],
                                                                     unlock_d=unlock_d)))
    return out, res, bm


def check_case(case, im, rv_area, kf):
    meta = json.loads((OUT / f'{case}.json').read_text(encoding='utf-8'))
    res = meta['result']
    z = np.load(OUT / f'{case}.npz')
    v, bm = z['frames112'], z['masks']
    n, fps = res['n_frames'], res['fps']
    v = v[:n]
    if (res.get('rolling') or {}).get('chain') == 2:   # 11-02 chain 2: keyframes by F1 on the 128 frame -> 112 nearest
        x0, y0, side = meta['crop']
        src = PLD / 'live' / 'src' / meta['source']
        W, H = struct.unpack_from('<II', open(src, 'rb').read(12), 4)
        raw = np.memmap(src, np.uint8, 'r', 32).reshape(-1, H, W)[:n]
        box = echo_core.LcBox(x=x0, y=y0, side=side)
        f1, _, _ = integer128(PLD / 'echo' / 'results' / 'quant' / 'lv_r128')
        q = np.stack([echo_core.f1_to112(l) for l in f1(np.stack([echo_core.lc_crop_resize_n(fr, box, 128) for fr in raw]))])
    else:
        with torch.no_grad():
            x = video_inputs(v, 't3', fps)
            q = np.concatenate([im(torch.from_numpy(x[i:i + 128]))[:, 0, PAD:PAD + 112, PAD:PAD + 112].to(torch.int8).cpu().numpy()
                                for i in range(0, len(x), 128)])
    cm, nc = zip(*[echo_core.clean112(qq >= 1) for qq in q])
    cm, nc = np.stack(cm), np.array(nc, np.int32)
    hmasks, hkey, _ = echo_core.propagate(v, cm, dict(kf['policy']), kf['track'], fps)
    key = np.array([c == '1' for c in res['key']])
    ck = dict(engine_key=bool(np.array_equal(hkey.astype(bool), key)), engine_masks=bool(np.array_equal(hmasks.astype(bool), bm)),
              live_masks=bool(np.array_equal(z['live_masks'], bm)) and len(z['live_t']) == n,
              vols=bool(np.array_equal(np.array([echo_core.volume_pca(m) for m in hmasks]), np.array(res['vols']))))
    if (res.get('rolling') or {}).get('chain') == 2:   # EF calibrated with the CAMUS a / b the run sent
        cam = jobs.load_roll_camus()['camus']
        ck['ef_camus_calibration'] = res['ef_raw'] is not None and abs(res['ef'] - (cam['ef_a'] * res['ef_raw'] + cam['ef_b'])) < 1e-9
    out = dict(case=case, source=meta['source'], n=n, fps=fps, ef=res['ef'], light_clip=res['quality']['light'],
               latency=res['stream']['latency_ms'])
    ro = res.get('rolling')
    if ro is not None and ro.get('chain', 0) in (1, 2):
        return check_camus(meta, res, z, v, bm, ck, out)
    if ro is None:
        ck['no_roll_bytes'] = bool((z['live_roll'] == 0).all())
        out['checks'] = ck
        return out, res, bm
    model = kf['model']
    upd_ok, light_seq = [], []
    for t, lt, sc in ro['updates']:
        a = t - ro['win'] + 1
        f = echo_core.clip_features(v[a:t + 1], q[a:t + 1], bm[a:t + 1].astype(np.uint8), key[a:t + 1].astype(np.uint8),
                                    nc[a:t + 1])
        s, l = score_light(model, f)
        upd_ok.append(l == lt and float(np.float32(s)) == float(np.float32(sc)))
        light_seq.append((t, lt, sc))
    ck['rolling_bit_exact'] = all(upd_ok) and len(upd_ok) == (n - ro['win']) // ro['every'] + 1
    view_ok, ratios, views = [], [], []
    for t, rv, lv, vw in ro['views']:
        hrv, hlv = rv_area(v[t]), int(bm[t].sum())
        ratios.append(hrv / max(hlv, 1))
        med = float(np.median(ratios[-ro['view_n']:]))
        hv = 2 if med >= ro['view_t'] else 1
        view_ok.append(hrv == rv and hlv == lv and hv == vw)
        views.append((t, vw, hrv / max(hlv, 1)))
    ck['views_bit_exact'] = all(view_ok) and len(view_ok) == n // ro['view_every']
    lr, vi, last, blob_ok, lag = z['live_roll'], 0, -1, True, []
    for k, t in enumerate(z['live_t']):                  # quality comes from the worker: any update <= t, in order
        while vi < len(views) and views[vi][0] <= t:
            vi += 1
        blob_ok &= int(lr[k, 1]) == (views[vi - 1][1] if vi else 0)
        if int(lr[k, 0]) == 0:
            blob_ok &= last < 0
            continue
        cand = [i for i in range(max(last, 0), len(light_seq)) if light_seq[i][0] <= t and light_seq[i][1] + 1 == int(lr[k, 0])
                and float(np.float32(light_seq[i][2])) == float(np.float32(lr[k, 2]))]
        blob_ok &= bool(cand)
        if cand:
            last = cand[0]
            lag.append(int(t - light_seq[last][0]))
    ck['live_blobs_carry_state'] = bool(blob_ok)
    out.update(checks=ck, rolling=dict(updates=len(ro['updates']), views=len(ro['views']), quality_ms=ro['quality_ms_mean'],
                                       quality_ms_max=ro['quality_ms_max'], view_ms=ro['view_ms_mean'],
                                       blob_lag_frames_max=max(lag) if lag else None, lights=[l for _, l, _ in light_seq],
                                       view_seq=[vw for _, vw, _ in views], ratios=[round(r, 3) for _, _, r in views]))
    if 'n_2ch' in meta:
        n2 = meta['n_2ch']
        out['view_by_part'] = dict(two_ch=[vw for t, vw, _ in views if t < n2], four_ch=[vw for t, vw, _ in views if t >= n2])
    return out, res, bm


def main():
    im, rv_area = program16(), rv_reference()
    kf = jobs.load_kf_config('keyframe')
    caps = json.loads((OUT / 'caps.json').read_text(encoding='utf-8'))
    rep = dict(caps_ok=caps.get('server') == 'v5.26' and caps.get('live_roll') is True and caps.get('live_roll_camus') is True,
               cases=[])
    got = {}
    for case in ('L0', 'L1', 'L2', 'V1', 'C1', 'C0', 'S1', 'C9'):
        if not (OUT / f'{case}.npz').exists():
            rep['cases'].append(dict(case=case, checks=dict(ran=False)))
            continue
        o, res, bm = check_case(case, im, rv_area, kf)
        got[case] = (res, bm)
        rep['cases'].append(o)
        print(case, o['source'], 'PASS' if all(o['checks'].values()) else 'FAIL', o['checks'],
              'latency', o['latency'], {k: o.get('rolling', {}).get(k) for k in ('quality_ms', 'quality_ms_max', 'view_ms', 'blob_lag_frames_max')}, flush=True)
        if 'rolling' in o:
            print('   lights', o['rolling']['lights'])
            print('   views', o['rolling']['view_seq'], 'ratios', o['rolling']['ratios'])
            if 'view_by_part' in o:
                print('   by part', o['view_by_part'])
            for sg in o['rolling'].get('by_segment', []):
                print('   segment %s %s %s: lights g/y/r %s, views %s, held %d frames' % (sg['patient'], sg['view'], sg['quality'], sg['lights'], sg['views'], sg.get('held', 0)))
            if o['rolling'].get('hold'):
                print('   hold', o['rolling']['hold'], 'f1_runs', o['rolling']['f1_runs'])
        if case == 'C0':                             # CAMUS chain with hold off == try 3 (before hold existed)
            r3 = json.loads((HERE / 'results_try3' / 'c1.json').read_text(encoding='utf-8'))['result']['rolling']
            r0 = res['rolling']
            v3, v0 = r3['views'], r0['views']             # need-3 rule: first view_n - 1 decisions are now 0, rest equal
            # chain 2: the frame a decision lands on (a[4]) follows the keyframe schedule, which F1 keyframes change
            same_v = len(v3) == len(v0) and all(a[:3] == b[:3] and (a[4] == b[4] or r0.get('chain') == 2) and
                                                (b[3] == (0 if i < r0['view_n'] - 1 else a[3]))
                                                for i, (a, b) in enumerate(zip(v3, v0)))
            o['checks']['same_as_try3_no_hold'] = r3['updates'] == r0['updates'] and same_v and 'hold' not in r0
            print('   C0 (hold off) == try 3:', o['checks']['same_as_try3_no_hold'], flush=True)
        if case == 'C9':                             # chain 1 must be exactly the active 2b0bc6a3's C1 run
            r9 = json.loads((HERE / 'results_post_activate_2b0b' / 'c1.json').read_text(encoding='utf-8'))['result']
            same = core(r9) == core(res) and r9['rolling']['updates'] == res['rolling']['updates'] and \
                r9['rolling']['views'] == res['rolling']['views'] and r9['rolling']['hold'] == res['rolling']['hold']
            o['checks']['same_as_2b0b_chain1'] = bool(same)
            print('   C9 (chain 1) == active 2b0bc6a3:', same, flush=True)
        if case not in ('C1', 'C0', 'S1', 'C9'):     # chain 0 must be exactly today's v5.25 run
            ref = json.loads((V525 / f'{case}.json').read_text(encoding='utf-8'))['result']
            r5, r6 = ref.get('rolling'), res.get('rolling')
            same = core(ref) == core(res) and (r5 is None) == (r6 is None)
            if r5 is not None and r6 is not None:
                same &= r5['updates'] == r6['updates'] and r5['views'] == r6['views'] and r6.get('chain') == 0
            o['checks']['same_as_v525'] = bool(same)
            print('   same as v5.25 (every field but timing):', same, flush=True)
    if 'L0' in got and 'L1' in got:
        rep['L0_equals_L1_except_rolling'] = (core(got['L0'][0]) == core(got['L1'][0])
                                             and bool(np.array_equal(got['L0'][1], got['L1'][1])))
        print('L0 == L1 (except rolling / timing):', rep['L0_equals_L1_except_rolling'])
    rep['pass_'] = rep['caps_ok'] and rep.get('L0_equals_L1_except_rolling', False) and \
        all(all(c['checks'].values()) for c in rep['cases'])
    (OUT / 'host_check.json').write_text(json.dumps(rep, indent=1, ensure_ascii=False, default=str), encoding='utf-8')
    print('HOST_CHECK_PASS' if rep['pass_'] else 'HOST_CHECK_FAIL')


if __name__ == '__main__':
    main()
