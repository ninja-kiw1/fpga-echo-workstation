# -*- coding: utf-8 -*-
"""roi_exactness.py —— 证明“ROI 局部推理”的中心区与整图推理逐值一致，并量出所需边距。

做法：用 HLS 两个算子的 NumPy 逐位参考（medical_fpga/deployment 的 conv / eltwise 参考，
PYNQ 模拟器同款，20 张图已与 lwnet 整数模型逐值一致），按部署层表在**裁剪尺寸**上跑整网，
与整图 512x512 的输出（medical_fpga 整网 C 仿真结果 final_actual.bin，与实板逐字节一致）比对。

1) 理论边距：在层表上做行依赖传播（3x3 卷积扩 1 行、池化 2 行并 1 行、转置卷积 1 行出 2 行），
   求出对齐裁剪时中心区不受裁剪边界影响所需的边距 —— 结果：每边 22 像素。
2) 实测：多张图、多种位置 / 尺寸（含贴图像边、非正方形），裁剪起点对齐到 4、
   每边留 ROI_MARGIN（板端用的值）时，ROI 内全部像素与整图逐值一致；
   并给出“实际出现差异的最大深度”，以及起点不对齐时会出错的反例。

用法（需要 numpy）：python roi_exactness.py [--quick]
"""

import os
import random
import shutil
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MED = os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "medical_fpga"))
PYNQ = os.path.join(MED, "embedded", "pynq")
PKG = os.path.join(PYNQ, "board_package")
IMAGES = os.path.join(MED, "deployment", "unet_network", "vectors", "images")
sys.path.insert(0, PYNQ)

from sim_device import SimDevice          # noqa: E402
from unet_fpga import UNetAccelerator, load_network   # noqa: E402

FULL = 512
ALIGN = 4          # 两级 2x2 池化：裁剪起点必须是 4 的倍数
SIZE_ALIGN = 8     # 裁剪尺寸取 8 的倍数（最深一层宽高仍为偶数）
ROI_MARGIN = 24    # 板端用的边距：理论需要 22，向上取到 8 的倍数

NODES = load_network(os.path.join(PKG, "network.txt"))


# --------------------------------------------------------------------------
# 1) 理论：行依赖传播
# --------------------------------------------------------------------------
def dirty_rows(y0, size):
    """裁剪行 [y0, y0+size) 时，整图坐标下哪些输出行可能与整图结果不同。"""
    dirty = {}
    for n in NODES:
        if n.kind == "INPUT":
            d = [not (y0 <= y < y0 + size) for y in range(FULL)]
        else:
            a = dirty[n.in0]
            la = len(a)
            if n.kind == "CONV" and n.mode == 0:
                d = [a[y] or (y > 0 and a[y - 1]) or (y < la - 1 and a[y + 1]) for y in range(la)]
            elif n.kind == "CONV" and n.mode == 1:
                d = list(a)
            elif n.kind == "CONV" and n.mode == 2:
                d = [a[y >> 1] for y in range(2 * la)]
            elif n.kind == "ELT" and n.mode == 2:
                d = [a[2 * y] or a[2 * y + 1] for y in range(la // 2)]
            else:
                b = dirty[n.in1]
                d = [a[y] or b[y] for y in range(la)]
        f = FULL // len(d)
        lo, hi = y0 // f, (y0 + size) // f
        dirty[n.name] = [d[y] or not (lo <= y < hi) for y in range(len(d))]
    return dirty[NODES[-1].name]


def theoretical_margin():
    worst = 0
    for y0 in range(64, 256, ALIGN):
        for size in (96, 128, 136, 200, 256):
            clean = [y for y, v in enumerate(dirty_rows(y0, size)) if not v]
            worst = max(worst, clean[0] - y0, (y0 + size) - (clean[-1] + 1))
    return worst


# --------------------------------------------------------------------------
# 2) 实测：NumPy 逐位参考跑裁剪
# --------------------------------------------------------------------------
class CropRunner(object):
    """按裁剪尺寸缩放层表（每个节点的 h、w 按 crop/512 等比缩放），用模拟器跑整网。"""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="roi_exact_")
        shutil.copytree(os.path.join(PKG, "params"), os.path.join(self.tmp, "params"))
        self.cache = {}

    def run(self, rgb):
        h, w = rgb.shape[:2]
        if (h, w) not in self.cache:
            lines = []
            for line in open(os.path.join(PKG, "network.txt")):
                f = line.split()
                if not f:
                    continue
                f[7] = str(int(f[7]) * h // FULL)
                f[8] = str(int(f[8]) * w // FULL)
                lines.append(" ".join(f))
            with open(os.path.join(self.tmp, "network.txt"), "w") as fh:
                fh.write("\n".join(lines) + "\n")
            self.cache[(h, w)] = UNetAccelerator(SimDevice(), self.tmp)
        return self.cache[(h, w)].run(np.ascontiguousarray(rgb))

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def load_image(name):
    raw = np.fromfile(os.path.join(IMAGES, name, "input.bin"), dtype="<i2").reshape(FULL, FULL, 8)
    rgb = raw[:, :, :3].astype(np.uint8)
    full = np.fromfile(os.path.join(IMAGES, name, "final_actual.bin"), dtype=np.int8).reshape(FULL, FULL)
    return rgb, full


def _crop_axis(r, n, margin):
    lo = max(0, r - margin)
    hi = min(FULL, r + n + margin)
    s = lo // ALIGN * ALIGN
    size = -(-(hi - s) // SIZE_ALIGN) * SIZE_ALIGN
    if s + size > FULL:
        s = FULL - size          # 512 是 8 的倍数，回挪后起点仍对齐到 4
    return s, size


def crop_for_roi(x, y, w, h, margin=ROI_MARGIN):
    """ROI -> 裁剪窗口：外扩 margin，起点向下对齐到 4，尺寸向上取到 8 的倍数，越界就把起点回挪。
    与板端 ai_view_roi_crop()、上位机 proto.roi_crop() 是同一算法
    （三份由 tcp_image_ui/_verify_roi.py 对照）。"""
    x0, cw = _crop_axis(x, w, margin)
    y0, ch = _crop_axis(y, h, margin)
    return x0, y0, cw, ch


def max_bad_depth(diff, y0, x0, ch, cw):
    """差异像素离裁剪窗口“非图像边界”那几条边最远有多深（0 = 没有差异）。"""
    ys, xs = np.nonzero(diff)
    if ys.size == 0:
        return 0
    depth = np.full(ys.shape, 10 ** 6)
    if y0 > 0:
        depth = np.minimum(depth, ys + 1)
    if y0 + ch < FULL:
        depth = np.minimum(depth, ch - ys)
    if x0 > 0:
        depth = np.minimum(depth, xs + 1)
    if x0 + cw < FULL:
        depth = np.minimum(depth, cw - xs)
    return int(depth.max())


def main():
    quick = "--quick" in sys.argv
    fails = []
    m = theoretical_margin()
    print("1) 理论边距（起点对齐到 %d）：每边 %d 像素；板端取 %d" % (ALIGN, m, ROI_MARGIN))
    if m > ROI_MARGIN:
        fails.append("ROI_MARGIN 小于理论边距")

    names = ["im0002"] if quick else ["im0002", "im0324", "im0077"]
    rnd = random.Random(20261007)
    rois = [(200, 180, 128, 128), (0, 0, 96, 64), (400, 420, 112, 92), (20, 300, 200, 40),
            (300, 0, 64, 160), (150, 150, 216, 216)]
    if not quick:
        for _ in range(6):
            w, h = rnd.randrange(24, 200), rnd.randrange(24, 200)
            rois.append((rnd.randrange(0, FULL - w), rnd.randrange(0, FULL - h), w, h))

    runner = CropRunner()
    worst_depth = 0
    try:
        print("\n2) 实测：ROI 内与整图逐值比对（NumPy 逐位参考）")
        for name in names:
            rgb, full = load_image(name)
            for (x, y, w, h) in rois:
                x0, y0, cw, ch = crop_for_roi(x, y, w, h)
                t0 = time.perf_counter()
                out = runner.run(rgb[y0:y0 + ch, x0:x0 + cw])
                dt = time.perf_counter() - t0
                diff = out != full[y0:y0 + ch, x0:x0 + cw]
                depth = max_bad_depth(diff, y0, x0, ch, cw)
                worst_depth = max(worst_depth, depth)
                roi_diff = int(diff[y - y0:y - y0 + h, x - x0:x - x0 + w].sum())
                ok = roi_diff == 0
                if not ok:
                    fails.append("%s roi=%s" % (name, (x, y, w, h)))
                print("  [%s] %s ROI(%3d,%3d,%3dx%3d) -> 裁剪(%3d,%3d,%3dx%3d) 占整图 %4.1f%%  "
                      "ROI 内差异 %d  边缘差异最深 %2d px  (%.1f s)"
                      % ("PASS" if ok else "FAIL", name, x, y, w, h, x0, y0, cw, ch,
                         100.0 * cw * ch / FULL / FULL, roi_diff, depth, dt))
        print("  所有用例中差异离裁剪边最深 %d px（理论上界 %d，板端边距 %d）"
              % (worst_depth, m, ROI_MARGIN))
        if worst_depth > m:
            fails.append("实测差异深度超过理论边距")

        print("\n3) 反例：起点不对齐到 4 时，中心区也会错")
        rgb, full = load_image(names[0])
        x0, y0, cw, ch = 202, 182, 176, 176
        out = runner.run(rgb[y0:y0 + ch, x0:x0 + cw])
        c = out[40:-40, 40:-40] != full[y0 + 40:y0 + ch - 40, x0 + 40:x0 + cw - 40]
        print("  起点 (202,182)：中心 %dx%d 内差异 %d 个像素（预期 > 0）"
              % (c.shape[1], c.shape[0], int(c.sum())))
        if c.sum() == 0:
            fails.append("不对齐反例没有出现差异，检查对齐假设")
    finally:
        runner.close()

    print("\n" + "=" * 62)
    if fails:
        print("ROI_EXACTNESS FAILED: %s" % fails)
        return 1
    print("ROI_EXACTNESS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
