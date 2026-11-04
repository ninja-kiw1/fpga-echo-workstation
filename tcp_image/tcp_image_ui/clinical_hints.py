# -*- coding: utf-8 -*-
"""clinical_hints.py —— 把测量值翻成「提示方向」：只说"可能与哪类问题有关、建议复核什么"，不作诊断。

心超工作站的三处共用：关键帧 / 现场模式结果卡片（heartview）、快照模式测量卡片（snapview）、证据报告（echo_ws.write_report）。
每条提示 = (级别, 文字)：2 = 建议复核（橙），1 = 注意，0 = 未见需要提示的异常（只在一条都没触发时给）。
判据（出处写在文字里）：
  ESC 2021 心衰指南：LVEF ≤40 / 41–49 / ≥50；HFpEF 要 EF 保留 + 结构 / 功能异常（左房扩大、左室肥厚等）。
  ASE 2015（Lang 等）：LAVI > 34 mL/m² 左房扩大；LVMI 上限 女 95 / 男 115 g/m²；双平面 EDV 上限 女 106 / 男 150 mL，
    EDV 指数上限 女 61 / 男 74 mL/m²（Table 1）。RWT 只显示数值（口径不同，项目 10-18 决定不套 0.42）。
  本系统门限（只在训练 / 验证 / 开发集上定）：80 % 区间跨界复核、节段运动减低、RR 不规则。
  右心：文献多用 A4C 的 RV/LV 直径比 > 1.0；我们是面积比，只作提示。
仅用于工程教学与算法演示，不作为临床诊断依据。
"""

EDV_UPPER = {"F": (106.0, 61.0), "M": (150.0, 74.0)}      # ASE 2015 Table 1，双平面：EDV mL、EDV 指数 mL/m² 上限
LAVI_LIMIT = 34.0
LVMI_LIMIT = {"F": 95.0, "M": 115.0}
LAD = (0, 1, 2, 3)                                          # HMC-QU 6 节段：间隔 3 段 + 心尖侧壁 -> 多对应前降支
LCX = (4, 5)                                                # 中段 / 基底段侧壁 -> 多对应回旋支


def ef_hint(ef):
    if ef is None:
        return None
    if ef <= 40:
        return (2, "LVEF %.0f%% ≤ 40%%：收缩功能降低（HFrEF 范围，ESC 2021）→ 常见于心梗后、扩张型心肌病，建议复核并结合病因"
                % ef)
    if ef < 50:
        return (2, "LVEF %.0f%% 在 41–49%%：收缩功能轻度降低（HFmrEF 范围，ESC 2021）→ 建议复核、结合症状" % ef)
    return None


def echo_hints(ef, review=False, hr=None, irregular=False, wall_motion=None, right_heart=None, light=None, names=None):
    """关键帧 / 现场模式（只有 A4C、没有毫米尺度）：EF、复核、心率 / 节律、节段运动、右心比值、图像质量。"""
    out = []
    h = ef_hint(ef)
    if h:
        out.append(h)
    if review:
        out.append((2, "EF 的 80% 区间跨过 40% / 50% 分界：分型待定，建议人工复测（本系统，验证集校准）"))
    if wall_motion and any(wall_motion.get("seg") or []):
        seg = wall_motion["seg"]
        low = [names[k] if names else str(k) for k, f in enumerate(seg) if f]
        terr = []
        if any(seg[k] for k in LAD):
            terr.append("间隔 / 心尖段多对应前降支")
        if any(seg[k] for k in LCX):
            terr.append("侧壁中段 / 基底段多对应回旋支")
        out.append((2, "节段运动减低（%s）→ 提示缺血性心脏病 / 心梗可能（%s），建议复核" % ("、".join(low), "；".join(terr))))
    rv = (right_heart or {}).get("rv_lv")
    if rv is not None and rv > 1.0:
        out.append((2, "RV/LV 面积比 %.2f > 1：右室相对扩大 → 急性肺栓塞、肺动脉高压、右室心梗等需鉴别（面积比，仅提示）" % rv))
    if irregular:
        out.append((1, "RR 间期不规则（由容积曲线推出，无心电图）→ 可能房颤；EF 宜多拍平均"))
    # 心率不出提示：它由容积曲线和视频文件头的帧率推出，没有心电图；HMC-QU 官方 25 fps 而 AVI 头写 30 fps，
    # 按文件头算会偏高约 20 %（ES000108：129 对超声机屏上的 96），据此说"偏快"会误导（hr 参数保留，接采集卡用时间戳后再开）
    _ = hr
    if light == 2:
        out.append((1, "图像质量红灯：以上结果可靠性低，建议重新采图"))
    if not out:
        out.append((0, "未见需要提示的异常（LVEF ≥ 50%%，区间未跨界%s）；EF 保留不排除 HFpEF，需结合左房 / 左室质量（快照模式）"
                    % ("，节段 / 右心未达提示门限" if wall_motion or right_heart else "")))
    return out


def measure_hints(ef, edv=None, sex=None, bsa=None, la_ml=None, la_truncated=False, lvmi=None, mono=False):
    """快照模式（有毫米尺度）：EF、左室大小、左房、左室质量，以及 HFpEF 的结构证据组合。mono = 单平面（毫升未验证，不判大小）。"""
    out = []
    h = None if mono else ef_hint(ef)                   # 单平面 EF 是几何法原值、没校准：不据此分档，免得和关键帧模式打架
    if h:
        out.append(h)
    big = la_big = lvh = False
    if edv is not None and not mono and sex in EDV_UPPER:
        lim, limi = EDV_UPPER[sex]
        if bsa:
            big = edv / bsa > limi
            if big:
                out.append((2, "EDV 指数 %.0f mL/m² > %.0f（ASE 2015）：左室扩大 → 扩张型心肌病、瓣膜反流等容量负荷，建议复核"
                            % (edv / bsa, limi)))
        else:
            big = edv > lim
            if big:
                out.append((2, "EDV %.0f mL > %.0f（ASE 2015）：左室扩大 → 扩张型心肌病、容量负荷，建议复核" % (edv, lim)))
    if la_ml is not None and not la_truncated and bsa:
        lavi = la_ml / bsa
        la_big = lavi > LAVI_LIMIT
        if la_big:
            out.append((2, "LAVI %.0f mL/m² > 34（ASE 2015）：左房扩大 → 左室充盈压升高 / 舒张功能不全、房颤风险、二尖瓣病变" % lavi))
    if lvmi is not None and sex in LVMI_LIMIT and lvmi > LVMI_LIMIT[sex]:
        lvh = True
        out.append((2, "LVMI %.0f g/m² > %.0f（ASE 2015）：左室肥厚 → 高血压、主动脉瓣狭窄、肥厚型心肌病等（二维掩膜口径，仅提示）"
                    % (lvmi, LVMI_LIMIT[sex])))
    if ef is not None and ef >= 50 and (la_big or lvh) and not mono:
        out.append((2, "EF 保留但有结构异常（%s）→ 符合 ESC 2021 诊断 HFpEF 时要看的结构证据，需结合症状、利钠肽"
                    % "、".join(n for n, f in (("左房扩大", la_big), ("左室肥厚", lvh)) if f)))
    if ef is not None and ef <= 40 and big:
        out.append((2, "EF 降低 + 左室扩大：典型的射血分数降低型心衰表现"))
    if mono:
        out.append((1, "单平面（只有 A4C）：EF 是几何法原值、没校准，毫升值依赖屏幕标尺、没有真值核对 → "
                       "不据此分档、也不判断左室大小，以关键帧 / 现场模式的结论为准"))
    if not out:
        out.append((0, "未见需要提示的异常（EF 保留，左室大小 / 左房 / 左室质量未超 ASE 2015 上限）"))
    return out


def text(hints):
    """给界面文本框 / 报告：每条一行，前面用符号区分级别。"""
    mark = {2: "▲ ", 1: "△ ", 0: "○ "}
    return "\n".join(mark[k] + s for k, s in hints)


if __name__ == "__main__":
    print(text(echo_hints(41.2, True, 129, False, dict(seg=[1, 0, 1, 1, 1, 1]), dict(rv_lv=0.36), 0,
                          ["基底段间隔", "中段间隔", "心尖段间隔", "心尖段侧壁", "中段侧壁", "基底段侧壁"])))
    print(text(measure_hints(39, 160, "M", 1.82, 95, False, 133)))
    print(text(measure_hints(62, 100, "F", 1.6, 70, False, 80)))
    print(text(measure_hints(33, 64, None, None, mono=True)))
