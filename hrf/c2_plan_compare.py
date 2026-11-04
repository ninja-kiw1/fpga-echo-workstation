# -*- coding: utf-8 -*-
"""c2_plan_compare.py —— 板端分块规划（src/tile_plan.c）与上位机 tile_engine.plan_axis 逐项对照（部分 C）。
WSL gcc 编 test/tile/tile_plan_dump.c + src/tile_plan.c，喂随机 / 边界的 (a, n, full)，两边输出必须完全相同；
另外按规划自身的不变式检查：块 <= 512、起点 4 对齐、尺寸 8 的倍数、在图内、自有区首尾相接铺满框、
每个自有像素离块的非图像边 >= 24。用法：python c2_plan_compare.py → results/c2_plan_compare.json"""
import json, os, random, subprocess, sys
sys.path.insert(0, r"E:\PLD\tcp_image\tcp_image_ui")
import tile_engine as te
RX = r"E:\PLD\tcp_image\tcp_image_rx"
def wsl(p): return "/mnt/" + p[0].lower() + p[2:].replace("\\", "/")
def main():
    exe = "/tmp/tile_plan_dump"
    cmd = "gcc -std=c99 -O2 -Wall -Wextra -I%s %s/test/tile/tile_plan_dump.c %s/src/tile_plan.c -o %s" % (wsl(RX + r"\src"), wsl(RX), wsl(RX), exe)
    r = subprocess.run(["wsl", "bash", "-lc", cmd], capture_output=True, text=True)
    if r.returncode or r.stderr.strip():
        print(r.stdout, r.stderr); return 1
    rnd = random.Random(20261018)
    cases = []
    for full in (8, 16, 504, 512, 520, 976, 1024, 1040, 1392, 2080, 2336, 3504, 4096):
        for _ in range(400):
            n = rnd.randrange(1, full + 1); a = rnd.randrange(0, full - n + 1); cases.append((a, n, full))
        cases += [(0, full, full), (full - 1, 1, full), (0, 1, full)]
    inp = "\n".join("%d %d %d" % c for c in cases) + "\n"
    r = subprocess.run(["wsl", exe], input=inp, capture_output=True, text=True)
    lines = r.stdout.split("\n")
    bad = viol = 0
    for c, line in zip(cases, lines):
        py = te.plan_axis(*c)
        f = [int(v) for v in line.split()]
        cc = [tuple(f[1 + 4 * i:5 + 4 * i]) for i in range(f[0])]
        bad += cc != py
        a, n, full = c
        pos = a
        for (t, size, o0, o1) in py:
            ok = (size <= 512 and t % 4 == 0 and size % 8 == 0 and 0 <= t and t + size <= full and o0 == pos and o1 > o0
                  and (o0 - t >= 24 or t == 0) and (t + size - o1 >= 24 or t + size == full))
            viol += not ok; pos = o1
        viol += pos != a + n
    rep = dict(cases=len(cases), c_vs_python_mismatch=bad, invariant_violations=viol,
               ok=bad == 0 and viol == 0 and len(lines) >= len(cases))
    os.makedirs(os.path.join(os.path.dirname(__file__), "results"), exist_ok=True)
    json.dump(rep, open(os.path.join(os.path.dirname(__file__), "results", "c2_plan_compare.json"), "w"), indent=1)
    print(rep); print("C2_PLAN_PASS" if rep["ok"] else "C2_PLAN_FAIL")
    return 0 if rep["ok"] else 1
if __name__ == "__main__":
    sys.exit(main())
