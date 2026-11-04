# -*- coding: utf-8 -*-
"""_verify_cardiac_ui.py —— P1 1.6：上位机 sequence.cardiac_board() 的无界面检查（板端 v5.16 + 程序包 17 / 18）。

VAL 5 位病人：slot 用缓存的 512 灰度帧（R=G=B）+ 整数 lv_r128 掩膜（= F1 板上结果），走 cardiac_board()（板上跑程序 17 / 18
再做 8 掩膜 simpson_ef），结果与 cardiac_measure.measure（同一组掩膜，PC）逐位比较，并打印 cardiac_text()。
部分 A 起（下一版服务端）：带 PATIENT = 170 cm / 65 kg / 男，再比 lvidd_mm / rwt / bsa_m2 / lvmi / LVMI 分类；
板端还是 v5.16 时这几项缺失 -> 报 diff（旧服务端请用 --v516）。
    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe _verify_cardiac_ui.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ECHO = os.path.normpath(os.path.join(HERE, "..", "..", "echo"))
sys.path.insert(0, HERE)
sys.path.insert(0, ECHO)
import numpy as np  # noqa: E402

import cardiac_measure as cm  # noqa: E402
import imagelink  # noqa: E402
import sequence  # noqa: E402
from common import CACHE, CAMUS, RESULTS  # noqa: E402
from eval_p1_16 import to_original  # noqa: E402
from nifti import read_nifti  # noqa: E402

KEYS = ("la_raw_ml", "la_ml", "endo_ml", "epi_ml", "lv_mass_g", "wall_mm")
KEYS_A = ("lvidd_mm", "rwt", "bsa_m2", "lvmi")                 # 部分 A
PATIENT = (170.0, 65.0, "M")
V516 = "--v516" in sys.argv


def main():
    meta = json.loads((CACHE / "val_meta.json").read_text(encoding="utf-8"))
    x = np.load(CACHE / "val_img512.npy", mmap_mode="r")
    idx = {(m["patient"], m["view"], m["phase"]): i for i, m in enumerate(meta)}
    pats = sorted({m["patient"] for m in meta})[:5]
    link = imagelink.ImageLink("192.168.1.254", 5060, timeout=60)
    bad = 0
    for p in pats:
        slots = {}
        for view in ("2CH", "4CH"):
            m = meta[idx[(p, view, "ED")]]
            _, sp = read_nifti(CAMUS / "database_nifti" / p / f"{p}_{view}_ED_gt.nii.gz")
            s = dict(view=view, patient=p, w=m["width"], h=m["height"], spacing=(sp[1], sp[0]))
            for ph in ("ED", "ES"):
                i = idx[(p, view, ph)]
                g = np.ascontiguousarray(x[i])
                s[ph.lower() + "_img"] = np.repeat(g[..., None], 3, 2).tobytes()
                q = np.load(RESULTS / "quant" / "lv_r128" / "outputs" / f"val_{i:03d}.npy")
                s[ph.lower() + "_mask"] = (np.repeat(np.repeat(q >= 1, 4, 0), 4, 1).astype(np.uint8) * 255).tobytes()
            slots[view] = s
        res = sequence.cardiac_board(slots["2CH"], slots["4CH"], link, None if V516 else PATIENT)
        c = res["cardiac"]
        vv = {}
        for view in ("2CH", "4CH"):
            s = slots[view]
            mk = lambda tag, ph: to_original(np.load(RESULTS / "quant" / tag / "outputs" /
                                                     f"val_{idx[(p, view, ph)]:03d}.npy"), s["w"], s["h"])
            vv[view] = dict(spacing=s["spacing"], ed_endo=mk("lv_r128", "ED"), ed_epi=mk("epi_r128", "ED"),
                            es_la=mk("la_r128", "ES"))
        r = cm.measure(vv, *((None,) * 3 if V516 else PATIENT))
        keys = KEYS if V516 else KEYS + KEYS_A
        diff = [k for k in keys if (c.get(k) is None) != (r.get(k) is None) or
                (c.get(k) is not None and float(c[k]) != float(r[k]))] + \
            (["la_truncated"] if c["la_truncated"] != r["la_truncated"] else []) + \
            ([] if V516 or (c.get("lvmi_class") or {}).get("code") == (r.get("lvmi_class") or {}).get("code") else ["lvmi_class"])
        bad += bool(diff)
        print(p, "diff" if diff else "same", diff, "|", sequence.cardiac_text(c, 170, 65, "M"))
    print("CARDIAC_UI_PASS" if bad == 0 else "CARDIAC_UI_FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
