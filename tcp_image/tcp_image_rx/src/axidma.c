/**
 * @file  axidma.c
 * @brief AXI DMA(MM2S, simple 模式)驱动实现。寄存器定义见 axidma.h。
 */
/* makefile 里也会传 -D_GNU_SOURCE; 这里用 #ifndef 兜底, 避免重复定义告警 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include "axidma.h"

#include <stdio.h>
#include <unistd.h>

/* ------------------------------------------------------------------ */
/*  寄存器读写小工具                                                  */
/* ------------------------------------------------------------------ */

/** @brief 读 MM2S 寄存器(off 为字节偏移, 必须 4 字节对齐)。 */
static inline uint32_t reg_rd(const axidma_t *dma, uint32_t off)
{
    return dma->regs[off / 4u];
}

/** @brief 写 MM2S 寄存器(off 为字节偏移, 必须 4 字节对齐)。 */
static inline void reg_wr(const axidma_t *dma, uint32_t off, uint32_t val)
{
    dma->regs[off / 4u] = val;
}

/* ------------------------------------------------------------------ */
/*  有界字符串拼接(避免 snprintf 截断告警, 也避免任何越界)            */
/* ------------------------------------------------------------------ */
static size_t append_str(char *buf, size_t pos, size_t cap, const char *s)
{
    while (*s != '\0' && (pos + 1u) < cap) {
        buf[pos] = *s;
        pos++;
        s++;
    }
    if (cap > 0u) {
        buf[pos] = '\0';
    }
    return pos;
}

/* ------------------------------------------------------------------ */
/*  对外接口                                                          */
/* ------------------------------------------------------------------ */

int axidma_init(axidma_t *dma, volatile uint32_t *mm2s_regs, int addr_width)
{
    uint32_t sr;

    if (dma == NULL || mm2s_regs == NULL) {
        return -2;
    }

    dma->regs         = mm2s_regs;
    dma->sa_msb_valid = (addr_width > 32) ? 1 : 0;
    dma->last_src_addr = 0u;
    dma->last_length   = 0u;

    /* 本驱动只支持 simple 模式; 若 IP 被配成 SG 模式则明确报错, 不静默继续。 */
    sr = reg_rd(dma, XAXIDMA_MM2S_DMASR_OFF);
    if ((sr & XAXIDMA_SR_SGINCLD_MASK) != 0u) {
        return -3;
    }

    return 0;
}

int axidma_reset(axidma_t *dma, int timeout_ms)
{
    int i;

    if (dma == NULL || dma->regs == NULL || timeout_ms <= 0) {
        return -2;
    }

    reg_wr(dma, XAXIDMA_MM2S_DMACR_OFF, XAXIDMA_CR_RESET_MASK);

    for (i = 0; i < timeout_ms; i++) {
        /* RESET 位是自清零的: 读到 0 说明复位完成 */
        if ((reg_rd(dma, XAXIDMA_MM2S_DMACR_OFF) & XAXIDMA_CR_RESET_MASK) == 0u) {
            return 0;
        }
        usleep(1000);
    }

    return -1;
}

int axidma_start_mm2s(axidma_t *dma, uint64_t src_addr, uint32_t length)
{
    uint32_t cr;
    uint32_t sr;

    if (dma == NULL || dma->regs == NULL) {
        return -2;
    }
    if (length == 0u || length > XAXIDMA_MAX_TRANSFER_BYTES) {
        return -2;
    }
    if (dma->sa_msb_valid == 0 && src_addr > 0xFFFFFFFFull) {
        return -2;   /* 32 位地址宽度的 IP 搬不到 4 GiB 以上 */
    }

    sr = reg_rd(dma, XAXIDMA_MM2S_DMASR_OFF);
    if ((sr & XAXIDMA_SR_SGINCLD_MASK) != 0u) {
        return -3;
    }

    /* 1) 清历史状态(写 1 清零) */
    reg_wr(dma, XAXIDMA_MM2S_DMASR_OFF, (sr & XAXIDMA_SR_W1C_MASK));

    /* 2) 使能运行, 并使能 IOC —— 轮询判断"传输完成"依赖 IOC_Irq 置位,
     *    而 IOC_Irq 只有在 DMACR.IOC_IrqEn = 1 时才会被硬件置起。 */
    cr  = reg_rd(dma, XAXIDMA_MM2S_DMACR_OFF);
    cr &= ~XAXIDMA_CR_RESET_MASK;
    cr |= (XAXIDMA_CR_RS_MASK | XAXIDMA_CR_IOC_IRQ_EN_MASK);
    reg_wr(dma, XAXIDMA_MM2S_DMACR_OFF, cr);

    /* 3) 写源地址 */
    reg_wr(dma, XAXIDMA_MM2S_SA_OFF, (uint32_t)(src_addr & 0xFFFFFFFFu));
    if (dma->sa_msb_valid != 0) {
        reg_wr(dma, XAXIDMA_MM2S_SA_MSB_OFF, (uint32_t)(src_addr >> 32));
    }

    dma->last_src_addr = src_addr;
    dma->last_length   = length;

    /* 4) 屏障: 保证前面几次寄存器写在 LENGTH 之前真正落到总线 */
    __sync_synchronize();

    /* 5) 写 LENGTH 即启动传输(必须最后写) */
    reg_wr(dma, XAXIDMA_MM2S_LENGTH_OFF, length);

    return 0;
}

int axidma_wait_done(axidma_t *dma, int timeout_ms)
{
    int      i;
    uint32_t sr;

    if (dma == NULL || dma->regs == NULL || timeout_ms <= 0) {
        return -3;
    }

    for (i = 0; i < timeout_ms; i++) {
        sr = reg_rd(dma, XAXIDMA_MM2S_DMASR_OFF);

        /* 任一错误位置起即判定失败, 交给调用者打印诊断 */
        if ((sr & (XAXIDMA_SR_DMAINTERR_MASK |
                   XAXIDMA_SR_DMASLVERR_MASK |
                   XAXIDMA_SR_DMADECERR_MASK)) != 0u) {
            return -2;
        }

        if ((sr & XAXIDMA_SR_IOC_IRQ_MASK) != 0u) {
            /* 清掉 IOC, 免得干扰下一次判断 */
            reg_wr(dma, XAXIDMA_MM2S_DMASR_OFF, XAXIDMA_SR_IOC_IRQ_MASK);
            return 0;
        }

        usleep(1000);
    }

    return -1;
}

uint32_t axidma_read_status(const axidma_t *dma)
{
    if (dma == NULL || dma->regs == NULL) {
        return 0u;
    }
    return reg_rd(dma, XAXIDMA_MM2S_DMASR_OFF);
}

void axidma_format_status(uint32_t sr, char *buf, size_t buf_len)
{
    char   hex[16];
    size_t pos = 0u;

    if (buf == NULL || buf_len == 0u) {
        return;
    }
    buf[0] = '\0';

    (void)snprintf(hex, sizeof(hex), "0x%08X", (unsigned)sr);

    pos = append_str(buf, pos, buf_len, "DMASR=");
    pos = append_str(buf, pos, buf_len, hex);
    pos = append_str(buf, pos, buf_len, " [");
    pos = append_str(buf, pos, buf_len,
                     (sr & XAXIDMA_SR_HALTED_MASK) != 0u ? "HALTED" : "running");
    pos = append_str(buf, pos, buf_len,
                     (sr & XAXIDMA_SR_IDLE_MASK) != 0u ? " idle" : " busy");
    pos = append_str(buf, pos, buf_len,
                     (sr & XAXIDMA_SR_SGINCLD_MASK) != 0u ? " SG" : " simple");
    if ((sr & XAXIDMA_SR_DMAINTERR_MASK) != 0u) {
        pos = append_str(buf, pos, buf_len, " DMAIntErr");
    }
    if ((sr & XAXIDMA_SR_DMASLVERR_MASK) != 0u) {
        pos = append_str(buf, pos, buf_len, " DMASlvErr");
    }
    if ((sr & XAXIDMA_SR_DMADECERR_MASK) != 0u) {
        pos = append_str(buf, pos, buf_len, " DMADecErr");
    }
    if ((sr & XAXIDMA_SR_IOC_IRQ_MASK) != 0u) {
        pos = append_str(buf, pos, buf_len, " IOC");
    }
    if ((sr & XAXIDMA_SR_ERR_IRQ_MASK) != 0u) {
        pos = append_str(buf, pos, buf_len, " ErrIRQ");
    }
    (void)append_str(buf, pos, buf_len, "]");
}

/* ------------------------------------------------------------------ */
/*  S2MM 通道(Stream -> DDR): 把 PL 处理完的图像写回 DDR              */
/* ------------------------------------------------------------------ */

int axidma_reset_s2mm(axidma_t *dma, int timeout_ms)
{
    int i;

    if (dma == NULL || dma->regs == NULL || timeout_ms <= 0) {
        return -2;
    }

    reg_wr(dma, XAXIDMA_S2MM_DMACR_OFF, XAXIDMA_CR_RESET_MASK);

    for (i = 0; i < timeout_ms; i++) {
        if ((reg_rd(dma, XAXIDMA_S2MM_DMACR_OFF) & XAXIDMA_CR_RESET_MASK) == 0u) {
            return 0;
        }
        usleep(1000);
    }
    return -1;
}

int axidma_start_s2mm(axidma_t *dma, uint64_t dst_addr, uint32_t length)
{
    uint32_t cr;
    uint32_t sr;

    if (dma == NULL || dma->regs == NULL) {
        return -2;
    }
    if (length == 0u || length > XAXIDMA_MAX_TRANSFER_BYTES) {
        return -2;
    }
    if (dma->sa_msb_valid == 0 && dst_addr > 0xFFFFFFFFull) {
        return -2;
    }

    sr = reg_rd(dma, XAXIDMA_S2MM_DMASR_OFF);
    if ((sr & XAXIDMA_SR_SGINCLD_MASK) != 0u) {
        return -3;
    }

    /* 1) 清历史状态 */
    reg_wr(dma, XAXIDMA_S2MM_DMASR_OFF, (sr & XAXIDMA_SR_W1C_MASK));

    /* 2) 使能运行 + 完成中断(IOC 置位才有得轮询) */
    cr  = reg_rd(dma, XAXIDMA_S2MM_DMACR_OFF);
    cr &= ~XAXIDMA_CR_RESET_MASK;
    cr |= (XAXIDMA_CR_RS_MASK | XAXIDMA_CR_IOC_IRQ_EN_MASK);
    reg_wr(dma, XAXIDMA_S2MM_DMACR_OFF, cr);

    /* 3) 目的地址 */
    reg_wr(dma, XAXIDMA_S2MM_DA_OFF, (uint32_t)(dst_addr & 0xFFFFFFFFu));
    if (dma->sa_msb_valid != 0) {
        reg_wr(dma, XAXIDMA_S2MM_DA_MSB_OFF, (uint32_t)(dst_addr >> 32));
    }

    dma->last_src_addr = dst_addr;
    dma->last_length   = length;

    /* 4) 屏障, 保证前面的寄存器写先落地 */
    __sync_synchronize();

    /* 5) 写 LENGTH 即启动 */
    reg_wr(dma, XAXIDMA_S2MM_LENGTH_OFF, length);

    return 0;
}

int axidma_wait_s2mm_done(axidma_t *dma, int timeout_ms)
{
    int      i;
    uint32_t sr;

    if (dma == NULL || dma->regs == NULL || timeout_ms <= 0) {
        return -3;
    }

    for (i = 0; i < timeout_ms; i++) {
        sr = reg_rd(dma, XAXIDMA_S2MM_DMASR_OFF);

        if ((sr & (XAXIDMA_SR_DMAINTERR_MASK |
                   XAXIDMA_SR_DMASLVERR_MASK |
                   XAXIDMA_SR_DMADECERR_MASK)) != 0u) {
            return -2;
        }
        if ((sr & XAXIDMA_SR_IOC_IRQ_MASK) != 0u) {
            reg_wr(dma, XAXIDMA_S2MM_DMASR_OFF, XAXIDMA_SR_IOC_IRQ_MASK);
            return 0;
        }
        usleep(1000);
    }
    return -1;
}

uint32_t axidma_read_s2mm_status(const axidma_t *dma)
{
    if (dma == NULL || dma->regs == NULL) {
        return 0u;
    }
    return reg_rd(dma, XAXIDMA_S2MM_DMASR_OFF);
}

void axidma_format_s2mm_status(uint32_t sr, char *buf, size_t buf_len)
{
    /* 位域含义与 MM2S 相同, 只是加个 S2MM 前缀便于分辨 */
    char   tmp[128];
    size_t pos = 0u;

    if (buf == NULL || buf_len == 0u) {
        return;
    }
    buf[0] = '\0';

    axidma_format_status(sr, tmp, sizeof(tmp));
    pos = append_str(buf, pos, buf_len, "S2MM ");
    (void)append_str(buf, pos, buf_len, tmp);
}
