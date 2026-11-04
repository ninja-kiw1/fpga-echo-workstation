/**
 * @file  axigpio.h
 * @brief AXI GPIO(双通道)的寄存器级驱动 —— 给 PL 送"处理模式", 再读回帧统计。
 *
 *  在 block design 里:
 *      ch1(输出 16bit) -> img_proc_axis.ctrl   ([2:0] 模式, [15:8] 阈值)
 *      ch2(输入  16bit) <- {stat_max, stat_min}(PL 统计的本帧灰度极值)
 *
 *  为什么也用 /dev/mem 直接写寄存器(而不是内核 gpio 驱动):
 *    同 axidma —— 学习项目里对照 PG144 手册逐位看最直观, 且不需要额外内核模块。
 *    我们只有"写一个字 / 读一个字"两种操作, 用不着字符设备那一套。
 *
 *  寄存器偏移与位域取自 Xilinx PG144(AXI GPIO v2.0)。
 */
#ifndef AXIGPIO_H
#define AXIGPIO_H

#include <stdint.h>
#include <stddef.h>

/* ------------------------------------------------------------------ */
/*  寄存器偏移(相对 AXI GPIO 基址)                                    */
/* ------------------------------------------------------------------ */
#define XAXIGPIO_DATA_OFF   0x0000u   /* ch1 数据(输出时写这里) */
#define XAXIGPIO_TRI_OFF    0x0004u   /* ch1 方向: 1=输入, 0=输出 */
#define XAXIGPIO_DATA2_OFF  0x0008u   /* ch2 数据(输入时读这里) */
#define XAXIGPIO_TRI2_OFF   0x000Cu   /* ch2 方向 */

/** @brief 驱动实例(只保存映射好的寄存器基址)。 */
typedef struct {
    volatile uint32_t *regs;          /* AXI GPIO 寄存器基址(已 mmap 到用户态) */
} axigpio_t;

/**
 * @brief 初始化句柄。
 *
 * @param gpio  驱动实例。
 * @param regs  已 mmap 的 AXI GPIO 寄存器基址。
 * @return 0 成功; -1 参数为空。
 */
int axigpio_init(axigpio_t *gpio, volatile uint32_t *regs);

/**
 * @brief 写 ch1(模式寄存器)。
 *
 *  ch1 在 BD 里配成 C_ALL_OUTPUTS=1, 方向寄存器由 IP 自己管, 这里只写数据。
 *
 * @param gpio  驱动实例。
 * @param val   16bit 控制字: 见 image_proto.h / image_processing_design.md ——
 *              [2:0]=模式 0..4, [15:8]=阈值, [7:3] 保留。
 * @return 0 成功; -1 句柄无效。
 */
int axigpio_write_ctrl(axigpio_t *gpio, uint32_t val);

/**
 * @brief 读 ch2(帧统计)。
 *
 * @return ch2 原始 16bit 值: 高 8 位 = stat_max, 低 8 位 = stat_min;
 *         句柄无效时返回 0。
 */
uint32_t axigpio_read_stat(const axigpio_t *gpio);

#endif /* AXIGPIO_H */
