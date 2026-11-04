"""Exercise the actual Tk UI against the deployed board, including local threshold and ROI."""
import json
from pathlib import Path
import tkinter as tk

import app
import confidence
import imageio
import imagelink

HERE = Path(__file__).resolve().parent
OUT = HERE / "board_check_v4"
root = tk.Tk()
root.withdraw()
ui = app.App(root, "192.168.1.254", 5060,
             preset_flags=imagelink._parse_flags("proc,mask,overlay,stats,logit,morph"))
report = {"pass": False}
phase = "full"
finished = False


def finish(error=None):
    global finished
    if finished:
        return
    finished = True
    if error:
        report["error"] = str(error)
    else:
        report["pass"] = True
    ui.link.close()
    (OUT / "gui_acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("GUI_V4_PASS" if report["pass"] else "GUI_V4_FAIL", report, flush=True)
    root.destroy()


def local_threshold():
    global phase
    try:
        assert ui.ai_stats["frame_count"] == report["full_frame_count"]
        assert ui.view_images["mask"] == confidence.mask_rgb(ui.logit_plane, 0)
        assert ui.view_images["mask"] != ui.reply_images["mask"]
        assert "本地重算" in ui.var_conf_note.get()
        report["local_threshold_without_new_frame"] = True
        ui.var_ai_thr.set("-3")
        ui.roi = (201, 183, 127, 95)
        ui._draw_roi()
        phase = "roi"
        root.after(100, ui._on_send)
        root.after(300, poll)
    except Exception as exc:
        finish(repr(exc))


def poll():
    if ui.busy:
        root.after(100, poll)
        return
    try:
        assert ui.ai_stats and ui.board_stat_version == 4, ui.var_status.get()
        assert "逐字节一致" in ui.verify_note, ui.verify_note
        assert "morph" in ui.reply_images and "morph" in ui.ai_stats
        if phase == "full":
            report["full_frame_count"] = ui.ai_stats["frame_count"]
            report["confidence_note"] = ui.verify_note
            report["morphology_note"] = ui.var_morph.get()
            ui.var_show.set("heat")
            assert ui._render_right() == "heat"
            imageio.save_image(str(OUT / "gui_heat.png"), ui.last_reply)
            ui.var_ai_thr.set("0")
            root.after(150, local_threshold)
        else:
            assert "ROI 与整图推理逐值一致" in ui.roi_verify_note, ui.roi_verify_note
            report["roi_note"] = ui.roi_verify_note
            report["roi_morphology_note"] = ui.var_morph.get()
            finish()
    except Exception as exc:
        finish(repr(exc))


ui._load_path(str(HERE / "samples/im0002.ppm"))
root.after(200, ui._on_send)
root.after(400, poll)
root.after(30000, lambda: finish("GUI test timeout"))
root.mainloop()
raise SystemExit(0 if report["pass"] else 1)
