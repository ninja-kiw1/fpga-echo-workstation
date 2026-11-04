"""Open the existing CT UI for a live FPGA reconstruction demonstration."""
import ctypes
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

BASE = Path(__file__).resolve().parent
UI = BASE.parents[1] / "tcp_image" / "tcp_image_ui"   # ct/demo_3d_20261011 -> E:/PLD
sys.path.insert(0, str(UI))
os.environ.setdefault("TCL_LIBRARY", r"C:\Users\21888\anaconda3\envs\srtp-vision\Library\lib\tcl8.6")
os.environ.setdefault("TK_LIBRARY", r"C:\Users\21888\anaconda3\envs\srtp-vision\Library\lib\tk8.6")
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

import tkinter as tk
import numpy as np
from PIL import Image
import ctwin
import imagelink

root = tk.Tk()
root.withdraw()
link = imagelink.ImageLink("192.168.1.254", 5060, timeout=30)
app = SimpleNamespace(root=root, link=link, busy=False,
                      var_host=tk.StringVar(value=link.host),
                      var_port=tk.StringVar(value=str(link.port)))
win = ctwin.CTWindow(app)
win.top.geometry("+60+35")
CASE = "spleen_47"
win.load(str(Path(ctwin.MSD_DIR) / "imagesTr" / (CASE + ".nii.gz")))
saved = False


def export_animation(surface):
    try:
        frames = [Image.fromarray(surface.render(a, 420)) for a in range(0, 360, 12)]
        frames[0].save(BASE / "spleen_47_reconstruction.gif", save_all=True,
                       append_images=frames[1:], duration=100, loop=0)
        print("Animation saved", flush=True)
    except Exception as exc:
        print("Animation export:", exc, flush=True)


def observe():
    global saved
    if win.final is not None and win.surface is not None and not saved:
        saved = True
        spacing = win.vol["spacing"]
        label = win.vol["label"]
        result = dict(case=CASE, source="live FPGA TCP 192.168.1.254:5060",
                      utc=datetime.now(timezone.utc).isoformat(),
                      slices=len(win.u8), spacing_mm=list(map(float, spacing)),
                      volume_ml=win.cv.volume_ml(win.final, spacing),
                      dice=win.cv.dice3d(win.final, label),
                      label_volume_ml=win.cv.volume_ml(label, spacing),
                      coarse=win.stats, fine=win.stats_fine,
                      roi=list(map(int, win.win)),
                      result=win.var_res.get(), status=win.var_txt.get())
        np.savez_compressed(BASE / "spleen_47_board_result.npz", mask=win.final,
                            spacing=spacing, coarse_logits=win.logits,
                            fine_logits=win.fine_logits)
        (BASE / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)
        link.close()
        threading.Thread(target=export_animation, args=(win.surface,), daemon=True).start()
    if win.top.winfo_exists():
        root.after(300, observe)


def close():
    win.close()
    link.close()
    root.destroy()


win.top.protocol("WM_DELETE_WINDOW", close)
root.after(300, observe)
root.mainloop()
