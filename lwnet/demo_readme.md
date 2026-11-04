# Little W-Net 本地推理

已在 Windows、Python 3.11、项目独立环境 `.venv` 中安装 CPU 推理依赖。
完整版本见 `requirements-demo-lock.txt`。此环境用于 `run_demo.py`，并非原仓库所有训练与数据工具的完整环境。
原始网络代码和 DRIVE 权重未修改。加载采用 `weights_only=True`，且严格匹配所有模型参数。

## 运行

在 PowerShell 执行，无需激活环境：

```powershell
& 'E:/PLD/lwnet/.venv/Scripts/python.exe' E:/PLD/lwnet/run_demo.py --image E:/PLD/medical_fpga/data/stare/png/images/im0001.png --label E:/PLD/medical_fpga/data/stare/png/labels_ah/im0001.png
```

换自己的图片时修改 `--image`；没有人工标注时省略 `--label`。
默认输出到 `E:/PLD/lwnet/results/demo`，重复运行会更新此目录。
可以用 `--output E:/PLD/lwnet/results/another_image` 保存到另一目录。

## 输入与输出约定

- 输入图片转 RGB，直接双线性缩放为 512x512，数值除以 255。
- 网络输入为 float32 NCHW `[1,3,512,512]`；输出 logit 为 `[1,1,512,512]`。
- 使用完整两阶段 W-Net，关闭测试时增强（TTA），CPU 线程数为 4。
- 概率由 Sigmoid 产生，默认阈值 0.4196（沿用仓库默认值，没有在本图上调参）。
- 人工标注采用最近邻缩放；Dice/IoU 在缩放后的完整画布计算，没有 FOV 排除。
- `probability.npy` 与 `logits.npy` 保存浮点结果；PNG 概率图仅为 8 位显示。
- `comparison.png` 从左至右是输入、概率、二值掩膜、叠加、AH 人工标注。
- `report.json` 保存权重哈希、输入处理、参数数量、单次预热后 CPU 前向时间和单图指标。

## 已完成验证

2026-09-23，STARE im0001 + AH 标注：权重严格加载成功；输出形状正确且所有值有限。
参数 68,482；Dice 0.744735；IoU 0.593290。
单次预热后 CPU 前向约 82 ms，仅为本次运行记录，不是稳定性能基准。

该流程直接缩放整图，不执行原脚本的 FOV 裁剪、预测恢复及翻转平均，因此不是论文复现。
这是一张 STARE 图上的跨数据集运行检查，不代表 20 张图的总体精度、量化精度或 FPGA 性能。
本次没有安装 GPU 版 PyTorch，也没有开始训练或量化。
仅用于工程教学与算法演示，不作为临床诊断依据。

## 20 张 STARE 批量浮点评估

```powershell
& 'E:/PLD/lwnet/.venv/Scripts/python.exe' E:/PLD/lwnet/evaluate_stare.py
```

输出为 `results/stare_fp32/report.md`、`summary.json`、`overview.png` 和每张图的独立结果目录。
沿用单图处理协议、AH 标注和固定阈值，不做阈值拟合。该评估会重新生成同一结果目录。
脚本检查 20 对输入完整性、原图标注尺寸、输出数值，并由保存的概率和掩膜独立核验指标。

## 完整模型与第一阶段对比

```powershell
& 'E:/PLD/lwnet/.venv/Scripts/python.exe' E:/PLD/lwnet/compare_models.py
```

比较结果、逐算子表和第一阶段独立权重保存在 `results/model_comparison/`。
部署建议见 `deployment_decision.md`。此阶段没有开始量化或 HLS 实现。

## INT8 开发参考

```powershell
# 每个节点独立量化的 v1
& 'E:/PLD/lwnet/.venv/Scripts/python.exe' E:/PLD/lwnet/quantize_stage1.py
# 保留 Conv/ReLU/BN 中间累加精度的 v2
& 'E:/PLD/lwnet/.venv/Scripts/python.exe' E:/PLD/lwnet/quantize_stage1.py --fused
# 整数边界测试
& 'E:/PLD/lwnet/.venv/Scripts/python.exe' E:/PLD/lwnet/check_integer_reference.py
```

详见 `integer_spec.md`。结果分别写入 `results/stage1_int8_v1/` 和 `results/stage1_int8_v2/`。
两版整数参考已运行，但开发留出集平均精度损失门禁均未通过；尚未开始 HLS 整网实现。

## 混合位宽敏感度分析

```powershell
& 'E:/PLD/lwnet/.venv/Scripts/python.exe' E:/PLD/lwnet/analyze_quant_sensitivity.py
& 'E:/PLD/lwnet/.venv/Scripts/python.exe' E:/PLD/lwnet/check_mixed_precision.py
```

当前选择仅提高两个激活节点到 INT16，权重保持 INT8；开发留出平均 Dice 下降 0.002355，达到暂定平均门槛。详见 `mixed_precision_spec.md`。
`results/quant_sensitivity/` 保存本版参数、层计划、逐图结果与正确 dtype 的逐节点参考张量。
