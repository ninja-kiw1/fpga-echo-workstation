# FZ3B 板端自检报告 2021-08-31 19:54:10

- 工具: `Fz3b_BoardCheck` v1.0.0
- 板端时间: 2021-08-31 19:54:08
- 图片: /root/test.ppm —— Netpbm P6 512x512 maxval=255 512x512 -> 尺寸已是 512x512
- 合计: **31 项** | PASS 31 | FAIL 0 | WARN 0 | SKIP 0

| 状态 | 检查项 | 耗时(ms) | 说明 |
| --- | --- | --- | --- |
| PASS | 载入图像 | - | Netpbm P6 512x512 maxval=255 512x512 -> 尺寸已是 512x512, 未重采样(逐字节不变) |
| PASS | 图像规格 512x512 RGB888 | - | 内部表示 786432 字节, 行 1536 字节, 像素 262144 |
| PASS | PL 指纹(VERSION@0x10) | - | 0xA0000000 读到 VERSION=0x494D5004, 是 img_proc_axil(4 像素/字, 行缓冲版) |
| PASS | img_proc 寄存器映射 | - | axilproc_init 成功, VERSION 校验通过 |
| PASS | AI 控制口 (conv/elt) | - | conv CTRL=0x00000004 (ap_idle=1), elt CTRL=0x00000004 |
| PASS | /dev/plbram (Normal-NC) | - | 内核模块已加载 —— 张量搬运会走 Normal-NC 映射(写比 /dev/mem 快一个量级, 读不变; 具体数见下方访问成本) |
| PASS | 张量 BRAM 写读回 | 10.4 | 0xB0000000 上 65536 字(256 KB)全部一致 | 写 4 ns/字, 读 155 ns/字 (plbram) |
| PASS | U-Net 子系统初始化 | - | conv=0xA0010000 elt=0xA0020000 bram=0xB0000000 均可用 |
| PASS | 阈值自动预设(Otsu) | - | Otsu 一级=82(全图黑占 60%)|排除< 34 的背景 24%|二级=112 |
| PASS | 阈值自动预设(边缘分位) | - | 边缘阈值(自动) = 42:Sobel 幅度前 8% 判为边缘、画白, 其余保持原图 |
| PASS | mode0 灰度归一化 | 21.0 | mismatch 0/786432 | 参考 min/max=0/197, PL 报 0/197 | 非灰度像素=0(应为 0) |
| PASS | mode1 阈值分割 | 17.5 | mismatch 0/786432 | 参考 min/max=0/197, PL 报 0/197 | 非 0/255 像素=0 |
| PASS | mode2 边缘增强 | 17.7 | mismatch 0/786432 | 参考 min/max=0/197, PL 报 0/197 | 非灰度像素=0(应为 0) |
| PASS | mode3 伪彩色映射 | 50.5 | mismatch 0/786432 | 参考 min/max=0/197, PL 报 0/197 | 非灰度像素=262144/262144 |
| PASS | mode4 归一化+伪彩 | 52.1 | mismatch 0/786432 | 参考 min/max=0/197, PL 报 0/197 | 非灰度像素=262144/262144 |
| PASS | mode6 第1步 PL 边缘图 | 17.6 | PL(模式2) 边缘图与参考逐字节一致 | min/max=0/197 vs PL 0/197 |
| PASS | mode6 第2步 边缘叠加 | - | 叠加结果与参考逐字节一致(阈值 42), 画白 22162 像素 (8.5%) |
| PASS | mode5 原图直通 | - | 输出与输入逐字节一致(不经 PL, 验证链路没有被改坏) |
| PASS | AI 整网(血管分割) | 340.5 | 输入 原始RGB 512x512 -> 输出 512x512 | logit[-107..100] | vessels=16458/262144 (6.28%) |
| PASS | AI 耗时分解 | - | ap(PL计算)=218 ms | put(PS->BRAM)=61 | get(BRAM->PS)=70 | zero=0 | 条带=853 | 搬运 7362464+5341184 字 |
| PASS | AI vs golden(逐字节) | - | shift=0 (512x512) mismatch 0/262144, 与 HLS golden 完全一致 |
| PASS | 掩膜 vs 独立复算 | - | 512x512 掩膜逐字节一致, 血管像素 16458 (连通块 119) |
| PASS | 不变量: 掩膜只 0/255 | - | 786432 字节全部合法 |
| PASS | 叠加 vs 独立复算 | - | 512x512 叠加图逐字节一致 |
| PASS | 不变量: 叠加红 == 掩膜白 | - | 16458 个像素位置完全对应 |
| PASS | 统计块自洽(64B) | - | magic/version/vessels/total/permille 都对 (permille=62) |
| PASS | 访问成本 PL BRAM(plbram, Normal-NC) | 9.6 | 写 1.9 ns/次, 读 145.3 ns/次 (各 65536 次) |
| PASS | 访问成本 PL BRAM(dev/mem, Device) | 10.8 | 写 19.9 ns/次, 读 145.2 ns/次 (各 65536 次) |
| PASS | 访问成本 PS DDR(dev/mem) | 8.1 | 写 9.5 ns/次, 读 114.4 ns/次 —— 读慢是 Device 映射语义, 与 PL 通路无关 |
| PASS | 访问成本 对照(可缓存 malloc) | - | 写 1.94 ns/次, 读 3.97 ns/次 —— 比 Device 映射快几十倍, 所以'减字节数'没意义, 要减的是访问次数 |
| PASS | baseline 对比 | - | 与 11 项历史耗时做了对比(慢 20% 以上标 WARN) |

## 备注

AI 视图已存图: ai_mask.ppm / ai_overlay.ppm / ai_source.ppm

