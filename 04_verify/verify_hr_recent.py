# -*- coding: utf-8 -*-
"""verify_hr_recent.py —— 心率显示口径核对（11-19）：最近 N 拍瞬时心率平均 vs 板上 60 / 中位 RR。

为什么改：板端 job_echo.c / echo_ws.analyze 给的心率是「60 / 中位 RR」。现场滚动每 0.25 s 用
**迄今为止的全部容积**重算一次，拍数少时中位数会在相邻两个 RR 之间换边，界面上就是心率在两个
数字之间跳（实测 ES000108：RR 15/18 帧交替 -> 120 与 109 来回跳）。上位机改成显示最近 5 拍的
瞬时心率算术平均（echo_ws.hr_recent），现场滚动与最终结果用同一个口径。

跑法（Windows，装了 numpy/scipy 的解释器）：
    python verify_hr_recent.py                 # 默认量 07_ui 里的那份
    python verify_hr_recent.py <上位机目录>

用例（B 用的就是真机上量到的 RR 交替模式）：
  A 恒定 75 次/分 -> 两种口径都该给 75（新口径不能把对的算错）；
  B RR 15 / 18 帧交替（30 fps）-> 旧口径在 120 / 109 之间跳，新口径不许再出现这么大的两值跳动；
  C 中间漏检一拍 -> 量出新口径被拽偏多少（已知代价，写出来）；
  D 不足两拍 -> 回 None（界面退回板上值），不能抛异常；
  E 直接走界面里的 EchoView._hr_recent（现场分支：缓存 + 合理性门限），并核对
    workstation.HR_RECENT 与 echo_ws.HR_RECENT 是同一个数。
仅用于工程教学与算法演示，不作为临床诊断依据。
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_UI = os.path.join(os.path.dirname(HERE), "07_ui")
UI = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_UI
sys.path.insert(0, UI)
import echo_ws                                                    # noqa: E402

try:                                                              # 界面那一份（用例 B 要用它的窗口常数、用例 E 要用方法）
    import workstation as W
except Exception as _e:                                           # 没有 tkinter 的环境：用例 E 跳过
    W, _W_ERR = None, _e


def trace(rr_s, fps, tail_s=3.0):
    """按给定的 RR 序列造一条容积曲线（一个心动周期 = 谷 -> 峰 -> 谷），另回谷的帧号真值。"""
    rrs = np.asarray(rr_s, np.float64)
    edges = np.concatenate([[0.0], np.cumsum(rrs)])
    n = int(round(edges[-1] * fps)) + int(round(tail_s * fps))
    t = np.arange(n) / float(fps)
    k = np.clip(np.searchsorted(edges, t, side="right") - 1, 0, len(rrs) - 1)
    u = np.clip((t - edges[k]) / rrs[k], 0, 0.999)
    v = 0.75 - 0.25 * np.cos(2 * np.pi * u)
    return v.tolist(), np.round(edges[:-1] * fps).astype(int).tolist()


def beats_of(vols, fps, cfg, span_s=None):
    a = 0 if span_s is None else max(0, len(vols) - int(round(span_s * fps)))
    return echo_ws.analyze(vols[a:], fps, 1, cfg)["beats"]


def report(tag, seq, warm=0):
    """seq: [(时刻, 旧口径, 新口径, 已有拍数)] -> 跳变次数、取值范围、稳态跨度。

    warm：跳过前 warm 个采样（新口径要攒够几拍才进入稳态，起步那几秒两边都还没数）。"""
    def stat(k):
        a = [round(x[k]) for x in seq[warm:] if x[k] is not None]
        flip = sum(1 for p, q in zip(a, a[1:]) if p != q)
        vals = sorted(set(a))
        return flip, vals, (vals[-1] - vals[0]) if vals else 0
    fo, uo, so = stat(1)
    fn, un, sn = stat(2)
    print("  %-4s 旧（60/中位RR）：%2d 次跳变，取值 %s，跨度 %.0f" % ("", fo, uo, so))
    print("  %-4s 新（最近 %d 拍平均）：%2d 次跳变，取值 %s，跨度 %.0f"
          % ("", echo_ws.HR_RECENT, fn, un, sn))
    return so, sn


def main():
    cfg = echo_ws.load_config()["cfg"]
    ok = True

    print("用例 A：恒定 75 次/分（RR 0.8 s，50 fps）")
    vols, tr = trace([0.8] * 8, 50.0)
    old = echo_ws.analyze(vols, 50.0, 1, cfg)["hr"]
    new = echo_ws.hr_recent(beats_of(vols, 50.0, cfg), 50.0)
    print("  真值 %d 个谷；旧 %.1f，新 %.1f" % (len(tr), old, new))
    if abs(new - 75.0) > 1.0 or abs(old - 75.0) > 1.0:
        print("  [FAIL] 恒定心率算错了")
        ok = False

    print("用例 B：真实病例 ES000108（golden/hr_es000108.json，板上实跑的那条容积曲线）")
    fx = json.load(open(os.path.join(HERE, "golden", "hr_es000108.json"), encoding="utf-8"))
    fps = fx["fps"]
    beats = [dict(dict(es=b["es"], ed=b["ed"])) for b in fx["beats"]]
    new = echo_ws.hr_recent(beats, fps)
    print("  板上：心率 %.0f 次/分（60/中位RR），%d 拍；最近 %d 拍平均：%.1f"
          % (fx["hr_board"], len(beats), echo_ws.HR_RECENT, new))
    seq, t = [], 0.0
    warm = None
    span = getattr(W, "HR_LIVE_SPAN", 10.0) if W is not None else 10.0    # 现场只看最近这几秒
    while t < fx["n_frames"] / fps:
        n = int(round(t * fps))
        if n >= 3:
            o = echo_ws.analyze(fx["vols"][:n], fps, 1, cfg)["hr"]
            b = beats_of(fx["vols"][:n], fps, cfg, span_s=span)
            v = echo_ws.hr_recent(b, fps)
            seq.append((t, o, v if v is not None else o, len(b)))
            if warm is None and len(b) >= echo_ws.HR_RECENT + 1:        # 攒够 5 个间期才开始比
                warm = len(seq) - 1
        t += 0.25
    so, sn = report("结果", seq, warm or 0)
    print("  稳态从第 %d 个采样（%.1f s，已攒够 %d 个间期）开始，之后 %d 个采样"
          % ((warm or 0), (seq[warm or 0][0] if warm else 0.0), echo_ws.HR_RECENT,
             len(seq) - (warm or 0)))
    if so < 8:
        print("  [FAIL] 没能复现旧口径的两值跳动（用例本身失效）")
        ok = False
    if sn >= 0.6 * so:
        print("  [FAIL] 新口径没有把两值跳动压下去（跨度 %.0f vs %.0f）" % (sn, so))
        ok = False
    if abs(new - 112.0) > 3.0:
        print("  [FAIL] 最近 5 拍平均与手算的 112 不一致")
        ok = False

    print("用例 C：中间漏检一拍（RR 0.8 -> 1.6 s），量代价")
    vols, tr = trace([0.8, 0.8, 1.6, 0.8, 0.8, 0.8], 50.0)
    old = echo_ws.analyze(vols, 50.0, 1, cfg)["hr"]
    new = echo_ws.hr_recent(beats_of(vols, 50.0, cfg), 50.0)
    print("  旧 %.1f（中位 RR 不受单个长间期影响），新 %.1f（5 拍里有 1 个长间期，低 %.0f%%）"
          % (old, new, 100 * (1 - new / old)))
    print("  说明：用户选的是「最近 5 拍算术平均」，这是它的已知代价；要更抗漏检得先剔除离群间期。")

    print("用例 D：不足两拍")
    if echo_ws.hr_recent([dict(es=10)], 50.0) is not None or echo_ws.hr_recent([], 50.0) is not None:
        print("  [FAIL] 不足两拍应当回 None")
        ok = False
    else:
        print("  回 None，界面退回板上值：符合预期")

    print("用例 E：界面里的 EchoView._hr_recent（现场分支：缓存 + 合理性门限）")
    if W is None:
        print("  跳过（import workstation 失败：%s）" % _W_ERR)
    else:
        if W.HR_RECENT != echo_ws.HR_RECENT:
            print("  [FAIL] workstation.HR_RECENT=%r 与 echo_ws.HR_RECENT=%r 不一致"
                  % (W.HR_RECENT, echo_ws.HR_RECENT))
            ok = False

        class Var(object):
            def __init__(self):
                self.v = None

            def set(self, s):
                self.v = s

        class Stub(object):
            pass

        s = Stub()
        s.ws, s.fps, s.conf = echo_ws, fx["fps"], dict(cfg=cfg)
        s.vols, s._hr_key, s._hr_val = list(fx["vols"]), None, None
        s.var_hr = Var()
        hr1 = W.EchoView._hr_recent(s)
        hr2 = W.EchoView._hr_recent(s)                            # 第二次命中缓存
        W.EchoView._show_hr(s, hr1, None)
        print("  现场分支：%.1f（缓存复用 %.1f，界面显示 %r）" % (hr1, hr2, s.var_hr.v))
        if hr1 is None or hr1 != hr2 or not s.var_hr.v or not s.var_hr.v.endswith("次/分"):
            print("  [FAIL] 现场分支不对")
            ok = False
        s.vols = [0.5, 0.6]                                       # 帧数太少：不能抛异常、要回 None
        if W.EchoView._hr_recent(s) is not None:
            print("  [FAIL] 曲线太短应当回 None")
            ok = False
        W.EchoView._show_hr(s, None, 88.0)                        # 算不出来：退回板上值
        if s.var_hr.v != "88 次/分":
            print("  [FAIL] 退回板上值的分支不对：%r" % s.var_hr.v)
            ok = False

    print("\n%s" % ("全部通过" if ok else "有不通过项，见上面 [FAIL]"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
