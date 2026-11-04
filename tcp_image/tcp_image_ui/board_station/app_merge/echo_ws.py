# -*- coding: utf-8 -*-
"""echo_ws.py —— ③「实时心超工作站」的上位机计算部分（numpy / scipy / OpenCV / Pillow）。

与离线评测（E:/fpga/my_project/pld2026final/03_算法与数据/echonet）一致的部分（verify_board_echonet.py 逐项核对）：
  * 读 AVI：灰度 = (B + G + R + 1) // 3；
  * t3 编码：R / G / B = 第 t-d / t / t+d 帧（d = round(0.04 s × 帧率)，至少 1，到头夹住），四周补 8 像素 0 -> 128x128；
  * 板端 int8 logit 裁回中间 112x112，>= 1 为左心室；取最大连通块、填洞；
  * 单平面圆盘法容积（主轴方向 20 个圆盘，像素单位）；逐拍分析与校准参数来自 echonet_config.json（只用训练集定的）；
  * 实时模式每 k 帧分析 1 帧，k = ceil(帧率 / 25)。
另外：质控（逐帧 / 逐段）、证据报告（HTML，图片内嵌）。
仅用于工程教学与算法演示，不作为临床诊断依据；EchoNet-Dynamic 数据按斯坦福研究使用协议不得外传。
"""
import base64
import io
import json
import math
import os
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "echonet_config.json")
PAD, N = 8, 112


# ---------------------------------------------------------------- 视频与负载
def read_avi(path):
    """112x112（EchoNet）照旧取三通道平均；其它尺寸（超声机录屏，如 HMC-QU）先按 screen_to_112 取扇区缩到 112。"""
    import cv2
    cap = cv2.VideoCapture(path)
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 50.0
    out, raw = [], []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if fr.shape[:2] == (N, N):
            f = fr.astype(np.uint16)
            out.append(((f[..., 0] + f[..., 1] + f[..., 2] + 1) // 3).astype(np.uint8))
        else:
            raw.append(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY))
    cap.release()
    if not out and not raw:
        raise ValueError("读不出视频帧")
    if raw:
        v, _ = screen_to_112(np.stack(raw))
        return v, fps
    return np.stack(out), fps


def screen_to_112(grey):
    """超声机录屏 [T,H,W] 灰度 -> [T,112,112]（与 E:/fpga/my_project/pld2026final/03_算法与数据/wallmotion/prep_hmcqu.py 相同）：扇区 = 时间最大值 > 20、7x7 开运算
    （去掉细的心电图线和文字）、最大连通块；正方形边长 = max(宽, 高)，水平居中、顶边对齐扇区顶（心尖朝上，同 EchoNet），
    外面补 0；INTER_AREA 缩到 112。回 (帧, 方块 dict)。"""
    import cv2
    m = (grey.max(0) > 20).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n < 2:
        raise ValueError("找不到超声扇区")
    k = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = (int(v) for v in st[k, :4])
    side = max(w, h)
    x0, y0 = x + w // 2 - side // 2, y
    T, H, W = grey.shape
    canvas = np.zeros((T, side, side), np.uint8)
    sx0, sy0, sx1, sy1 = max(0, x0), max(0, y0), min(W, x0 + side), min(H, y0 + side)
    canvas[:, sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = grey[:, sy0:sy1, sx0:sx1]
    return np.stack([cv2.resize(f, (N, N), interpolation=cv2.INTER_AREA) for f in canvas]), dict(x=x0, y=y0, side=side)


def temporal_offset(fps):
    return max(1, int(round(0.04 * fps)))


def realtime_step(fps, board_fps=25.0):
    return max(1, int(math.ceil(fps / board_fps - 1e-9)))


def payloads(frames, fps, mode="t3", step=1):
    """分析的帧号列表 + 每帧一个 128x128x3 负载（bytes）。"""
    n = len(frames)
    d = temporal_offset(fps)
    idx = list(range(0, n, step))
    out = []
    for t in idx:
        if mode == "t3":
            rgb = np.stack([frames[max(t - d, 0)], frames[t], frames[min(t + d, n - 1)]], -1)
        else:
            rgb = np.repeat(frames[t][..., None], 3, -1)
        out.append(np.ascontiguousarray(np.pad(rgb, ((PAD, PAD), (PAD, PAD), (0, 0)))).tobytes())
    return idx, out


def mask_from_logit(logit128):
    q = np.frombuffer(logit128, np.int8).reshape(128, 128)[PAD:PAD + N, PAD:PAD + N]
    return q, q >= 1


# ---------------------------------------------------------------- 容积（与 echonet/volume.py 相同）
def clean(mask):
    from scipy import ndimage
    lab, n = ndimage.label(mask)
    if n > 1:
        sizes = ndimage.sum(mask, lab, range(1, n + 1))
        mask = lab == (int(np.argmax(sizes)) + 1)
    return ndimage.binary_fill_holes(mask), n


def volume_pca(mask, ndisk=20):
    r, c = np.nonzero(mask)
    if len(r) < 3:
        return 0.0
    p = np.stack([r, c], 1).astype(np.float64)
    q = p - p.mean(0)
    w, v = np.linalg.eigh(q.T @ q)
    s = q @ v[:, 1]
    lo, hi = s.min() - 0.5, s.max() + 0.5
    h = (hi - lo) / ndisk
    k = np.minimum(((s - lo) / h).astype(int), ndisk - 1)
    width = np.bincount(k, minlength=ndisk) / h
    return float(np.sum(np.pi / 4 * width ** 2 * h))


# ---------------------------------------------------------------- 逐拍分析（与 echonet/beats.py 相同）
def analyze(vol, fps, step, cfg):
    from scipy.signal import find_peaks, medfilt
    v = np.asarray(vol, np.float64)
    fe = fps / step
    if cfg["smooth"] > 1 and len(v) >= cfg["smooth"]:
        v = medfilt(v, cfg["smooth"])
    out = dict(beats=[], fallback=False, hr=None, cycle_s=None)
    if len(v) < 3 or v.max() <= 0:
        out.update(ef=float("nan"), fallback=True)
        return out
    rng = np.percentile(v, 95) - np.percentile(v, 5)
    troughs, _ = find_peaks(-v, distance=max(1, int(round(cfg["dist_s"] * fe))), prominence=max(cfg["prom"] * rng, 1e-9))
    cycle = float(np.median(np.diff(troughs))) if len(troughs) >= 2 else 0.8 * fe
    if len(troughs) >= 2:
        out["cycle_s"] = cycle / fe
        out["hr"] = 60.0 / out["cycle_s"]
    w = max(2, int(round(cfg["win"] * cycle)))
    for t in troughs:
        a = max(0, t - w)
        if t - a < 2:
            continue
        e = a + int(np.argmax(v[a:t]))
        if v[e] <= v[t]:
            continue
        out["beats"].append(dict(ed=int(e * step), es=int(t * step), v_ed=float(v[e]), v_es=float(v[t]),
                                 ef=float(100 * (v[e] - v[t]) / v[e])))
    if out["beats"]:
        efs = [b["ef"] for b in out["beats"]]
        out["ef"] = float(np.mean(efs) if cfg["agg"] == "mean" else np.median(efs))
        out["ef_sd"] = float(np.std(efs))
    else:
        out["ef"] = float(100 * (v.max() - v.min()) / v.max())
        out["fallback"] = True
    return out


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def calibrated(ef_raw, conf, mode):
    c = conf[mode]
    return c["a"] * ef_raw + c["b"]


# ---------------------------------------------------------------- 质控
def qc(frames_info, result):
    """frames_info: 每个分析帧 dict(ncomp, area, unc, border)。返回 (等级 0/1/2, 原因列表, 分数)。"""
    n = max(1, len(frames_info))
    multi = sum(f["ncomp"] > 1 for f in frames_info) / n
    unc = float(np.mean([f["unc"] for f in frames_info])) if frames_info else 1.0
    border = sum(f["border"] for f in frames_info) / n
    empty = sum(f["area"] == 0 for f in frames_info) / n
    spread = min(1.0, (result.get("ef_sd") or 0.0) / 20.0)
    score = multi + 2 * unc + border + 2 * empty + spread + float(result.get("fallback", False))
    why = qc_bits(multi, border, empty, result)
    reasons = qc_reasons(why, dict(multi=multi, border=border, empty=empty), result)
    level = 2 if score >= THRESH[1] else (1 if score >= THRESH[0] or reasons else 0)
    return level, reasons, score


def qc_bits(multi, border, empty, result):
    """质控原因的位（板端 job_echo.c 的 qc 回同样的位）：1 空帧 2 不连通 4 碰边 8 各拍 EF 差大 16 无完整周期 32 拍数少。"""
    why = 0
    if empty > 0.02:
        why |= 1
    if multi > 0.3:
        why |= 2
    if border > 0.05:
        why |= 4
    if (result.get("ef_sd") or 0) > 8:
        why |= 8
    if result.get("fallback"):
        why |= 16
    if len(result.get("beats", [])) < 2 and not result.get("fallback"):
        why |= 32
    return why


def qc_reasons(why, parts, result):
    """位 -> 文字（PC 和板端结果共用，措辞一致）。parts 要有 multi / border / empty。"""
    reasons = []
    if why & 1:
        reasons.append("%.0f%% 的帧没分割出左心室" % (100 * parts["empty"]))
    if why & 2:
        reasons.append("%.0f%% 的帧分割结果不连通" % (100 * parts["multi"]))
    if why & 4:
        reasons.append("%.0f%% 的帧轮廓碰到画面边缘" % (100 * parts["border"]))
    if why & 8:
        reasons.append("各拍 EF 相差大（标准差 %.1f）" % result["ef_sd"])
    if why & 16:
        reasons.append("没找到完整心动周期，按最大 / 最小容积估计")
    if why & 32:
        reasons.append("只找到 %d 拍" % len(result.get("beats", [])))
    return reasons


THRESH = (0.35, 0.6)          # 由 echonet_config.json 的 qc_thresholds 覆盖（VAL 上 80 / 95 百分位）


def pp_request():
    """PL 后处理请求（板端 v5.10+、固件带 pp_layer）：112 裁剪窗口内 logit >= 1，保留最大连通块并填洞，
    与 mask_from_logit + clean 逐位相同（E:/fpga/my_project/pld2026final/01_FPGA/ai/pp_hls）。"""
    import proto
    return dict(op=proto.PP_OP_MASK | proto.PP_OP_CLEAN, k=1, thr=1, win=(PAD, PAD, N, N))


def pp_clean(pp):
    """板端 PL 后处理块 -> (清理后的 112x112 掩膜, 原掩膜的连通块数)；没做成时 None（退回 PC 上的 clean）。"""
    if not pp or pp.get("status") != 0 or pp.get("mask") is None:
        return None
    import proto
    return proto.pp_mask_array(pp)[:N, :N], int(pp["comps"])


def frame_info(q, mask_raw, cleaned=None):
    """一帧的质控信息（q 为裁好的 int8 logit）。cleaned = 板端 PL 已做好的 (掩膜, 连通块数)，否则在 PC 上 clean。"""
    from scipy import ndimage
    if cleaned is not None:
        mc, ncomp = cleaned
    else:
        mc, ncomp = clean(mask_raw) if mask_raw.any() else (mask_raw, 0)
    band = ndimage.binary_dilation(mc, iterations=3) if mc.any() else np.zeros_like(mc)
    scale = SCALE[0]
    lg = (q.astype(np.float32) - 0.5) * scale
    unc = float((np.abs(lg[band]) < 1.0).mean()) if band.any() else 1.0
    border = bool(mc[0].any() or mc[-1].any() or mc[:, 0].any() or mc[:, -1].any())
    return mc, dict(ncomp=int(ncomp), area=int(mc.sum()), unc=unc, border=border)


SCALE = [1.0]                 # 由模型表的输出 scale 设置


# ---------------------------------------------------------------- 临床测量（辅助测量与复核，不作诊断）
# LVEF 三档区间：≤40 / 41–49 / ≥50（2021 ESC 心衰指南的 LVEF 划分）。这里只描述测量值落在哪个区间，不判断是否心衰。
# 节律：EchoNet 没有心电图，RR 间期由容积曲线上相邻两拍的舒张末（ED）帧间隔推出。
# 两个门限（复核边界宽度、RR 变异门限）只在 VAL 上定（echonet/eval_clinical.py），写在 echonet_config.json 的 "clinical"。
LVEF_BOUNDS = (40.0, 50.0)
LVEF_BANDS = ("≤40%（降低）", "41–49%（轻度降低）", "≥50%（保留）")
CLINICAL_DEFAULT = dict(review_margin=5.0, rr_cv_irregular=0.10, min_beats_avg=5)
CLINICAL_REFS = {
    "esc2021": "2021 ESC 心衰指南：LVEF ≤40% / 41–49% / ≥50% 三档 "
               "(https://acc.org/Latest-in-Cardiology/ten-points-to-remember/2021/08/29/18/05/2021-ESC-Guidelines-for-HF-ESC-2021)",
    "af": "房颤时超声测量应多拍平均（ASE/EACVI 共识至少 5 拍），或取前两个 RR 间期接近的“指数心拍” "
          "(https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5834126/)",
}


def clinical_conf(conf, mode):
    """echonet_config.json 的 "clinical" 里取一种模式（"full" / "realtime"）的门限；没有就用缺省值。"""
    return dict(CLINICAL_DEFAULT, **(conf.get("clinical", {}).get(mode, {})))


def lvef_band(ef):
    """EF（%）-> 区间编号 0/1/2；EF 是 nan 时 None。"""
    if ef is None or not np.isfinite(ef):
        return None
    return 0 if ef <= LVEF_BOUNDS[0] else (1 if ef < LVEF_BOUNDS[1] else 2)


def clinical(beats, fps, ef, cal=lambda e: e, cc=None):
    """逐拍结果 -> 临床测量（纯计算，PC 与板端 job_echo.c 同一套规则）。
    beats: analyze() 的 beats（ed / es 为原视频帧号，ef 为未校准的逐拍 EF）；ef: 整段 EF（已校准）；
    cal: 逐拍 EF 的校准函数；cc: echonet_config.json 的 "clinical"（缺省用 CLINICAL_DEFAULT）。"""
    cc = dict(CLINICAL_DEFAULT, **(cc or {}))
    out = dict(band=lvef_band(ef), review=False, nearest_bound=None, margin=cc["review_margin"],
               n_beats=len(beats), beat_ef=[float(cal(b["ef"])) for b in beats],
               beat_sd=None, beat_range=None, rr_s=[], rr_mean_s=None, rr_cv=None, rr_max_step_ms=None,
               irregular=None, index_beat=None, few_beats=len(beats) < cc["min_beats_avg"],
               rr_cv_irregular=cc["rr_cv_irregular"], min_beats_avg=cc["min_beats_avg"])
    if out["band"] is not None:
        d = [abs(ef - b) for b in LVEF_BOUNDS]
        k = int(np.argmin(d))
        out["nearest_bound"] = LVEF_BOUNDS[k]
        out["review"] = bool(d[k] < cc["review_margin"])
    if beats:
        e = np.array(out["beat_ef"], np.float64)
        out["beat_sd"] = float(np.std(e))
        out["beat_range"] = float(e.max() - e.min())
    if len(beats) >= 2 and fps > 0:
        ed = np.array([b["ed"] for b in beats], np.float64)
        rr = np.diff(ed) / fps
        out["rr_s"] = [float(x) for x in rr]
        out["rr_mean_s"] = float(np.mean(rr))
        if len(rr) >= 2:
            out["rr_cv"] = float(np.std(rr) / np.mean(rr))
            out["rr_max_step_ms"] = float(np.max(np.abs(np.diff(rr))) * 1000.0)
            out["irregular"] = bool(out["rr_cv"] > cc["rr_cv_irregular"])
            # 指数心拍：前两个 RR 间期（ED_{k-2}→ED_{k-1}、ED_{k-1}→ED_k）最接近的那一拍（k ≥ 2），同差取靠前的
            diff = np.abs(rr[1:] - rr[:-1])
            j = int(np.argmin(diff))
            out["index_beat"] = dict(k=j + 2, ed=int(beats[j + 2]["ed"]), es=int(beats[j + 2]["es"]),
                                     ef=out["beat_ef"][j + 2], rr_diff_ms=float(diff[j] * 1000.0))
    return out


def clinical_text(c, ef):
    """clinical() -> 给界面 / 报告用的几行文字（措辞：辅助测量与复核，不下诊断）。"""
    lines = []
    if c["band"] is None:
        lines.append("LVEF 未能测出")
    else:
        s = "LVEF %.0f%% 落在 %s 区间" % (ef, LVEF_BANDS[c["band"]])
        if c["review"]:
            s += "；距 %.0f%% 分界不到 %.1f 个百分点（门限在验证集上定，见报告），建议复核" % (
                c["nearest_bound"], c["margin"])
        lines.append(s)
    if c["beat_sd"] is not None:
        lines.append("逐拍 EF：%d 拍，标准差 %.1f、极差 %.1f 个百分点" % (c["n_beats"], c["beat_sd"], c["beat_range"]))
    if c["rr_mean_s"] is not None:
        s = "RR 间期（由容积曲线推出，无心电图）：均值 %.0f ms" % (c["rr_mean_s"] * 1000)
        if c["rr_cv"] is not None:
            s += "，变异系数 %.1f%%，相邻最大变化 %.0f ms" % (100 * c["rr_cv"], c["rr_max_step_ms"])
        lines.append(s)
    if c["irregular"]:
        lines.append("RR 间期不规则（变异系数 > %.0f%%）：建议多拍平均（ASE/EACVI 共识至少 %d 拍），本段 %d 拍%s" % (
            100 * c["rr_cv_irregular"], c["min_beats_avg"], c["n_beats"], "，不足" if c["few_beats"] else ""))
    if c["index_beat"] is not None:
        ib = c["index_beat"]
        lines.append("指数心拍：第 %d 拍（ED 帧 %d），前两个 RR 间期相差 %.0f ms，该拍 EF %.0f%%"
                     "（单拍，仅供参考；验证集上单拍误差大于多拍中位数）" % (
                         ib["k"] + 1, ib["ed"], ib["rr_diff_ms"], ib["ef"]))
    return lines


# ---------------------------------------------------------------- ②③（板端 v5.21 echo_ef mode 2）
# 质量灯的原因（板上按 w_i z_i 取最大的两项）。"stability" 的权重为正是特征共线的结果，不是"越稳越差"，界面不当原因显示。
QUALITY_REASON = dict(touch="左心室贴着扇区边缘（可能被截断）", apex_gap="心尖离扇区顶端偏远（可能缩短）",
                      length="左心室长轴偏短（可能缩短）", round="左心室偏圆（可能缩短）", unc="网络对左心室边界没有把握",
                      multi="分割碎成多块", contrast="心腔内外对比度低", roughness="容积曲线抖动")
LIGHT_TXT = {0: ("绿", "#080"), 1: ("黄", "#c80"), 2: ("红", "#c00")}


def quality_text(q):
    """板上 "quality" -> 一行文字（只作原因提示，不参与是否复核）。绿灯不列原因。"""
    if not q:
        return None
    why = [QUALITY_REASON[k] for k in q.get("reasons", []) if k in QUALITY_REASON] if q["light"] > 0 else []
    return "图像质量提示：%s灯%s（零额外模型：由分割输出和画面算出，CAMUS 上与专家质量等级 AUC 0.91；只作提示）" % (
        LIGHT_TXT[q["light"]][0], ("，主要原因：" + "；".join(why)) if why else "")


def interval_text(iv, ef):
    """板上 "interval"（分割式保形，验证集校准）-> (主界面一行, 是否建议复核)。80 % 区间跨过 40 / 50 = 分型待定。"""
    if not iv:
        return None, False
    lo, hi = max(0.0, iv["lo80"]), min(100.0, iv["hi80"])
    if iv["review"]:
        s = "80%% 区间 %.0f–%.0f%%：跨过 %s 分界，分型待定，建议复核" % (
            lo, hi, "、".join("%d%%" % b for b in (40, 50) if lo < b < hi))
    else:
        s = "80%% 区间 %.0f–%.0f%%：整体落在 %s 区间" % (lo, hi, LVEF_BANDS[lvef_band(ef)])
    return s, bool(iv["review"])


# ---------------------------------------------------------------- 全心扩展（板端 v5.24 "HC01"：节段室壁运动 + 右室 / 右房）
WM_NAMES = ["基底段间隔", "中段间隔", "心尖段间隔", "心尖段侧壁", "中段侧壁", "基底段侧壁"]   # HMC-QU SEG3/9/14/16/12/6
RV_COLOR, RA_COLOR = (190, 110, 255), (255, 170, 40)


def heart_text(res, hc=None):
    """板上 "wall_motion" / "right_heart" -> 文字行（辅助测量与复核提示，不作诊断）。没有就回 []。"""
    lines = []
    wm = res.get("wall_motion")
    if "wall_motion" in res:
        if wm is None:
            lines.append("节段室壁运动：没有可用的心动周期，不给结果")
        else:
            low = [WM_NAMES[k] for k, f in enumerate(wm["seg"]) if f]
            cv = ((hc or {}).get("wall_motion") or {}).get("dev_cv") or {}
            auc = cv.get("segment", {}).get("auc")
            lines.append("节段室壁运动（板上由左室掩膜算出，零额外模型；%d 拍平均）：%s" % (
                wm["beats"], ("提示运动减低：" + "、".join(low) + "（建议复核）") if low else "6 个节段均未达减低提示门限"))
            if wm["score"] is not None:
                lines.append("  整体：%s（得分 %.2f；结合 EF 与节段，HMC-QU 开发集交叉验证%s）" % (
                    "室壁运动异常提示，建议复核" if wm["flag"] else "未达室壁运动异常提示门限", wm["score"],
                    ("：节段 AUC %.2f，整段灵敏度 %.2f / 特异度 %.2f" % (auc, cv["recording"]["sens"], cv["recording"]["spec"]))
                    if auc else ""))
    rh = res.get("right_heart")
    if rh:
        if rh.get("error"):
            lines.append("右心：%s" % rh["error"])
        n = len(rh.get("pairs", []))
        if rh.get("rv_lv") is not None:
            big = rh["rv_lv"] > 1.0
            lines.append("右室：RV/LV 舒张末面积比 %.2f%s；RV 面积变化分数 %.0f%%（探索性，未与人工测量核对，不作提示）——%d / %d 拍通过位置检查" % (
                rh["rv_lv"], "（> 1.0，提示右室相对扩大，建议复核）" if big else "", 100 * rh["rv_fac"], rh["n_rv"], n))
        else:
            lines.append("右室：%d 拍都没通过位置合理性检查（面积、在左室左侧、不与左室重叠），不给数值" % n)
        if rh.get("ra_lv") is not None:
            lines.append("右房：收缩末面积 / 左室舒张末面积 %.2f（%d / %d 拍通过位置检查；无像素间距，只给比值）" % (
                rh["ra_lv"], rh["n_ra"], n))
        else:
            lines.append("右房：没有通过位置合理性检查的拍，不给数值")
    return lines


def add_contour(rgb, mask, scale, color):
    """在已放大的 rgb 上加一条掩膜轮廓（contour_rgb 的同一画法）。"""
    rgb[_edge(mask, scale)] = color
    return rgb


def draw_wm_glyph(im, seg, x0, y0, w=64, h=84):
    """PIL 图上画心尖四腔切面的左室示意：倒 U 形（左 = 间隔，右 = 侧壁，下 = 基底），按弧长 6 等分，顺序 = HMC-QU 的
    基底段间隔、中段间隔、心尖段间隔、心尖段侧壁、中段侧壁、基底段侧壁；提示减低的段橙色，其余绿色。"""
    import math
    from PIL import ImageDraw
    d = ImageDraw.Draw(im)
    r = w / 2 - 6
    cx, top, bot = x0 + w / 2, y0 + 6, y0 + h - 6
    st = bot - (top + r)                                  # 两侧直线段长度
    total = 2 * st + math.pi * r

    def point(u):                                         # u 从左侧基底沿倒 U 走到右侧基底的弧长
        if u <= st:
            return cx - r, bot - u
        if u <= st + math.pi * r:
            a = math.pi - (u - st) / r                    # 左 (pi) -> 心尖 (pi/2) -> 右 (0)
            return cx + r * math.cos(a), top + r - r * math.sin(a)
        return cx + r, top + r + (u - st - math.pi * r)

    n = 60
    for i in range(n):
        u0, u1 = total * i / n, total * (i + 1) / n
        k = min(5, int(6 * (u0 + u1) / 2 / total))
        d.line([point(u0), point(u1)], fill=(255, 140, 0) if seg[k] else (90, 200, 120), width=5)
    return im


# ---------------------------------------------------------------- 证据报告
def _png(img):
    from PIL import Image
    b = io.BytesIO()
    Image.fromarray(img).save(b, format="PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode("ascii")


def _edge(mask, scale):
    """放大后的掩膜内边界（4 邻域里有背景的像素），各方向一样连续（10-25 前只比上 / 左邻居，右下边是虚线）。"""
    from PIL import Image
    m = np.array(Image.fromarray(mask.astype(np.uint8) * 255).resize((N * scale, N * scale), Image.NEAREST)) > 0
    p = np.pad(m, 1)
    inner = p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return m & ~inner


def contour_rgb(gray, mask, scale=3, color=(255, 64, 64)):
    from PIL import Image
    big = np.array(Image.fromarray(gray).resize((N * scale, N * scale), Image.BILINEAR))
    rgb = np.repeat(big[..., None], 3, -1)
    rgb[_edge(mask, scale)] = color
    return rgb


def _font(size):
    from PIL import ImageFont
    for f in ("msyh.ttc", "simhei.ttf", "arial.ttf",                 # Windows；板上（Ubuntu）用文泉驿，默认位图字体写不了中文
              "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def curve_png(t_s, vol, beats, fps, cal=lambda e: e, width=760, height=220):
    """容积曲线图；每拍标的 EF 用 cal（与报告表格同一个校准）。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (width, height), (255, 255, 255))
    d = ImageDraw.Draw(img)
    font = _font(13)
    if len(vol) < 2:
        return _png(np.array(img))
    x0, x1, y0, y1 = 50, width - 10, 10, height - 30
    vmax, vmin = max(vol), min(vol)
    tmax = max(t_s[-1], 1e-6)
    X = lambda t: x0 + (x1 - x0) * t / tmax
    Y = lambda v: y1 - (y1 - y0) * (v - vmin) / max(vmax - vmin, 1e-9)
    d.rectangle([x0, y0, x1, y1], outline=(200, 200, 200))
    d.line([(X(t), Y(v)) for t, v in zip(t_s, vol)], fill=(30, 90, 200), width=2)
    for b in beats:
        te, ts = b["ed"] / fps, b["es"] / fps
        d.polygon([(X(te), Y(b["v_ed"]) - 9), (X(te) - 6, Y(b["v_ed"]) - 1), (X(te) + 6, Y(b["v_ed"]) - 1)], fill=(220, 50, 50))
        d.polygon([(X(ts), Y(b["v_es"]) + 9), (X(ts) - 6, Y(b["v_es"]) + 1), (X(ts) + 6, Y(b["v_es"]) + 1)], fill=(40, 120, 220))
        d.text((X(ts) - 14, Y(b["v_es"]) + 11), "%.0f%%" % cal(b["ef"]), fill=(40, 40, 40), font=font)
    for k in range(int(tmax) + 1):
        d.line([(X(k), y1), (X(k), y1 + 4)], fill=(120, 120, 120))
        d.text((X(k) - 3, y1 + 6), "%ds" % k, fill=(90, 90, 90), font=font)
    d.text((4, y0), "容积", fill=(90, 90, 90), font=font)
    return _png(np.array(img))


def write_report(path, info):
    """info: 窗口收集的全部结果（见 echowin._report_info）。返回写出的文件路径。"""
    r = info["result"]
    rows = "".join("<tr><td>%d</td><td>%d（%.2f s）</td><td>%d（%.2f s）</td><td>%.0f%%</td></tr>"
                   % (i + 1, b["ed"] + 1, b["ed"] / info["fps"], b["es"] + 1, b["es"] / info["fps"], info["cal_beat"](b["ef"]))
                   for i, b in enumerate(r["beats"]))
    keys = "".join('<figure><img src="%s"><figcaption>%s</figcaption></figure>' % (img, cap) for img, cap in info["keyframes"])
    qc_txt = {0: "通过", 1: "注意", 2: "不可靠"}[info["qc_level"]]
    ref = ("<p>数据集标注 EF（FileList.csv，仅作对照）：<b>%.1f%%</b></p>" % info["ref_ef"]) if info.get("ref_ef") is not None else ""
    clin = ""
    if info.get("clinical"):
        c = info["clinical"]
        rr = "、".join("%.0f" % (1000 * x) for x in c["rr_s"]) or "—"
        clin = ("<h2>临床测量（辅助测量与复核，%s）</h2><ul>%s<li>RR 间期（ms，相邻两拍舒张末之间）：%s</li></ul>"
                "<p style='font-size:13px;color:#555'>说明：只给测量值和所在区间，不作诊断。帧号从 1 数（与界面一致）。输入视频不带心电图，RR 间期由左心室容积曲线"
                "推出，漏检一拍时会被当成一个长 RR。复核边界宽度 %.1f 个百分点、RR 变异门限 %.0f%% 只在验证集上定。"
                "出处：%s；%s。</p>") % (
            info.get("clinical_where", ""), "".join("<li>%s</li>" % t for t in clinical_text(c, info["ef"])), rr,
            c["margin"], 100 * c["rr_cv_irregular"], CLINICAL_REFS["esc2021"], CLINICAL_REFS["af"])
    if info.get("interval"):                      # ③ + ② + ①（板端 v5.21 关键帧模式）
        iv, q, k = info["interval"], info.get("quality"), info.get("keyframe") or {}
        line80, _ = interval_text(iv, info["ef"])
        clin += ("<h2>不确定度与质量提示（板上计算）</h2><ul><li>EF %.0f%%，%s</li>"
                 "<li>90%% 区间 %.0f–%.0f%%（报告用；分割式保形预测，在 EchoNet 验证集上校准，覆盖率 0.90）</li>%s%s</ul>"
                 "<p style='font-size:13px;color:#555'>说明：区间宽度随逐拍 EF 一致性和网络置信度变化；覆盖率是验证集上的平均值，"
                 "不是对单个病人的保证。区间跨过 40%% 或 50%% 时只提示复核，不给分型。质量灯不参与复核判断。</p>") % (
            info["ef"], line80, max(0.0, iv["lo90"]), min(100.0, iv["hi90"]),
            ("<li>%s</li>" % quality_text(q)) if q else "",
            ("<li>关键帧：PL 推理 %d / %d 帧（%.1f%%），其余帧在 PS 上块匹配跟踪（每帧 %.2f ms，每个关键帧 PL %.1f ms）</li>"
             % (k["n_key"], info["nframes"], 100 * k["key_frac"], k["track_ms_per_frame"], k["pl_ms_per_key"])) if k else "")
    if info.get("heart"):                         # 全心扩展（板端 v5.24 HC01）
        hl = heart_text(info["heart"], info.get("heart_cfg"))
        figs = "".join('<figure><img src="%s"><figcaption>%s</figcaption></figure>' % (img, cap)
                       for img, cap in info.get("heart_figs", []))
        clin += ("<h2>全心：节段室壁运动与右心（板上计算，辅助测量与复核提示）</h2><ul>%s</ul>%s"
                 "<p style='font-size:13px;color:#555'>说明：节段室壁运动由 FPGA 分割的左室在每拍舒张末 / 收缩末的面积变化按 6 个心尖四腔节段"
                 "（基底 / 中段 / 心尖 × 间隔 / 侧壁）算出，门限和整体打分只在 HMC-QU 开发集（%s）上拟合，测试集另评一次；"
                 "右室（紫）/ 右房（橙）由 CardiacUDA 训练的程序 21 / 22 在同一帧上分割，只给比值，位置检查不过就不给数。"
                 "节段与右心都只作复核提示，不作心梗或右心疾病的诊断。</p>") % (
            "".join("<li>%s</li>" % t for t in hl), figs,
            ((info.get("heart_cfg") or {}).get("wall_motion") or {}).get("source", "E:/fpga/my_project/pld2026final/03_算法与数据/wallmotion"))
    if info.get("corrections"):                   # 心超工作站的点击纠错（heartview.py）
        c = info["corrections"]
        def fr(ts):                               # 连续帧写成区间：11–33
            runs = []
            for t in ts:
                if runs and t == runs[-1][1] + 1:
                    runs[-1][1] = t
                else:
                    runs.append([t, t])
            return "、".join(("%d" % a) if a == b else ("%d–%d" % (a, b)) for a, b in runs) or "无"
        prop = fr(c["prop"])
        clin += ("<h2>人工纠错（医生点击，FPGA 重算）</h2><ul><li>纠错 %d 次；人工纠正的帧：%s；"
                 "纠正后跟着重算的帧（FPGA 从纠正后的轮廓逐帧往后，到视频末尾或下一个人工纠正的帧为止）：%s</li>"
                 "<li>板上原始 EF %.0f%% → 纠错后 %s（逐拍 EF 与临床测量由上位机按纠正后的容积、同一规则重算；"
                 "80%% 区间与质量灯为纠错前的板上结果）</li><li>程序：%s；每次点击 FPGA 整网 %s ms</li></ul>") % (
            c["n"], fr(c["frames"]), prop, c["ef_board"],
            ("%.0f%%" % c["ef_now"]) if c.get("ef_now") is not None else "—", c["models"],
            " / ".join("%.1f" % v for v in c["pl_ms"]) or "—")
    if info.get("hints"):                          # clinical_hints：测量值 -> 提示方向（辅助复核，不作诊断）
        mk = {2: "▲", 1: "△", 0: "○"}
        clin = ("<h2>提示方向（辅助复核，不作诊断）</h2><ul>%s</ul><p style='font-size:13px;color:#555'>说明：只说测量值可能与哪类问题有关、"
                "建议复核什么，诊断由医生结合病史、心电图、化验等做。判据：ESC 2021 心衰指南（LVEF 分档、HFpEF 结构证据）；"
                "ASE 2015（左房容积指数、左室质量指数、左室容积上限）；本系统门限（区间复核、节段运动、RR 不规则）只在训练 / 验证 / "
                "开发集上定。右心为面积比，只作提示。</p>") % "".join(
            "<li>%s %s</li>" % (mk.get(k, ""), s) for k, s in info["hints"]) + clin
    note = info.get("data_note") or "数据：EchoNet-Dynamic（Stanford AIMI，研究使用协议，不得外传），Ouyang et al., Nature 2020"
    html ="""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>心超分析证据报告 %(name)s</title>
<style>body{font-family:"Microsoft YaHei UI",sans-serif;margin:24px;color:#222;max-width:860px}
h1{font-size:22px}h2{font-size:17px;margin-top:22px;border-bottom:1px solid #ddd}
table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 10px;text-align:center}
.k{font-size:28px;font-weight:bold;color:#a33}.warn{color:#a60}.bad{color:#c00}.ok{color:#080}
figure{display:inline-block;margin:6px}figcaption{text-align:center;font-size:13px}.foot{color:#a33;font-size:13px;margin-top:24px}</style></head><body>
<h1>左心室功能分析报告（工程演示）</h1>
<p>视频：%(name)s ｜ %(nframes)d 帧，%(fps).0f 帧/秒（%(dur).1f s）｜ 生成时间 %(now)s</p>
<h2>结论</h2>
<p>射血分数 EF（逐拍中位数，已校准）：<span class="k">%(ef).0f%%</span> &nbsp; 心率：<b>%(hr)s</b> &nbsp; 检出心动周期：<b>%(nb)d</b> 拍</p>
%(ref)s<p>质控：<b class="%(qcc)s">%(qc)s</b>%(reasons)s</p>
<h2>容积曲线（▲ 舒张末 ED，▼ 收缩末 ES，数字为该拍 EF）</h2><img src="%(curve)s">
<h2>逐拍结果</h2><table><tr><th>拍</th><th>ED 帧</th><th>ES 帧</th><th>EF</th></tr>%(rows)s</table>
%(clin)s
<h2>关键帧（红色 = FPGA 分割的左心室）</h2>%(keys)s
<h2>计算过程</h2><ul>
<li>分割：FPGA（FZ3B，固定 U-Net 引擎，模型库 %(model)s，128 档，34,201 参数，INT8/INT16 混合精度，与离线整数参考逐位一致）</li>
<li>模式：%(mode)s，分析 %(nan)d 帧；板端每帧 PL 计算 %(ai).1f ms，整段 %(wall).2f s（%(rfps).1f 帧/秒，实时倍数 %(rt).2f）</li>
<li>容积：单平面圆盘法（左心室掩膜主轴方向 20 个圆盘）；EF 逐拍计算后取中位数，再按训练集拟合的线性关系校准</li></ul>
<p class="foot">仅用于工程教学与算法演示，不作为临床诊断依据。%(note)s。</p></body></html>""" % dict(
        name=info["name"], nframes=info["nframes"], fps=info["fps"], dur=info["nframes"] / info["fps"],
        now=time.strftime("%Y-%m-%d %H:%M:%S"), ef=info["ef"], hr=("%.0f 次/分" % r["hr"]) if r.get("hr") else "—",
        nb=len(r["beats"]), ref=ref, qcc={0: "ok", 1: "warn", 2: "bad"}[info["qc_level"]], qc=qc_txt,
        reasons=("：" + "；".join(info["qc_reasons"])) if info["qc_reasons"] else "", curve=info["curve"], rows=rows,
        keys=keys, clin=clin, note=note, model=info["model"], mode=info["mode"], nan=info["n_analyzed"], ai=info["ai_ms"], wall=info["wall"],
        rfps=info["n_analyzed"] / max(info["wall"], 1e-9), rt=(info["nframes"] / info["fps"]) / max(info["wall"], 1e-9))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
