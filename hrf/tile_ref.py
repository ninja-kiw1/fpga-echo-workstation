# -*- coding: utf-8 -*-
"""tile_ref.py —— 眼底血管模型 0 的整数逐位参考，任意尺寸（部分 C）。

用 medical_fpga 的 HLS 两个算子的 NumPy 逐位参考（PYNQ 模拟器同款：sim_device.run_conv / run_eltwise，
20 张图与 lwnet 整数模型逐值一致，512 整图与实板逐字节一致），按部署层表在任意 (h, w)（8 的倍数）上跑整网。
与 sim_device 唯一的不同：去掉了 HLS 核"一行最多 512 / 转置最多 256"的尺寸检查——那是硬件行缓冲的限制，
不是算法的一部分；去掉后才能在 > 512 的整幅图上算"整图推理"的参照。其余检查和计算原样调用 sim_device 的函数。

用法：from tile_ref import Ref; ref = Ref(); logits = ref.run(rgb)   # rgb (h, w, 3) uint8 -> (h, w) int8
"""
import os
import shutil
import sys
import tempfile

import numpy as np

MED = r"E:\PLD\medical_fpga"
PYNQ = os.path.join(MED, "embedded", "pynq")
PKG = os.path.join(PYNQ, "board_package")
sys.path.insert(0, PYNQ)

import sim_device                                  # noqa: E402
from sim_device import SimDevice, conv_ref, elt_ref, _reg, _unpack, _store, LANES   # noqa: E402
from unet_fpga import UNetAccelerator              # noqa: E402

FULL = 512


def run_conv_any(ip):
    """sim_device.run_conv 去掉 in_h / in_w 上限（其余逐行相同）。"""
    mode, cin, cout = _reg(ip, 0x3C), _reg(ip, 0x44), _reg(ip, 0x4C)
    in_h, in_w = _reg(ip, 0x54), _reg(ip, 0x5C)
    relu, shift, qmin, qmax = _reg(ip, 0x64), _reg(ip, 0x6C), _reg(ip, 0x74), _reg(ip, 0x7C)
    if not (0 <= mode <= 2 and 1 <= cin <= 32 and 1 <= cout <= 32 and 1 <= in_h and
            1 <= in_w and relu in (0, 1) and 0 <= shift <= 31 and -32768 <= qmin <= qmax <= 32767):
        return 1
    taps = conv_ref.TAPS[mode]
    cig, cog = conv_ref.groups(cin), conv_ref.groups(cout)
    words = np.asarray(ip.buffer(0x30), dtype=np.uint64)
    nw = taps * cog * cig * LANES
    w = words[:nw].view(np.int8).reshape(taps, cog, cig, LANES, LANES)
    w_taps = w.transpose(0, 1, 3, 2, 4).reshape(taps, cog * LANES, cig * LANES)[:, :cout, :cin]
    per_ch = words[nw:nw + 3 * cog * LANES].view(np.int64).reshape(3, cog * LANES)[:, :cout]
    x = _unpack(ip.buffer(0x18), cin)
    y = conv_ref.layer_reference(x, w_taps, per_ch[0], per_ch[1], per_ch[2], mode, bool(relu), shift, qmin, qmax)
    _store(ip.buffer(0x24), y)
    return 0


def run_eltwise_any(ip):
    """sim_device.run_eltwise 去掉 h / w 上限（其余逐行相同）。"""
    mode, ca, cb, h, w = (_reg(ip, o) for o in (0x3C, 0x44, 0x4C, 0x54, 0x5C))
    m0, m1, shift, qmin, qmax = (_reg(ip, o) for o in (0x64, 0x6C, 0x74, 0x7C, 0x84))
    if not (0 <= mode <= 2 and 1 <= ca <= 32 and 1 <= h and 1 <= w):
        return 1
    if mode == 2:
        if h % 2 or w % 2:
            return 1
    elif not (0 <= shift <= 31 and -32768 <= qmin <= qmax <= 32767) or \
            (mode == 0 and cb != ca) or (mode == 1 and (ca % 8 or cb < 1 or ca + cb > 32)):
        return 1
    a = _unpack(ip.buffer(0x18), ca)
    b = _unpack(ip.buffer(0x24), cb) if mode != elt_ref.MODE_MAXPOOL else None
    y = elt_ref.eltwise_reference(mode, a, b, m0, m1, shift, qmin, qmax)
    _store(ip.buffer(0x30), y)
    return 0


class Ref(object):
    """按输入尺寸缩放层表（每个节点 h、w 按 size/512 等比，= 板端 unet_set_size），缓存每种尺寸的加速器。
    limit=True 时用 sim_device 原样的检查（= HLS 核的限制；块 <= 512 时两者结果相同）。"""

    def __init__(self, limit=False, max_cache=6):
        self.tmp = tempfile.mkdtemp(prefix="tile_ref_")
        shutil.copytree(os.path.join(PKG, "params"), os.path.join(self.tmp, "params"))
        self.limit = limit
        self.cache = {}
        self.max_cache = max_cache

    def _acc(self, h, w):
        if (h, w) in self.cache:
            return self.cache[(h, w)]
        if len(self.cache) >= self.max_cache:
            k, acc = next(iter(self.cache.items()))
            acc.close()
            del self.cache[k]
        d = os.path.join(self.tmp, "n%dx%d" % (h, w))
        os.makedirs(d, exist_ok=True)
        if not os.path.exists(os.path.join(d, "params")):
            shutil.copytree(os.path.join(self.tmp, "params"), os.path.join(d, "params"))
        lines = []
        for line in open(os.path.join(PKG, "network.txt")):
            f = line.split()
            if not f:
                continue
            f[7] = str(int(f[7]) * h // FULL)
            f[8] = str(int(f[8]) * w // FULL)
            lines.append(" ".join(f))
        with open(os.path.join(d, "network.txt"), "w") as fh:
            fh.write("\n".join(lines) + "\n")
        dev = SimDevice()
        if not self.limit:
            dev.ips["conv_layer_0"].compute = run_conv_any
            dev.ips["eltwise_layer_0"].compute = run_eltwise_any
        acc = UNetAccelerator(dev, d, timeout_s=3600.0)
        self.cache[(h, w)] = acc
        return acc

    def run(self, rgb):
        rgb = np.ascontiguousarray(rgb, np.uint8)
        h, w = rgb.shape[:2]
        if h % 8 or w % 8:
            raise ValueError("size must be a multiple of 8, got %dx%d" % (w, h))
        return self._acc(h, w).run(rgb)

    def close(self):
        for acc in self.cache.values():
            acc.close()
        self.cache = {}
        shutil.rmtree(self.tmp, ignore_errors=True)
