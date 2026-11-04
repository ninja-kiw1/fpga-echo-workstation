/**
 * @file    image_proto.h
 * @brief   网口图像接收的"线上协议"定义(与 PC 端 script/send_image.py 一一对应)。
 *
 *  图像规格 : 512 x 512, RGB888(每像素 3 字节, 顺序 R,G,B)
 *  一张图   : 512 行 x 1536 字节/行 = 786432 字节
 *  传输方式 : TCP 字节流, 把一张图切成 512 个"行包"依次发送。
 *
 *  每个行包的布局(全部小端序):
 *
 *     偏移  长度  字段      说明
 *     ----  ----  --------  ------------------------------------------
 *      0     2    row       行号, 取值 0 .. 511
 *      2     2    length    本包负载长度, 固定 1536
 *      4     2    crc16     CRC-16/CCITT-FALSE, 对后面 1536 字节负载计算
 *      6    1536  payload   该行 512 个像素, 每像素 R,G,B
 *
 *  单包总长 1542 字节; 整幅图像总计 512 x 1542 = 789504 字节。
 *
 *  接收端对 row / length 做取值校验, 对 payload 做 CRC16 校验; 三项都通过才把
 *  该行写入 DDR 图像缓冲区。因为 row 明确写在包头里, 接收端天然允许 TCP 分片、
 *  粘包和行乱序(乱序时按 row 落到正确位置即可)。
 */
#ifndef IMAGE_PROTO_H
#define IMAGE_PROTO_H

#include <stdint.h>

/* ------------------------------------------------------------------ */
/*  图像参数                                                          */
/* ------------------------------------------------------------------ */
#define IMG_WIDTH          512u                              /* 图像宽度(像素) */
#define IMG_HEIGHT         512u                              /* 图像高度(像素) */
#define IMG_BYTES_PER_PIX  3u                                /* RGB888 = 3 字节/像素 */
#define IMG_ROW_BYTES      (IMG_WIDTH * IMG_BYTES_PER_PIX)   /* 单行字节数 = 1536 */
#define IMG_TOTAL_BYTES    (IMG_ROW_BYTES * IMG_HEIGHT)      /* 整幅字节数 = 786432 */

/* ------------------------------------------------------------------ */
/*  行包参数                                                          */
/* ------------------------------------------------------------------ */
#define PKT_HDR_BYTES      6u                                /* 包头字节数 */
#define PKT_PAYLOAD_BYTES  IMG_ROW_BYTES                     /* 负载 = 一行像素 */
#define PKT_TOTAL_BYTES    (PKT_HDR_BYTES + PKT_PAYLOAD_BYTES)  /* 单包 = 1542 */
#define PKT_COUNT          IMG_HEIGHT                        /* 包数 = 行数 = 512 */
#define STREAM_TOTAL_BYTES (PKT_TOTAL_BYTES * PKT_COUNT)     /* 整幅流 = 789504 */

/* 包头内部字段的字节偏移 */
#define PKT_OFF_ROW        0u
#define PKT_OFF_LEN        2u
#define PKT_OFF_CRC16      4u
#define PKT_OFF_PAYLOAD    PKT_HDR_BYTES

/* ------------------------------------------------------------------ */
/*  控制头(v2): 整幅线上字节流的最前面 4 字节, 用来告诉板子处理模式   */
/*                                                                     */
/*     偏移  长度  字段    说明                                        */
/*     ----  ----  ------  ------------------------------------------  */
/*      0     2    magic   固定 0x4D43(小端存放即 ASCII "CM")          */
/*      2     2    mode    处理模式 0..5                               */
/*                                                                     */
/*  与 PC 端 tcp_image_ui/proto.py 的 CTRL_MAGIC / CTRL_BYTES 一致。   */
/*  板子回给 PC 的仍是纯图像流(不带控制头)。                           */
/* ------------------------------------------------------------------ */
#define CTRL_MAGIC      0x4D43u   /* 控制头魔数, 小端存放即 ASCII "CM" */
#define CTRL_BYTES      4u        /* 控制头字节数 */
#define CTRL_OFF_MAGIC  0u        /* magic 字段在控制头内的偏移 */
#define CTRL_OFF_MODE   2u        /* mode 字段在控制头内的偏移 */
#define CTRL_MODE_MAX   6u        /* 模式 0..5 + 6=边缘叠加(见下) */
#define CTRL_MODE_BYPASS 5u       /* 5 = 原图直通: PS 不推 PL, 直接把收到原图回传 */

/* 6 = 边缘叠加: **PL 侧没有这个模式**, 是 PS 组合出来的 ——
 * 先用 PL 的模式 2(Sobel 边缘增强)跑一遍拿到边缘灰度图,
 * 再把"边缘 >= 阈值"的像素在**原图**上画成白色, 其余保持原图。
 * 原图 PS 手里本来就有(rx_buf), 所以只多花一趟 PL 往返; 结构上与
 * AI 掩膜叠加(ai_view_overlay_rgb)是同一套做法, 不用改 RTL。 */
#define CTRL_MODE_EDGE_OVERLAY  6u
#define CTRL_MODE_EDGE_PL       2u    /* 边缘叠加内部实际发给 PL 的模式 */

/* ------------------------------------------------------------------ */
/*  控制头(v3): 8 字节, 多了 flags(回传内容) 与 aiparam(AI 参数)      */
/*                                                                     */
/*     偏移  长度  字段      说明                                      */
/*     ----  ----  --------  ----------------------------------------  */
/*      0     2    magic     固定 0x4D44(小端即 ASCII "DM")            */
/*      2     2    mode      预处理模式 0..5(语义同 v2)                */
/*      4     2    flags     回传内容位图(见下)                        */
/*      6     2    aiparam   AI 参数(见下)                             */
/*                                                                     */
/*  板子**先读 2 字节 magic 再决定读几字节** —— 于是老客户端(发 4 字节 */
/*  v2 头)照旧能用, 只要它不带 flags 就等价于 flags=b0。              */
/*  下行(板->PC) 仍是(可选的统计块 +)纯图像流。                        */
/* ------------------------------------------------------------------ */
#define CTRL_MAGIC_V3       0x4D44u   /* 控制头 v3 魔数, 小端即 ASCII "DM" */
#define CTRL_BYTES_V3       8u
#define CTRL_V3_OFF_MAGIC   0u
#define CTRL_V3_OFF_MODE    2u
#define CTRL_V3_OFF_FLAGS   4u
#define CTRL_V3_OFF_AIPARAM 6u

/* ------------------------------------------------------------------ */
/*  控制头(v4): 12 字节, 多了**逐帧可调的阈值**(阈值分割 mode1 用)     */
/*                                                                     */
/*     偏移  长度  字段      说明                                      */
/*     ----  ----  --------  ----------------------------------------  */
/*      0     2    magic     固定 0x4D45(小端即 ASCII "EM")            */
/*      2     2    mode      预处理模式 0..5(语义同 v2/v3)              */
/*      4     2    flags     回传内容位图(同 v3)                        */
/*      6     2    aiparam   AI 参数(同 v3)                             */
/*      8     2    thr       阈值分割的门限 0..255(低字节有效)          */
/*     10     2    reserved  保留(写 0)                                 */
/*                                                                     */
/*  为什么加这一版: v2/v3 里阈值只是**板端命令行**选项, 上位机够不着;   */
/*  而"阈值分割"要按图调, 所以必须逐帧传。                              */
/*  板子仍然**先读 2 字节 magic 再决定读几字节** —— v2/v3 老客户端照旧。*/
/*  (伪彩映射 mode3 在 PL 里是直接 jet[gray], 没有阈值参数, 不在协议内)  */
/* ------------------------------------------------------------------ */
#define CTRL_MAGIC_V4       0x4D45u   /* 控制头 v4 魔数, 小端即 ASCII "EM" */
#define CTRL_BYTES_V4       12u
#define CTRL_V4_OFF_MAGIC   0u
#define CTRL_V4_OFF_MODE    2u
#define CTRL_V4_OFF_FLAGS   4u
#define CTRL_V4_OFF_AIPARAM 6u
#define CTRL_V4_OFF_THR     8u
#define CTRL_V4_OFF_RESV    10u
#define CTRL_THR_DEFAULT    128u      /* 不带 thr 的老头 / 板端 --thr 的默认 */

/* ------------------------------------------------------------------ */
/*  控制头(v5): 20 字节 = v4 的 12 字节 + ROI 框(局部推理, 2026-10-07) */
/*                                                                     */
/*     偏移  长度  字段      说明                                      */
/*      0..11      同 v4(magic 换成 0x4D46, 小端即 ASCII "FM")         */
/*     12     2    roi_x     ROI 左上角 x(512x512 图像坐标)            */
/*     14     2    roi_y     ROI 左上角 y                              */
/*     16     2    roi_w     ROI 宽; roi_w 或 roi_h 为 0 = 不用 ROI     */
/*     18     2    roi_h     ROI 高                                    */
/*                                                                     */
/*  有 ROI 时板端只在"ROI 外扩 24 像素、起点对齐到 4、尺寸取 8 的倍数" */
/*  的裁剪窗口上跑整网, 再把 ROI 内的 logit 贴回 512x512(ROI 外 = -128)。*/
/*  ROI 内的结果与整图推理逐值一致(证明见 ai/net/roi_exactness.py)。    */
/*  上位机只在统计块 version >= 3 的板端上发 v5 头。                    */
/* ------------------------------------------------------------------ */
#define CTRL_MAGIC_V5       0x4D46u
#define CTRL_BYTES_V5       20u
#define CTRL_V5_OFF_ROI_X   12u
#define CTRL_V5_OFF_ROI_Y   14u
#define CTRL_V5_OFF_ROI_W   16u
#define CTRL_V5_OFF_ROI_H   18u
#define ROI_OUTSIDE_LOGIT   (-128)    /* ROI 模式下 ROI 外的 logit(不算血管) */

/* flags: 让上位机挑"回传什么" */
#define FLAG_ECHO_PROC   0x0001u  /* b0 回传预处理图(= v2 的 --echo 行为, 默认) */
#define FLAG_ECHO_MASK   0x0002u  /* b1 回传 AI 血管掩膜图(0/255 展开成 RGB) */
#define FLAG_ECHO_OVER   0x0004u  /* b2 回传掩膜叠加图(掩膜以红色叠在预处理图上) */
#define FLAG_STATS       0x0008u  /* b3 在图像流之前先回一个 64B 统计块 */
#define FLAG_NO_IMAGE    0x0010u  /* b4 只做识别, 不回传任何图像 */
#define FLAG_ECHO_LOGIT  0x0020u  /* b5 回传 AI 原始 logit 图(排在掩膜图之后):
                                   *    每像素 R=G=B=logit+128(偏移码), 与掩膜同样最近邻放大到
                                   *    512x512。上位机据此画置信度热力图、本地改门限重算掩膜。
                                   *    只有统计块 version>=2 的板端才认这一位(见 ai_view.h) */
#define FLAG_MORPH       0x0040u  /* b6 血管形态指标(src/vessel_morph.h): 统计块之后多一个 64 字节
                                   *    指标块(要同时带 b3), 图像最后多一幅"形态骨架"视图。
                                   *    只有统计块 version>=4 的板端认这一位 */
#define FLAG_DEFAULT     FLAG_ECHO_PROC
#define LOGIT_IMG_OFFSET 128      /* logit 图的偏移码: 像素值 = logit + 128 */

/* aiparam */
#define AIPARAM_SRC_RAW      0u   /* AI 输入 = 收到的原始 RGB */
#define AIPARAM_SRC_PROC     1u   /* AI 输入 = PL 预处理后的结果 */
#define AIPARAM_THR_DEFAULT  (-3) /* logit >= thr 判为血管(模型量化阈值) */

/* aiparam bits[4:2] = 工作分辨率档位: 分辨率 = 512 >> shift。
 * 0(默认) = 512 全分辨率; 1 = 256; 2 = 128; 3 = 64; 4 = 32。
 * 条带调度对不同档位都成立, 所以这只是"质量 vs 速度"的旋钮, 不是能不能跑的问题。 */
#define AIPARAM_RES_SHIFT    2u
#define AIPARAM_RES_MASK     0x001Cu
#define AIPARAM_RES_DEFAULT  0u   /* 0 = 不自选, 用板端 --ai-res 的默认值 */

/* 上线总字节数 = 控制头 + 图像流(与 PC 端 proto.py 的 WIRE_TOTAL_BYTES 一致) */
#define WIRE_TOTAL_BYTES (CTRL_BYTES + STREAM_TOTAL_BYTES)
#define WIRE_TOTAL_BYTES_V3 (CTRL_BYTES_V3 + STREAM_TOTAL_BYTES)
#define WIRE_TOTAL_BYTES_V4 (CTRL_BYTES_V4 + STREAM_TOTAL_BYTES)

/* ------------------------------------------------------------------ */
/*  小端序读写辅助(显式按字节拼装, 不依赖主机字节序)                  */
/* ------------------------------------------------------------------ */

/** @brief 从缓冲区读一个小端 16 位数。 */
static inline uint16_t proto_get_le16(const uint8_t *p)
{
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

/** @brief 向缓冲区写一个小端 16 位数。 */
static inline void proto_put_le16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
}

#endif /* IMAGE_PROTO_H */
