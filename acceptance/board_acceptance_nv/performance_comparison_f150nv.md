# f150 → f150nv（去掉 vis_layer）前后对比

前 = f150 + 服务端 v5.26；后 = f150nv + v5.26（同一服务端二进制）。计时均为板上实测均值（首个冷启动样本剔除）。

## 1. PL 资源（布线后）

| 模块 | LUT 前 | LUT 后 | BRAM36 前 | 后 | BRAM18 前 | 后 | DSP 前 | 后 |
|---|---|---|---|---|---|---|---|---|
| system_wrapper | 59801 | 53203 | 160 | 128 | 24 | 17 | 306 | 290 |
| system_i | 59801 | 53203 | 160 | 128 | 24 | 17 | 306 | 290 |
| ai_data_ic | 2218 | 2031 | 0 | 0 | 0 | 0 | 0 | 0 |
| bram_ctrl | 529 | 524 | 0 | 0 | 0 | 0 | 0 | 0 |
| bram_gen | 174 | 170 | 64 | 64 | 0 | 0 | 0 | 0 |
| conv_layer_0 | 27101 | 27028 | 39 | 39 | 0 | 0 | 185 | 185 |
| ctl_xbar | 203 | 159 | 0 | 0 | 0 | 0 | 0 | 0 |
| eltwise_layer_0 | 13859 | 13842 | 20 | 20 | 2 | 2 | 76 | 76 |
| img_proc_axil_0 | 661 | 656 | 1 | 1 | 1 | 1 | 1 | 1 |
| pc_axil | 407 | 404 | 0 | 0 | 0 | 0 | 0 | 0 |
| pp_layer_0 | 8395 | 8375 | 4 | 4 | 14 | 14 | 28 | 28 |
| vis_layer_0 | 6250 | 0 | 32 | 0 | 7 | 0 | 16 | 0 |

时序（按板上实际 pl0_ref 101.376 MHz → 152.15 MHz 复核）：f150 WNS +0.470 / WHS +0.013；f150nv WNS +0.486 / WHS +0.015。

## 2. U-Net 直接调用（strict_net_test，逐位比对通过）

| 模型_分辨率 | 指标 | 前 (ms) | 后 (ms) | 变化 |
|---|---|---|---|---|
| m0_32 | ap | 1.44 | 1.44 | +0.0% |
| m0_32 | put | 0.24 | 0.31 | +29.2% |
| m0_32 | get | 0.78 | 0.84 | +7.7% |
| m0_32 | run | 2.71 | 2.82 | +4.1% |
| m0_512 | ap | 203.31 | 203.50 | +0.1% |
| m0_512 | put | 60.97 | 60.08 | -1.5% |
| m0_512 | get | 62.71 | 62.94 | +0.4% |
| m0_512 | run | 292.64 | 292.34 | -0.1% |
| m1_128 | ap | 13.94 | 14.01 | +0.5% |
| m1_128 | put | 3.88 | 4.20 | +8.2% |
| m1_128 | get | 4.61 | 4.61 | +0.0% |
| m1_128 | run | 22.06 | 22.42 | +1.6% |
| m2_128 | ap | 13.96 | 14.05 | +0.6% |
| m2_128 | put | 4.02 | 3.97 | -1.2% |
| m2_128 | get | 4.71 | 4.71 | +0.0% |
| m2_128 | run | 22.65 | 22.42 | -1.0% |

逐层 ap 合计（ms）：fundus512 前 203.32 / 后 203.76；skin128 前 13.99 / 后 13.93

## 3. TCP 往返（PC → 板 → PC）

| 用例 | 指标 | 前 | 后 | 变化 |
|---|---|---|---|---|
| echo128 | ai_ms | 22.38 | 22.40 | +0.1% |
| echo128 | total_ms | 75.32 | 76.42 | +1.5% |
| echo128 | total_p99 | 76.00 | 78.02 | +2.7% |
| fundus512 | ai_ms | 296.11 | 294.63 | -0.5% |
| fundus512 | total_ms | 363.63 | 360.69 | -0.8% |
| fundus512 | total_p99 | 364.94 | 367.20 | +0.6% |
| skin128 | ai_ms | 22.55 | 22.33 | -1.0% |
| skin128 | total_ms | 76.09 | 75.84 | -0.3% |
| skin128 | total_p99 | 76.62 | 76.27 | -0.5% |

## 4. PL 后处理 pp_layer（182 例向量，逐位一致）

| 指标 | 前 | 后 | 变化 |
|---|---|---|---|
| 平均每例 PL 时间 (ms) | 0.891 | 0.900 | +1.0% |

## 5. BoardCheck 计时项（ms）

| 项 | 前 | 后 | 变化 |
|---|---|---|---|
| 张量 BRAM 写读回 | 10.50 | 10.40 | -1.0% |
| mode0 灰度归一化 | 20.50 | 21.00 | +2.4% |
| mode1 阈值分割 | 18.00 | 17.50 | -2.8% |
| mode2 边缘增强 | 18.00 | 17.70 | -1.7% |
| mode3 伪彩色映射 | 50.90 | 50.50 | -0.8% |
| mode4 归一化+伪彩 | 53.50 | 52.10 | -2.6% |
| mode6 第1步 PL 边缘图 | 18.10 | 17.60 | -2.8% |
| AI 整网(血管分割) | 325.60 | 340.50 | +4.6% |
| 访问成本 PL BRAM(plbram, Normal-NC) | 10.00 | 9.60 | -4.0% |
| 访问成本 PL BRAM(dev/mem, Device) | 11.20 | 10.80 | -3.6% |
| 访问成本 PS DDR(dev/mem) | 7.50 | 8.10 | +8.0% |
| 512 ap/put/get | 221/66/67 | 218/61/70 | - |

## 6. 心脏现场模式（v5.26 run_board.py，50 fps 信号源）

前 = board_acceptance_v526/results_post_activate_9851（f150，11-03 上午）。延迟定义：采到 → 掩膜 + 容积就绪。

| 用例 | 指标 | 前 | 后 | 变化 |
|---|---|---|---|---|
| C0 | 延迟 p50 (ms) | 36.76 | None | - |
| C0 | 延迟 p99 (ms) | 74.66 | None | - |
| C0 | 延迟 max (ms) | 75.143 | None | - |
| C0 | pl_ms_per_key | 23.299 | None | - |
| C0 | track_ms | 0.878 | None | - |
| C0 | ap_ms | 2958.563 | None | - |
| C0 | put_ms | 849.965 | None | - |
| C0 | get_ms | 1012.422 | None | - |
| C0 | EF | 74.16554495170003 | None | 不同 |
| C1 | 延迟 p50 (ms) | 32.298 | None | - |
| C1 | 延迟 p99 (ms) | 74.341 | None | - |
| C1 | 延迟 max (ms) | 75.086 | None | - |
| C1 | pl_ms_per_key | 23.172 | None | - |
| C1 | track_ms | 0.881 | None | - |
| C1 | ap_ms | 2373.178 | None | - |
| C1 | put_ms | 681.523 | None | - |
| C1 | get_ms | 794.704 | None | - |
| C1 | EF | 74.16554495170003 | None | 不同 |
| C9 | 延迟 p50 (ms) | 32.318 | None | - |
| C9 | 延迟 p99 (ms) | 74.325 | None | - |
| C9 | 延迟 max (ms) | 75.063 | None | - |
| C9 | pl_ms_per_key | 23.156 | None | - |
| C9 | track_ms | 0.882 | None | - |
| C9 | ap_ms | 2371.673 | None | - |
| C9 | put_ms | 683.726 | None | - |
| C9 | get_ms | 811.263 | None | - |
| C9 | EF | 79.7599623172307 | None | 不同 |
| L0 | 延迟 p50 (ms) | 29.328 | None | - |
| L0 | 延迟 p99 (ms) | 67.899 | None | - |
| L0 | 延迟 max (ms) | 68.682 | None | - |
| L0 | pl_ms_per_key | 23.586 | None | - |
| L0 | track_ms | 0.792 | None | - |
| L0 | ap_ms | 609.498 | None | - |
| L0 | put_ms | 174.676 | None | - |
| L0 | get_ms | 201.172 | None | - |
| L0 | EF | 70.31050307620167 | None | 不同 |
| L1 | 延迟 p50 (ms) | 28.904 | None | - |
| L1 | 延迟 p99 (ms) | 66.847 | None | - |
| L1 | 延迟 max (ms) | 66.905 | None | - |
| L1 | pl_ms_per_key | 22.92 | None | - |
| L1 | track_ms | 0.763 | None | - |
| L1 | ap_ms | 684.027 | None | - |
| L1 | put_ms | 193.553 | None | - |
| L1 | get_ms | 227.412 | None | - |
| L1 | EF | 70.31050307620167 | None | 不同 |
| L2 | 延迟 p50 (ms) | 28.894 | None | - |
| L2 | 延迟 p99 (ms) | 66.704 | None | - |
| L2 | 延迟 max (ms) | 70.482 | None | - |
| L2 | pl_ms_per_key | 22.937 | None | - |
| L2 | track_ms | 0.768 | None | - |
| L2 | ap_ms | 837.682 | None | - |
| L2 | put_ms | 238.0 | None | - |
| L2 | get_ms | 278.142 | None | - |
| L2 | EF | 70.31050307620167 | None | 不同 |
| S1 | 延迟 p50 (ms) | 31.875 | None | - |
| S1 | 延迟 p99 (ms) | 74.571 | None | - |
| S1 | 延迟 max (ms) | 83.584 | None | - |
| S1 | pl_ms_per_key | 23.288 | None | - |
| S1 | track_ms | 0.82 | None | - |
| S1 | ap_ms | 2915.944 | None | - |
| S1 | put_ms | 835.995 | None | - |
| S1 | get_ms | 998.93 | None | - |
| S1 | EF | 35.10343770603466 | None | 不同 |
| V1 | 延迟 p50 (ms) | 3.462 | None | - |
| V1 | 延迟 p99 (ms) | 60.044 | None | - |
| V1 | 延迟 max (ms) | 61.86 | None | - |
| V1 | pl_ms_per_key | 22.929 | None | - |
| V1 | track_ms | 0.932 | None | - |
| V1 | ap_ms | 1186.057 | None | - |
| V1 | put_ms | 335.054 | None | - |
| V1 | get_ms | 396.48 | None | - |
| V1 | EF | 45.54484107798328 | None | 不同 |

## 7. 眼底大图分块（JOB_TILE，叠加从 PL vis_layer 改走 PS）

前 = board_acceptance_v524/tile.json（f150 + v5.24，叠加在 PL）；后 = 本次（叠加在 PS）。

| 图 | 指标 | 前 | 后 | 变化 |
|---|---|---|---|---|
| stare_im0077_w600 | job_ms | 85.712 | None | - |
| stare_im0077_w600 | unet_ms | 81.77 | None | - |
| stare_im0077_w600 | ovl_ms | 2.38 | None | - |
| stare_im0077_w600 | vis_pl_ms | 1.143 | None | - |
| stare_im0077_w600 | pp_pl_ms | 0.481 | None | - |
| stare_im0077_w1200 | job_ms | 150.302 | None | - |
| stare_im0077_w1200 | unet_ms | 144.038 | None | - |
| stare_im0077_w1200 | ovl_ms | 3.84 | None | - |
| stare_im0077_w1200 | vis_pl_ms | 1.713 | None | - |
| stare_im0077_w1200 | pp_pl_ms | 0.659 | None | - |
| hrf_01_h_w600 | job_ms | 31.957 | None | - |
| hrf_01_h_w600 | unet_ms | 30.911 | None | - |
| hrf_01_h_w600 | ovl_ms | 0.572 | None | - |
| hrf_01_h_w600 | vis_pl_ms | 0.257 | None | - |
| hrf_01_h_w600 | pp_pl_ms | 0.114 | None | - |
| hrf_01_h_w1200 | job_ms | 91.085 | None | - |
| hrf_01_h_w1200 | unet_ms | 86.643 | None | - |
| hrf_01_h_w1200 | ovl_ms | 2.71 | None | - |
| hrf_01_h_w1200 | vis_pl_ms | 1.231 | None | - |
| hrf_01_h_w1200 | pp_pl_ms | 0.643 | None | - |
| hrf_01_h_w2400 | job_ms | 367.048 | None | - |
| hrf_01_h_w2400 | unet_ms | 338.805 | None | - |
| hrf_01_h_w2400 | ovl_ms | 13.165 | None | - |
| hrf_01_h_w2400 | vis_pl_ms | 5.965 | None | - |
| hrf_01_h_w2400 | pp_pl_ms | 3.609 | None | - |

## 8. 回归

回归总判定：None；组：echo PASS

心脏现场 host_check：None
