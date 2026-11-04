/**
 * @file  ddr_layout.h
 * @brief 预留 DDR 区的内存布局定义 —— 收下来的图像存在哪里、状态存在哪里。
 *
 *  这块区域由设备树 reserved-memory(no-map)从 Linux 手里挖走
 *  (见 dts/zynqmp-image-reserved-memory.dtsi), 于是:
 *    - Linux 内核不会把它当普通内存分配, 不会被页缓存/伙伴系统污染;
 *    - 用户态通过 /dev/mem 映射后按"设备内存(非缓存)"访问,
 *      CPU 写进去的数据立即可见, 后续 AXI DMA 读 DDR 时无需额外刷 cache。
 *
 *  物理布局(相对保留区基址, 默认基址见 tcp_image_server.c 的 --ddr-addr):
 *
 *      偏移 0x000000  ┌────────────────────────────────┐
 *                     │ 输入图区 (32bit/像素)           │ 1048576 B = 0x100000
 *                     │ 逐行连续, 每行 2048 B           │
 *      偏移 0x100000  ├────────────────────────────────┤
 *                     │ 输出图区 (32bit/像素)           │ 1048576 B
 *      偏移 0x200000  ├────────────────────────────────┤
 *                     │ frame_status 状态结构           │ 64 B
 *      偏移 0x200040  ├────────────────────────────────┤
 *                     │ 其余保留(暂未使用)             │
 *      偏移 0x400000  └────────────────────────────────┘
 *
 *  预留窗口取 4 MiB。注意设备树那边的 reserved-memory 不用改 —— 厂商在
 *  0x40000000 留的是一整块 1 GB 的 no-map 区, 我们只是把窗口从 1 MiB 扩到 4 MiB。
 *
 *  状态结构是给"PL 侧 / 上位机"读的握手信息: 软件写完一帧后把 magic 最后
 *  写下去, 读端轮询 magic 变为 FRAME_MAGIC 即可确认"新帧就绪",
 *  再读 frame_count 判断是否是新的一帧。
 */
#ifndef DDR_LAYOUT_H
#define DDR_LAYOUT_H

#include <stdint.h>

#include "image_proto.h"        /* 用到 IMG_WIDTH / IMG_HEIGHT */

/* ------------------------------------------------------------------ */
/*  保留窗口与各子区偏移                                              */
/* ------------------------------------------------------------------ */
#define RESV_WINDOW_BYTES   0x00400000u   /* 预留窗口大小: 4 MiB */
#define IMG_BUF_OFFSET      0x00000000u   /* 输入图区(给 PL 处理用), 1 MiB */
#define IMG_OUT_OFFSET      0x00100000u   /* 输出图区(PL 处理完写回), 1 MiB */
#define IMG_STAT_OFFSET     0x00200000u   /* 状态区, 32 B */

/* ------------------------------------------------------------------ */
/*  DDR 里的图像格式: 32bit/像素, 不是紧包 RGB888                     */
/* ------------------------------------------------------------------ */
/*
 *  DDR 里必须按 4 字节/像素摆放:
 *
 *      31      24 23      16 15       8 7        0
 *      ┌─────────┬──────────┬──────────┬──────────┐
 *      │  8'h00  │    R     │    G     │    B     │
 *      └─────────┴──────────┴──────────┴──────────┘
 *
 *  原因: AXI DMA 按 4 字节搬运, 紧包的 3 字节/像素会让**像素跨 beat 边界**,
 *  PL 侧对不齐。所以 TCP 收到紧包数据后, 由软件转成 32bit/像素再交给 DMA。
 */
#define IMG_PIX_BYTES       4u                              /* DDR 里每像素 4 字节 */
#define IMG_DDR_ROW         (IMG_WIDTH * IMG_PIX_BYTES)     /* 一行 2048 字节 */
#define IMG_DDR_BYTES       (IMG_DDR_ROW * IMG_HEIGHT)      /* 整幅 1048576 字节 */

/* ------------------------------------------------------------------ */
/*  状态结构                                                          */
/* ------------------------------------------------------------------ */

/**
 * "一帧就绪"魔数。写成 0x314D5246 是因为本机(以及 ARM64 Linux)按小端存放,
 * 落到内存里的 4 个字节依次是 'F','R','M','1', 便于用 hexdump 肉眼确认。
 */
#define FRAME_MAGIC         0x314D5246u

/**
 * @brief 图像帧状态块(放在 IMG_STAT_OFFSET 处)。
 *
 * 写序约定: **magic 必须最后写**。写端先写其余字段(此时 magic 保持 0),
 * 全部写完后才把 magic 单独写下去; 读端以 magic 从"非 FRAME_MAGIC"变为
 * FRAME_MAGIC 作为"这一帧所有字段都已可见"的标志。
 * (发布函数 publish_status() 就是按这个顺序写的: 除 magic 外的字先写, magic 最后。)
 *
 * 大小固定在 64 字节(编译期断言校验), 留了保留字段供以后扩展;
 * 改了字段就要同步改 tcp_image_server.c 里的 IMG_STAT_BYTES。
 */
struct frame_status {
    uint32_t magic;         /* "帧就绪"标志, 必须最后写 */
    uint32_t frame_count;   /* 累计成功收满的帧数, 从 1 开始递增 */
    uint32_t rows_ok;       /* 本帧 CRC 正确并已落盘的行数(正常 = 512) */
    uint32_t crc_errors;    /* 本帧 CRC 校验失败的行数 */
    uint32_t seq_errors;    /* 本帧行号越界 / 长度不符的包数 */
    uint32_t bytes_recv;    /* 本帧从 TCP 实际收到的字节数(正常 = 789504) */
    uint32_t frame_crc16;   /* 整幅图像的 CRC16(按行 0→511 顺序累积, 仅 -v 时算) */
    uint32_t dma_status;    /* MM2S 传输结束后 MM2S_DMASR 的快照 */
    uint32_t s2mm_status;   /* S2MM 传输结束后 S2MM_DMASR 的快照 */
    uint32_t mode;          /* 本帧控制头里的处理模式(0..5) */
    uint32_t proc_min;      /* PL 统计的本帧最小灰度(0..255, 取自模式寄存器旁路) */
    uint32_t proc_max;      /* PL 统计的本帧最大灰度(0..255) */
    uint32_t ai_vessels;    /* 本帧 AI 判为血管的像素数(网络分辨率下) */
    uint32_t ai_comps;      /* 本帧血管连通域数(4 邻域) */
    uint32_t resv[2];       /* 保留: 让整块正好 64 B, 便于以后加字段 */
};

#endif /* DDR_LAYOUT_H */
