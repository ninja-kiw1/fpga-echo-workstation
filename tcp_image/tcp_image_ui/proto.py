# -*- coding: utf-8 -*-
"""proto.py —— 图像传输协议（PC 端 GUI 用的自包含实现）。

本文件是**自包含**的：不 import tcp_image_rx 的任何东西，方便这个工具单独拷走使用。
里面每个常量都必须与板端 `tcp_image_rx/src/image_proto.h` 保持一致；
为了能立刻发现漂移，模块在导入时用标准校验向量自检 CRC16 与各字段算术。

协议（与 image_proto.h 一致）
----------------------------
 图像    : 512 x 512，RGB888（每像素 R,G,B 三字节），整幅 786 432 字节
 传输    : 切成 512 个"行包"，每包正好一行像素

 行包布局（全部小端）::

     偏移  长度  字段      说明
     ----  ----  --------  ------------------------------------------
      0     2    row       行号 0..511
      2     2    length    负载长度，固定 1536
      4     2    crc16     CRC-16/CCITT-FALSE，对后面 1536 字节负载计算
      6    1536  payload   该行 512 个像素，序 R,G,B

 单包 1542 字节，整幅 512 * 1542 = 789 504 字节。
"""

import binascii
import struct

# --------------------------------------------------------------------------
# 图像参数（对应 image_proto.h）
# --------------------------------------------------------------------------
IMG_WIDTH = 512
IMG_HEIGHT = 512
IMG_BYTES_PER_PIX = 3                      # RGB888
IMG_ROW_BYTES = IMG_WIDTH * IMG_BYTES_PER_PIX      # 1536
IMG_TOTAL_BYTES = IMG_ROW_BYTES * IMG_HEIGHT       # 786432

PKT_HDR_BYTES = 6
PKT_PAYLOAD_BYTES = IMG_ROW_BYTES
PKT_TOTAL_BYTES = PKT_HDR_BYTES + PKT_PAYLOAD_BYTES   # 1542
PKT_COUNT = IMG_HEIGHT                                # 512
STREAM_TOTAL_BYTES = PKT_TOTAL_BYTES * PKT_COUNT      # 789504

# --------------------------------------------------------------------------
# 控制头（v2 新增）：整幅图像流最前面 4 字节，用来告诉板子用哪种处理模式。
#
#     偏移  长度  字段    说明
#     ----  ----  ------  ------------------------------------
#      0     2    magic   固定 0x4D43（内存小端存放即 ASCII "CM"）
#      2     2    mode    处理模式 0..4，见下表
#
#   mode: 0 灰度归一化 / 1 阈值分割 / 2 边缘增强 / 3 伪彩色映射 / 4 组合 / 5 原图直通
#         （5 = 直通：板子不经 PL，直接把收到的原图回显回来）
#
# 板子回给 PC 的仍是**纯图像流**（512 个行包，不带控制头）—— PC 自己知道发的是哪个模式。
# --------------------------------------------------------------------------
CTRL_MAGIC = 0x4D43
CTRL_BYTES = 4
WIRE_TOTAL_BYTES = CTRL_BYTES + STREAM_TOTAL_BYTES    # 789508，v2 帧总长

# --------------------------------------------------------------------------
# 控制头 v3（2026-10-01 新增 flags / aiparam，8 字节）
#
#     偏移  长度  字段      说明
#     ----  ----  --------  ------------------------------------------
#      0     2    magic     固定 0x4D44（内存小端即 ASCII "DM"）
#      2     2    mode      预处理模式 0..5（语义同 v2）
#      4     2    flags     回传内容位图，见 FLAG_*
#      6     2    aiparam   AI 参数，见 AIPARAM_*
#
#  板子**先读 2 字节 magic 再决定读几字节**，所以发 v2 头的旧客户端照旧能用。
#  下行仍是图像流，但按 flags 可能：
#    * 先来一个 64 字节统计块（FLAG_STATS）
#    * 再来 0..3 个图像流，顺序固定 = 预处理图 -> 叠加图 -> 掩膜图
# --------------------------------------------------------------------------
CTRL_MAGIC_V3 = 0x4D44
CTRL_BYTES_V3 = 8
WIRE_TOTAL_BYTES_V3 = CTRL_BYTES_V3 + STREAM_TOTAL_BYTES

# --------------------------------------------------------------------------
# 控制头 v4（2026-10-02 新增逐帧阈值，12 字节）
#
#     偏移  长度  字段      说明
#     ----  ----  --------  ------------------------------------------
#      0     2    magic     固定 0x4D45（内存小端即 ASCII "EM"）
#      2     2    mode      预处理模式 0..5
#      4     2    flags     回传内容位图（同 v3）
#      6     2    aiparam   AI 参数（同 v3）
#      8     2    thr       阈值分割(mode 1)的门限 0..255
#     10     2    reserved  保留(写 0)
#
#  v2/v3 里阈值只是**板端命令行**选项，上位机够不着；"阈值分割"要按图调，
#  所以 v4 把它放进控制头。板子仍然是"先读 2 字节 magic 再决定读几字节"，
#  三种头都认。
#  注：伪彩映射(mode 3)在 PL 里是直接 jet[gray]，没有阈值参数，不在协议内。
# --------------------------------------------------------------------------
CTRL_MAGIC_V4 = 0x4D45
CTRL_BYTES_V4 = 12
WIRE_TOTAL_BYTES_V4 = CTRL_BYTES_V4 + STREAM_TOTAL_BYTES
CTRL_THR_DEFAULT = 128          # 不带 thr 的老头 / 板端 --thr 的默认值
CTRL_THR_MIN = 0
CTRL_THR_MAX = 255
_CTRL_FMT_V4 = "<HHHHHH"        # magic, mode, flags, aiparam, thr, reserved

# --------------------------------------------------------------------------
# 控制头 v5（2026-10-07，ROI 局部推理，20 字节）= v4 的 12 字节 + roi_x, roi_y, roi_w, roi_h
#   magic 0x4D46（内存小端即 ASCII "FM"）；roi_w 或 roi_h 为 0 = 不用 ROI。
#   有 ROI 时板端只在 roi_crop() 给出的裁剪窗口上跑整网，ROI 内结果贴回 512x512
#   （ROI 外 logit = -128），并且与整图推理逐值一致
#   （证明见 ../tcp_image_rx/ai/net/roi_exactness.py）。
#   只有统计块 version >= AI_STAT_VERSION_ROI 的板端认 v5 头。
# --------------------------------------------------------------------------
CTRL_MAGIC_V5 = 0x4D46
CTRL_BYTES_V5 = 20

# 控制头 v6c（紧凑帧，"GM"，16 字节，实时化）：头后面直接是 w*w*kind 字节负载（模型真正要的像素），
# 不走 PL 预处理；板端每个连接保存一份跟踪状态。只在统计块 version >= 7 的板端上用。
#   magic u16 | kind u8 (1 灰度 / 3 RGB) | track u8 (0 无 / 1 跟踪一步 / 2 清状态 / 3 结果设为状态)
#   | flags u16 | model0 u8 | model1 u8 | thr i8 | 保留 u8 | w u16 | h u16 | 负载 CRC16
# 回传：统计块（b3）-> 分块和块（b7）-> w*w 个 int8 logit + CRC16（b8）-> 位掩膜 w*w/8 + CRC16（b9）
CTRL_MAGIC_V6C = 0x4D47
CTRL_BYTES_V6C = 16
_CTRL_FMT_V6C = "<HBBHBBbBHHH"
V6C_KIND_GRAY = 1
V6C_KIND_RGB = 3
V6C_TRACK_NONE, V6C_TRACK_STEP, V6C_TRACK_RESET, V6C_TRACK_SET = 0, 1, 2, 3
V6C_MAX_EXTRA = 3            # 多程序流水线：程序 0 之外最多 3 个
WIRE_TOTAL_BYTES_V5 = CTRL_BYTES_V5 + STREAM_TOTAL_BYTES
_CTRL_FMT_V5 = "<HHHHHHHHHH"    # v4 的 6 个字段 + roi_x, roi_y, roi_w, roi_h

ROI_MARGIN = 24        # 板端 UNET_ROI_MARGIN：裁剪边界的影响最深 22 像素，留 24
ROI_ALIGN = 4          # 裁剪起点对齐（两级 2x2 池化）
ROI_SIZE_ALIGN = 8     # 裁剪尺寸对齐
ROI_OUTSIDE_LOGIT = -128

FLAG_ECHO_PROC = 0x0001     # b0 回传预处理图（默认，等价于旧行为）
FLAG_ECHO_MASK = 0x0002     # b1 回传 AI 血管掩膜图
FLAG_ECHO_OVER = 0x0004     # b2 回传掩膜叠加图（红色叠在预处理图上）
FLAG_STATS = 0x0008         # b3 图像流之前先来一个 64B 统计块
FLAG_NO_IMAGE = 0x0010      # b4 只做识别，不回传任何图像
FLAG_ECHO_LOGIT = 0x0020    # b5 回传 AI 原始 logit 图（排在掩膜之后，R=G=B=logit+128）
FLAG_MORPH = 0x0040         # b6 血管形态指标：统计块后多一个 64B 指标块（须带 b3），图像最后多一幅形态骨架视图
FLAG_C_LOGIT = 0x0100       # b8（紧凑帧）回 w*w 个 int8 logit + 2 字节 CRC16
FLAG_C_MASK = 0x0200        # b9（紧凑帧）回按位打包的掩膜（行优先，每字节低位在前）+ 2 字节 CRC16
FLAG_C_PP = 0x0400          # b10（紧凑帧）PL 后处理：多 12 字节请求，回 32 字节块 + 位掩膜（见 PP_*）
FLAG_REG = 0x0080           # b7 分块求和（F5 配准）：统计块 / 形态块之后多一个 96B 块，logit 图 4x4 分块的有符号和（须带 b3）
FLAG_DEFAULT = FLAG_ECHO_PROC

LOGIT_IMG_OFFSET = 128      # logit 图的偏移码：像素值 = logit + 128
AI_LOGIT_SCALE = 0.1017041844645823   # 输出量化 scale：概率 = sigmoid(logit * scale)

AIPARAM_SRC_RAW = 0         # AI 输入 = 收到的原始 RGB
AIPARAM_SRC_PROC = 1        # AI 输入 = PL 预处理后的结果
AIPARAM_THR_DEFAULT = -3    # logit >= thr 判为血管

# aiparam bits[4:2] = 工作分辨率档位: 分辨率 = 512 >> shift
#   0 = 512(全分辨率, 最慢最准) / 1 = 256 / 2 = 128 / 3 = 64 / 4 = 32
#   0 也是"不自选, 用板端 --ai-res 的默认值"
AIPARAM_RES_SHIFT = 2
AIPARAM_RES_MASK = 0x001C
AI_RES_CHOICES = (512, 256, 128, 64, 32)

# aiparam bits[7:5] = 模型（板端 src/unet_models.h 的模型库）：0 = 板端默认（--model，缺省眼底），
# k = 第 k-1 号模型。老板端不认这三位，一律按眼底跑。
AIPARAM_MODEL_SHIFT = 5
AIPARAM_MODEL_MASK = 0x00E0

# 模型库，顺序与板端 unet_models.c 一致（gen_unet_models.py 生成）。
#   thr   默认判定门限（logit >= thr）；res 默认分辨率；scale 输出量化 scale（概率 = sigmoid(logit*scale)）
#   roi   是否支持 ROI 局部推理（只有全分辨率训练的模型才有意义）；morph 形态指标是否有意义
#   inter 配套的点击纠错模型下标；hidden 不出现在界面下拉框
MODELS = (
    dict(index=0, name="fundus", label="眼底血管", target="血管", thr=-3, res=512,
         scale=0.1017041844645823, roi=True, morph=True),
    dict(index=1, name="skin", label="皮肤镜病灶", target="病灶", thr=1, res=128,
         scale=0.4723758547324834, roi=False, morph=False),
    dict(index=2, name="echo_lv", label="心超左心室", target="左心室", thr=1, res=128,
         scale=0.24494746350866603, roi=False, morph=False, inter=3, seq=4),
    # 点击纠错（F2）用的配套模型：输入是 interactive.compose() 拼的“图像 + 先验 + 点击”，
    # 不能直接拿原图跑，所以不出现在下拉框里（hidden），由「点击纠错」自动选用
    dict(index=3, name="echo_inter", label="心超点击纠错", target="左心室", thr=1, res=128,
         scale=0.4401192552461399, roi=False, morph=False, hidden=True),
    # 序列传播（F3）：输入是 sequence.py 拼的“本帧 + 上一步结果 + B=128”（enc="none"，与无点击的 compose 相同），
    # 由「序列跟踪」窗口自动选用
    dict(index=4, name="echo_seq", label="心超序列传播", target="左心室", thr=1, res=128,
         scale=0.5600521658349225, roi=False, morph=False, hidden=True, enc="none"),
    # 配准代价（F5）：手工构造的参数，logit ≈ |R - G| / 2；16 个候选平移拼成 4x4 一帧，PS 分块求和（FLAG_REG）。
    # 由「连续帧配准」自动选用；门限 8 = |a - b| >= 16 算“变化像素”
    dict(index=5, name="reg_absdiff", label="配准代价 |a-b|", target="变化像素", thr=8, res=512,
         scale=1.0 / 127, roi=False, morph=False, hidden=True),
    # ② 编译出来的算子程序（E:/PLD/opcomp）：输出 = logit * scale + mid（数值在 out_range 内，1.0 = 255 灰度）。
    # 7 号以上只能经紧凑帧调用（aiparam 的模型字段只有 3 位）
    dict(index=6, name="op_median3", label="三帧中值", target="去斑", thr=0, res=128, scale=0.003937007874015748, mid=0.5,
         roi=False, morph=False, hidden=True, op=True, out_range=[0.0, 1.0]),
    dict(index=7, name="op_mean3", label="三帧均值", target="平均", thr=0, res=128, scale=0.003937007874015748, mid=0.5,
         roi=False, morph=False, hidden=True, op=True, out_range=[0.0, 1.0]),
    dict(index=8, name="op_sobel", label="Sobel 梯度", target="边缘", thr=0, res=128, scale=0.007874015748031496, mid=1.0,
         roi=False, morph=False, hidden=True, op=True, out_range=[0.0, 2.0]),
    dict(index=9, name="op_gauss11", label="11×11 高斯", target="模糊", thr=0, res=128, scale=0.003937007874015748, mid=0.5,
         roi=False, morph=False, hidden=True, op=True, out_range=[0.0, 1.0]),
    dict(index=10, name="op_unsharp", label="反锐化增强", target="增强", thr=0, res=128, scale=0.003937007874015748, mid=0.5,
         roi=False, morph=False, hidden=True, op=True, out_range=[0.0, 1.0]),
    dict(index=11, name="op_motion", label="三帧运动能量", target="运动", thr=0, res=128, scale=0.007874015748031496, mid=1.0,
         roi=False, morph=False, hidden=True, op=True, out_range=[0.0, 2.0]),
    dict(index=12, name="op_change", label="变化检测", target="变化", thr=0, res=128, scale=0.003937007874015748, mid=0.5,
         roi=False, morph=False, hidden=True, op=True, out_range=[0.0, 1.0]),
    dict(index=13, name="op_vessel4", label="线检测（血管增强）", target="血管响应", thr=0, res=128, scale=0.007874015748031496, mid=0.0,
         roi=False, morph=False, hidden=True, op=True, out_range=[-1.0, 1.0]),
    # ⑤ CT 脾脏（E:/PLD/ct）：输入是 ct_volume.payloads 拼的“上方约 5 mm / 本层 / 下方约 5 mm”三层（W400/L40，4x4 块平均到 128），
    # 只能经紧凑帧调用；由「CT 整卷分割」窗口自动选用
    dict(index=14, name="ct_spleen", label="CT 脾脏", target="脾脏", thr=1, res=128, scale=0.6008696668730007,
         roi=False, morph=False, hidden=True, enc="z3"),
    # ⑤ 第二阶段 ROI 精分割：按 14 号的结果定 256x256 框，框内 2x2 块平均到 128；R = 本层，G = 粗结果掩膜，B = 上下层平均
    # （ct_volume.roi_payloads）。「CT 整卷分割」窗口在粗分割后自动接着跑
    dict(index=15, name="ct_spleen_roi", label="CT 脾脏 ROI 精分割", target="脾脏", thr=1, res=128, scale=4.15080825925812,
         roi=False, morph=False, hidden=True, enc="roi_prior"),
    # ③ EchoNet-Dynamic 左心室：112x112 灰度补边到 128，R / G / B = 前约 40 ms / 本帧 / 后约 40 ms（echo_ws.payloads），
    # 输出裁回 112；由「实时心超工作站」窗口自动选用
    dict(index=16, name="echonet_lv", label="心超左心室（EchoNet）", target="左心室", thr=1, res=128,
         scale=0.13784920699953093, roi=False, morph=False, hidden=True, enc="t3"),
)


def model_by_index(index):
    """模型表里的一项；index 为 None 或越界时返回眼底（老板端只有眼底）。"""
    if index is None or not 0 <= index < len(MODELS):
        return MODELS[0]
    return MODELS[index]


def res_to_shift(res):
    """512 -> 0, 256 -> 1, ... 32 -> 4。不合法返回 None。"""
    if res not in AI_RES_CHOICES:
        return None
    return {512: 0, 256: 1, 128: 2, 64: 3, 32: 4}[res]


def shift_to_res(shift):
    return 512 >> shift

AI_STAT_BYTES = 64
AI_STAT_MAGIC = 0x31414953   # 小端字节 'S','I','A','1'
AI_STAT_VERSION = 8          # 板端 ai_view.h 同名常量
AI_STAT_VERSION_LOGIT = 2    # 统计块 version >= 2 的板端才会回传 logit 图
AI_STAT_VERSION_ROI = 3      # 统计块 version >= 3 的板端才认 v5 头（ROI）；
                             # ROI 帧的 reserved = (裁剪高 << 16) | 裁剪宽，total = ROI 面积
AI_STAT_VERSION_MORPH = 4    # 统计块 version >= 4 的板端才认 b6（形态指标）
AI_STAT_VERSION_MODEL = 5    # 统计块 version >= 5 的板端有模型库；flags bits[23:16] = 模型下标 + 1
AI_STAT_VERSION_REG = 6      # 统计块 version >= 6 的板端认 b7；真的跟着分块求和块时，回显的 flags 里 b7 才是 1
AI_STAT_VERSION_COMPACT = 7  # 统计块 version >= 7 的板端认紧凑帧（v6c）、板端跟踪状态、b8 / b9
AI_STAT_VERSION_PP = 8       # 统计块 version >= 8 的板端认 b10（PL 后处理）；紧凑帧 reserved 的 bit2 = 这块板能做

# PL 后处理（b10，板端 image_proto.h FLAG_C_PP / ai_view.h PP_BLOCK_*，固件 xu2dp_e2b_pp 的 pp_layer）
# 请求：程序列表之后、负载之前 12 字节 = u8 op | u8 k | i8 thr | i8 tn | u16 x0, y0, cw, ch（cw = 0 表示整帧）
# 回传排在最后：32 字节块 u32 magic 'PPR1' / status / count / comps / largest / pl_us / mask_bytes / rows<<16|row_bytes，
# mask_bytes > 0 时再跟位掩膜（每行 row_bytes 字节，行内低位在前）+ CRC16
PP_OP_MASK, PP_OP_CLEAN, PP_OP_CONN8 = 1, 2, 4
PP_BLOCK_BYTES = 32
PP_MAGIC = 0x31525050         # 小端字节 'P','P','R','1'
PP_STATUS_UNAVAILABLE = 0x80000000

# 形态指标块（板端 src/vessel_morph.h 的 vm_fill_block，u32 小端 x 16）
# 分块求和块（板端 ai_view.h 的 REG_*，F5 配准）：u32 magic / version / grid / out_w / out_h / run_us / 2 保留，
# 然后 i32 sum[16]（4x4 分块，行优先）。配 reg_absdiff 模型时 sum[k] = 第 k 个候选平移的 SAD（logit 单位 ≈ |a-b|/2）
REG_BLOCK_BYTES = 96
REG_MAGIC = 0x31474552       # 小端字节 'R','E','G','1'
REG_GRID = 4

MORPH_BYTES = 64
MORPH_MAGIC = 0x3150524D     # 小端字节 'M','R','P','1'
MORPH_FIELDS = ("magic", "version", "grid", "vessel_px", "fov_px", "length_x10",
                "caliber_x100", "branches", "endpoints", "segments", "tortuosity_x1000",
                "fractal_x1000", "density_x1000", "quad01", "quad23", "run_us")

# 统计块字段顺序（u32 小端）
AI_STAT_FIELDS = ("magic", "version", "flags", "ai_res", "vessels", "total",
                  "permille", "logit_min", "logit_max", "ai_us", "components",
                  "proc_min", "proc_max", "mode", "frame_count", "reserved")
_AI_STAT_SIGNED = ("logit_min", "logit_max")

_CTRL_FMT_V3 = "<HHHH"       # magic, mode, flags, aiparam —— 小端

MODE_NAMES = {
    0: "灰度归一化",
    1: "阈值分割",
    2: "边缘增强",
    3: "伪彩色映射",
    4: "组合(归一化+伪彩色)",
    5: "原图直通",
    6: "边缘叠加(白边)",
}

# 模式 6 在 PL 里**没有对应实现**：板子让 PL 跑模式 2 拿到边缘图，
# 再由 PS 把"边缘 >= 阈值"的像素在原图上画成白色。
# 所以模式 6 用的阈值就是那个"边缘阈值"（和模式 1 共用控制头里的 thr 字段）。
CTRL_MODE_MAX = 6
MODE_EDGE_OVERLAY = 6
MODE_EDGE_PL = 2              # 边缘叠加内部实际发给 PL 的模式

_ROW_CRC16 = "<HHH"          # row, length, crc16 —— 小端
_CTRL_FMT = "<HH"            # magic, mode —— 小端

# --------------------------------------------------------------------------
# CRC-16/CCITT-FALSE（与板端 crc16.c 一致）
# --------------------------------------------------------------------------
CRC16_INIT = 0xFFFF
CRC16_CHECK = 0x29B1         # CRC16("123456789") 的标准校验值


def crc16_ccitt_false_ref(data, crc=CRC16_INIT):
    """纯 Python 逐位参考实现。**慢**（786432 字节要 621 ms），只用于自检对照。"""
    for byte in data:
        crc ^= (byte << 8)
        crc &= 0xFFFF
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# --------------------------------------------------------------------------
# 热路径一律用标准库的 C 实现
#
# binascii.crc_hqx 用的是 CRC-CCITT 多项式 0x1021、MSB-first、不反转、不异或输出，
# 和我们需要的 CRC-16/CCITT-FALSE 是**同一个算法**：只要初值传 0xFFFF，结果逐位相同。
# 实测 786432 字节：纯 Python 621.7 ms → crc_hqx 1.2 ms，**快约 500 倍**。
# 导入时会用参考实现交叉验证，防止哪天搞错。
# --------------------------------------------------------------------------
def crc16_ccitt_false(data, crc=CRC16_INIT):
    """CRC-16/CCITT-FALSE，热路径实现（C）。

    包一层是为了补默认初值 0xFFFF —— binascii.crc_hqx 两个参数都是必填。
    """
    return binascii.crc_hqx(data, crc)


FAST_CRC = True


# --------------------------------------------------------------------------
# 组包 / 解包
# --------------------------------------------------------------------------
def build_packet(row, payload):
    """把一个行包拼成 bytes（payload 必须正好 1536 字节）。"""
    if len(payload) != PKT_PAYLOAD_BYTES:
        raise ValueError("payload must be %d bytes, got %d"
                         % (PKT_PAYLOAD_BYTES, len(payload)))
    crc = crc16_ccitt_false(payload)
    return struct.pack(_ROW_CRC16, row, len(payload), crc) + payload


def iter_packets(image):
    """按行号 0..511 依次产出 packets（image 必须正好 786 432 字节）。"""
    if len(image) != IMG_TOTAL_BYTES:
        raise ValueError("image must be %d bytes, got %d"
                         % (IMG_TOTAL_BYTES, len(image)))
    for row in range(PKT_COUNT):
        off = row * IMG_ROW_BYTES
        yield build_packet(row, image[off:off + IMG_ROW_BYTES])


def parse_header(hdr):
    """解析 6 字节包头，返回 (row, length, crc16)。"""
    return struct.unpack(_ROW_CRC16, hdr)


def _roi_axis(r, n, full):
    lo = max(0, r - ROI_MARGIN)
    hi = min(full, r + n + ROI_MARGIN)
    s = lo // ROI_ALIGN * ROI_ALIGN
    size = -(-(hi - s) // ROI_SIZE_ALIGN) * ROI_SIZE_ALIGN
    if s + size > full:
        s = full - size              # 512 是 8 的倍数，回挪后起点仍对齐到 4
    return s, size


def roi_crop(x, y, w, h):
    """ROI 框 -> 板端实际计算的裁剪窗口 (cx, cy, cw, ch)。与板端 ai_view_roi_crop() 同一算法。"""
    cx, cw = _roi_axis(x, w, IMG_WIDTH)
    cy, ch = _roi_axis(y, h, IMG_HEIGHT)
    return cx, cy, cw, ch


def pack_stream(image, mode=0, flags=FLAG_DEFAULT, aiparam=None,
                thr=CTRL_THR_DEFAULT, version=4, roi=None):
    """把整幅图打成**完整的一帧字节流**：控制头 + 512 个行包。

    roi=(x, y, w, h) 时发 20 字节 v5 头（ROI 局部推理），返回 WIRE_TOTAL_BYTES_V5；
    version=4（默认）发 12 字节 v4 头（多了逐帧阈值 thr），返回 WIRE_TOTAL_BYTES_V4；
    version=3 发 8 字节 v3 头（没有阈值），返回 WIRE_TOTAL_BYTES_V3；
    version=2 发 4 字节老头（只为验证板端兼容性），返回 WIRE_TOTAL_BYTES。
    aiparam=None 时用默认（原始 RGB + 阈值 -3 + 512 分辨率）。

    CRC 走 C 实现，组包用 pack_into，系统调用从 512 次降到 1 次。
    """
    if len(image) != IMG_TOTAL_BYTES:
        raise ValueError("image must be %d bytes, got %d"
                         % (IMG_TOTAL_BYTES, len(image)))
    if not (0 <= mode <= CTRL_MODE_MAX):
        raise ValueError("mode must be 0..%d, got %r" % (CTRL_MODE_MAX, mode))
    if aiparam is None:
        aiparam = make_aiparam()
    if not (CTRL_THR_MIN <= thr <= CTRL_THR_MAX):
        raise ValueError("thr must be %d..%d, got %r"
                         % (CTRL_THR_MIN, CTRL_THR_MAX, thr))

    if roi is not None:
        x, y, w, h = (int(v) for v in roi)
        if not (w > 0 and h > 0 and x >= 0 and y >= 0
                and x + w <= IMG_WIDTH and y + h <= IMG_HEIGHT):
            raise ValueError("roi must lie inside the %dx%d image, got %r"
                             % (IMG_WIDTH, IMG_HEIGHT, roi))
        version = 5
    if version == 5:
        hdr_bytes, wire = CTRL_BYTES_V5, WIRE_TOTAL_BYTES_V5
    elif version == 4:
        hdr_bytes, wire = CTRL_BYTES_V4, WIRE_TOTAL_BYTES_V4
    elif version == 3:
        hdr_bytes, wire = CTRL_BYTES_V3, WIRE_TOTAL_BYTES_V3
    elif version == 2:
        hdr_bytes, wire = CTRL_BYTES, WIRE_TOTAL_BYTES
    else:
        raise ValueError("version must be 2, 3, 4 or 5 (5 needs roi)")

    out = bytearray(wire)
    if version == 5:
        struct.pack_into(_CTRL_FMT_V5, out, 0, CTRL_MAGIC_V5, mode,
                         flags & 0xFFFF, aiparam & 0xFFFF, thr & 0xFFFF, 0, x, y, w, h)
    elif version == 4:
        struct.pack_into(_CTRL_FMT_V4, out, 0, CTRL_MAGIC_V4, mode,
                         flags & 0xFFFF, aiparam & 0xFFFF, thr & 0xFFFF, 0)
    elif version == 3:
        struct.pack_into(_CTRL_FMT_V3, out, 0, CTRL_MAGIC_V3, mode,
                         flags & 0xFFFF, aiparam & 0xFFFF)
    else:
        struct.pack_into(_CTRL_FMT, out, 0, CTRL_MAGIC, mode)

    mvb = memoryview(out)
    pos = hdr_bytes
    for row in range(PKT_COUNT):
        off = row * IMG_ROW_BYTES
        payload = image[off:off + IMG_ROW_BYTES]
        struct.pack_into(_ROW_CRC16, out, pos, row, PKT_PAYLOAD_BYTES,
                         crc16_ccitt_false(payload))
        mvb[pos + PKT_HDR_BYTES:pos + PKT_TOTAL_BYTES] = payload
        pos += PKT_TOTAL_BYTES
    return bytes(out)


def pack_v6c(payload, w, kind=V6C_KIND_GRAY, flags=None, model0=2, model1=4, thr=1, track=V6C_TRACK_NONE,
             extra=(), pp=None):
    """紧凑帧：16 字节头 + 额外程序描述（每个 4 字节）+ [PL 后处理请求 12 字节] + 负载（w*w*kind 字节）。
    flags 缺省 = 统计块 + int8 logit。
    extra = [(model, src, dst, thr)]：多程序流水线，src 0 = 本帧 / 1 = 上一个程序的输出（偏移码 logit+128），
    dst 0 = 复制到 R/G/B，1/2/3 = 只替换 R/G/B。最多 3 个；b8 依次回所有程序的 logit。
    pp = dict(op, k=1, thr=1, tn=1, win=None)：程序 0 的 logit 交给 PL 的 pp_layer（自动加 FLAG_C_PP）；
    只发给统计块 version >= 8 的板端（老板端不认这 12 字节，会把协议搞乱）。"""
    if flags is None:
        flags = FLAG_STATS | FLAG_C_LOGIT
    if pp is not None:
        flags |= FLAG_C_PP
    if len(payload) != w * w * kind:
        raise ValueError("v6c payload must be %d bytes, got %d" % (w * w * kind, len(payload)))
    if w not in (32, 64, 128, 256, 512):
        raise ValueError("v6c size must be 512>>shift, got %d" % w)
    if len(extra) > V6C_MAX_EXTRA:
        raise ValueError("at most %d extra programs" % V6C_MAX_EXTRA)
    progs = b"".join(struct.pack("<BBBb", m, src, dst, int(t)) for m, src, dst, t in extra)
    if flags & FLAG_C_PP:
        progs += pack_pp(**(pp or {}))
    return struct.pack(_CTRL_FMT_V6C, CTRL_MAGIC_V6C, kind, track, flags & 0xFFFF, model0, model1,
                       int(thr), len(extra), w, w, crc16_ccitt_false(payload)) + progs + bytes(payload)


def pack_pp(op=PP_OP_MASK, k=1, thr=1, tn=1, win=None):
    """PL 后处理请求（12 字节）。win = (x0, y0, cw, ch)，logit 坐标；None = 整帧。"""
    x0, y0, cw, ch = win if win is not None else (0, 0, 0, 0)
    if k not in (1, 2, 4):
        raise ValueError("pp k must be 1, 2 or 4")
    return struct.pack("<BBbbHHHH", op & 7, k, int(thr), int(tn), x0, y0, cw, ch)


def unpack_pp(blk):
    """32 字节 PL 后处理块 -> dict（status 0 = 成功；bit31 = 这块板做不了）。"""
    v = struct.unpack("<8I", blk)
    if v[0] != PP_MAGIC:
        raise ValueError("bad pp block magic 0x%08X" % v[0])
    return dict(status=v[1], count=v[2], comps=v[3], largest=v[4], pl_us=v[5], mask_bytes=v[6],
                rows=v[7] >> 16, row_bytes=v[7] & 0xFFFF)


def pp_mask_array(pp):
    """pp 块里的位掩膜 -> 二维 bool 数组 [rows, row_bytes*8]（numpy；调用方再按 cw*k 截列）。"""
    import numpy as np
    bits = np.unpackbits(np.frombuffer(pp["mask"], np.uint8), bitorder="little")
    return bits.reshape(pp["rows"], pp["row_bytes"] * 8).astype(bool)


def stats_pp_available(ai_stats):
    """板端能做 PL 后处理（统计块 version >= 8、紧凑帧、reserved bit2）。"""
    st = ai_stats or {}
    r = st.get("reserved", 0)
    return st.get("version", 0) >= AI_STAT_VERSION_PP and bool(r & 0x80000000) and bool(r & 4)


def stats_compact_info(ai_stats):
    """紧凑帧的统计块 reserved：(是紧凑帧, 先验取自板端状态, 本帧后板端存有状态)。"""
    r = (ai_stats or {}).get("reserved", 0)
    return bool(r & 0x80000000), bool(r & 1), bool(r & 2)


def make_aiparam(src=AIPARAM_SRC_RAW, thr=AIPARAM_THR_DEFAULT, res=512, model=None):
    """打包 aiparam：bit0 输入源，bits[4:2] 分辨率档位，bits[7:5] 模型，bits[15:8] AI 阈值（int8）。

    res=512（默认）时档位是 0，也就是"不自选、用板端的默认"（选了模型时是该模型的默认档位）。
    model=None 时模型字段为 0（板端默认模型），否则是模型下标（0 = 眼底）。
    """
    sh = res_to_shift(res)
    if sh is None:
        raise ValueError("res must be one of %s" % (AI_RES_CHOICES,))
    mf = 0 if model is None else model + 1
    if not 0 <= mf <= 7:
        raise ValueError("model index must be 0..6, got %r" % (model,))
    return ((src & 0x3) | ((sh << AIPARAM_RES_SHIFT) & AIPARAM_RES_MASK)
            | ((mf << AIPARAM_MODEL_SHIFT) & AIPARAM_MODEL_MASK) | ((thr & 0xFF) << 8))


def expected_images(flags, ai_stats=None):
    """按 flags 推出发回图像流的顺序（与板端一致：预处理图 -> 叠加图 -> 掩膜图 -> logit 图）。

    ai_stats 是本帧先收到的统计块（没要统计时为 None）。有它就能避开两种"空等到超时"：
    板端这帧没跑出 AI（ai_us == 0）时不会发叠加 / 掩膜 / logit；
    老板端（version < AI_STAT_VERSION_LOGIT）不认 b5，不会发 logit 图。
    """
    if flags & FLAG_NO_IMAGE:
        return []
    ai_ok = ai_stats is None or ai_stats.get("ai_us", 0) != 0
    logit_ok = ai_stats is None or ai_stats.get("version", 0) >= AI_STAT_VERSION_LOGIT
    morph_ok = ai_stats is not None and ai_stats.get("version", 0) >= AI_STAT_VERSION_MORPH
    order = []
    if flags & FLAG_ECHO_PROC:
        order.append("proc")
    if flags & FLAG_ECHO_OVER and ai_ok:
        order.append("overlay")
    if flags & FLAG_ECHO_MASK and ai_ok:
        order.append("mask")
    if flags & FLAG_ECHO_LOGIT and ai_ok and logit_ok:
        order.append("logit")
    if flags & FLAG_MORPH and ai_ok and morph_ok:
        order.append("morph")
    return order


def expects_reg_block(ai_stats):
    """统计块之后是否跟着 96 字节分块求和块：板端认 b7（version >= 6）且回显的 flags 里 b7 仍是 1。"""
    return (ai_stats is not None and ai_stats.get("version", 0) >= AI_STAT_VERSION_REG
            and bool(ai_stats.get("flags", 0) & FLAG_REG))


def unpack_reg(blk):
    """解析 96 字节分块求和块 -> dict(grid, out_w, out_h, run_us, sums=[16 个有符号和])。"""
    if len(blk) != REG_BLOCK_BYTES:
        raise ValueError("reg block must be %d bytes, got %d" % (REG_BLOCK_BYTES, len(blk)))
    head = struct.unpack_from("<8I", blk, 0)
    if head[0] != REG_MAGIC:
        raise ValueError("bad reg magic 0x%08X" % head[0])
    return dict(version=head[1], grid=head[2], out_w=head[3], out_h=head[4], run_us=head[5],
                sums=list(struct.unpack_from("<16i", blk, 32)))


def expects_morph_block(flags, ai_stats):
    """统计块之后是否还跟着 64 字节形态指标块（板端：要了 b6 + b3、认 b6、这帧 AI 跑出来了）。"""
    return (bool(flags & FLAG_MORPH) and ai_stats is not None
            and ai_stats.get("version", 0) >= AI_STAT_VERSION_MORPH and ai_stats.get("ai_us", 0) != 0)


def unpack_morph(blk):
    """解析 64 字节形态指标块；长度 / 管径是分析网格的像素，派生字段换成浮点。"""
    if len(blk) != MORPH_BYTES:
        raise ValueError("morph block must be %d bytes, got %d" % (MORPH_BYTES, len(blk)))
    d = dict(zip(MORPH_FIELDS, struct.unpack("<16I", blk)))
    if d["magic"] != MORPH_MAGIC:
        raise ValueError("bad morph magic 0x%08X" % d["magic"])
    d["w"], d["h"] = d["grid"] & 0xFFFF, d["grid"] >> 16
    d["length"] = d["length_x10"] / 10.0
    d["caliber"] = d["caliber_x100"] / 100.0
    d["tortuosity"] = d["tortuosity_x1000"] / 1000.0
    d["fractal"] = d["fractal_x1000"] / 1000.0
    d["density_pct"] = d["density_x1000"] / 10.0
    d["quad_pct"] = [(d["quad01"] & 0xFFFF) / 10.0, (d["quad01"] >> 16) / 10.0,
                     (d["quad23"] & 0xFFFF) / 10.0, (d["quad23"] >> 16) / 10.0]
    return d


def unpack_stats(blk):
    """解析 64 字节统计块，返回 dict（含派生字段 vessels_pct）。"""
    if len(blk) != AI_STAT_BYTES:
        raise ValueError("stats block must be %d bytes, got %d"
                         % (AI_STAT_BYTES, len(blk)))
    vals = struct.unpack("<16I", blk)
    d = dict(zip(AI_STAT_FIELDS, vals))
    for k in _AI_STAT_SIGNED:
        if d[k] >= 0x80000000:
            d[k] -= 0x100000000
    if d["magic"] != AI_STAT_MAGIC:
        raise ValueError("bad stats magic 0x%08X" % d["magic"])
    d["vessels_pct"] = (100.0 * d["vessels"] / d["total"]) if d["total"] else 0.0
    # ROI 帧（version >= 3）：reserved = (裁剪高 << 16) | 裁剪宽；整图帧为 0
    d["crop_w"] = d["reserved"] & 0xFFFF if d["version"] >= AI_STAT_VERSION_ROI else 0
    d["crop_h"] = d["reserved"] >> 16 if d["version"] >= AI_STAT_VERSION_ROI else 0
    # 模型库（version >= 5）：flags bits[23:16] = 模型下标 + 1；老板端为 None（只有眼底）
    mf = (d["flags"] >> 16) & 0xFF if d["version"] >= AI_STAT_VERSION_MODEL else 0
    d["model"] = mf - 1 if mf else None
    return d


def unpack_stream(stream):
    """解析一整帧字节流，返回 (image_bytes, stats)。与 pack_stream 对称。

    stats = {'rows_ok', 'crc_errors', 'seq_errors'}
    行乱序也能正确落位（按包头里的 row 放）。
    """
    if len(stream) != STREAM_TOTAL_BYTES:
        raise ValueError("stream must be %d bytes, got %d"
                         % (STREAM_TOTAL_BYTES, len(stream)))
    image = bytearray(IMG_TOTAL_BYTES)
    seen = bytearray(PKT_COUNT)
    crc_errors = 0
    for k in range(PKT_COUNT):
        pos = k * PKT_TOTAL_BYTES
        row, length, crc = struct.unpack_from(_ROW_CRC16, stream, pos)
        if row >= PKT_COUNT or length != PKT_PAYLOAD_BYTES:
            raise ValueError("bad header at packet %d: row=%d len=%d"
                             % (k, row, length))
        off = pos + PKT_HDR_BYTES
        payload = stream[off:off + PKT_PAYLOAD_BYTES]
        if crc16_ccitt_false(payload) != crc:
            crc_errors += 1
            continue
        d = row * IMG_ROW_BYTES
        image[d:d + IMG_ROW_BYTES] = payload
        seen[row] = 1
    stats = {"rows_ok": sum(seen), "crc_errors": crc_errors, "seq_errors": 0}
    return bytes(image), stats


def verify_row(hdr, payload):
    """校验包头与负载；返回 (row, ok, reason)。"""
    row, length, crc = parse_header(hdr)
    if row >= PKT_COUNT:
        return row, False, "row %d out of range" % row
    if length != PKT_PAYLOAD_BYTES:
        return row, False, "length %d != %d" % (length, PKT_PAYLOAD_BYTES)
    if len(payload) != PKT_PAYLOAD_BYTES:
        return row, False, "payload short (%d)" % len(payload)
    got = crc16_ccitt_false(payload)
    if got != crc:
        return row, False, "crc 0x%04X != 0x%04X" % (got, crc)
    return row, True, ""


# --------------------------------------------------------------------------
# 导入自检：常量算术 + CRC 校验向量
# --------------------------------------------------------------------------
def _self_check():
    errs = []
    v6 = pack_v6c(bytes(range(256)) * 64, 128, track=V6C_TRACK_STEP)
    if len(v6) != CTRL_BYTES_V6C + 16384 or struct.unpack_from("<H", v6, 0)[0] != CTRL_MAGIC_V6C or \
            struct.unpack_from("<H", v6, 14)[0] != crc16_ccitt_false(bytes(range(256)) * 64) or v6[3] != 1:
        errs.append("pack_v6c layout")
    v7 = pack_v6c(bytes(64 * 64), 64, pp=dict(op=PP_OP_MASK | PP_OP_CLEAN, win=(8, 8, 48, 48)))
    if (struct.unpack_from("<H", v7, 4)[0] & FLAG_C_PP) == 0 or len(v7) != CTRL_BYTES_V6C + 12 + 4096 or \
            v7[16:28] != struct.pack("<BBbbHHHH", 3, 1, 1, 1, 8, 8, 48, 48):
        errs.append("pack_v6c pp request")
    pb = unpack_pp(struct.pack("<8I", PP_MAGIC, 0, 5, 2, 3, 7, 32, (16 << 16) | 2))
    if (pb["rows"], pb["row_bytes"], pb["mask_bytes"]) != (16, 2, 32):
        errs.append("unpack_pp")
    blk = struct.pack("<8I", REG_MAGIC, 1, 4, 512, 512, 9, 0, 0) + struct.pack("<16i", *range(-8, 8))
    if unpack_reg(blk)["sums"] != list(range(-8, 8)) or unpack_reg(blk)["out_w"] != 512:
        errs.append("unpack_reg round trip")
    if not expects_reg_block(dict(version=6, flags=FLAG_REG | FLAG_STATS)) or \
            expects_reg_block(dict(version=5, flags=FLAG_REG)) or expects_reg_block(dict(version=6, flags=FLAG_STATS)):
        errs.append("expects_reg_block")
    if IMG_ROW_BYTES != 1536:
        errs.append("IMG_ROW_BYTES=%d" % IMG_ROW_BYTES)
    if IMG_TOTAL_BYTES != 786432:
        errs.append("IMG_TOTAL_BYTES=%d" % IMG_TOTAL_BYTES)
    if PKT_TOTAL_BYTES != 1542:
        errs.append("PKT_TOTAL_BYTES=%d" % PKT_TOTAL_BYTES)
    if STREAM_TOTAL_BYTES != 789504:
        errs.append("STREAM_TOTAL_BYTES=%d" % STREAM_TOTAL_BYTES)
    if WIRE_TOTAL_BYTES != STREAM_TOTAL_BYTES + 4:
        errs.append("WIRE_TOTAL_BYTES=%d" % WIRE_TOTAL_BYTES)
    if struct.unpack(_CTRL_FMT, struct.pack(_CTRL_FMT, CTRL_MAGIC, 3)) != (CTRL_MAGIC, 3):
        errs.append("control header packing round-trip failed")

    if CTRL_BYTES_V3 != 8 or WIRE_TOTAL_BYTES_V3 != STREAM_TOTAL_BYTES + 8:
        errs.append("v3 wire size wrong")
    if struct.unpack(_CTRL_FMT_V3,
                     struct.pack(_CTRL_FMT_V3, CTRL_MAGIC_V3, 3, FLAG_ECHO_MASK, 0x00FD)) \
            != (CTRL_MAGIC_V3, 3, FLAG_ECHO_MASK, 0x00FD):
        errs.append("v3 control header round-trip failed")

    if CTRL_BYTES_V4 != 12 or WIRE_TOTAL_BYTES_V4 != STREAM_TOTAL_BYTES + 12:
        errs.append("v4 wire size wrong")
    if struct.unpack(_CTRL_FMT_V4,
                     struct.pack(_CTRL_FMT_V4, CTRL_MAGIC_V4, 1, FLAG_ECHO_PROC,
                                 0x0000, 137, 0)) != (CTRL_MAGIC_V4, 1, FLAG_ECHO_PROC,
                                                      0x0000, 137, 0):
        errs.append("v4 control header round-trip failed")
    if CTRL_MAGIC == CTRL_MAGIC_V3 or CTRL_MAGIC_V3 == CTRL_MAGIC_V4:
        errs.append("control header magics must be distinct")
    if make_aiparam() != ((AIPARAM_THR_DEFAULT & 0xFF) << 8):
        errs.append("make_aiparam default wrong: 0x%04X" % make_aiparam())
    if make_aiparam(AIPARAM_SRC_PROC, 12) != ((12 & 0xFF) << 8) | 1:
        errs.append("make_aiparam(SRC_PROC,12) wrong")
    if make_aiparam(AIPARAM_SRC_RAW, -3, 128) != (((-3) & 0xFF) << 8) | (2 << AIPARAM_RES_SHIFT):
        errs.append("make_aiparam(res=128) wrong: 0x%04X" % make_aiparam(0, -3, 128))
    if [shift_to_res(res_to_shift(r)) for r in AI_RES_CHOICES] != list(AI_RES_CHOICES):
        errs.append("res<->shift round-trip failed")
    if make_aiparam(0, -3, 512, None) != make_aiparam(0, -3, 512):
        errs.append("model=None must leave the model field 0")
    if (make_aiparam(0, 1, 128, 2) >> AIPARAM_MODEL_SHIFT) & 0x7 != 3 \
            or make_aiparam(0, 1, 128, 2) & ~AIPARAM_MODEL_MASK != make_aiparam(0, 1, 128):
        errs.append("make_aiparam(model=2) wrong: 0x%04X" % make_aiparam(0, 1, 128, 2))
    if [m["index"] for m in MODELS] != list(range(len(MODELS))) or MODELS[0]["name"] != "fundus":
        errs.append("MODELS must be indexed 0.. with fundus first")
    if MODELS[0]["scale"] != AI_LOGIT_SCALE or MODELS[0]["thr"] != AIPARAM_THR_DEFAULT:
        errs.append("fundus model entry drifted from AI_LOGIT_SCALE / AIPARAM_THR_DEFAULT")
    _sb = struct.pack("<16I", AI_STAT_MAGIC, 5, 0x0003002B, 128, 1, 2, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0)
    if unpack_stats(_sb)["model"] != 2:
        errs.append("stats v5 model decode wrong")
    if unpack_stats(_sb[:4] + struct.pack("<I", 4) + _sb[8:])["model"] is not None:
        errs.append("stats v4 must not report a model")

    if sorted(MODE_NAMES) != list(range(CTRL_MODE_MAX + 1)):
        errs.append("MODE_NAMES must cover exactly 0..%d, got %s"
                    % (CTRL_MODE_MAX, sorted(MODE_NAMES)))
    if MODE_EDGE_OVERLAY != CTRL_MODE_MAX or MODE_EDGE_PL != 2:
        errs.append("edge-overlay mode constants drifted")
    try:
        _m6 = pack_stream(bytes(IMG_TOTAL_BYTES), mode=MODE_EDGE_OVERLAY, thr=64)
        if len(_m6) != WIRE_TOTAL_BYTES_V4:
            errs.append("pack_stream(mode=6) wrong length")
    except Exception as exc:                        # noqa: BLE001 - self check
        errs.append("pack_stream(mode=6) failed: %s" % exc)
    if expected_images(FLAG_DEFAULT) != ["proc"]:
        errs.append("expected_images(default) = %r" % expected_images(FLAG_DEFAULT))
    if expected_images(FLAG_NO_IMAGE) != []:
        errs.append("expected_images(FLAG_NO_IMAGE) should be empty")
    if expected_images(FLAG_ECHO_PROC | FLAG_ECHO_OVER | FLAG_ECHO_MASK) != \
            ["proc", "overlay", "mask"]:
        errs.append("expected_images order wrong")
    _all = FLAG_ECHO_PROC | FLAG_ECHO_OVER | FLAG_ECHO_MASK | FLAG_ECHO_LOGIT | FLAG_STATS
    if expected_images(_all, {"version": 2, "ai_us": 1}) != ["proc", "overlay", "mask", "logit"]:
        errs.append("expected_images with logit order wrong")
    if expected_images(_all, {"version": 1, "ai_us": 1}) != ["proc", "overlay", "mask"]:
        errs.append("old board (stats v1) must not be expected to send logit")
    if expected_images(_all | FLAG_MORPH, {"version": 4, "ai_us": 1})[-1] != "morph" or \
            "morph" in expected_images(_all | FLAG_MORPH, {"version": 3, "ai_us": 1}):
        errs.append("morph view must come last, and only from a v4 board")
    try:
        _m = unpack_morph(struct.pack("<16I", MORPH_MAGIC, 1, 512 | (512 << 16), 100, 200, 123,
                                      456, 7, 8, 9, 1100, 1450, 105, 100 | (200 << 16),
                                      300 | (400 << 16), 999))
        if (_m["w"], _m["h"], _m["length"], _m["quad_pct"]) != (512, 512, 12.3, [10.0, 20.0, 30.0, 40.0]):
            errs.append("morph block decode wrong: %r" % (_m,))
    except Exception as exc:                     # noqa: BLE001 - self check
        errs.append("morph block parse failed: %s" % exc)
    if expected_images(_all, {"version": 2, "ai_us": 0}) != ["proc"]:
        errs.append("no AI images must be expected when the board ran no AI")
    if CTRL_BYTES_V5 != 20 or struct.calcsize(_CTRL_FMT_V5) != CTRL_BYTES_V5:
        errs.append("v5 header size wrong")
    try:
        _w5 = pack_stream(bytes(IMG_TOTAL_BYTES), roi=(10, 20, 30, 40))
        if len(_w5) != WIRE_TOTAL_BYTES_V5 or \
                struct.unpack_from(_CTRL_FMT_V5, _w5, 0)[0] != CTRL_MAGIC_V5 or \
                struct.unpack_from(_CTRL_FMT_V5, _w5, 0)[6:] != (10, 20, 30, 40):
            errs.append("v5 header packing wrong")
    except Exception as exc:                        # noqa: BLE001 - self check
        errs.append("pack_stream(roi) failed: %s" % exc)
    for _r in ((200, 180, 128, 128), (0, 0, 1, 1), (500, 500, 12, 12), (0, 0, 512, 512)):
        _cx, _cy, _cw, _ch = roi_crop(*_r)
        if (_cx % ROI_ALIGN or _cy % ROI_ALIGN or _cw % ROI_SIZE_ALIGN or _ch % ROI_SIZE_ALIGN
                or _cx + _cw > IMG_WIDTH or _cy + _ch > IMG_HEIGHT
                or _cx > max(0, _r[0] - ROI_MARGIN) or _cy > max(0, _r[1] - ROI_MARGIN)
                or _cx + _cw < min(IMG_WIDTH, _r[0] + _r[2] + ROI_MARGIN)
                or _cy + _ch < min(IMG_HEIGHT, _r[1] + _r[3] + ROI_MARGIN)):
            errs.append("roi_crop%r = %r breaks the alignment / margin rules"
                        % (_r, (_cx, _cy, _cw, _ch)))
    if (LOGIT_IMG_OFFSET - 3) & 0xFF != 125:
        errs.append("logit offset coding drifted")

    try:
        _blk = struct.pack("<16I", AI_STAT_MAGIC, AI_STAT_VERSION, FLAG_STATS, 32,
                           100, 1024, 97, (0x100000000 - 3) & 0xFFFFFFFF, 14,
                           2900, 5, 0, 255, 0, 1, 0)
        _d = unpack_stats(_blk)
        if (_d["vessels"] != 100 or _d["logit_min"] != -3 or _d["logit_max"] != 14
                or _d["reserved"] != 0):
            errs.append("stats block round-trip mismatch: %r" % (_d,))
    except Exception as exc:                     # noqa: BLE001 - self check
        errs.append("stats block parse failed: %s" % exc)

    # 标准校验向量
    if crc16_ccitt_false(b"123456789") != CRC16_CHECK:
        errs.append("CRC16 check vector failed")

    # 快速实现必须与逐位参考实现逐位一致(几组不同长度/内容)
    probes = [b"", b"123456789", bytes(range(256)),
              bytes(((i * 7 + 3) & 0xFF) for i in range(1536))]
    for p in probes:
        if crc16_ccitt_false(p) != crc16_ccitt_false_ref(p):
            errs.append("fast CRC != reference CRC on %d-byte probe" % len(p))
    return errs


SELF_CHECK_ERRORS = _self_check()

if __name__ == "__main__":
    if SELF_CHECK_ERRORS:
        print("proto self-check FAILED:")
        for e in SELF_CHECK_ERRORS:
            print("  -", e)
        raise SystemExit(1)
    print("proto self-check OK")
    print("  image %dx%d RGB888 = %d bytes" % (IMG_WIDTH, IMG_HEIGHT, IMG_TOTAL_BYTES))
    print("  packet %d bytes x %d = %d bytes stream"
          % (PKT_TOTAL_BYTES, PKT_COUNT, STREAM_TOTAL_BYTES))
    print("  CRC16(\"123456789\") = 0x%04X" % crc16_ccitt_false(b"123456789"))
