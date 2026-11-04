"""ctypes wrapper of echo_core.dll = the board server's own C (tcp_image_rx/src: echo_num.c, kf.c, echo_quality.c,
bm_sad.c) compiled for the PC by build_core.sh with the board's numeric flags (-ffp-contract=off). Every number the
host evaluation reports for innovations 1-3 comes from this code, so the board and the host reference are one source.
"""
import ctypes
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
lib = ctypes.CDLL(str(HERE / 'echo_core.dll'))
P = ctypes.c_void_p
lib.en_volume_pca.argtypes = [P]
lib.en_volume_pca.restype = ctypes.c_double
lib.en_clean112.argtypes = [P, P]
lib.en_clean112.restype = ctypes.c_int
lib.en_analyze_flat.argtypes = [P, ctypes.c_int, ctypes.c_double, ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                ctypes.c_double, ctypes.c_double, ctypes.c_double, P, P]
lib.en_analyze_flat.restype = ctypes.c_int


def volume_pca(mask112):
    m = np.ascontiguousarray(mask112, np.uint8)
    assert m.shape == (112, 112)
    return lib.en_volume_pca(m.ctypes.data)


def clean112(mask112):
    m = np.ascontiguousarray(mask112, np.uint8)
    out = np.zeros_like(m)
    n = lib.en_clean112(m.ctypes.data, out.ctypes.data)
    return out.astype(bool), n


def analyze(vol, fps, step, cfg):
    """beats.analyze -> dict(ef, ef_sd, hr, fallback, beats=[(ed, es, v_ed, v_es, ef)])"""
    v = np.ascontiguousarray(vol, np.float64)
    head = np.zeros(8, np.float64)                  # ef, ef_sd, hr, cycle_s, has_hr, has_sd, fallback, nbeats
    beats = np.zeros((2048, 5), np.float64)
    lib.en_analyze_flat(v.ctypes.data, len(v), float(fps), int(step), int(cfg['smooth']),
                        1 if cfg['agg'] == 'mean' else 0, float(cfg['dist_s']), float(cfg['prom']), float(cfg['win']),
                        head.ctypes.data, beats.ctypes.data)
    nb = int(head[7])
    return dict(ef=head[0], ef_sd=head[1] if head[5] else None, hr=head[2] if head[4] else None,
                fallback=bool(head[6]), beats=[tuple(b) for b in beats[:nb]])


class KfCfg(ctypes.Structure):
    _fields_ = [('kind', ctypes.c_int), ('n', ctypes.c_int), ('n_max', ctypes.c_int), ('n_min', ctypes.c_int),
                ('win', ctypes.c_int), ('res', ctypes.c_double), ('R', ctypes.c_int), ('k', ctypes.c_int),
                ('reg', ctypes.c_int), ('pad', ctypes.c_int), ('spacing', ctypes.c_double)]


KIND = {'full': 0, 'hold': 1, 'fixed': 2, 'adaptive': 3}
REASON = ['track', 'start', 'lost', 'full', 'interval', 'residual', 'phase']
lib.kf_contour.argtypes = [P, ctypes.c_int, ctypes.c_int, ctypes.c_double, P, P, ctypes.c_int]
lib.kf_contour.restype = ctypes.c_int
lib.kf_fill.argtypes = [P, P, ctypes.c_int, ctypes.c_int, ctypes.c_int, P]
lib.kf_run_sequence.argtypes = [ctypes.POINTER(KfCfg), P, P, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_double,
                                P, P, P]
lib.kf_run_sequence.restype = ctypes.c_int


def kf_cfg(policy, cfg):
    """kf.py-style policy / tracking dicts -> KfCfg (defaults = kf.DEFAULT, chain mode)"""
    c = dict(R=4, k=5, reg=5, spacing=3.0, pad=24)
    c.update({k: v for k, v in cfg.items() if k != 'mode'})
    win = policy.get('win', -1)
    return KfCfg(KIND[policy['kind']], int(policy.get('n', 0)), int(policy.get('n_max', 0)), int(policy.get('n_min', 1)),
                 int(win) if win is not None and win >= 0 else -1,
                 float(policy['res']) if policy.get('res', 0) and policy['res'] < 1e8 else 0.0,
                 int(c['R']), int(c['k']), int(c['reg']), int(c['pad']), float(c['spacing']))


def contour(mask, spacing):
    m = np.ascontiguousarray(mask, np.uint8)
    H, W = m.shape
    py, px = np.zeros(480), np.zeros(480)
    n = lib.kf_contour(m.ctypes.data, W, H, float(spacing), py.ctypes.data, px.ctypes.data, 480)
    return np.stack([py[:n], px[:n]], 1) if n else None


def fill(pts, shape):
    H, W = shape
    m = np.zeros((H, W), np.uint8)
    if pts is not None and len(pts):
        py, px = np.ascontiguousarray(pts[:, 0]), np.ascontiguousarray(pts[:, 1])
        lib.kf_fill(py.ctypes.data, px.ctypes.data, len(py), W, H, m.ctypes.data)
    return m.astype(bool)


def propagate(frames, key_masks, policy, cfg, fps):
    """kf.propagate through the board C: -> masks [T, H, W] bool, is_key [T], info(resid, reason)"""
    f = np.ascontiguousarray(frames, np.uint8)
    km = np.ascontiguousarray(key_masks, np.uint8)
    T, H, W = f.shape
    out = np.zeros((T, H, W), np.uint8)
    reason = np.zeros(T, np.int32)
    resid = np.zeros(T, np.float64)
    c = kf_cfg(policy, cfg)
    lib.kf_run_sequence(ctypes.byref(c), f.ctypes.data, km.ctypes.data, T, W, H, float(fps),
                        out.ctypes.data, reason.ctypes.data, resid.ctypes.data)
    return out.astype(bool), reason != 0, dict(resid=resid, reason=[REASON[r] if r else '' for r in reason])


QF = ['touch', 'apex_gap', 'length', 'round', 'unc', 'multi', 'contrast', 'stability', 'roughness']
QS = ['beat_sd', 'inv_beats', 'fallback', 'unc', 'roughness', 'stability', 'contrast', 'apex_gap', 'multi']
lib.eq_clip_features.argtypes = [P, P, P, P, P, ctypes.c_int, ctypes.c_int, ctypes.c_int, P]
lib.eq_clip_features.restype = None


class EqModel(ctypes.Structure):
    _fields_ = [('w', ctypes.c_double * 10), ('mu', ctypes.c_double * 9), ('sd', ctypes.c_double * 9),
                ('t_green', ctypes.c_double), ('t_red', ctypes.c_double),
                ('b', ctypes.c_double * 10), ('zmu', ctypes.c_double * 9), ('zsd', ctypes.c_double * 9),
                ('q80', ctypes.c_double), ('q90', ctypes.c_double)]


class EqInterval(ctypes.Structure):
    _fields_ = [('sigma', ctypes.c_double), ('lo80', ctypes.c_double), ('hi80', ctypes.c_double),
                ('lo90', ctypes.c_double), ('hi90', ctypes.c_double), ('review', ctypes.c_int)]


lib.eq_interval.argtypes = [ctypes.POINTER(EqModel), ctypes.c_double, ctypes.c_double, ctypes.c_int, ctypes.c_int, P,
                            ctypes.POINTER(EqInterval)]
lib.eq_interval.restype = None


def clip_features(frames, q, masks, is_key, ncomp):
    # keep every converted array in a name: .ctypes.data of a temporary copy dangles once the copy is freed
    f = np.ascontiguousarray(frames, np.uint8)
    qq = np.ascontiguousarray(q, np.int8)
    mm = np.ascontiguousarray(masks, np.uint8)
    kk = np.ascontiguousarray(is_key, np.uint8)
    nn = np.ascontiguousarray(ncomp, np.int32)
    T, H, W = f.shape
    out = np.zeros(9, np.float64)
    lib.eq_clip_features(f.ctypes.data, qq.ctypes.data, mm.ctypes.data, kk.ctypes.data, nn.ctypes.data, T, W, H,
                         out.ctypes.data)
    return dict(zip(QF, out.tolist()))


def eq_model(m):
    """dict (quality_kf.py model file) -> EqModel"""
    e = EqModel()
    for name in ('w', 'mu', 'sd', 'b', 'zmu', 'zsd'):
        getattr(e, name)[:] = [float(x) for x in m[name]]
    e.t_green, e.t_red, e.q80, e.q90 = (float(m[k]) for k in ('t_green', 't_red', 'q80', 'q90'))
    return e


def interval(model, ef, beat_sd, nbeats, fallback, feats):
    f = np.ascontiguousarray([feats[k] for k in QF], np.float64)
    o = EqInterval()
    lib.eq_interval(ctypes.byref(model), float(ef), float(beat_sd), int(nbeats), int(fallback), f.ctypes.data, ctypes.byref(o))
    return dict(sigma=o.sigma, lo80=o.lo80, hi80=o.hi80, lo90=o.lo90, hi90=o.hi90, review=bool(o.review))


# ---- live entry (live_crop.c, 10-25): moving square on a captured screen + exact box resize to 112
class LcBox(ctypes.Structure):
    _fields_ = [(k, ctypes.c_int) for k in ('x', 'y', 'side', 'bx', 'by', 'bw', 'bh', 'cells')]


lib.lc_detect.argtypes = [P, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(LcBox)]
lib.lc_detect.restype = ctypes.c_int
lib.lc_crop_resize.argtypes = [P, ctypes.c_int, ctypes.c_int, ctypes.POINTER(LcBox), P, P]
lib.lc_crop_resize.restype = None


def lc_detect(frames, thr):
    """frames [K, H, W] uint8 -> LcBox (or None when nothing moves)"""
    f = np.ascontiguousarray(frames, np.uint8)
    K, H, W = f.shape
    ptrs = (ctypes.c_void_p * K)(*[f.ctypes.data + k * H * W for k in range(K)])
    box = LcBox()
    return box if lib.lc_detect(ctypes.cast(ptrs, ctypes.c_void_p), K, W, H, int(thr), ctypes.byref(box)) == 0 else None


def lc_crop_resize(frame, box):
    """one [H, W] uint8 frame -> [112, 112] uint8"""
    f = np.ascontiguousarray(frame, np.uint8)
    out = np.zeros((112, 112), np.uint8)
    tmp = np.zeros(box.side * 112, np.uint32)
    lib.lc_crop_resize(f.ctypes.data, f.shape[1], 1, ctypes.byref(box), out.ctypes.data, tmp.ctypes.data)
    return out


# ---- v5.26 (11-02): CAMUS quality chain in live mode (E:/PLD/quality/live_camus)
lib.lc_crop_resize_n.argtypes = [P, ctypes.c_int, ctypes.c_int, ctypes.POINTER(LcBox), P, P, ctypes.c_int]
lib.lc_crop_resize_n.restype = None
lib.eq_clean.argtypes = [P, P, ctypes.c_int, ctypes.c_int]
lib.eq_clean.restype = ctypes.c_int


class EqQuality(ctypes.Structure):
    _fields_ = [('f', ctypes.c_double * 9), ('score', ctypes.c_double), ('light', ctypes.c_int),
                ('reason', ctypes.c_int * 2), ('nkey', ctypes.c_int)]


lib.eq_window.argtypes = [P, ctypes.c_int, ctypes.c_int, ctypes.c_int, P, ctypes.c_int, P, P, P, ctypes.POINTER(EqModel),
                          ctypes.POINTER(EqQuality)]
lib.eq_window.restype = ctypes.c_int


lib.eq_f1_to112.argtypes = [P, P]
lib.eq_f1_to112.restype = None


def f1_to112(lg128):
    """v5.26 chain 2: F1 128 x 128 int8 logits -> the live engine's 112 x 112 logits (nearest, as on the board)"""
    q = np.ascontiguousarray(lg128, np.int8)
    out = np.zeros((112, 112), np.int8)
    lib.eq_f1_to112(q.ctypes.data, out.ctypes.data)
    return out


def lc_crop_resize_n(frame, box, n):
    """one [H, W] uint8 frame -> [n, n] uint8 (the same box filter; n <= box.side)"""
    f = np.ascontiguousarray(frame, np.uint8)
    out = np.zeros((n, n), np.uint8)
    tmp = np.zeros(box.side * n, np.uint32)
    lib.lc_crop_resize_n(f.ctypes.data, f.shape[1], 1, ctypes.byref(box), out.ctypes.data, tmp.ctypes.data, n)
    return out


def eq_clean(mask):
    """any [H, W] bool mask -> (largest 4-connected component, holes filled, component count) as on the board"""
    m = np.ascontiguousarray(mask, np.uint8)
    out = np.zeros_like(m)
    n = lib.eq_clean(m.ctypes.data, out.ctypes.data, m.shape[1], m.shape[0])
    return out.astype(bool), n


def eq_window(frames, sel, q, masks, ncomp, model):
    """window frames [T, H, W]; sel = window indices of the segmented frames; q / masks / ncomp of those, in order;
    model = EqModel -> dict(f=features, score, light, reason, nkey)"""
    f = np.ascontiguousarray(frames, np.uint8)
    s = np.ascontiguousarray(sel, np.int32)
    qq = np.ascontiguousarray(q, np.int8)
    mm = np.ascontiguousarray(masks, np.uint8)
    nn = np.ascontiguousarray(ncomp, np.int32)
    T, H, W = f.shape
    o = EqQuality()
    if lib.eq_window(f.ctypes.data, T, W, H, s.ctypes.data, len(s), qq.ctypes.data, mm.ctypes.data, nn.ctypes.data,
                     ctypes.byref(model), ctypes.byref(o)) != 0:
        raise MemoryError('eq_window')
    return dict(f=dict(zip(QF, list(o.f))), score=o.score, light=o.light, reason=list(o.reason), nkey=o.nkey)


# ---- cardiac_ext.c (10-25): regional wall motion + right-heart plausibility
class HcPar(ctypes.Structure):
    _fields_ = [('flags', ctypes.c_int), ('rv_model', ctypes.c_int), ('ra_model', ctypes.c_int),
                ('mu', ctypes.c_double * 6), ('sd', ctypes.c_double * 6), ('t_seg', ctypes.c_double),
                ('w', ctypes.c_double * 4), ('t_rec', ctypes.c_double), ('min_frac', ctypes.c_double),
                ('max_overlap', ctypes.c_double)]


class WmOut(ctypes.Structure):
    _fields_ = [('fac', ctypes.c_double * 6), ('z', ctypes.c_double * 6), ('score', ctypes.c_double),
                ('seg', ctypes.c_int * 6), ('flag', ctypes.c_int), ('has_score', ctypes.c_int), ('nbeats', ctypes.c_int)]


class RhPart(ctypes.Structure):
    _fields_ = [(k, ctypes.c_int) for k in ('area', 'lv_area', 'present', 'side', 'ok')] + \
               [(k, ctypes.c_double) for k in ('overlap', 'cy', 'cx')]


lib.hc_parse.argtypes = [P, ctypes.c_uint32, ctypes.POINTER(HcPar)]
lib.hc_parse.restype = ctypes.c_int
lib.wm_beat.argtypes = [P, P, ctypes.c_int, ctypes.c_int, P]
lib.wm_beat.restype = ctypes.c_int
lib.wm_eval.argtypes = [ctypes.POINTER(HcPar), P, ctypes.c_int, ctypes.c_double, ctypes.POINTER(WmOut)]
lib.wm_eval.restype = None
lib.rh_largest.argtypes = [P, ctypes.c_int, ctypes.c_int, P]
lib.rh_largest.restype = ctypes.c_int
lib.rh_check.argtypes = [P, P, ctypes.c_int, ctypes.c_int, ctypes.POINTER(HcPar), ctypes.POINTER(RhPart)]
lib.rh_check.restype = None


def hc_par(block):
    """HC01 bytes -> HcPar (the board's parser)"""
    b = bytes(block)
    p = HcPar()
    assert lib.hc_parse(b, len(b), ctypes.byref(p)) == 0, 'bad HC01 block'
    return p


def wm_beat(ed, es):
    """-> fac[6] (HMC-QU order) or None"""
    a, b = (np.ascontiguousarray(m, np.uint8) for m in (ed, es))
    fac = np.zeros(6, np.float64)
    return fac if lib.wm_beat(a.ctypes.data, b.ctypes.data, a.shape[1], a.shape[0], fac.ctypes.data) == 0 else None


def wm_clip(masks, beats, vols):
    """board order: every beat (ed, es) whose ED mask is usable, fac summed in beat order and divided; no beats ->
    (argmax, argmin) of the volume curve. -> (fac[6] or None, n used)"""
    if not beats:
        beats = [(int(np.argmax(vols)), int(np.argmin(vols)))]
    s, n = np.zeros(6), 0
    for ed, es in beats:
        f = wm_beat(masks[ed], masks[es])
        if f is not None:
            s = s + f if n else f.copy()
            n += 1
    return (s / n if n else None), n


def wm_eval(par, fac, nbeats, ef):
    f = np.ascontiguousarray(fac, np.float64)
    o = WmOut()
    lib.wm_eval(ctypes.byref(par), f.ctypes.data, int(nbeats), float('nan') if ef is None else float(ef), ctypes.byref(o))
    return dict(fac=list(o.fac), z=list(o.z), seg=[bool(v) for v in o.seg], score=o.score if o.has_score else None,
                flag=bool(o.flag) if o.has_score else None, beats=o.nbeats)


def rh_largest(m):
    a = np.ascontiguousarray(m, np.uint8)
    out = np.zeros_like(a)
    area = lib.rh_largest(a.ctypes.data, a.shape[1], a.shape[0], out.ctypes.data)
    return out.astype(bool), area


def rh_check(part, lv, par):
    a, b = (np.ascontiguousarray(m, np.uint8) for m in (part, lv))
    o = RhPart()
    lib.rh_check(a.ctypes.data, b.ctypes.data, a.shape[1], a.shape[0], ctypes.byref(par), ctypes.byref(o))
    return {k: getattr(o, k) for k, _ in RhPart._fields_}
