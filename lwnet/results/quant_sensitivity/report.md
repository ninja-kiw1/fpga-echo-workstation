# 量化逐节点敏感度与混合位宽验证

所有候选均保持原权重、校准范围、模型结构和阈值。仅将一个存储节点的激活从 INT8 提高到 INT16，池化等保尺度节点随输入继承位宽。权重仍全部 INT8。

先在预先固定的两张校准图上逐节点筛选，再在全部 8 张校准图上确认前三名及前两名组合，冻结校准集平均 Dice 最优方案后才运行 12 张开发留出图。此结论不是独立测试性能。

两张筛选图：im0001, im0319。

选择节点：first_block_2, up_path_0_conv_layer_block_2。

| 子集 | 张数 | FP32 Dice | 混合位宽 Dice | 下降 |
|---|---:|---:|---:|---:|
| calibration | 8 | 0.779824 | 0.779411 | +0.000413 |
| holdout | 12 | 0.752385 | 0.750030 | +0.002355 |
| all | 20 | 0.763361 | 0.761782 | +0.001578 |

暂定开发留出平均下降≤0.01：通过。

最差单张：im0324，下降 0.025151。平均门禁不保证每张图都满足同样误差。

## 硬件影响

输出 scale=0.101704184465，整数分割条件 q>=-3。

提升位宽不改变卷积 MAC 数。若 INT16 节点后接卷积，则该层需要 INT16×INT8 乘法；不是免费增加精度。INT32 累加和 INT64 重缩放静态范围检查仍必须通过。

- first_block_2：新增单张张量存储 2,097,152 字节；后继 [{'name': 'first_block_5', 'op': 'ConvAffine'}]。
- up_path_0_conv_layer_block_2：新增单张张量存储 1,048,576 字节；后继 [{'name': 'up_path_0_conv_layer_block_5', 'op': 'ConvAffine'}]。

这些字节数按单张张量计算，不能直接当作整网峰值 BRAM/DDR 或每帧实际流量；是否落 DDR、读几次由调度决定。

## 筛选排名（两张校准图，不是最终精度）

| 节点 | 平均 Dice | 相比 v2 增益 |
|---|---:|---:|
| first_block_2 | 0.705506 | +0.016299 |
| up_path_0_conv_layer_block_2 | 0.691915 | +0.002707 |
| down_path_0_block_2 | 0.691555 | +0.002348 |
| up_path_1_conv_layer_block_2 | 0.690777 | +0.001570 |
| down_path_1_block_2 | 0.690549 | +0.001342 |
| down_path_0_block_5 | 0.690221 | +0.001014 |
| up_path_0_conv_layer_block_5 | 0.689962 | +0.000755 |
| add | 0.689645 | +0.000437 |
| up_path_1_conv_layer_block_5 | 0.689504 | +0.000297 |
| cat | 0.689301 | +0.000094 |
| up_path_1_conv_bridge_layer_block_2 | 0.689189 | -0.000019 |
| up_path_0_conv_bridge_layer_block_2 | 0.689045 | -0.000162 |
| add_3 | 0.688855 | -0.000352 |
| down_path_1_shortcut_1 | 0.688704 | -0.000503 |
| up_path_0_conv_layer_shortcut_1 | 0.688693 | -0.000514 |
| up_path_1_conv_layer_shortcut_1 | 0.688677 | -0.000530 |
| add_4 | 0.688609 | -0.000598 |
| up_path_0_up_layer_block_0 | 0.688596 | -0.000611 |
| down_path_1_block_5 | 0.688523 | -0.000684 |
| first_shortcut_1 | 0.688136 | -0.001071 |
| add_2 | 0.687999 | -0.001208 |
| cat_1 | 0.687324 | -0.001883 |
| up_path_1_up_layer_block_0 | 0.687184 | -0.002023 |
| final | 0.687082 | -0.002126 |
| add_1 | 0.686669 | -0.002538 |
| down_path_0_shortcut_1 | 0.686120 | -0.003087 |
| first_block_5 | 0.684943 | -0.004264 |

## 8 张校准图确认

| 节点组合 | Dice |
|---|---:|
| v2 baseline | 0.773454 |
| first_block_2 | 0.779250 |
| up_path_0_conv_layer_block_2 | 0.773812 |
| down_path_0_block_2 | 0.774966 |
| first_block_2, up_path_0_conv_layer_block_2 | 0.779411 |

## 逐图结果

| 图像 | 子集 | FP32 | v2 INT8 | 混合位宽 |
|---|---|---:|---:|---:|
| [im0001](im0001/comparison.png) | calibration | 0.7286 | 0.7153 | 0.7300 |
| [im0002](im0002/comparison.png) | holdout | 0.7613 | 0.7549 | 0.7647 |
| [im0003](im0003/comparison.png) | holdout | 0.7796 | 0.7779 | 0.7774 |
| [im0004](im0004/comparison.png) | holdout | 0.6581 | 0.6013 | 0.6474 |
| [im0005](im0005/comparison.png) | calibration | 0.7794 | 0.7759 | 0.7761 |
| [im0044](im0044/comparison.png) | holdout | 0.7959 | 0.7989 | 0.7970 |
| [im0077](im0077/comparison.png) | holdout | 0.7683 | 0.7775 | 0.7723 |
| [im0081](im0081/comparison.png) | holdout | 0.7616 | 0.7691 | 0.7642 |
| [im0082](im0082/comparison.png) | calibration | 0.7926 | 0.7998 | 0.7965 |
| [im0139](im0139/comparison.png) | holdout | 0.7446 | 0.7424 | 0.7407 |
| [im0162](im0162/comparison.png) | holdout | 0.7696 | 0.7730 | 0.7707 |
| [im0163](im0163/comparison.png) | holdout | 0.7859 | 0.7944 | 0.7903 |
| [im0235](im0235/comparison.png) | holdout | 0.8105 | 0.8127 | 0.8110 |
| [im0236](im0236/comparison.png) | calibration | 0.8185 | 0.8204 | 0.8196 |
| [im0239](im0239/comparison.png) | calibration | 0.8088 | 0.8016 | 0.8080 |
| [im0240](im0240/comparison.png) | calibration | 0.8038 | 0.7962 | 0.8055 |
| [im0255](im0255/comparison.png) | calibration | 0.8154 | 0.8153 | 0.8161 |
| [im0291](im0291/comparison.png) | holdout | 0.8005 | 0.7913 | 0.7971 |
| [im0319](im0319/comparison.png) | calibration | 0.6914 | 0.6631 | 0.6835 |
| [im0324](im0324/comparison.png) | holdout | 0.5927 | 0.5106 | 0.5676 |
