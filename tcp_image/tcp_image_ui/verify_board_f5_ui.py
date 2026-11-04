# -*- coding: utf-8 -*-
"""Drive the real「连续帧配准」window (F5) against the v5.3 board.

Loads samples/seq_patient0027_4CH in the sequence window, opens the registration window and:
  1. frames 1 -> 2 with a known shift (3, -2) added to B: three-step search must return it, 3 frames;
  2. same frames, known shift (-5, 4): full search must return it, 15 frames;
  3. both difference images shown (before / after alignment, computed on the PL) equal 2 x the
     integer reference logits of |A - B| (clamped to 255);
  4. frames 1 -> 2 without an added shift: report the shift and the change of the mean difference.
Results: board_check_v5/gui_acceptance_f5.json and gui_f5_*.png.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe verify_board_f5_ui.py   (TCL_LIBRARY / TK_LIBRARY pinned)
"""
import json
import sys
from pathlib import Path
import tkinter as tk

HERE = Path(__file__).resolve().parent
ECHO = HERE.parents[1] / "echo"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ECHO))

import numpy as np                      # noqa: E402
import torch                            # noqa: E402

import app                              # noqa: E402
import imageio                          # noqa: E402
import regwin                           # noqa: E402
import registration as reg              # noqa: E402
from common import RESULTS              # noqa: E402
from int8_reference import run_integer  # noqa: E402

OUT = HERE / "board_check_v5"
qdir = RESULTS / "quant" / "reg_absdiff"
PLAN = json.loads((qdir / "quant_plan.json").read_text(encoding="utf-8"))
with np.load(qdir / "parameters.npz", allow_pickle=False) as z:
    ARRAYS = {k: z[k] for k in z.files}

root = tk.Tk()
root.withdraw()
ui = app.App(root, "192.168.1.254", 5060)
sw = ui._open_sequence()
report = {"pass": False, "cases": []}
state = {"done": False}
CASES = [("tss", (3, -2)), ("full", (-5, 4)), ("tss", (0, 0))]


def finish(error=None):
    if state["done"]:
        return
    state["done"] = True
    if error:
        report["error"] = str(error)
    else:
        report["pass"] = True
    ui.link.close()
    (OUT / "gui_acceptance_f5.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("GUI_F5_PASS" if report["pass"] else "GUI_F5_FAIL", json.dumps(report, ensure_ascii=False)[:900], flush=True)
    root.destroy()


def ref_diff(a, g):
    with torch.inference_mode():
        q = torch.from_numpy(np.stack([a, g, np.zeros_like(a)], -1)).permute(2, 0, 1).unsqueeze(0)
        lg = run_integer(PLAN, ARRAYS, q)[0][0, 0].numpy().astype(np.int64)
    return np.clip(2 * lg, 0, 255).astype(np.uint8)


def run_case():
    if not CASES:
        return finish()
    kind, known = CASES[0]
    rw.var_a.set("1")
    rw.var_b.set("2")
    rw.var_dx.set(str(known[0]))
    rw.var_dy.set(str(known[1]))
    rw.start(kind)
    root.after(300, poll)


def poll():
    if rw.busy:
        root.after(100, poll)
        return
    kind, known = CASES.pop(0)
    try:
        res = rw.result
        assert res is not None and res["kind"] == kind and res["known"] == known, rw.var_txt.get()
        a = np.frombuffer(res["a"], np.uint8).reshape(128, 128)
        b = res["b"]
        d0 = ref_diff(a, np.frombuffer(reg.shifted(b, res["a"], 0, 0), np.uint8).reshape(128, 128))
        d1 = ref_diff(a, np.frombuffer(reg.shifted(b, res["a"], *res["best"]), np.uint8).reshape(128, 128))
        same = (np.array_equal(np.frombuffer(res["diff0"], np.uint8).reshape(128, 128), d0)
                and np.array_equal(np.frombuffer(res["diff1"], np.uint8).reshape(128, 128), d1))
        assert same, "difference images differ from the integer reference"
        if known != (0, 0):
            assert res["best"] == known, (res["best"], known)
        if res.get("evals") is None:                  # U-Net 老路：三步 3 帧、全搜索 15 帧；PL 配准引擎不发 U-Net 帧
            assert res["frames"] == (3 if kind == "tss" else 15), res["frames"]
        else:
            assert res["evals"] > 0, res
        g = 255.0 / 127.0
        report["cases"].append(dict(kind=kind, known=known, found=res["best"], frames=res["frames"],
                                    search_s=round(res["search_s"], 2), board_ai_ms=round(res["ai_ms"], 1),
                                    ps_sum_us=max(res["ps_us"]), mad_before=round(res["cost"][(0, 0)] * g, 2),
                                    mad_after=round(res["cost"][res["best"]] * g, 2), diff_images_equal_reference=True,
                                    line=rw.var_txt.get()))
        imageio.save_image(str(OUT / ("gui_f5_%s_%d_%d.png" % (kind, known[0], known[1]))),
                           regwin._quad([regwin._panel(res["a"]), regwin._panel(res["b"]),
                                         regwin._panel(res["diff0"]), regwin._panel(res["diff1"])]))
        run_case()
    except Exception as exc:
        finish(repr(exc))


if not sw.load(str(ECHO / "samples" / "seq_patient0027_4CH")):
    finish("load failed")
else:
    rw = sw._open_reg()
    root.after(300, run_case)
    root.after(240000, lambda: finish("GUI F5 test timeout"))
    root.mainloop()
raise SystemExit(0 if report["pass"] else 1)
