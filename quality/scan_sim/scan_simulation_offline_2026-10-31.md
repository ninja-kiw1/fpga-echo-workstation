# 模拟扫查（离线版，2026-10-31）

目的：演示"医生扫查时，质量灯提示调探头、切面提示转到四腔"。只在 PC 离线做，板子和 11-01 演示流程都没动。
只用 CAMUS 训练集 / 验证集，测试集没碰。CAMUS 的许可是 CC BY-NC-SA 4.0（非商业，须引用 Leclerc 2019），派生文件只放在本地。

## 成品
- `results/模拟扫查_质量灯.mp4`：10 s，50 fps，960×540
- `results/shot_*.png`：每段末尾各截一张

剧本是 4 段，每段 2 s，最后一段放两遍。每段在对应的（切面, 专家质量）组里取打分中位数那段，规则事先定好，不挑片子：

| 段 | 片段（CAMUS 验证集） | 质量灯 | 切面判断 |
|---|---|---|---|
| 1 探头在两腔位置 | patient0245 A2C Good | 绿 | 不是四腔 ✓ |
| 2 转到四腔，画面差 | patient0292 A4C Poor | 红 | 四腔 ✓ |
| 3 调整中 | patient0305 A4C Medium | 黄 | **不是四腔 ✗（误判：右室分得太小）** |
| 4 到位 | patient0255 A4C Good | 绿 | 四腔 ✓ |

## 用的方法
- **质量灯**：10-24 在 CAMUS 上验证过的那版。F1 左室程序 128 档的整数 logits，加上 `features.sequence_features`，再用 `quality_camus.json` 的 portable 打分（CAMUS 训练集拟合，验证集差片子 AUC 0.911）。这里改成窗口版，每 0.1 s 用最近 1 s 的帧更新一次。
- **切面**：右室程序 `rv_pl1_5` 和 F1 左室程序在同一张 128 灰度图上分割，取右室最大连通块面积 / 左室面积的中位数，≥ 0.211 判为四腔。门限是训练集上准确率最高的值。**验证集准确率 0.92，AUC 0.96**，训练集准确率 0.873。不用新模型。

## 没走通的路（要记住）
板上现场模式那条链是"录屏裁切到 112 → 程序 16 → 板上 C 质量特征 → EchoNet 标准化"。把它直接用在 CAMUS 上（`prep_val.py` / `run_val.py` / `restd.py`）：
- 用 EchoNet 标准化：差片子 AUC 0.75，但好片子 40/50 亮红灯。
- 改用 CAMUS 训练集重新标准化：AUC 只有 0.63。

结论：**板上现在的质量灯只适合 EchoNet 这类画面**，换成别的机器、别的画面要重新标定，或者改用 F1 口径。这条链上的切面规则：验证集 AUC 0.914，准确率 0.84。

## 还没做（11-01 之后）
- 移植到板上：现场模式里每 0.1 s 出一次滚动质量灯和切面提示。需要在现场模式里跑 F1 + 右室程序，或者给程序 16 重新标定。
- 真正的扫查数据：EchoWorld 的探头位姿数据不公开；CACTUS 的许可还没确认。

## 复现
```
C:\Users\21888\anaconda3\envs\srtp-vision\python.exe -I prep_val.py val|train   # 程序 16 链（对照）
E:\PLD\skin\.venv\Scripts\python.exe -I run_val.py val|train
E:\PLD\skin\.venv\Scripts\python.exe -I restd.py
E:\PLD\skin\.venv\Scripts\python.exe -I stream_camus.py                        # 成品用的 CAMUS 口径
C:\Users\21888\anaconda3\envs\srtp-vision\python.exe -I render.py
```
