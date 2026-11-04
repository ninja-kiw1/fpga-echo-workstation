"""Host reference of the board's whole-heart extension (src/job_echo.c heart_json, HC01, server v5.24): the same C
(echo_core.dll = src/cardiac_ext.c) called in the same order on the engine's LV masks and on programs 21 / 22's
integer logits (E:/PLD/wallmotion/rh_logits.py = the PL, bit for bit). Returns the "wall_motion" / "right_heart"
members as the board writes them (numbers as Python floats; JSON %.17g round-trips them) and the "rh_masks" list.
"""
import numpy as np

import echo_core

RH_MAXB = 6
SEGS = ["SEG3", "SEG9", "SEG14", "SEG16", "SEG12", "SEG6"]


def pairs_of(beats, vols):
    """beats = [(ed, es), ...] in the board's order; none -> (first argmax, first argmin) of the volume curve"""
    if beats:
        return [(int(a), int(b)) for a, b in beats]
    v = list(vols)
    imax = imin = 0
    for t in range(1, len(v)):
        imax = t if v[t] > v[imax] else imax
        imin = t if v[t] < v[imin] else imin
    return [(imax, imin)]


def heart_reference(par, flags, masks, beats, vols, ef, rv_q=None, ra_q=None):
    """par = echo_core.hc_par(block); masks [T,112,112] bool (engine); beats = [(ed, es)]; ef = calibrated EF or None;
    rv_q / ra_q = int8 [T,112,112] logits of programs 21 / 22 (only frames of the used pairs are read)."""
    out, rh = {}, []
    pairs = pairs_of(beats, vols)
    if flags & 1:
        s, used = None, 0
        for ed, es in pairs:
            f = echo_core.wm_beat(masks[ed], masks[es])
            if f is not None:
                s = f.copy() if used == 0 else s + f
                used += 1
        if used:
            o = echo_core.wm_eval(par, s / used, used, ef)
            out["wall_motion"] = dict(beats=used, segments=SEGS, fac=o["fac"], z=o["z"], seg=o["seg"],
                                      score=o["score"], flag=o["flag"])
        else:
            out["wall_motion"] = None
    if flags & 2:
        rows, n_rv, n_ra, rv_lv, rv_fac, ra_lv = [], 0, 0, 0.0, 0.0, 0.0
        for ed, es in pairs[:RH_MAXB]:
            parts, chk = [], []
            for fi, q in ((ed, rv_q), (es, rv_q), (es, ra_q)):
                p, _ = echo_core.rh_largest(q[fi] >= 1)
                parts.append(p)
                chk.append(echo_core.rh_check(p, masks[fi], par))
            ra_ok = bool(chk[2]["ok"] and (not chk[1]["ok"] or chk[2]["cy"] > chk[1]["cy"]))
            rows.append(dict(ed=ed, es=es, lv_ed=chk[0]["lv_area"], lv_es=chk[2]["lv_area"], rv_ed=chk[0]["area"],
                             rv_es=chk[1]["area"], ra_es=chk[2]["area"], rv_ok_ed=bool(chk[0]["ok"]),
                             rv_ok_es=bool(chk[1]["ok"]), ra_ok=ra_ok, rv_overlap_ed=chk[0]["overlap"],
                             ra_overlap=chk[2]["overlap"]))
            if chk[0]["ok"] and chk[1]["ok"] and chk[0]["lv_area"] > 0:
                rv_lv += chk[0]["area"] / chk[0]["lv_area"]
                rv_fac += 1.0 - chk[1]["area"] / chk[0]["area"]
                n_rv += 1
            if ra_ok and chk[0]["lv_area"] > 0:
                ra_lv += chk[2]["area"] / chk[0]["lv_area"]
                n_ra += 1
            rh.append(tuple(parts))
        out["right_heart"] = dict(rv_model=par.rv_model, ra_model=par.ra_model, pairs=rows, n_rv=n_rv, n_ra=n_ra,
                                  rv_lv=rv_lv / n_rv if n_rv else None, rv_fac=rv_fac / n_rv if n_rv else None,
                                  ra_lv=ra_lv / n_ra if n_ra else None)
    return out, rh


def compare(board, host, rh_board=None, rh_host=None):
    """board / host result members -> dict of per-field equality (exact)."""
    ck = {}
    if "wall_motion" in host:
        b, h = board.get("wall_motion"), host["wall_motion"]
        ck["wall_motion"] = (b is None and h is None) or (b is not None and h is not None and all(
            b[k] == h[k] for k in ("beats", "fac", "z", "seg", "score", "flag")))
    if "right_heart" in host:
        b, h = board.get("right_heart") or {}, host["right_heart"]
        ck["right_heart"] = all(b.get(k) == h[k] for k in ("pairs", "n_rv", "n_ra", "rv_lv", "rv_fac", "ra_lv"))
        if rh_board is not None:
            ck["rh_masks"] = len(rh_board) == len(rh_host) and all(
                all(np.array_equal(x, y) for x, y in zip(a, c)) for a, c in zip(rh_board, rh_host))
    return ck
