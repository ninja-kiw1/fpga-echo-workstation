#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_station.py —— P1 1.5：板子独立运行的界面（DP 显示器 + USB 鼠标，拔掉网线也能完成整病例）。

跑在 FZ3B 自己的 X（lxdm，:0）上：python3 + GTK 3.22 + numpy 1.13（板上自带，不装新东西）。
通过 127.0.0.1:5060 调用同一个服务端（病例任务协议 v7，jobs.py），所以算法和 PC 上一样、结果逐位相同：
  CT     ct_seg（两阶段 PL 分割 + PL 放大 + 三维连通块 + 脾脏测量 + 板上三维表面 + 窗宽窗位后的各层）
         每一层的叠加图（灰度 + 半透明掩膜 + 轮廓）都由 PL 的 vis_layer 合成（JOB_OVERLAY）
  心超   echo_ef（逐帧 PL 分割 + PL 清理 + 逐拍 EF + LVEF 分级 + RR），逐帧叠加图同样由 PL 合成
病例从 SD 卡读（/root/cases/manifest.json，由 PC 上 make_board_cases.py 准备）：CT 是原始 .nii.gz；
心超视频**不能在板上解码**（10-13：libjpeg 与 OpenCV 有 2.7% 像素不同），所以 PC 预先解码成灰度帧文件。
仅辅助测量与复核，不作诊断。

    DISPLAY=:0 python3 /root/station/board_station.py
"""
import json
import os
import sys
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk  # noqa: E402

import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import imagelink  # noqa: E402
import jobs  # noqa: E402

CASES = os.environ.get("STATION_CASES", "/root/cases")
HOST, PORT = "127.0.0.1", 5060
NOTE = "仅辅助测量与复核，不作诊断"
LVEF_BANDS = ("≤40%（降低）", "41–49%（轻度降低）", "≥50%（保留）")      # 2021 ESC 的三档，只描述数值区间


# ------------------------------------------------------------------ numpy 1.13: no bitorder= in packbits
def pack_le(m):
    m = np.ascontiguousarray(m, bool).ravel()
    pad = (-len(m)) % 8
    if pad:
        m = np.concatenate([m, np.zeros(pad, bool)])
    return np.packbits(m.reshape(-1, 8)[:, ::-1], axis=1).ravel().tobytes()


def unpack_le(b, shape):
    n = int(np.prod(shape))
    a = np.unpackbits(np.frombuffer(b, np.uint8)).reshape(-1, 8)[:, ::-1].ravel()[:n]
    return a.reshape(shape).astype(bool)


def pack_rows128(m):
    """bit mask [h, w] -> rows padded to whole 128-bit words (the overlay's bit-mask layout)."""
    h, w = m.shape
    p = np.zeros((h, (w + 127) // 128 * 128), bool)
    p[:, :w] = m
    return b"".join(pack_le(r) for r in p)


def overlay(link, ow, oh, flags, base=None, bits=None, fill=(255, 64, 64), fill_a=6, contour=(255, 255, 0)):
    """JOB_OVERLAY（PL 合成；无 vis_layer 时板端用 PS 按同一规格算）-> (oh, ow, 3) uint8, engine."""
    import struct
    mh, mw = bits.shape if bits is not None else (0, 0)
    rgb = lambda c: c[0] | c[1] << 8 | c[2] << 16
    params = struct.pack("<16i", ow, oh, flags, 0, 0, mw, mh, 0, 0, fill_a, 16, rgb(fill), 0, rgb(contour), 0, 0)
    data = b""
    if flags & 3 in (jobs.OVL_BASE_GREY, jobs.OVL_BASE_RGB):
        data += np.ascontiguousarray(base, np.uint8).tobytes()
    if flags & jobs.OVL_HAS_BITS:
        data += pack_rows128(bits)
    r = jobs.run(link, jobs.JOB_OVERLAY, 0, params, data)
    return np.frombuffer(r["blobs"]["overlay"], np.uint8).reshape(oh, ow, 3), r["result"].get("engine")


def to_pixbuf(rgb):
    rgb = np.ascontiguousarray(rgb, np.uint8)
    h, w = rgb.shape[:2]
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(rgb.tobytes()), GdkPixbuf.Colorspace.RGB, False, 8, w, h, w * 3)


# ------------------------------------------------------------------ window
class Station(object):
    def __init__(self):
        self.link = imagelink.ImageLink(HOST, PORT, timeout=60.0)
        self.lock = threading.Lock()                     # one request at a time on the link
        self.cases = self._load_cases()
        self.ct = self.echo = None
        self.win = Gtk.Window(title="FZ3B 边缘影像工作站（板上独立运行）")
        self.win.connect("destroy", Gtk.main_quit)
        self.win.set_default_size(1280, 760)
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root.set_border_width(8)
        self.win.add(root)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        root.pack_start(left, False, False, 0)
        left.pack_start(Gtk.Label(label="病例（SD 卡 %s）" % CASES), False, False, 0)
        self.listbox = Gtk.ListBox()
        for c in self.cases:
            self.listbox.add(Gtk.Label(label="%s  %s" % ("CT" if c["kind"] == "ct" else "心超", c["name"]), xalign=0))
        left.pack_start(self.listbox, True, True, 0)
        self.btn = Gtk.Button(label="▶ 在板上分析")
        self.btn.connect("clicked", self.on_run)
        left.pack_start(self.btn, False, False, 0)
        row = Gtk.Box(spacing=4)
        row.pack_start(Gtk.Label(label="体重 kg"), False, False, 0)
        self.weight = Gtk.SpinButton.new_with_range(0, 250, 1)
        row.pack_start(self.weight, False, False, 0)
        left.pack_start(row, False, False, 0)
        mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        root.pack_start(mid, False, False, 0)
        self.img = Gtk.Image()
        self.img.set_size_request(512, 512)
        mid.pack_start(self.img, False, False, 0)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
        self.scale.set_draw_value(True)
        self.scale.connect("value-changed", self.on_slice)
        mid.pack_start(self.scale, False, False, 0)
        self.img_note = Gtk.Label(label="", xalign=0)
        mid.pack_start(self.img_note, False, False, 0)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        root.pack_start(right, True, True, 0)
        self.img3d = Gtk.Image()
        self.img3d.set_size_request(320, 320)
        right.pack_start(self.img3d, False, False, 0)
        self.text = Gtk.Label(label="选一个病例，点「在板上分析」。\n" + NOTE, xalign=0, yalign=0)
        self.text.set_line_wrap(True)
        self.text.set_selectable(True)
        right.pack_start(self.text, True, True, 0)
        self.status = Gtk.Label(label="", xalign=0)
        right.pack_start(self.status, False, False, 0)
        self.views, self.view_i = [], 0
        GLib.timeout_add(150, self.spin3d)
        self.play_i = 0
        GLib.timeout_add(120, self.play)
        self.win.show_all()

    def _load_cases(self):
        try:
            return json.load(open(os.path.join(CASES, "manifest.json"), encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def say(self, s):
        GLib.idle_add(self.status.set_text, s)

    # ---- run
    def on_run(self, _):
        row = self.listbox.get_selected_row()
        if row is None or not self.cases:
            self.text.set_text("先在左边选一个病例。\n" + NOTE)
            return
        c = self.cases[row.get_index()]
        self.btn.set_sensitive(False)
        self.ct = self.echo = None
        self.views = []
        threading.Thread(target=self.work_ct if c["kind"] == "ct" else self.work_echo, args=(c,), daemon=True).start()

    def work_ct(self, c):
        try:
            data = open(os.path.join(CASES, c["file"]), "rb").read()
            t0 = time.time()
            with self.lock:
                self.link.connect()
                r = jobs.ct_seg(self.link, None, data=data, on_progress=lambda p: self.say("板上：%s %s/%s" % (
                    p.get("stage"), p.get("done", ""), p.get("total", ""))),
                    flags=jobs.CT_MASK_FINAL | jobs.CT_RENDER3D | jobs.CT_SLICES, views=36, size=320)
            res = r["result"]
            nz = res["nz"]
            ct = dict(res=res, nz=nz, wall=time.time() - t0,
                      slices=np.frombuffer(r["blobs"]["slices_u8"], np.uint8).reshape(nz, 512, 512),
                      mask=unpack_le(r["blobs"]["mask_final"], (nz, 512, 512)))
            s = res.get("surface") or {}
            if "render3d" in r["blobs"] and s.get("size"):
                n = s["size"]
                a = np.frombuffer(r["blobs"]["render3d"], np.uint8).reshape(-1, n, n, 3)
                self.views = [a[i] for i in range(len(a))]
            area = ct["mask"].reshape(nz, -1).sum(1)
            self.ct = ct
            GLib.idle_add(self.show_ct, int(np.argmax(area)) if area.any() else nz // 2)
        except Exception as e:
            GLib.idle_add(self.text.set_text, "CT 失败：%s" % e)
        GLib.idle_add(self.btn.set_sensitive, True)

    def show_ct(self, z):
        ct, res = self.ct, self.ct["res"]
        self.scale.set_range(0, ct["nz"] - 1)
        self.scale.set_value(z)
        sp = res.get("spleen") or {}
        w = self.weight.get_value()
        lines = ["CT 脾脏（板上两阶段分割，PL 推理 + PL 放大，板上三维连通块与测量）",
                 "体积 %.0f mL" % res["volume_ml"]]
        if w > 0:
            thr = 503.0 if w > 125 else 3.01 * w + 127.0
            lines.append("按体重的脾大体积阈值 %.0f mL（3.01 × kg + 127，Perez 2023）：%s" % (thr, "超过" if res["volume_ml"] > thr else "未超过"))
        if sp:
            lines.append("头尾长度 %.1f cm，宽 %.1f cm，厚 %.1f cm，脾指数 %.0f（只显示数值：手工测量定义，不套用其范围）" % (
                sp["length_mm"] / 10, sp["width_mm"] / 10, sp["thickness_mm"] / 10, sp["splenic_index"]))
        s = res.get("surface") or {}
        lines.append("三维表面：板上渲染（%s 个角度，建表面 %.0f ms，每张 %.0f ms）" % (len(self.views), s.get("build_ms", 0), s.get("render_ms", 0)))
        lines.append("耗时：板上 %.2f s（PL 推理 %.2f s），界面合计 %.2f s" % (res["ms"]["total"] / 1000, res["pl_ms"] / 1000, ct["wall"]))
        lines.append(NOTE)
        self.text.set_text("\n".join(lines))
        self.on_slice(None)

    def on_slice(self, _):
        if self.ct is None:
            return
        z = int(self.scale.get_value())
        ct = self.ct

        def work():
            try:
                with self.lock:
                    self.link.connect()
                    img, eng = overlay(self.link, 512, 512, jobs.OVL_BASE_GREY | jobs.OVL_HAS_BITS | jobs.OVL_FILL_FROM_BITS |
                                       jobs.OVL_FILL | jobs.OVL_CONTOUR, base=ct["slices"][z], bits=ct["mask"][z])
                GLib.idle_add(self.img.set_from_pixbuf, to_pixbuf(img))
                GLib.idle_add(self.img_note.set_text, "第 %d / %d 层｜叠加图由 %s 合成" % (
                    z + 1, ct["nz"], "PL（vis_layer）" if eng == "vis" else "PS（板子没有 vis_layer）"))
            except Exception as e:
                GLib.idle_add(self.img_note.set_text, "叠加失败：%s" % e)
        threading.Thread(target=work, daemon=True).start()

    def work_echo(self, c):
        try:
            sh = c["shape"]
            frames = np.fromfile(os.path.join(CASES, c["frames"]), np.uint8).reshape(sh)
            t0 = time.time()
            with self.lock:
                self.link.connect()
                # params precomputed on the PC (make_board_cases.py: jobs.echo_params needs echo_ws / scipy)
                r = jobs.run(self.link, jobs.JOB_ECHO_EF, jobs.ECHO_MASKS, bytes.fromhex(c["params_hex"]), frames.tobytes(),
                             on_progress=lambda p: self.say("板上：%s %s/%s" % (p.get("stage"), p.get("done", ""),
                                                                               p.get("total", ""))))
            res = r["result"]
            idx = list(range(0, res["n_frames"], res["step"]))
            masks = unpack_le(r["blobs"]["masks"], (len(idx), 112, 112)) if "masks" in r["blobs"] else None
            self.echo = dict(res=res, frames=frames, idx=idx, masks=masks, wall=time.time() - t0, cache={})
            GLib.idle_add(self.show_echo)
        except Exception as e:
            GLib.idle_add(self.text.set_text, "心超失败：%s" % e)
        GLib.idle_add(self.btn.set_sensitive, True)

    def show_echo(self):
        e, res = self.echo, self.echo["res"]
        cl = res.get("clinical") or {}
        lines = ["心超（EchoNet，逐帧 PL 分割 + PL 清理，板上逐拍 EF）"]
        if res.get("ef") is not None:
            lines.append("EF %.1f%%（各拍中位数，已校准）" % res["ef"])
        if cl.get("band") is not None:
            lines.append("LVEF 区间：%s%s" % (LVEF_BANDS[cl["band"]], "｜离分界不到 %.1f 个百分点，建议复核" % cl["margin"] if cl.get("review") else ""))
        if cl.get("n_beats") is not None:
            lines.append("拍数 %d%s" % (cl["n_beats"], "（不足 %d 拍，建议多拍平均）" % cl.get("min_beats_avg", 5) if cl.get("few_beats") else ""))
        if cl.get("rr_cv") is not None:
            lines.append("RR 变异系数 %.3f%s（RR 由容积曲线推出，没有心电图）" % (cl["rr_cv"], "，节律不齐提示" if cl.get("irregular") else ""))
        lines.append("耗时：板上 %.2f s（PL 推理 %.2f s），界面合计 %.2f s" % (res["ms"]["total"] / 1000, res["pl_ms"] / 1000, e["wall"]))
        lines.append(NOTE)
        self.text.set_text("\n".join(lines))
        self.scale.set_range(0, len(e["idx"]) - 1)

    def play(self):
        e = self.echo
        if e is not None and e["masks"] is not None and not self.lock.locked():
            k = self.play_i % len(e["idx"])
            self.play_i += 1
            img = e["cache"].get(k)
            if img is None:
                t = e["idx"][k]
                base = np.repeat(np.repeat(e["frames"][t], 4, 0), 4, 1)
                try:
                    with self.lock:
                        self.link.connect()
                        img, eng = overlay(self.link, 448, 448, jobs.OVL_BASE_GREY | jobs.OVL_HAS_BITS | jobs.OVL_FILL_FROM_BITS |
                                           jobs.OVL_FILL | jobs.OVL_CONTOUR, base=base, bits=e["masks"][k],
                                           fill=(255, 64, 64), fill_a=5, contour=(0, 255, 0))
                    e["cache"][k] = img
                    self.img_note.set_text("第 %d 帧｜叠加图由 %s 合成" % (t + 1, "PL（vis_layer）" if eng == "vis" else "PS"))
                except Exception as ex:
                    self.img_note.set_text("叠加失败：%s" % ex)
                    return True
            self.img.set_from_pixbuf(to_pixbuf(img))
        return True

    def spin3d(self):
        if self.views:
            self.view_i = (self.view_i + 1) % len(self.views)
            self.img3d.set_from_pixbuf(to_pixbuf(self.views[self.view_i]))
        return True


def main():
    Station()
    Gtk.main()


if __name__ == "__main__":
    main()
