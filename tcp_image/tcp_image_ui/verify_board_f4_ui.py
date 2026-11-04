# -*- coding: utf-8 -*-
"""Drive the real sequence window against the board for F4 (EF), all three demo patients.

For each patient: load the 4CH folder, play it on the board, 「记入当前视图」; same for 2CH;
「算 EF」. Checks:
  * every step's mask equals the offline integer reference (echo/gen_f4_sample_ref.py),
    every step verified against the board's mask bytes, models 2 then 4;
  * the stored keyframes are the largest / smallest raw-area frames of that reference;
  * EDV / ESV / EF in the window equal the evaluation code applied to the reference masks
    (echo/eval_f4.py: to_original -> raw-area keyframes -> clean -> volumes).
The demo patients come from the test split; their EF is printed for the demo only, no metric.
Results: board_check_v5/gui_acceptance_f4.json and gui_f4_ef_<patient>.png.

Run with the srtp-vision python (numpy / scipy / scikit-image / Tk), TCL_LIBRARY / TK_LIBRARY
pinned as in run.bat:
    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe verify_board_f4_ui.py
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

import app                              # noqa: E402
import imageio                          # noqa: E402
import sequence                         # noqa: E402
from ef import clean, ejection_fraction, volumes   # noqa: E402  (same code as the UI)
from eval_f4 import to_original         # noqa: E402

OUT = HERE / "board_check_v5"
OUT.mkdir(exist_ok=True)
REF = np.load(ECHO / "results" / "f4" / "sample_ref_masks.npz")
PATIENTS = ("patient0027", "patient0213", "patient0231")

root = tk.Tk()
root.withdraw()
ui = app.App(root, "192.168.1.254", 5060)
win = ui._open_sequence()
report = {"pass": False, "patients": {}}
todo = [(p, v) for p in PATIENTS for v in ("4CH", "2CH")]
state = {"done": False}


def finish(error=None):
    if state["done"]:
        return
    state["done"] = True
    if error:
        report["error"] = str(error)
    else:
        report["pass"] = True
    ui.link.close()
    (OUT / "gui_acceptance_f4.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("GUI_F4_PASS" if report["pass"] else "GUI_F4_FAIL", json.dumps(report, ensure_ascii=False)[:1200], flush=True)
    root.destroy()


def offline_ef(p):
    """Evaluation code on the reference masks (eval_f4.py, strategy f3_auto)."""
    picked, spacing, frames = {}, {}, {}
    for v in ("2CH", "4CH"):
        name = "seq_%s_%s" % (p, v)
        meta = json.loads((ECHO / "samples" / name / "sequence.json").read_text(encoding="utf-8"))
        from PIL import Image
        w, h = Image.open(ECHO / "samples" / name / "frame_000.png").size
        full = [to_original(m, w, h) for m in REF[name]]
        area = np.array([m.sum() for m in full])
        ed, es = int(np.argmax(area)), int(np.argmin(area))
        picked[v] = (clean(full[ed]), clean(full[es]))
        spacing[v] = (meta["spacing_mm"][1], meta["spacing_mm"][0])
        frames[v] = (ed, es)
    edv, esv = volumes(picked["2CH"][0], picked["2CH"][1], spacing["2CH"],
                       picked["4CH"][0], picked["4CH"][1], spacing["4CH"])
    return dict(edv=edv, esv=esv, ef=ejection_fraction(edv, esv), keyframes=frames)


def start_next():
    if not todo:
        return finish()
    p, v = todo[0]
    if not win.load(str(ECHO / "samples" / ("seq_%s_%s" % (p, v)))):
        return finish("load failed %s %s" % (p, v))
    win._toggle_play()
    root.after(300, poll)


def poll():
    if win.busy or win.playing:
        root.after(100, poll)
        return
    p, v = todo.pop(0)
    name = "seq_%s_%s" % (p, v)
    try:
        tr = win.tracker
        ref = REF[name]
        assert len(tr.steps) == len(ref), (len(tr.steps), len(ref), win.var_status.get())
        got = [np.frombuffer(s.mask, np.uint8).reshape(512, 512)[::4, ::4].astype(bool) for s in tr.steps]
        assert all(np.array_equal(a, b) for a, b in zip(got, ref)), "%s: steps differ from offline" % name
        assert all(s.verified for s in tr.steps), "%s: board mask bytes differ" % name
        assert [s.kind for s in tr.steps] == ["auto"] + ["prop"] * (len(ref) - 1)
        assert win._ef_store(), win.var_status.get()
        slot = win.ef_slots[v]
        rec = report["patients"].setdefault(p, {})
        rec[v] = dict(frames=len(ref), ed_frame=slot["ed_frame"], es_frame=slot["es_frame"],
                      ai_ms_mean=float(np.mean([s.ai_us for s in tr.steps]) / 1000))
        if v == "2CH":
            res = win._ef_compute(confirm=False)
            assert res is not None, win.var_ef.get()
            off = offline_ef(p)
            assert (res["edv"], res["esv"], res["ef"]) == (off["edv"], off["esv"], off["ef"]), (res["edv"], res["esv"], res["ef"], off)
            assert (rec["2CH"]["ed_frame"], rec["2CH"]["es_frame"]) == off["keyframes"]["2CH"]
            assert (rec["4CH"]["ed_frame"], rec["4CH"]["es_frame"]) == off["keyframes"]["4CH"]
            rec.update(edv=res["edv"], esv=res["esv"], ef=res["ef"], equals_offline=True,
                       ef_ref_camus=win.ef_slots["4CH"]["ef_ref"], line=win.var_ef.get())
            imageio.save_image(str(OUT / ("gui_f4_ef_%s.png" % p)),
                               sequence.render_ef(win.ef_slots["2CH"], win.ef_slots["4CH"], res))
            win.ef_win.destroy()
            win._ef_clear()
        start_next()
    except Exception as exc:
        finish(repr(exc))


start_next()
root.after(400000, lambda: finish("GUI F4 test timeout"))
root.mainloop()
raise SystemExit(0 if report["pass"] else 1)
