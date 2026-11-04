# -*- coding: utf-8 -*-
"""cardiac_measure.py —— P1 1.6：左心房容积 / 指数、左室质量 / 指数、壁厚（辅助测量与复核，不作诊断）。

全部建立在 F4 的双平面 Simpson 移植（ef_simpson.diameters，CAMUS 官方 notebook 的逐步移植）上：
  左房容积  LA 掩膜（标签 3）在 LV 收缩末（= LA 最大）帧，两切面 20 圆盘：sum(d2·d4)·step·π/4（mL，不取整）
  左室质量  1.05 g/mL ×（心外膜容积 − 心内膜容积），两者都在 ED 帧、各自用双平面 Simpson
  壁厚      ED 帧每个切面：心内膜轮廓点（去掉二尖瓣平面那一段）到心外膜边界的距离，取中位数（mm），两切面平均
  体表面积  Mosteller：sqrt(身高 cm × 体重 kg / 3600)（ASE 2015 原文没指定公式，见 docs/临床测量 第 3 节）

阈值（2026-10-15 核对原文：Lang RM et al., Recommendations for Cardiac Chamber Quantification by Echocardiography in
Adults, EHJ-CVI 2015;16:233–271 / JASE 2015;28:1–39）：
  LAVI（Table 4，双平面圆盘法，男女相同）  正常 16–34，轻度 35–41，中度 42–48，重度 > 48 mL/m²
  LV 质量指数（Table 6，2D 法）           正常 女 44–88、男 50–102 g/m²；**原文 2D 法是短轴乳头肌水平的面积-长度 / 截头椭球法，
                                          本系统是两个心尖切面上心外膜 − 心内膜的双平面 Simpson，方法不同，阈值只作参考、界面注明**
  壁厚（Table 6，线性法，胸骨旁长轴）       正常 女 0.6–0.9、男 0.6–1.0 cm；本系统的定义不同（心尖切面、全轮廓中位数），只显示数值

几何分型（部分 A，2026-10-16；阈值核对 ASE 官方托管的指南日文全译本 asecho.org/wp-content/uploads/2018/01/ChamberQuant_Japanese.pdf
第 13–14 页：正文、表 6、RWT × LVMI 分型图——图的坐标轴保留英文原文；JASE / OUP 原站有机器人校验打不开）：
  RWT = (2 × 后壁厚) / 左室舒张末内径；> 0.42 向心，≤ 0.42 非向心（正文与图坐标轴；图注写 ≧ / ≦，等号只是测度为零的边界）
  LVMI 上限（分型图，原文注明"左室质量按线性法"）女 ≤ 95、男 ≤ 115 g/m²（表 6 线性法 女 43–95、男 49–115；2D 法上限 88 / 102）
  指南四型：LVMI 不高 + RWT ≤ 0.42 正常构型；不高 + > 0.42 向心性重构；高 + > 0.42 向心性肥厚；高 + ≤ 0.42 离心性肥厚
  **本系统不套 RWT 0.42（用户 10-16 定）**：CAMUS 二维掩膜的心肌偏宽，专家掩膜上 RWT 中位 0.58、VAL 49 / 50 人 > 0.42
  （训练集换「与内径同一水平的壁厚」也一样，382 / 400），四型实际只剩 LVMI 一维。所以 RWT 只显示数值，
  分类只按 LVMI：> 95（女）/ 115（男）g/m² 为「增高」，否则「不增高」（lvmi_class）；不叫几何分型、不给四型名称。
  **口径**：指南的 RWT / LVMI 按胸骨旁长轴 M 型 / 二维线性测量定义；本系统全部来自两个心尖切面的二维掩膜：
    壁厚    = 上面的「全轮廓中位数」（代替后壁厚）
    内径    = ED 心内膜第 3–6 个圆盘（沿长轴 15%–30%，二尖瓣叶尖附近）直径的平均，两切面平均（代替 PLAX 的 LVIDd）
    质量    = 双平面 Simpson 心外膜 − 心内膜（代替线性法立方公式）
  所以 LVMI 分类只作辅助测量与复核的参考，界面 / 报告都注明口径不同，不作诊断。需要性别、身高、体重（BSA = Mosteller）。
"""
import math

import numpy as np
from scipy import ndimage

import ef_simpson as ef

DENSITY = 1.05                       # g/mL，ASE 2015 §4
LAVI_BANDS = ((34, '正常'), (41, '轻度增大'), (48, '中度增大'), (float('inf'), '重度增大'))     # Table 4 上限（含）
LVMI_2D = {'F': (44, 88), 'M': (50, 102)}                                                    # Table 6，2D 法
WALL_LINEAR_CM = {'F': (0.6, 0.9), 'M': (0.6, 1.0)}                                          # Table 6，线性法
SOURCE = 'Lang RM et al. EHJ-CVI 2015;16:233-271 (ASE/EACVI), Table 4 / Table 6'
# 部分 A：LVMI > LVMI_LIMIT[性别] 为增高（ASE 2015 分型图的线性法上限）；RWT 只显示数值，不套指南的 0.42
RWT_GUIDELINE = 0.42                 # 只用于文档 / 评测对照，不参与分类
LVMI_LIMIT = {'F': 95, 'M': 115}
LVIDD_DISKS = (3, 4, 5, 6)           # ED 心内膜 20 圆盘里取这几个（f = 0.15–0.30）平均 = 一个切面的舒张末内径
LVMI_CLASS = {'not_increased': '左室质量指数不增高', 'increased': '左室质量指数增高'}
LVMI_CLASS_NOTE = '按 ASE 2015 LVMI 上限（线性法）；本系统质量由心尖切面二维掩膜测得，口径不同，仅作辅助测量与复核；RWT 只显示数值'
# 左房截断（10-15 与用户商定）：CAMUS 切面以左室为中心，左房常被扇区下缘截掉（VAL 41/100 个 ES 帧）。
# 任一切面的 LA 掩膜碰到原图最下 LA_EDGE_ROWS 行 -> 报「左房被截断，不可测」，不给容积 / LAVI。
LA_EDGE_ROWS = 2
# 线性校准 la = a * raw + b，只在训练集（两切面都未截断的病人）上拟合：echo/fit_la_calib.py -> results/p1_16/la_calib.json
LA_CAL = dict(a=0.9533886925323705, b=9.024183337520322, n_fit=192, fit='train', json_sha256_16='89f79b072179dabc')


def bsa_mosteller(height_cm, weight_kg):
    return math.sqrt(height_cm * weight_kg / 3600.0)


def biplane_volume(m2, sp2, m4, sp4):
    """ef_simpson.volumes 的单时相版本（同一个求和，不取整）：两切面掩膜（原图分辨率）-> mL。"""
    d2, s2, _ = ef.diameters(m2, sp2)
    d4, s4, _ = ef.diameters(m4, sp4)
    step = max(s2, s4)
    return float(np.sum(np.asarray(d2) * np.asarray(d4)) * step * math.pi / 4.0 / 1000.0)


def wall_thickness_mm(endo, epi, spacing):
    """一个切面（ED）：心内膜轮廓点到心外膜外的距离的中位数（mm）；二尖瓣平面上的点不算。"""
    endo_i, sp = ef.to_isotropic(endo, spacing)
    epi_i, _ = ef.to_isotropic(epi, spacing)
    if not endo_i.any() or not epi_i.any():
        return None
    _, _, geo = ef.diameters(endo, spacing)
    dist = ndimage.distance_transform_edt(epi_i) * sp           # inside the epicardium: distance to its outside
    edge = endo_i & ~ndimage.binary_erosion(endo_i)
    ys, xs = np.nonzero(edge)
    keep = np.ones(len(ys), bool)
    back = sp / np.array(spacing, dtype=float)                  # original -> isotropic pixel coordinates
    mv = [np.asarray(p, float) / back for p in geo['base']]
    if mv:                                                      # drop points within 3 mm of the mitral chord
        (y0, x0), (y1, x1) = mv
        v = np.array([y1 - y0, x1 - x0], float)
        L = np.hypot(*v) or 1.0
        t = np.clip(((ys - y0) * v[0] + (xs - x0) * v[1]) / (L * L), 0, 1)
        dd = np.hypot(ys - (y0 + t * v[0]), xs - (x0 + t * v[1])) * sp
        keep = dd > 3.0
    vals = dist[ys[keep], xs[keep]]
    return float(np.median(vals)) if len(vals) else None


def lvidd_view_mm(endo, spacing):
    """一个切面 ED 心内膜的舒张末内径（mm）：LVIDD_DISKS 几个圆盘直径从左到右相加再除（板端 C 同一顺序）。"""
    d, _, _ = ef.diameters(endo, spacing)
    s = float(d[LVIDD_DISKS[0]])
    for k in LVIDD_DISKS[1:]:
        s = s + float(d[k])
    return s / len(LVIDD_DISKS)


def lvmi_class(lvmi, sex):
    """LVMI 增高 / 不增高（ASE 2015 线性法上限）-> dict；缺 LVMI 或性别返回 None。"""
    if lvmi is None or sex not in LVMI_LIMIT:
        return None
    code = 'increased' if lvmi > LVMI_LIMIT[sex] else 'not_increased'
    return dict(code=code, name=LVMI_CLASS[code], lvmi_limit=LVMI_LIMIT[sex], note=LVMI_CLASS_NOTE)


def la_truncated(mask):
    m = np.asarray(mask, bool)
    return bool(m[-LA_EDGE_ROWS:].any())


def la_calibrated(raw_ml):
    return raw_ml if LA_CAL is None else LA_CAL['a'] * raw_ml + LA_CAL['b']


def lavi_band(lavi):
    for hi, name in LAVI_BANDS:
        if lavi <= hi:
            return name
    return LAVI_BANDS[-1][1]


def measure(views, height_cm=None, weight_kg=None, sex=None, calibrate=True):
    """views = {'2CH': dict(spacing, ed_endo, ed_epi, es_la), '4CH': ...}（原图分辨率的布尔掩膜）-> dict。
    calibrate=False：专家掩膜（评测参照）不做 LA 校准。"""
    a, b = views['2CH'], views['4CH']
    out = {}
    out['la_truncated'] = la_truncated(a['es_la']) or la_truncated(b['es_la'])
    if out['la_truncated']:
        out['la_raw_ml'] = out['la_ml'] = None
        out['la_note'] = '左房被截断，不可测'
    else:
        out['la_raw_ml'] = biplane_volume(a['es_la'], a['spacing'], b['es_la'], b['spacing'])
        out['la_ml'] = la_calibrated(out['la_raw_ml']) if calibrate else out['la_raw_ml']
    v_epi = biplane_volume(a['ed_epi'], a['spacing'], b['ed_epi'], b['spacing'])
    v_endo = biplane_volume(a['ed_endo'], a['spacing'], b['ed_endo'], b['spacing'])
    out.update(epi_ml=v_epi, endo_ml=v_endo, lv_mass_g=DENSITY * (v_epi - v_endo))
    w = [wall_thickness_mm(v['ed_endo'], v['ed_epi'], v['spacing']) for v in (a, b)]
    out['wall_mm'] = float(np.mean([x for x in w if x is not None])) if any(x is not None for x in w) else None
    out['wall_mm_views'] = w
    li = [lvidd_view_mm(v['ed_endo'], v['spacing']) for v in (a, b)]
    out['lvidd_mm_views'] = li
    out['lvidd_mm'] = (li[0] + li[1]) / 2.0
    out['rwt'] = 2.0 * out['wall_mm'] / out['lvidd_mm'] if out['wall_mm'] is not None and out['lvidd_mm'] > 0 else None
    if height_cm and weight_kg:
        bsa = bsa_mosteller(height_cm, weight_kg)
        out['bsa_m2'] = bsa
        if out['la_ml'] is not None:
            out['lavi'] = out['la_ml'] / bsa
            out['lavi_band'] = lavi_band(out['lavi'])
        out['lvmi'] = out['lv_mass_g'] / bsa
        if sex in LVMI_2D:
            lo, hi = LVMI_2D[sex]
            out['lvmi_ref'] = dict(range=[lo, hi], above=out['lvmi'] > hi, note='ASE 2D 法（短轴面积-长度）的参考范围；本系统方法不同，仅作参考')
        out['lvmi_class'] = lvmi_class(out['lvmi'], sex)
    return out
