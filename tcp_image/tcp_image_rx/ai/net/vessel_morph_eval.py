# -*- coding: utf-8 -*-
"""vessel_morph_eval.py —— 形态指标靠不靠谱：模型掩膜 vs 人工标注，并以两位专家之间的差异为参照。

对 20 张 STARE 图分别在三种掩膜上算指标（vessel_morph_ref.py，与板端 C 逐项一致）：
  * 模型：部署版整数模型的输出（logit >= -3，512x512，即板上实际产出的掩膜）；
  * AH、VK：两位专家的人工标注（700x605 最近邻缩放到 512x512）。
每个指标给出：三者的均值、模型与 AH 的 Pearson 相关和平均相对偏差，
以及 VK 与 AH 的同样两个数（人与人之间本来就有的差异，作为“够不够好”的参照）。

20 张图都参与过模型选择（量化校准、方案比较），所以这是开发评估，不是独立测试。
用法：python vessel_morph_eval.py [输出.md]（默认 ../../docs/vessel_morphology_metrics_eval.md）
"""

import json
import os
import sys

import numpy as np
from PIL import Image

import vessel_morph_ref as ref

HERE = os.path.dirname(os.path.abspath(__file__))
MED = os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "medical_fpga"))
LOGITS = os.path.join(MED, "deployment", "unet_network", "vectors", "images")
STARE = os.path.join(MED, "data", "stare", "png")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "docs", "vessel_morphology_metrics_eval.md")

METRICS = [  # (字段, 中文名, 换算)
    ("length", "血管总长度（px）", lambda r: r["length_x10"] / 10.0),
    ("caliber", "平均管径（px）", lambda r: r["caliber_x100"] / 100.0),
    ("branches", "分支点数", lambda r: r["branches"]),
    ("endpoints", "端点数", lambda r: r["endpoints"]),
    ("tortuosity", "迂曲度", lambda r: r["tortuosity_x1000"] / 1000.0),
    ("fractal", "分形维数", lambda r: r["fractal_x1000"] / 1000.0),
    ("density", "FOV 内血管密度（%）", lambda r: r["density_x1000"] / 10.0),
]


def load_label(kind, name):
    img = Image.open(os.path.join(STARE, kind, name + ".png")).resize((512, 512), Image.NEAREST)
    return np.asarray(img) > 127


def pearson(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def rel_dev(a, ref_v):
    a, ref_v = np.asarray(a, float), np.asarray(ref_v, float)
    ok = ref_v != 0
    return float(np.mean(np.abs(a[ok] - ref_v[ok]) / np.abs(ref_v[ok])) * 100.0)


def main():
    names = sorted(d for d in os.listdir(LOGITS) if os.path.isdir(os.path.join(LOGITS, d)))
    rows = {k: {"model": [], "ah": [], "vk": []} for k, _, _ in METRICS}
    per_image = []
    for n in names:
        lg = np.fromfile(os.path.join(LOGITS, n, "final_actual.bin"), np.int8).reshape(512, 512)
        rgb = np.asarray(Image.open(os.path.join(STARE, "images", n + ".png")).convert("RGB")
                         .resize((512, 512), Image.BILINEAR))
        fov = ref.fov_from_rgb(rgb, 512, 512)
        res = {"model": ref.analyze(lg >= -3, fov), "ah": ref.analyze(load_label("labels_ah", n), fov),
               "vk": ref.analyze(load_label("labels_vk", n), fov)}
        per_image.append({"image": n, **res})
        for k, _, f in METRICS:
            for src in ("model", "ah", "vk"):
                rows[k][src].append(f(res[src]))
        print("%s  模型/AH/VK 长度 %.0f / %.0f / %.0f" % (n, *(r["length_x10"] / 10 for r in res.values())))

    lines = [
        "# 血管形态指标：模型掩膜 vs 人工标注",
        "",
        "由 `ai/net/vessel_morph_eval.py` 生成。指标定义见 `src/vessel_morph.h`；计算用 "
        "`ai/net/vessel_morph_ref.py`（与板端 C 实现逐项一致，见 `vessel_morph_check.py`）。",
        "",
        "- 样本：STARE 20 张，512×512；模型掩膜为部署版整数模型输出（logit ≥ −3，与实板逐字节一致的那份）；",
        "  AH、VK 为两位专家标注（最近邻缩放）。",
        "- “VK vs AH” 是两位专家之间的差异，作为参照：模型与 AH 的差距若接近它，说明指标在模型掩膜上可用。",
        "- 20 张图都参与过模型选择，这是开发评估，不是独立测试；指标仅用于工程教学与算法演示，不作临床诊断依据。",
        "",
        "| 指标 | 模型均值 | AH 均值 | VK 均值 | r(模型, AH) | r(VK, AH) | 模型 vs AH 平均相对偏差 | VK vs AH 平均相对偏差 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    summary = {}
    for k, label, _ in METRICS:
        m, a, v = rows[k]["model"], rows[k]["ah"], rows[k]["vk"]
        s = dict(model_mean=float(np.mean(m)), ah_mean=float(np.mean(a)), vk_mean=float(np.mean(v)),
                 r_model_ah=pearson(m, a), r_vk_ah=pearson(v, a),
                 dev_model_ah=rel_dev(m, a), dev_vk_ah=rel_dev(v, a))
        summary[k] = s
        lines.append("| %s | %.2f | %.2f | %.2f | %.2f | %.2f | %.1f%% | %.1f%% |"
                     % (label, s["model_mean"], s["ah_mean"], s["vk_mean"], s["r_model_ah"],
                        s["r_vk_ah"], s["dev_model_ah"], s["dev_vk_ah"]))
    lines += ["", "逐图数值见同名 `.json`。", ""]
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    with open(os.path.splitext(OUT)[0] + ".json", "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "per_image": per_image}, fh, ensure_ascii=False, indent=1)
    print("\n".join(lines[9:]))
    print("wrote", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
