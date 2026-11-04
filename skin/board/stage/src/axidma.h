/**
 * @file  axidma.h
 * @brief AXI DMA(simple 模式, 仅 MM2S 通道)的寄存器级驱动。
 *
 *  用途: 图像收完后, 让 DMA 把 DDR 里的图像搬到 PL 侧的 AXI4-Stream
 *        (Memory-Mapped to Stream, 简称 MM2S), 交给 PL 做后续处理。
 *
 *  为什么直接用 /dev/mem 写寄存器, 而不是用内核 xilinx_dma 驱动?
 *    - 学习项目里这样最直观: 寄存器一览无余, 对应 PG021 手册可逐位对照;
 *    - 不必额外编译内核模块 / 处理 dmaengine 接口;
 *    - 我们只用 simple 模式、每次一整幅、无中断轮询, 逻辑量很小。
 *  生产环境若要多路并发/用中断, 再换成 xilinx_dma + dma-proxy 更合适。
 *
 *  寄存器偏移与位域均取自 Xilinx PG021(AXI DMA v7.1)的 MM2S 部分。
 */
#ifndef AXIDMA_H
#define AXIDMA_H

#include <stdint.h>
#include <stddef.h>

/* ------------------------------------------------------------------ */
/*  MM2S 寄存器偏移(simple 模式)                                      */
/* ------------------------------------------------------------------ */
#define XAXIDMA_MM2S_DMACR_OFF    0x00u   /* 控制寄存器 */
#define XAXIDMA_MM2S_DMASR_OFF    0x04u   /* 状态寄存器 */
#define XAXIDMA_MM2S_SA_OFF       0x18u   /* 源地址低 32 位 */
#define XAXIDMA_MM2S_SA_MSB_OFF   0x1Cu   /* 源地址高 32 位(地址宽度 > 32 时才有) */
#define XAXIDMA_MM2S_LENGTH_OFF   0x28u   /* 传输长度, 最后写, 写入即启动 */

/* ------------------------------------------------------------------ */
/*  S2MM 寄存器偏移(相对 DMA 基址; S2MM 块从 0x30 开始)               */
/* ------------------------------------------------------------------ */
#define XAXIDMA_S2MM_DMACR_OFF    0x30u   /* 控制寄存器 */
#define XAXIDMA_S2MM_DMASR_OFF    0x34u   /* 状态寄存器 */
#define XAXIDMA_S2MM_DA_OFF       0x48u   /* 目的地址低 32 位 */
#define XAXIDMA_S2MM_DA_MSB_OFF   0x4Cu   /* 目的地址高 32 位 */
#define XAXIDMA_S2MM_LENGTH_OFF   0x58u   /* 传输长度, 最后写, 写入即启动 */

/* ------------------------------------------------------------------ */
/*  MM2S_DMACR (0x00) 位域                                            */
/* ------------------------------------------------------------------ */
#define XAXIDMA_CR_RS_MASK         0x00000001u  /* bit0  Run/Stop: 1 = 运行 */
#define XAXIDMA_CR_RESET_MASK      0x00000004u  /* bit2  软复位(自清零) */
#define XAXIDMA_CR_IOC_IRQ_EN_MASK 0x00001000u  /* bit12 完成中断使能 */
#define XAXIDMA_CR_ERR_IRQ_EN_MASK 0x00004000u  /* bit14 错误中断使能 */

/* ------------------------------------------------------------------ */
/*  MM2S_DMASR (0x04) 位域(均为写 1 清零)                            */
/* ------------------------------------------------------------------ */
#define XAXIDMA_SR_HALTED_MASK     0x00000001u  /* bit0  1 = 已停止 */
#define XAXIDMA_SR_IDLE_MASK       0x00000002u  /* bit1  1 = 空闲 */
#define XAXIDMA_SR_SGINCLD_MASK    0x00000008u  /* bit3  1 = 该 IP 是 SG 模式 */
#define XAXIDMA_SR_DMAINTERR_MASK  0x00000010u  /* bit4  内部错误 */
#define XAXIDMA_SR_DMASLVERR_MASK  0x00000020u  /* bit5  从机接口错误 */
#define XAXIDMA_SR_DMADECERR_MASK  0x00000040u  /* bit6  目标地址译码错误 */
#define XAXIDMA_SR_IOC_IRQ_MASK    0x00001000u  /* bit12 传输完成 */
#define XAXIDMA_SR_DLY_IRQ_MASK    0x00002000u  /* bit13 延时中断 */
#define XAXIDMA_SR_ERR_IRQ_MASK    0x00004000u  /* bit14 错误中断 */

/** 所有"写 1 清零"位的集合(用于发起传输前清干净历史状态)。 */
#define XAXIDMA_SR_W1C_MASK  (XAXIDMA_SR_DMAINTERR_MASK | \
                              XAXIDMA_SR_DMASLVERR_MASK | \
                              XAXIDMA_SR_DMADECERR_MASK | \
                              XAXIDMA_SR_IOC_IRQ_MASK   | \
                              XAXIDMA_SR_DLY_IRQ_MASK   | \
                              XAXIDMA_SR_ERR_IRQ_MASK)

/** 单次 simple 传输的最大字节数(LENGTH 字段为 26 位)。 */
#define XAXIDMA_MAX_TRANSFER_BYTES  0x03FFFFFFu

/** @brief 驱动实例。 */
typedef struct {
    volatile uint32_t *regs;        /* MM2S 寄存器基址(已 mmap 到用户态) */
    int         sa_msb_valid;       /* 地址宽度 > 32 位时, 需写 SA_MSB */
    uint64_t    last_src_addr;      /* 最近一次传输的源地址(用于诊断打印) */
    uint32_t    last_length;        /* 最近一次传输的长度 */
} axidma_t;

/**
 * @brief 初始化驱动句柄。
 *
 * @param dma           驱动实例。
 * @param mm2s_regs     MM2S 寄存器基地址(mmapped)。
 * @param addr_width    IP 的地址宽度(位数), 用于判断是否需要写 SA_MSB。
 * @return 0 成功; 负值失败。
 */
int axidma_init(axidma_t *dma, volatile uint32_t *mm2s_regs, int addr_width);

/**
 * @brief 软复位 MM2S 通道, 并等待复位位自清零。
 *
 * @param timeout_ms 轮询超时(毫秒)。
 * @return 0 成功; -1 超时; -2 参数非法。
 */
int axidma_reset(axidma_t *dma, int timeout_ms);

/**
 * @brief 发起一次 MM2S(simple)传输: 把 DDR 里的 length 字节推到 AXI-Stream。
 *
 *  顺序遵循 PG021: 先写 DMACR(使能运行 + 完成中断), 清历史状态,
 *  再写源地址, **最后写 LENGTH** —— 写 LENGTH 的动作本身就是启动传输。
 *
 * @param dma       驱动实例。
 * @param src_addr  源物理地址(DDR 中图像缓冲区地址)。
 * @param length    传输字节数, 1 .. XAXIDMA_MAX_TRANSFER_BYTES。
 * @return 0 成功; 负值表示参数非法或 IP 处于 SG 模式。
 */
int axidma_start_mm2s(axidma_t *dma, uint64_t src_addr, uint32_t length);

/**
 * @brief 轮询等待当前 MM2S 传输完成。
 *
 * @param timeout_ms 超时(毫秒)。
 * @return  0 完成(IOC 置位);
 *         -1 超时;
 *         -2 传输错误(内部/从机/译码错误);
 *         -3 参数非法。
 */
int axidma_wait_done(axidma_t *dma, int timeout_ms);

/** @brief 读取原始 MM2S_DMASR。 */
uint32_t axidma_read_status(const axidma_t *dma);

/**
 * @brief 软复位 S2MM 通道。
 * @return 0 成功; -1 超时; -2 参数非法。
 */
int axidma_reset_s2mm(axidma_t *dma, int timeout_ms);

/**
 * @brief 发起一次 S2MM(simple)传输: 把 AXI-Stream 的数据写进 DDR。
 *
 *  顺序与 MM2S 对称: 先 DMACR, 清状态, 再写目的地址, **最后写 LENGTH** 启动。
 *
 * @return 0 成功; 负值表示参数非法或 IP 处于 SG 模式。
 */
int axidma_start_s2mm(axidma_t *dma, uint64_t dst_addr, uint32_t length);

/**
 * @brief 轮询等待当前 S2MM 传输完成。
 * @return 0 完成; -1 超时; -2 传输错误; -3 参数非法。
 */
int axidma_wait_s2mm_done(axidma_t *dma, int timeout_ms);

/** @brief 读取原始 S2MM_DMASR。 */
uint32_t axidma_read_s2mm_status(const axidma_t *dma);

/**
 * @brief 把 S2MM_DMASR 位域翻译成一行可读文本。
 */
void axidma_format_s2mm_status(uint32_t sr, char *buf, size_t buf_len);

/**
 * @brief 把 DMASR 位域翻译成一行可读文本(英文, 避免串口日志中文乱码)。
 *
 * @param sr       DMASR 原始值。
 * @param buf      输出缓冲区。
 * @param buf_len  缓冲区长度。
 */
void axidma_format_status(uint32_t sr, char *buf, size_t buf_len);

#endif /* AXIDMA_H */
