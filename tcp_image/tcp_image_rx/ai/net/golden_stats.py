# -*- coding: utf-8 -*-
"""检查 golden 张量是否"有内容"(不是一堆常数/零), 即这个测试是否真的有力。"""
import os
import struct
import sys

G = r"E:/fpga/my_project/tcp_image_rx/ai/net/vectors_small/golden"
NAMES = ["x", "first_block_2", "add", "down_path_0_pool", "add_1", "down_path_1_pool",
         "add_2", "cat", "add_3", "cat_1", "add_4", "final"]

for name in NAMES:
    p = os.path.join(G, name + ".bin")
    raw = open(p, "rb").read()
    v = struct.unpack("<%dh" % (len(raw) // 2), raw)
    n = len(v)
    nz = sum(1 for a in v if a != 0)
    # 只统计"有效通道"(每 8 个 lane 里的前 k 个), 这里直接看全部 lane
    lo, hi = min(v), max(v)
    uniq = len(set(v))
    print("%-38s n=%6d  zeros=%5.1f%%  min=%6d max=%6d  distinct=%5d" %
          (name, n, 100.0 * (n - nz) / n, lo, hi, uniq))
