/**
 * @file  axigpio.c
 * @brief AXI GPIO 驱动实现。寄存器定义见 axigpio.h。
 */
/* makefile 里也会传 -D_GNU_SOURCE; 这里用 #ifndef 兜底, 避免重复定义告警 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include "axigpio.h"

/* ------------------------------------------------------------------ */
/*  寄存器读写小工具                                                  */
/* ------------------------------------------------------------------ */

/** @brief 读寄存器(off 为字节偏移, 必须 4 字节对齐)。 */
static inline uint32_t gpio_rd(const axigpio_t *gpio, uint32_t off)
{
    return gpio->regs[off / 4u];
}

/** @brief 写寄存器(off 为字节偏移, 必须 4 字节对齐)。 */
static inline void gpio_wr(const axigpio_t *gpio, uint32_t off, uint32_t val)
{
    gpio->regs[off / 4u] = val;
}

/* ------------------------------------------------------------------ */
/*  对外接口                                                          */
/* ------------------------------------------------------------------ */

int axigpio_init(axigpio_t *gpio, volatile uint32_t *regs)
{
    if (gpio == NULL || regs == NULL) {
        return -1;
    }
    gpio->regs = regs;
    return 0;
}

int axigpio_write_ctrl(axigpio_t *gpio, uint32_t val)
{
    if (gpio == NULL || gpio->regs == NULL) {
        return -1;
    }

    /* ch1 已由 IP 配成全输出(C_ALL_OUTPUTS=1), 方向寄存器由 IP 自己维护,
     * 这里只把 16bit 控制字写到数据寄存器。
     * 屏障放在写之后: 保证这一笔先落到 PL, 再去启动 DMA, 否则 DMA 推流时
     * 处理 IP 可能还用着上一帧的模式。 */
    gpio_wr(gpio, XAXIGPIO_DATA_OFF, val & 0xFFFFu);
    __sync_synchronize();

    return 0;
}

uint32_t axigpio_read_stat(const axigpio_t *gpio)
{
    if (gpio == NULL || gpio->regs == NULL) {
        return 0u;
    }
    return gpio_rd(gpio, XAXIGPIO_DATA2_OFF) & 0xFFFFu;
}
