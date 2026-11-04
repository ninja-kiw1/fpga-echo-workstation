# 第一阶段 U-Net INT8 v1 验证

现有 DRIVE 第一阶段权重，512×512 RGB，固定阈值 0.4196，AH 标注、整幅画布、无 TTA。未重新训练或调整阈值。

固定随机种子 20260923：8 张校准，只使用图像激活范围；12 张未参与范围校准。全部 STARE 图此前均已参与模型选择，因此这里只是开发留出验证，不是独立测试结果。

| 子集 | 张数 | FP32 Dice | INT8 Dice | Dice 下降 | FP32 IoU | INT8 IoU |
|---|---:|---:|---:|---:|---:|---:|
| calibration | 8 | 0.779824 | 0.769096 | +0.010728 | 0.641071 | 0.628126 |
| holdout | 12 | 0.752385 | 0.739702 | +0.012684 | 0.606619 | 0.594341 |
| all | 20 | 0.763361 | 0.751459 | +0.011901 | 0.620400 | 0.607855 |

暂定平均 Dice 下降 ≤0.01：未通过（仅针对开发留出集）。

单张最大下降：im0324，0.088675。

输出 logit 的量化 scale=0.101704184465；判为血管的整数条件为 q >= -3，不需要在阈值分割硬件中计算 Sigmoid。

## 验证

- 有符号舍入与 Python 整数 oracle 一致；普通/转置卷积与标量 INT64 循环一致。
- 每层卷积累加的保守界满足 INT32，定点乘法与 BN 仿射满足 INT64。
- 使用导出后重新读入的参数做整网验证。所有图的 FP32 指标与此前基线一致。
- 卷积用 float64 执行整数操作数乘加，在绝对求和界 <2^53 下精确表示；重缩放、BN、相加等均按指定整数规则运行。这不是 PyTorch 原生 INT8 加速测速。
- im0002 的所有节点保存为 golden_tensors/*.npy；layer_errors.json 记录该图逐层量化误差及全体图像的饱和计数。

## 交付文件

- quant_plan.json：节点顺序、输入连接、scale、shift、卷积参数和累加上界。
- parameters.npz：INT8 权重、INT32 bias/乘数、INT64 BN 偏置、参考 scale；这是软件交换格式，尚不是 PS/DDR 权重 blob。
- split.json：冻结的校准/开发留出划分。
- summary.json：逐图指标与文件哈希。

## 逐图结果

| 图像 | 子集 | FP32 Dice | INT8 Dice | 下降 |
|---|---|---:|---:|---:|
| [im0001](im0001/comparison.png) | calibration | 0.7286 | 0.7088 | +0.0198 |
| [im0002](im0002/comparison.png) | holdout | 0.7613 | 0.7362 | +0.0251 |
| [im0003](im0003/comparison.png) | holdout | 0.7796 | 0.7742 | +0.0054 |
| [im0004](im0004/comparison.png) | holdout | 0.6581 | 0.5846 | +0.0735 |
| [im0005](im0005/comparison.png) | calibration | 0.7794 | 0.7737 | +0.0057 |
| [im0044](im0044/comparison.png) | holdout | 0.7959 | 0.7995 | -0.0036 |
| [im0077](im0077/comparison.png) | holdout | 0.7683 | 0.7811 | -0.0128 |
| [im0081](im0081/comparison.png) | holdout | 0.7616 | 0.7747 | -0.0131 |
| [im0082](im0082/comparison.png) | calibration | 0.7926 | 0.8011 | -0.0085 |
| [im0139](im0139/comparison.png) | holdout | 0.7446 | 0.7510 | -0.0064 |
| [im0162](im0162/comparison.png) | holdout | 0.7696 | 0.7735 | -0.0039 |
| [im0163](im0163/comparison.png) | holdout | 0.7859 | 0.7948 | -0.0089 |
| [im0235](im0235/comparison.png) | holdout | 0.8105 | 0.8137 | -0.0032 |
| [im0236](im0236/comparison.png) | calibration | 0.8185 | 0.8207 | -0.0022 |
| [im0239](im0239/comparison.png) | calibration | 0.8088 | 0.8012 | +0.0076 |
| [im0240](im0240/comparison.png) | calibration | 0.8038 | 0.7863 | +0.0176 |
| [im0255](im0255/comparison.png) | calibration | 0.8154 | 0.8151 | +0.0004 |
| [im0291](im0291/comparison.png) | holdout | 0.8005 | 0.7891 | +0.0114 |
| [im0319](im0319/comparison.png) | calibration | 0.6914 | 0.6460 | +0.0455 |
| [im0324](im0324/comparison.png) | holdout | 0.5927 | 0.5040 | +0.0887 |
