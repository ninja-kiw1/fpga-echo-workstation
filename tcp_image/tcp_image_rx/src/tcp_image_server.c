/**
 * @file  tcp_image_server.c
 * @brief 网口 TCP 图像接收程序(PetaLinux / ARM64 Linux 用户态)。
 *
 *  功能
 *  ----
 *  作为 TCP 服务端监听指定端口, 接收上位机(PC)发来的 512x512 RGB888 图像。
 *  图像流最前面带控制头(magic + 处理模式 0..6 + 回传内容 + AI 参数 + 阈值,
 *  见 image_proto.h), 其后切成 512 个"行包", 每包 = 6 字节包头(行号/长度/CRC16)
 *  + 一行 1536 字节像素。逐包校验通过后写入 DDR 里一块**保留内存区**(设备树
 *  no-map 预留), 收满整幅后走完整的 PL 处理往返:
 *
 *     1) 把紧包 RGB888 展开成 DDR 里要求的 **32bit/像素** {8'h00,R,G,B}
 *        (展开必须在启动 DMA 之前 —— DMA 直接从这个区读);
 *     2) 若启用 PL 处理: 先把控制头里的模式写进 **AXI GPIO 模式寄存器**
 *        (ctrl=[2:0]模式, [15:8]阈值), 让 img_proc_axis 用对模式;
 *     3) 跑一次 DMA 往返: S2MM **先架好** -> MM2S 把输入图推给 PL ->
 *        img_proc_axis 处理 -> S2MM 把处理结果写回输出图区;
 *     4) 在保留区状态块里写下帧统计(含 MM2S/S2MM 的 DMASR 快照)并最后写
 *        magic=FRAME_MAGIC(给读端一个"新帧就绪"的握手标志);
 *     5) --echo 时把**处理后的图**(32bit/像素 -> 紧包 RGB888)用同一套行包协议
 *        回发给 PC。没开 DMA 时退化成"原图回显"(纯软件联调路径)。
 *
 *  为什么是 Linux 用户态而不是裸机 lwIP
 *  ------------------------------------
 *  板上千兆 PHY(KSZ9031)接在 PS 的 MIO(RGMII), PL 侧拿不到网口信号; 且用户
 *  选定跑 PetaLinux, 于是 TCP 协议栈直接用 Linux 内核现成的, 程序只做 socket
 *  收数据 + 内存搬运, 无需自带协议栈。
 *
 *  用法示例
 *  --------
 *    ./tcp_image_server                       # 默认端口 5000, DDR 0x30000000
 *    ./tcp_image_server -p 6000 -v            # 换端口并打印逐包日志
 *    ./tcp_image_server --no-dma              # 只收图不推 DMA(纯软件联调)
 *    ./tcp_image_server -a 0x40000000 -d 0x40400000 -g 0xA0000000 --echo
 *                                             # 完整 PL 往返, 回传处理后的图
 *
 *  注意: 控制台日志一律用英文。串口/日志经工具抓取后中文可能变乱码,
 *        中文只保留在源码注释和 docs/ 文档里。
 */
/* makefile 里也会传 -D_GNU_SOURCE; 这里用 #ifndef 兜底, 避免重复定义告警 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#include "axidma.h"
#include "axigpio.h"
#include "axilproc.h"
#include "ai_view.h"
#include "crc16.h"
#include "ddr_layout.h"
#include "image_proto.h"
#include "job.h"
#include "pp_ai.h"
#include "progpkg.h"
#include "unet_ai.h"
#include "unet_models.h"
#include "vessel_morph.h"

/* ------------------------------------------------------------------ */
/*  编译期自检: 协议常量之间的算术必须自洽                            */
/* ------------------------------------------------------------------ */
#if IMG_ROW_BYTES != 1536u
#error "IMG_ROW_BYTES 应为 512*3 = 1536"
#endif
#if IMG_TOTAL_BYTES != 786432u
#error "IMG_TOTAL_BYTES 应为 512*512*3 = 786432"
#endif
#if PKT_TOTAL_BYTES != 1542u
#error "PKT_TOTAL_BYTES 应为 6+1536 = 1542"
#endif
#if (PKT_TOTAL_BYTES * PKT_COUNT) != STREAM_TOTAL_BYTES
#error "流总字节数应等于单包长 * 包数"
#endif
#if (PKT_OFF_PAYLOAD + PKT_PAYLOAD_BYTES) != PKT_TOTAL_BYTES
#error "包头 + 负载 应等于单包总长"
#endif
/* DDR 里按 32bit/像素摆放, 所以三个区各占 IMG_DDR_BYTES, 不能按紧包宽度算 */
#if (IMG_BUF_OFFSET + IMG_DDR_BYTES) > IMG_OUT_OFFSET
#error "输入图区与输出图区重叠"
#endif
#if (IMG_OUT_OFFSET + IMG_DDR_BYTES) > IMG_STAT_OFFSET
#error "输出图区与状态区重叠"
#endif

/* ------------------------------------------------------------------ */
/*  默认参数                                                          */
/* ------------------------------------------------------------------ */
#define DEF_TCP_PORT     5000u
#define DEF_DDR_BASE     0x30000000ULL   /* 保留区物理基址(需与设备树一致) */
#define DEF_DMA_BASE     0xA0000000ULL   /* AXI DMA 寄存器基址(BD 分配: 见 bd/build_full_design.tcl) */
#define DEF_GPIO_BASE    0xA0010000ULL   /* AXI GPIO(模式寄存器)基址(BD 分配) */
#define DEF_MEM_DEV      "/dev/mem"
#define DEF_DMA_TIMEOUT  2000            /* DMA 等待超时(毫秒) */
#define DEF_THR          128u            /* 模式 1(阈值分割)的默认阈值 */
#define DEF_AI_CONV      0xA0010000ULL   /* conv_layer 控制寄存器(见 ai/bd/build_ai_sys.tcl) */
#define DEF_AI_ELT       0xA0020000ULL   /* eltwise_layer 控制寄存器 */
#define DEF_AI_BRAM      0xB0000000ULL   /* 张量 BRAM 窗口(HPM1_FPD 的一半地址段) */

/** 状态块在 DDR 中占用的字节数(与 struct frame_status 对应)。 */
#define IMG_STAT_BYTES   64u

#if (IMG_STAT_OFFSET + IMG_STAT_BYTES) > RESV_WINDOW_BYTES
#error "状态结构超出预留窗口"
#endif

/* ------------------------------------------------------------------ */
/*  全局运行状态                                                      */
/* ------------------------------------------------------------------ */

/** @brief 收到 Ctrl-C / SIGTERM 时置 1, 用于优雅退出 accept 循环。 */
static volatile sig_atomic_t g_stop = 0;

/** @brief 本帧各行校验通过的 CRC16。回发时直接复用, 省一次 786 KB 的 CRC 重算。 */
static uint16_t g_row_crc[PKT_COUNT];

static void on_signal(int sig)
{
    (void)sig;
    g_stop = 1;
}

/** @brief SIGHUP: 下一次回到主循环时重新加载程序包目录(装 / 卸程序不用重启服务)。 */
static volatile sig_atomic_t g_reload = 0;

static void on_hup(int sig)
{
    (void)sig;
    g_reload = 1;
}

/**
 * @brief 安装 SIGINT / SIGTERM 处理函数。
 *
 *  ⚠ 这里**必须用 sigaction 而不是 signal()**, 且 **flags 里不能有 SA_RESTART**。
 *  glibc 的 signal() 会默认带上 SA_RESTART, 那样 accept()/recv() 被信号打断后
 *  会被内核**自动重启**, 程序永远收不到 EINTR —— 结果就是 systemd 发 SIGTERM 后
 *  干等到 90 秒超时, 最后 SIGKILL 掉, 日志里留下
 *  "State 'stop-sigterm' timed out ... Failed with result 'timeout'"。
 *  (这是 2026-09-30 做开机自启时从 journal 里发现的。)
 */
static void install_signal_handlers(void)
{
    struct sigaction sa;

    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_signal;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = 0;                    /* 不加 SA_RESTART: 让阻塞调用返回 EINTR */

    (void)sigaction(SIGINT, &sa, NULL);
    (void)sigaction(SIGTERM, &sa, NULL);
    sa.sa_handler = on_hup;
    (void)sigaction(SIGHUP, &sa, NULL);

    /* 对端断开时用返回值处理, 不让 SIGPIPE 杀掉进程 */
    (void)signal(SIGPIPE, SIG_IGN);
}

/* ------------------------------------------------------------------ */
/*  小工具                                                            */
/* ------------------------------------------------------------------ */

/** @brief 单调时钟, 返回微秒(用于细分耗时)。 */
static uint64_t now_us(void)
{
    struct timespec ts;
    (void)clock_gettime(CLOCK_MONOTONIC, &ts);
    return ((uint64_t)ts.tv_sec * 1000000u) + ((uint64_t)ts.tv_nsec / 1000u);
}

/** @brief 单调时钟, 返回毫秒。 */
static uint64_t now_ms(void)
{
    struct timespec ts;
    (void)clock_gettime(CLOCK_MONOTONIC, &ts);
    return ((uint64_t)ts.tv_sec * 1000u) + ((uint64_t)ts.tv_nsec / 1000000u);
}

/** @brief 解析十进制/十六进制整数(strtoull, base=0 支持 0x 前缀)。 */
static int parse_u64(const char *s, uint64_t *out)
{
    char              *end = NULL;
    unsigned long long v;

    if (s == NULL || out == NULL) {
        return -1;
    }
    errno = 0;
    v = strtoull(s, &end, 0);
    if (errno != 0 || end == s || *end != '\0') {
        return -1;
    }
    *out = (uint64_t)v;
    return 0;
}

/**
 * @brief 把物理地址映射到用户态。
 *
 *  用 MAP_SHARED + /dev/mem, 并且打开设备时带 O_SYNC。
 *  更关键的一点: 保留区在设备树里必须写成 reserved-memory 的 **no-map** 节点。
 *  原因: ARM64 的 phys_mem_access_prot() 对 pfn_valid() 为真的普通 RAM 页会返回
 *  可缓存/写合并的映射, 但对 no-map 挖走的区域 pfn_valid() 为假, 于是返回
 *  pgprot_noncached() —— 得到"非缓存(Device)"映射, CPU 写入即刻落到 DDR,
 *  随后 AXI DMA 读到的一定是最新数据, 不需要手动刷 cache。
 *  (若不做 no-map, 就要自己处理 cache 维护, 否则 DMA 可能读到旧数据。)
 *
 *  只接受页对齐地址, 避免引入映射首地址与目标地址的偏移换算。
 *
 * @return 映射首地址; 失败返回 MAP_FAILED。
 */
static void *map_phys(int fd, uint64_t phys, size_t len)
{
    long   page = sysconf(_SC_PAGESIZE);
    void  *p;

    if (page <= 0) {
        page = 4096;
    }
    if ((phys & ((uint64_t)page - 1u)) != 0u) {
        errno = EINVAL;
        return MAP_FAILED;
    }

    p = mmap(NULL, len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, (off_t)phys);
    return p;
}

/**
 * @brief 分配一块匿名内存当作图像缓冲区(bring-up 用, 不碰 /dev/mem)。
 *
 *  用途: 在还没配好设备树保留区、或者只想验证"网络 + 协议 + CRC + 落盘"这条
 *  软件链路时, 用它代替物理地址映射, 完全不触碰任何既有内存。
 *
 *  ⚠ 局限: 匿名页是**可缓存**的, 给 AXI DMA 用会导致 DMA 读到旧数据。
 *         所以这个模式只用于 --no-dma 的软件联调。
 */
static void *anon_map(size_t len)
{
    void *p = mmap(NULL, len, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    return (p == MAP_FAILED) ? NULL : p;
}

/**
 * @brief 把状态块发布到目的地(通常是 /dev/mem 映射的保留区)。
 *
 *  用 memcpy(用户态 store)而不是让内核直接写, 因为 /dev/mem 的
 *  VM_PFNMAP|VM_IO 映射在 copy_to_user 路径上会返回 EFAULT。
 *
 *  ⚠ 写序: **magic(第 0 个字)必须最后写**。先按字把其余字段写下去, 再单独写
 *  magic —— 这样读端一旦看到 FRAME_MAGIC, 就能确信其余字段已经全部可见,
 *  不会读到半新半旧的统计。(IMG_STAT_OFFSET 是 4 字节对齐的, 可以按字访问。)
 */
static void publish_status(uint8_t *dst_base, const struct frame_status *s)
{
    volatile uint32_t *dst = (volatile uint32_t *)(void *)(dst_base + IMG_STAT_OFFSET);
    const uint32_t    *src = (const uint32_t *)(const void *)s;
    const size_t       n   = sizeof(*s) / sizeof(uint32_t);
    size_t             i;

    for (i = 1u; i < n; i++) {
        dst[i] = src[i];
    }
    __sync_synchronize();
    dst[0] = src[0];                       /* magic 最后写 */
}

/* ------------------------------------------------------------------ */
/*  像素格式转换: 紧包 RGB888(3B/像素) <-> DDR 32bit/像素            */
/* ------------------------------------------------------------------ */

/**
 * @brief 紧包 RGB888(3 字节/像素) -> DDR 要求的 32bit/像素 {8'h00,R,G,B}。
 *
 *  DDR 里必须按 4 字节/像素摆放, 否则 AXI DMA 按 beat 搬运时像素会跨 beat 边界,
 *  PL 侧对不齐(见 ddr_layout.h 的说明)。
 *
 *  目标区可能是 /dev/mem 的**非缓存(Device)映射**, 所以按 volatile 32bit 字写,
 *  保证每笔都真的落到 DDR, 不做合并/缓存。
 */
static void expand_rgb888_to_ddr(const uint8_t *src, uint8_t *dst)
{
    volatile uint32_t *out = (volatile uint32_t *)(void *)dst;
    const uint32_t     npix = (uint32_t)IMG_WIDTH * (uint32_t)IMG_HEIGHT;
    uint32_t           i;

    for (i = 0u; i < npix; i++) {
        const uint8_t *p = src + ((size_t)i * IMG_BYTES_PER_PIX);
        out[i] = ((uint32_t)p[0] << 16) |      /* R -> [23:16] */
                 ((uint32_t)p[1] << 8)  |      /* G -> [15:8]  */
                 ((uint32_t)p[2]);             /* B -> [7:0]   */
    }
}

/**
 * @brief DDR 32bit/像素 -> 紧包 RGB888(3 字节/像素), 供回发用。
 *
 *  源区是 /dev/mem 映射(非缓存), 按 volatile 32bit 读。
 */
static void pack_ddr_to_rgb888(const uint8_t *src, uint8_t *dst)
{
    const volatile uint32_t *in = (const volatile uint32_t *)(const void *)src;
    const uint32_t           npix = (uint32_t)IMG_WIDTH * (uint32_t)IMG_HEIGHT;
    uint32_t                 i;

    for (i = 0u; i < npix; i++) {
        uint32_t w = in[i];
        uint8_t *p = dst + ((size_t)i * IMG_BYTES_PER_PIX);

        p[0] = (uint8_t)((w >> 16) & 0xFFu);   /* R */
        p[1] = (uint8_t)((w >>  8) & 0xFFu);   /* G */
        p[2] = (uint8_t)( w        & 0xFFu);   /* B */
    }
}

/* ------------------------------------------------------------------ */
/*  PL 处理往返(一次 DMA: MM2S -> img_proc_axis -> S2MM)             */
/* ------------------------------------------------------------------ */

/**
 * @brief 把输入图区经 PL 处理后写回输出图区。
 *
 *  顺序很关键: **先架 S2MM(水槽), 再开 MM2S(水源)**。反过来的话 MM2S 一推流,
 *  PL 的输出没人接收, m_axis 会背压回 PL 内部, 轻则拖慢重则丢像素。
 *
 *  长度都是 IMG_DDR_BYTES(32bit/像素的整幅), 不是紧包的 IMG_TOTAL_BYTES ——
 *  PL 侧按 4 字节/像素收发。
 *
 * @param mm2s_sr  [out] MM2S_DMASR 快照。
 * @param s2mm_sr  [out] S2MM_DMASR 快照。
 * @return 0 两路都完成; -1 S2MM 启动失败; -2 MM2S 启动失败;
 *         -3 MM2S 未完成/出错; -4 S2MM 未完成/出错。
 */
static int dma_process_frame(axidma_t *dma, uint64_t ddr_base, int timeout_ms,
                             uint32_t *mm2s_sr, uint32_t *s2mm_sr)
{
    uint64_t src = ddr_base + (uint64_t)IMG_BUF_OFFSET;
    uint64_t dst = ddr_base + (uint64_t)IMG_OUT_OFFSET;
    uint32_t len = (uint32_t)IMG_DDR_BYTES;
    int      rc;

    *mm2s_sr = 0u;
    *s2mm_sr = 0u;

    /* 1) 先把 S2MM 架起来(目的地址已写好, 一写 LENGTH 就开始等流) */
    rc = axidma_start_s2mm(dma, dst, len);
    if (rc != 0) {
        *s2mm_sr = axidma_read_s2mm_status(dma);
        return -1;
    }

    /* 2) 再启动 MM2S, 把输入图推给 PL */
    rc = axidma_start_mm2s(dma, src, len);
    if (rc != 0) {
        *mm2s_sr = axidma_read_status(dma);
        return -2;
    }

    /* 3) 等 MM2S 发完(它是上游, 先完成), 再等 S2MM 收完 */
    rc = axidma_wait_done(dma, timeout_ms);
    *mm2s_sr = axidma_read_status(dma);
    if (rc != 0) {
        (void)axidma_wait_s2mm_done(dma, timeout_ms);   /* 尽量收个残局 */
        *s2mm_sr = axidma_read_s2mm_status(dma);
        return -3;
    }

    rc = axidma_wait_s2mm_done(dma, timeout_ms);
    *s2mm_sr = axidma_read_s2mm_status(dma);
    if (rc != 0) {
        return -4;
    }
    return 0;
}

/**
 * @brief 从 socket 精确读满 len 字节(TCP 是字节流, 一次 recv 不保证读全)。
 *
 * @return  0 读满; -1 出错; -2 对端提前关闭。
 */
static int recv_exact(int fd, uint8_t *buf, size_t len)
{
    size_t got = 0u;

    while (got < len) {
        ssize_t n = recv(fd, buf + got, len - got, 0);

        if (n == 0) {
            return -2;                       /* 对端关闭 */
        }
        if (n < 0) {
            if (errno == EINTR) {
                if (g_stop != 0) {
                    return -1;
                }
                continue;
            }
            return -1;
        }
        got += (size_t)n;
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/*  回发(--echo)                                                      */
/* ------------------------------------------------------------------ */

/** @brief 把缓冲区的 len 字节全部写出(处理短写与 EINTR)。 */
static int send_all(int fd, const uint8_t *buf, size_t len)
{
    size_t sent = 0u;

    while (sent < len) {
        ssize_t n = send(fd, buf + sent, len - sent, MSG_NOSIGNAL);

        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        sent += (size_t)n;
    }
    return 0;
}

/**
 * @brief 用同一套行包协议把一整幅图回发给客户端。
 *
 *  上行和下行用的是同一个格式, 所以 PC 端一份解析代码两边通用。
 *
 * @param img            紧包 RGB888(3 字节/像素)的整幅图。
 * @param reuse_row_crc  非 0: 复用收帧时已经校验过的行 CRC(原图回显, 省一次
 *                       786 KB 的 CRC); 0: 逐行重算(回传 PL 处理结果 ——
 *                       行内容变了, 复用的旧 CRC 会让 PC 端全部校验失败)。
 * @return 0 成功; -1 发送失败(对端断开等)。
 */
static int send_result_frame(int cfd, const uint8_t *img, int reuse_row_crc)
{
    /* 整帧一次拼好再发: 系统调用从 512 次降到 1 次, 由内核按 MSS 分段。
     * 配合查表 CRC, 回发一帧从 ~43 ms 降到 ~10 ms。 */
    static uint8_t stream[STREAM_TOTAL_BYTES];
    uint32_t r;
    size_t   off = 0u;

    for (r = 0u; r < PKT_COUNT; r++) {
        const uint8_t *src = img + ((size_t)r * IMG_ROW_BYTES);
        uint8_t       *dst = stream + off;
        uint16_t       crc = (reuse_row_crc != 0)
                             ? g_row_crc[r]
                             : crc16_ccitt_false(src, IMG_ROW_BYTES);

        proto_put_le16(dst + PKT_OFF_ROW, (uint16_t)r);
        proto_put_le16(dst + PKT_OFF_LEN, (uint16_t)PKT_PAYLOAD_BYTES);
        proto_put_le16(dst + PKT_OFF_CRC16, crc);
        memcpy(dst + PKT_OFF_PAYLOAD, src, IMG_ROW_BYTES);

        off += PKT_TOTAL_BYTES;
    }

    return send_all(cfd, stream, (size_t)STREAM_TOTAL_BYTES);
}

/* ------------------------------------------------------------------ */
/*  开机自检                                                          */
/* ------------------------------------------------------------------ */

/**
 * @brief 用标准向量验证 CRC16 实现, 顺带验证协议常量。
 *
 *  "123456789" 在 CRC-16/CCITT-FALSE 下的标准校验值是 0x29B1。若这里都不对,
 *  说明收图一定会 CRC 全错, 不如直接退出。
 *
 * @return 0 通过; -1 失败。
 */
static int self_test(void)
{
    static const uint8_t vec[] = { '1', '2', '3', '4', '5', '6', '7', '8', '9' };
    uint16_t crc;

    /* sizeof(struct frame_status) 必须与 DDR 布局里预留的 32 字节一致 */
    if (sizeof(struct frame_status) != (size_t)IMG_STAT_BYTES) {
        printf("[self] FAIL: sizeof(frame_status)=%u, expected %u\n",
               (unsigned)sizeof(struct frame_status), (unsigned)IMG_STAT_BYTES);
        return -1;
    }

    crc = crc16_ccitt_false(vec, sizeof(vec));
    if (crc != 0x29B1u) {
        printf("[self] FAIL: CRC16(\"123456789\")=0x%04X, expected 0x29B1\n",
               (unsigned)crc);
        return -1;
    }

    printf("[self] OK: CRC16 check value 0x29B1, image %ux%u RGB888, "
           "%u bytes/frame, %u pkts x %u bytes\n",
           (unsigned)IMG_WIDTH, (unsigned)IMG_HEIGHT,
           (unsigned)IMG_TOTAL_BYTES, (unsigned)PKT_COUNT,
           (unsigned)PKT_TOTAL_BYTES);
    return 0;
}

/* ------------------------------------------------------------------ */
/*  单帧接收                                                          */
/* ------------------------------------------------------------------ */

/**
 * @brief 从已连接的 socket 收满一整幅图像并写入 DDR 保留区。
 *
 * @param cfd      已 accept 的客户端 socket。
 * @param img_buf  图像缓冲区(DDR 保留区内, 非缓存映射)。
 * @param st       状态块指针(DDR 保留区内), 本函数负责填好除 dma_status 外字段。
 * @param verbose  非 0 时打印逐包/逐错日志。
 * @return 0 整幅收齐且每行 CRC 正确; 负值失败(见返回码注释)。
 */
/* 紧凑帧(控制头 v6c)的请求; 负载放在静态缓冲里(最大一帧 512x512 RGB) */
struct v6c_req {
    uint32_t kind, track, flags, model0, model1, w, h;
    int32_t  thr;
    uint32_t nextra;
    uint8_t  extra[V6C_MAX_EXTRA][V6C_EXTRA_BYTES];   /* model, src, dst, thr */
    uint8_t  pp[V6C_PP_BYTES];                        /* FLAG_C_PP: PL post-processing request */
    uint8_t  payload[IMG_TOTAL_BYTES];
};

/* 板端跟踪状态: 每个连接一份, accept 时清空 */
static struct {
    int      valid;
    uint32_t w;
    uint8_t  mask[(size_t)UNET_OUT_H * UNET_OUT_W];
} g_track;

/* 读紧凑帧的其余 14 字节头 + 负载 + 校验。成功返回 1(调用者走快速通路)。 */
static int receive_v6c(int cfd, const uint8_t *first2, struct v6c_req *q, int verbose)
{
    uint8_t  h[CTRL_BYTES_V6C];
    uint32_t len;
    uint16_t crc;

    memcpy(h, first2, 2u);
    if (q == NULL || recv_exact(cfd, h + 2u, CTRL_BYTES_V6C - 2u) != 0) {
        printf("[rx] recv v6c header failed\n");
        return -1;
    }
    q->kind   = h[CTRL_V6C_OFF_KIND];
    q->track  = h[CTRL_V6C_OFF_TRACK];
    q->flags  = proto_get_le16(h + CTRL_V6C_OFF_FLAGS);
    q->model0 = h[CTRL_V6C_OFF_MODEL0];
    q->model1 = h[CTRL_V6C_OFF_MODEL1];
    q->thr    = (int32_t)(int8_t)h[CTRL_V6C_OFF_THR];
    q->w      = proto_get_le16(h + CTRL_V6C_OFF_W);
    q->h      = proto_get_le16(h + CTRL_V6C_OFF_H);
    crc       = proto_get_le16(h + CTRL_V6C_OFF_CRC);
    q->nextra = h[CTRL_V6C_OFF_NEXTRA];
    if (q->nextra > V6C_MAX_EXTRA) {
        printf("[rx] v6c: %u extra programs (max %u)\n", (unsigned)q->nextra, (unsigned)V6C_MAX_EXTRA);
        return -1;
    }
    if (q->nextra > 0u && recv_exact(cfd, &q->extra[0][0], q->nextra * V6C_EXTRA_BYTES) != 0) {
        printf("[rx] v6c program list incomplete\n");
        return -1;
    }
    if ((q->flags & FLAG_C_PP) != 0u && recv_exact(cfd, q->pp, V6C_PP_BYTES) != 0) {
        printf("[rx] v6c post-processing request incomplete\n");
        return -1;
    }
    if ((q->kind != V6C_KIND_GRAY && q->kind != V6C_KIND_RGB) || q->track > V6C_TRACK_SET ||
        q->w != q->h || (q->w != 32u && q->w != 64u && q->w != 128u && q->w != 256u && q->w != 512u)) {
        printf("[rx] bad v6c header: kind=%u track=%u %ux%u\n",
               (unsigned)q->kind, (unsigned)q->track, (unsigned)q->w, (unsigned)q->h);
        return -1;
    }
    len = q->w * q->h * q->kind;
    if (recv_exact(cfd, q->payload, len) != 0) {
        printf("[rx] v6c payload (%u bytes) incomplete\n", (unsigned)len);
        return -1;
    }
    if (crc16_ccitt_false(q->payload, len) != crc) {
        printf("[rx] v6c payload CRC mismatch\n");
        return -1;
    }
    if (verbose != 0) {
        printf("[rx] v6c %ux%u kind=%u track=%u flags=0x%04X model %u/%u thr=%d\n",
               (unsigned)q->w, (unsigned)q->h, (unsigned)q->kind, (unsigned)q->track,
               (unsigned)q->flags, (unsigned)q->model0, (unsigned)q->model1, (int)q->thr);
    }
    return 1;
}

static int receive_frame(int cfd, uint8_t *img_buf,
                         struct frame_status *st, uint32_t *mode_out,
                         uint32_t *flags_out, uint32_t *aiparam_out,
                         uint32_t *thr_out, uint32_t *roi_out,
                         struct v6c_req *creq, int verbose)
{
    static uint8_t stream[STREAM_TOTAL_BYTES];   /* 整帧先落地的普通内存 */
    uint8_t  row_seen[PKT_COUNT];
    uint32_t crc_errors = 0u;
    uint32_t seq_errors = 0u;
    uint32_t bytes_recv = 0u;
    uint32_t rows_ok    = 0u;
    uint32_t k;
    uint32_t i;
    uint64_t tA = now_ms();     /* 进入 */
    uint64_t tB = 0u;           /* 读完整帧 */
    uint64_t tC = 0u;           /* 解析完 */
    uint64_t tD = 0u;           /* 整幅 CRC 完 */

    memset(row_seen, 0, sizeof(row_seen));

    /* 开始接收前先把 magic 清掉: 让读端能观察到 0 -> FRAME_MAGIC 的"新帧"边沿 */
    st->magic = 0u;
    __sync_synchronize();

    /* ---- 0) 先读控制头 ----
     * 线上字节流最前面是控制头(小端)。**先读 2 字节 magic 再决定读几字节**:
     *   magic == 0x4D43 ("CM", v2): 再读 2 字节 mode          -> 4 字节
     *   magic == 0x4D44 ("DM", v3): 再读 6 字节 mode/flags/aiparam -> 8 字节
     *   magic == 0x4D45 ("EM", v4): 再读 10 字节(+thr+保留)   -> 12 字节
     *   magic == 0x4D46 ("FM", v5): 再读 18 字节(v4 + ROI 框)  -> 20 字节
     * 四级都认, 老客户端照旧能用; 阈值(v4 起才有)缺省时用板端 --thr;
     * ROI(v5 才有)缺省 = 不用 ROI。
     * 控制头不并入 bytes_recv(它保持"图像流 789504 字节"的语义)。 */
    {
        uint8_t  ctrl[CTRL_BYTES_V5];
        uint16_t ctrl_magic;
        uint16_t ctrl_mode;

        if (roi_out != NULL) {
            roi_out[0] = roi_out[1] = roi_out[2] = roi_out[3] = 0u;   /* 0 宽 = 不用 ROI */
        }

        if (recv_exact(cfd, ctrl, 2u) != 0) {
            /* rc=-2 对端正常关闭; -1 出错(含 SO_RCVTIMEO 超时) */
            printf("[rx] no control header (peer idle/closed); back to accept\n");
            goto fail;
        }
        ctrl_magic = proto_get_le16(ctrl + CTRL_V4_OFF_MAGIC);

        if (ctrl_magic == CTRL_MAGIC_V6C) {          /* 紧凑帧: 另一条路, 不是 512 行包流 */
            return receive_v6c(cfd, ctrl, creq, verbose);
        }
        if (ctrl_magic == CTRL_MAGIC_JOB) {          /* 病例任务(v7 "HM", job.c): 调用者接着读 */
            return 2;
        }
        if (ctrl_magic == CTRL_MAGIC_V4 || ctrl_magic == CTRL_MAGIC_V5) {
            uint32_t n = (ctrl_magic == CTRL_MAGIC_V5) ? CTRL_BYTES_V5 : CTRL_BYTES_V4;

            if (recv_exact(cfd, ctrl + 2u, n - 2u) != 0) {
                printf("[rx] recv v4/v5 control header (rest) failed\n");
                goto fail;
            }
            ctrl_mode = proto_get_le16(ctrl + CTRL_V4_OFF_MODE);
            if (flags_out != NULL)   { *flags_out   = proto_get_le16(ctrl + CTRL_V4_OFF_FLAGS); }
            if (aiparam_out != NULL) { *aiparam_out = proto_get_le16(ctrl + CTRL_V4_OFF_AIPARAM); }
            if (thr_out != NULL)     { *thr_out     = proto_get_le16(ctrl + CTRL_V4_OFF_THR) & 0xFFu; }
            if (ctrl_magic == CTRL_MAGIC_V5 && roi_out != NULL) {
                uint32_t rx = proto_get_le16(ctrl + CTRL_V5_OFF_ROI_X);
                uint32_t ry = proto_get_le16(ctrl + CTRL_V5_OFF_ROI_Y);
                uint32_t rw = proto_get_le16(ctrl + CTRL_V5_OFF_ROI_W);
                uint32_t rh = proto_get_le16(ctrl + CTRL_V5_OFF_ROI_H);

                if (rw > 0u && rh > 0u && rx + rw <= IMG_WIDTH && ry + rh <= IMG_HEIGHT) {
                    roi_out[0] = rx;
                    roi_out[1] = ry;
                    roi_out[2] = rw;
                    roi_out[3] = rh;
                } else if (rw > 0u || rh > 0u) {
                    printf("[rx] ROI %u,%u %ux%u outside the image; running full frame\n",
                           (unsigned)rx, (unsigned)ry, (unsigned)rw, (unsigned)rh);
                }
            }
        } else if (ctrl_magic == CTRL_MAGIC_V3) {
            if (recv_exact(cfd, ctrl + 2u, CTRL_BYTES_V3 - 2u) != 0) {
                printf("[rx] recv v3 control header (rest) failed\n");
                goto fail;
            }
            ctrl_mode = proto_get_le16(ctrl + CTRL_V3_OFF_MODE);
            if (flags_out != NULL)   { *flags_out   = proto_get_le16(ctrl + CTRL_V3_OFF_FLAGS); }
            if (aiparam_out != NULL) { *aiparam_out = proto_get_le16(ctrl + CTRL_V3_OFF_AIPARAM); }
            if (thr_out != NULL)     { *thr_out     = CTRL_THR_DEFAULT; }   /* v3 不带 thr */
        } else if (ctrl_magic == CTRL_MAGIC) {
            if (recv_exact(cfd, ctrl + 2u, CTRL_BYTES - 2u) != 0) {
                printf("[rx] recv v2 control header (rest) failed\n");
                goto fail;
            }
            ctrl_mode = proto_get_le16(ctrl + CTRL_OFF_MODE);
            if (flags_out != NULL)   { *flags_out   = FLAG_DEFAULT; }              /* 旧客户端 = 只回预处理图 */
            if (aiparam_out != NULL) { *aiparam_out = AIPARAM_SRC_RAW | ((uint32_t)(uint8_t)AIPARAM_THR_DEFAULT << 8); }
            if (thr_out != NULL)     { *thr_out     = CTRL_THR_DEFAULT; }
        } else {
            printf("[rx] bad control magic=0x%04X (expect 0x%04X/0x%04X/0x%04X/0x%04X)\n",
                   (unsigned)ctrl_magic, (unsigned)CTRL_MAGIC,
                   (unsigned)CTRL_MAGIC_V3, (unsigned)CTRL_MAGIC_V4, (unsigned)CTRL_MAGIC_V5);
            goto fail;
        }

        if (ctrl_mode > CTRL_MODE_MAX) {
            printf("[rx] bad control mode=%u (expect 0..%u)\n",
                   (unsigned)ctrl_mode, (unsigned)CTRL_MODE_MAX);
            goto fail;
        }

        if (mode_out != NULL) {
            *mode_out = (uint32_t)ctrl_mode;
        }
        printf("[rx] control header: magic=0x%04X mode=%u flags=0x%04X aiparam=0x%04X thr=%u\n",
               (unsigned)ctrl_magic, (unsigned)ctrl_mode,
               (unsigned)((flags_out != NULL) ? *flags_out : 0u),
               (unsigned)((aiparam_out != NULL) ? *aiparam_out : 0u),
               (unsigned)((thr_out != NULL) ? *thr_out : 0u));
    }

    /* ---- 1) 大块读满整帧 ----
     * 原来是"每包读 6 字节 + 再读 1536 字节", 一帧 1024 次 recv, 实测把这个方向
     * 的吞吐压到 ~147 Mbit/s(裸 TCP 能到 ~714)。改成尽量一次多读,
     * 数据先落在普通内存, 再在内存里解析。 */
    {
        size_t got = 0u;
        uint64_t tu0 = now_us();
        uint64_t tuFirst = 0u;

        while (got < (size_t)STREAM_TOTAL_BYTES) {
            ssize_t n = recv(cfd, stream + got,
                             (size_t)STREAM_TOTAL_BYTES - got, 0);

            if (n < 0) {
                if (errno == EINTR) {
                    if (g_stop != 0) {
                        break;
                    }
                    continue;
                }
                printf("[rx] recv error after %u bytes (errno=%d %s)\n",
                       (unsigned)got, errno, strerror(errno));
                goto fail;
            }
            if (n == 0) {
                printf("[rx] peer closed after %u/%u bytes\n",
                       (unsigned)got, (unsigned)STREAM_TOTAL_BYTES);
                goto fail;
            }
            if (tuFirst == 0u) {
                tuFirst = now_us();
            }
            got += (size_t)n;
        }
        bytes_recv = (uint32_t)got;
        if (verbose != 0) {
            printf("[rx]   recv detail: wait-first-byte=%u us, transfer=%u us\n",
                   (unsigned)(tuFirst - tu0), (unsigned)(now_us() - tuFirst));
        }
    }
    tB = now_ms();

    /* ---- 2) 在内存里按包解析 ---- */
    for (k = 0u; k < PKT_COUNT; k++) {
        const uint8_t *pkt    = stream + ((size_t)k * (size_t)PKT_TOTAL_BYTES);
        uint16_t       row    = proto_get_le16(pkt + PKT_OFF_ROW);
        uint16_t       plen   = proto_get_le16(pkt + PKT_OFF_LEN);
        uint16_t       crc_rx = proto_get_le16(pkt + PKT_OFF_CRC16);

        /* 包头字段越界 = 字节流已失步(没有可信的长度字段可用于重新同步),
         * 直接放弃本次连接, 由上位机重连重发。
         * 这里的强制转换是为了避免 signed/unsigned 比较告警。 */
        if (((uint32_t)row >= (uint32_t)PKT_COUNT) ||
            ((uint32_t)plen != (uint32_t)PKT_PAYLOAD_BYTES)) {
            printf("[rx] desync at pkt %u: row=%u len=%u (expect row<512, len=1536)\n",
                   (unsigned)k, (unsigned)row, (unsigned)plen);
            seq_errors++;
            goto fail;
        }

        {
            const uint8_t *payload = pkt + PKT_OFF_PAYLOAD;

            if (crc16_ccitt_false(payload, PKT_PAYLOAD_BYTES) != crc_rx) {
                crc_errors++;
                if (verbose != 0) {
                    printf("[rx] CRC mismatch on row %u (rx=0x%04X)\n",
                           (unsigned)row, (unsigned)crc_rx);
                }
                continue;                 /* 该行不计入 rows_ok */
            }

            memcpy(img_buf + ((size_t)row * (size_t)IMG_ROW_BYTES),
                   payload, (size_t)PKT_PAYLOAD_BYTES);
            g_row_crc[row] = crc_rx;   /* 留着回发时复用, 免得再算一遍 */
            row_seen[row] = 1u;
        }
    }

    for (i = 0u; i < PKT_COUNT; i++) {
        if (row_seen[i] != 0u) {
            rows_ok++;
        }
    }
    tC = now_ms();

    /* 统计字段(不含 dma_status, 由调用者在 DMA 结束后补写) */
    st->rows_ok    = rows_ok;
    st->crc_errors = crc_errors;
    st->seq_errors = seq_errors;
    st->bytes_recv = bytes_recv;
    st->frame_crc16 = 0u;
    __sync_synchronize();

    printf("[rx] frame done: rows_ok=%u/512, crc_err=%u, seq_err=%u, bytes=%u\n",
           (unsigned)rows_ok, (unsigned)crc_errors, (unsigned)seq_errors,
           (unsigned)bytes_recv);

    if (rows_ok != PKT_COUNT) {
        printf("[rx] WARNING: frame incomplete, image buffer not trustworthy\n");
        return -3;
    }

    /* 整幅 CRC: 按行 0->511 顺序累加, 给上位机/PL 一个整帧一致性校验值。
     * 这是**纯诊断**信息(逐行 CRC 已经保证了正确性), 而它要多跑一遍 786 KB,
     * 实测约 7 ms —— 所以只在 -v 时才算, 默认不算, 省下这一块延迟。 */
    if (verbose != 0) {
        uint16_t fcrc = CRC16_CCITT_FALSE_INIT;
        for (i = 0u; i < PKT_COUNT; i++) {
            fcrc = crc16_ccitt_false_update(
                       fcrc,
                       img_buf + ((size_t)i * (size_t)IMG_ROW_BYTES),
                       IMG_ROW_BYTES);
        }
        st->frame_crc16 = (uint32_t)fcrc;
        printf("[rx] frame CRC16 = 0x%04X\n", (unsigned)fcrc);
    } else {
        st->frame_crc16 = 0u;
    }
    tD = now_ms();

    if (verbose != 0) {
        printf("[rx] timing: recv=%u ms  parse(crc+copy)=%u ms  frame_crc=%u ms  total=%u ms\n",
               (unsigned)(tB - tA), (unsigned)(tC - tB), (unsigned)(tD - tC),
               (unsigned)(tD - tA));
    }

    return 0;

fail:
    /* 失败也把已经看到的统计落盘, 方便排查 */
    st->rows_ok    = rows_ok;
    st->crc_errors = crc_errors;
    st->seq_errors = seq_errors;
    st->bytes_recv = bytes_recv;
    __sync_synchronize();
    return -1;
}

/* ------------------------------------------------------------------ */
/*  参数与用法                                                        */
/* ------------------------------------------------------------------ */

/* FLAG_C_PP: 程序 0 的 logit 交给 PL 的 pp_layer, 回 32 字节 PP 块 + 位掩膜 + CRC16(ai_view.h PP_BLOCK_*) */
static int send_pp(int cfd, const struct v6c_req *q, const int8_t *lg, int ai_rc)
{
    static uint8_t mask[(size_t)UNET_OUT_H * (UNET_OUT_W / 8u)];
    uint8_t        blk[PP_BLOCK_BYTES], crc2[2];
    pp_result_t    r;
    const unsigned op = q->pp[V6C_PP_OFF_OP] & (PP_OP_MASK | PP_OP_CLEAN | PP_OP_CONN8);
    const int      k = q->pp[V6C_PP_OFF_K];
    int            x0 = proto_get_le16(q->pp + V6C_PP_OFF_X0), y0 = proto_get_le16(q->pp + V6C_PP_OFF_X0 + 2u);
    int            cw = proto_get_le16(q->pp + V6C_PP_OFF_X0 + 4u), ch = proto_get_le16(q->pp + V6C_PP_OFF_X0 + 6u);
    uint32_t       status, rows = 0u, row_bytes = 0u, mask_bytes = 0u;
    int            rc = -1;

    memset(&r, 0, sizeof(r));
    if (cw == 0 || ch == 0) {
        x0 = y0 = 0;
        cw = (int)q->w;
        ch = (int)q->h;
    }
    if (pp_ready() == 0 || ai_rc != 0) {
        status = PP_STATUS_UNAVAILABLE;
    } else if (x0 < 0 || y0 < 0 || x0 + cw > (int)q->w || y0 + ch > (int)q->h || (k != 1 && k != 2 && k != 4) ||
               cw * k > (int)UNET_OUT_W || ch * k > (int)UNET_OUT_H) {
        status = 4u;                                 /* bad window */
    } else {
        rc = pp_run(lg, (int)q->h, (int)q->w, op, x0, y0, cw, ch, k, (int8_t)q->pp[V6C_PP_OFF_THR],
                    (int8_t)q->pp[V6C_PP_OFF_TN], mask, &r);
        status = (rc != 0) ? (PP_STATUS_DRIVER | (uint32_t)(-rc)) : r.status;
        if (rc == 0 && (op & PP_OP_MASK) != 0u) {
            rows = (uint32_t)(ch * k);
            row_bytes = (uint32_t)pp_mask_row_bytes(cw * k);
            mask_bytes = rows * row_bytes;
        }
    }
    memset(blk, 0, sizeof(blk));
    proto_put_le32(blk + 0u, PP_BLOCK_MAGIC);
    proto_put_le32(blk + 4u, status);
    proto_put_le32(blk + 8u, r.count);
    proto_put_le32(blk + 12u, r.comps);
    proto_put_le32(blk + 16u, r.largest);
    proto_put_le32(blk + 20u, r.pl_us);
    proto_put_le32(blk + 24u, mask_bytes);
    proto_put_le32(blk + 28u, (rows << 16) | row_bytes);
    if (send_all(cfd, blk, PP_BLOCK_BYTES) != 0) {
        return -1;
    }
    if (mask_bytes != 0u) {
        proto_put_le16(crc2, crc16_ccitt_false(mask, mask_bytes));
        if (send_all(cfd, mask, mask_bytes) != 0 || send_all(cfd, crc2, 2u) != 0) {
            return -1;
        }
    }
    return 0;
}

/* 紧凑帧快速通路: 按 w 选档位, 输入在板上拼(跟踪时把上一帧结果放进 G), 只回紧凑结果。
 * 返回 0 继续收下一帧, 非 0 断开连接。 */
static int handle_v6c(int cfd, const struct v6c_req *q, uint32_t frame_count, int verbose)
{
    static uint8_t in[(size_t)UNET_IN_H * UNET_IN_W * 3u];
    static int8_t  lg[(size_t)UNET_OUT_H * UNET_OUT_W];
    static uint8_t bits[(size_t)UNET_OUT_H * UNET_OUT_W / 8u + 2u];
    uint8_t        blk[AI_STAT_BYTES];
    uint8_t        crc2[2];
    unet_stats_t   ust;
    uint32_t       n = q->w * q->h;
    uint32_t       comps = 0u, vessels = 0u, k;
    uint32_t       m;
    int            shift = 0, use_state = 0, rc = -1, reg_ok;
    uint64_t       t0 = now_us();

    while ((512u >> shift) > q->w) {
        shift++;
    }
    if (q->track == V6C_TRACK_RESET) {
        g_track.valid = 0;
    }
    use_state = (q->track == V6C_TRACK_STEP && g_track.valid != 0 && g_track.w == q->w) ? 1 : 0;
    m = use_state ? q->model1 : q->model0;
    if (prog_model(m) == NULL) {
        printf("[v6c] model %u not built in (%u models) and not loaded\n", (unsigned)m,
               (unsigned)UNET_MODELS_COUNT);
        return -1;
    }
    if (q->kind == V6C_KIND_GRAY || q->track == V6C_TRACK_STEP || q->track == V6C_TRACK_RESET) {
        for (k = 0u; k < n; k++) {
            /* 跟踪类请求(step / reset)且负载是 RGB 时, 本帧是 G 通道(R / B 可放前后帧给运动类程序);
             * 没有状态时程序 0 看到的就是 G 复制成灰度, 与灰度负载完全一样 */
            uint8_t v = (q->kind == V6C_KIND_GRAY) ? q->payload[k] : q->payload[3u * k + 1u];

            in[3u * k]      = v;
            in[3u * k + 1u] = use_state ? (g_track.mask[k] ? 255u : 0u) : v;
            in[3u * k + 2u] = use_state ? 128u : v;
        }
    } else {
        memcpy(in, q->payload, (size_t)n * 3u);
    }
    if (unet_ready() != 0 && prog_select(m) == 0 && unet_set_shift(shift) == 0) {
        rc = unet_run(in, lg, &ust);
    }
    if (rc == 0) {
        vessels = ai_view_count(lg, (int)q->w, (int)q->h, (int)q->thr, &comps);
        if (q->track != V6C_TRACK_NONE) {
            for (k = 0u; k < n; k++) {
                g_track.mask[k] = (lg[k] >= (int8_t)q->thr) ? 1u : 0u;
            }
            g_track.valid = 1;
            g_track.w     = q->w;
        }
    } else {
        printf("[v6c] unet_run failed\n");
        memset(lg, 0, (size_t)n);
    }

    reg_ok = (rc == 0 && (q->flags & FLAG_REG) != 0u && (q->flags & FLAG_STATS) != 0u) ? 1 : 0;
    if ((q->flags & FLAG_STATS) != 0u) {
        ai_view_fill_stats(blk, (q->flags & 0xFFFFu & ~(reg_ok != 0 ? 0u : FLAG_REG)) | ((m + 1u) << 16),
                           q->w, vessels, n, (rc == 0) ? &ust : NULL, comps, 0u, 0u, 0u, frame_count);
        ai_view_set_stats_extra(blk, 0x80000000u | (uint32_t)use_state | ((uint32_t)(g_track.valid != 0) << 1) |
                                     ((uint32_t)(pp_ready() != 0) << 2));
        if (send_all(cfd, blk, AI_STAT_BYTES) != 0) {
            return -1;
        }
        if (reg_ok != 0) {
            uint8_t rblk[REG_BLOCK_BYTES];

            ai_view_fill_reg(rblk, lg, (int)q->w, (int)q->h, 0u);
            if (send_all(cfd, rblk, REG_BLOCK_BYTES) != 0) {
                return -1;
            }
        }
    }
    if ((q->flags & FLAG_C_LOGIT) != 0u) {
        proto_put_le16(crc2, crc16_ccitt_false((const uint8_t *)lg, n));
        if (send_all(cfd, (const uint8_t *)lg, n) != 0 || send_all(cfd, crc2, 2u) != 0) {
            return -1;
        }
    }
    /* ---- 多程序流水线: 额外程序依次在 PL 上跑, logit 跟在程序 0 后面回传 ---- */
    {
        static int8_t xlg[V6C_MAX_EXTRA][(size_t)UNET_OUT_H * UNET_OUT_W];
        const int8_t *prev = lg;
        uint32_t      e;

        for (e = 0u; e < q->nextra; e++) {
            uint32_t xm = q->extra[e][0], src = q->extra[e][1], dst = q->extra[e][2];
            int      xrc = -1;
            unet_stats_t xst;

            if (prog_model(xm) == NULL || src > 1u || dst > 3u) {
                printf("[v6c] bad extra program %u: model %u src %u dst %u\n", (unsigned)e, (unsigned)xm,
                       (unsigned)src, (unsigned)dst);
                return -1;
            }
            for (k = 0u; k < n; k++) {
                uint8_t base[3];
                int     v = (int)prev[k] + 128;
                uint8_t u = (uint8_t)(v < 0 ? 0 : (v > 255 ? 255 : v));

                if (q->kind == V6C_KIND_GRAY) {
                    base[0] = base[1] = base[2] = q->payload[k];
                } else {
                    base[0] = q->payload[3u * k];
                    base[1] = q->payload[3u * k + 1u];
                    base[2] = q->payload[3u * k + 2u];
                }
                if (src == 1u) {
                    if (dst == 0u) {
                        base[0] = base[1] = base[2] = u;
                    } else {
                        base[dst - 1u] = u;
                    }
                }
                in[3u * k] = base[0];
                in[3u * k + 1u] = base[1];
                in[3u * k + 2u] = base[2];
            }
            if (prog_select(xm) == 0 && unet_set_shift(shift) == 0) {
                xrc = unet_run(in, xlg[e], &xst);
            }
            if (xrc != 0) {
                printf("[v6c] extra program %u (%s) failed\n", (unsigned)e, prog_model(xm)->name);
                memset(xlg[e], 0, (size_t)n);
            }
            if ((q->flags & FLAG_C_LOGIT) != 0u) {
                proto_put_le16(crc2, crc16_ccitt_false((const uint8_t *)xlg[e], n));
                if (send_all(cfd, (const uint8_t *)xlg[e], n) != 0 || send_all(cfd, crc2, 2u) != 0) {
                    return -1;
                }
            }
            prev = xlg[e];
        }
    }
    if ((q->flags & FLAG_C_MASK) != 0u) {
        memset(bits, 0, n / 8u);
        for (k = 0u; k < n; k++) {
            if (lg[k] >= (int8_t)q->thr) {
                bits[k >> 3] |= (uint8_t)(1u << (k & 7u));
            }
        }
        proto_put_le16(crc2, crc16_ccitt_false(bits, n / 8u));
        if (send_all(cfd, bits, n / 8u) != 0 || send_all(cfd, crc2, 2u) != 0) {
            return -1;
        }
    }
    if ((q->flags & FLAG_C_PP) != 0u && send_pp(cfd, q, lg, rc) != 0) {
        return -1;
    }
    if (verbose != 0 || (frame_count % 100u) == 0u) {     /* 高帧率下每帧打日志本身就费时间 */
        printf("[v6c] #%u model=%s %ux%u state=%d ai=%u us total=%u us vessels=%u\n",
               (unsigned)frame_count, prog_model(m)->name, (unsigned)q->w, (unsigned)q->h, use_state,
               (unsigned)((rc == 0) ? ust.run_us : 0u), (unsigned)(now_us() - t0), (unsigned)vessels);
    }
    return 0;
}

static void usage(const char *prog)
{
    printf(
        "Usage: %s [options]\n"
        "\n"
        "  -p, --port <n>       TCP listen port              (default %u)\n"
        "  -a, --ddr-addr <hex> reserved DDR base address    (default 0x%llX)\n"
        "  -d, --dma-addr <hex> AXI DMA register base        (default 0x%llX)\n"
        "  -g, --gpio-addr <hex> AXI GPIO (mode reg) base    (default 0x%llX)\n"
        "                       0 = no mode register (skip PL mode programming)\n"
        "  -x, --axil-addr <hex> PS<->PL register file base  (0 = off, e.g. 0xA0000000)\n"
        "                       use this on boards whose PL->PS slave port is dead\n"
        "  -m, --mem <path>     physical memory device       (default %s)\n"
        "  -t, --dma-timeout <ms>  DMA wait timeout          (default %d)\n"
        "      --thr <n>        DEFAULT threshold 0..255 (default %u)\n"
        "                       mode 1 = 灰度阈值; mode 6 = 边缘阈值\n"
        "                       (v4 control header overrides it per frame)\n"
        "      --ai-conv <hex>  conv_layer control base       (default 0x%llX)\n"
        "      --ai-elt <hex>   eltwise_layer control base    (default 0x%llX)\n"
        "      --ai-bram <hex>  tensor BRAM window base       (default 0x%llX)\n"
        "      --no-ai          do not map/use the U-Net subsystem\n"
        "      --ai-res <n>     default AI working resolution: 32|64|128|256|512\n"
        "                       (default: the model's own, 512 for fundus, 128 for\n"
        "                        skin / echo_lv; a client choice overrides it)\n"
        "      --model <name|n> default model when the client sends model 0\n"
        "                       (fundus | skin | echo_lv | echo_inter | echo_seq | reg_absdiff |\n"
        "                        op_median3 ... op_vessel4 = compiled operator programs,\n"
        "                        ct_spleen = CT spleen, z3 RGB slices via compact frames,\n"
        "                        ct_spleen_roi = its 2x ROI refinement (stage 2),\n"
        "                        echonet_lv = EchoNet echo LV (112 padded to 128),\n"
        "                        or the index)\n"
        "      --programs <dir> program packages (*.upk) loaded at start-up and on\n"
        "                       SIGHUP, each self-tested on the PL; compact frames\n"
        "                       select them by the id in the package (default %s)\n"
        "      --no-pp          do not use the PL post-processing kernel (pp_layer); it is only\n"
        "                       used when the firmware's sha256 is listed in " PP_FIRMWARE_LIST "\n"
        "  -n, --frames <n>     stop after n frames, 0=forever (default 0)\n"
        "  -D, --no-dma         receive only, do not start AXI DMA\n"
        "  -A, --anon-buf       use an anonymous buffer instead of /dev/mem\n"
        "                       (bring-up only; NOT DMA-coherent)\n"
        "  -E, --echo           send back what the client asked for in the control\n"
        "                       header flags (default flags = b0 preprocessed image)\n"
        "  -v, --verbose        per-packet / per-error logging\n"
        "  -h, --help           show this help\n"
        "\n"
        "Control header: the board reads 2 magic bytes first, so all four\n"
        "versions work:\n"
        "    0x4D43 + mode(u16)                                     (4 bytes)\n"
        "    0x4D44 + mode + flags + aiparam                        (8 bytes)\n"
        "    0x4D45 + mode + flags + aiparam + thr + reserved      (12 bytes)\n"
        "    0x4D46 + the v4 fields + ROI x, y, w, h (u16 each)    (20 bytes)\n"
        "  ROI:      run the network only on the ROI grown by 24 px (aligned),\n"
        "            paste it back; logits outside the ROI are -128\n"
        "  flags:    b0 preprocessed image, b1 vessel mask, b2 mask overlay,\n"
        "            b3 64-byte stats block first, b4 no image at all,\n"
        "            b5 raw logit image (R=G=B=logit+128, sent after the mask),\n"
        "            b6 vessel morphology: 64-byte block after the stats block\n"
        "               (needs b3) + a skeleton view image sent last\n"
        "            b7 tile sums (F5 registration): 96-byte block of the 4x4 tile\n"
        "               sums of the logits after the stats / morphology block\n"
        "               (needs b3, whole-image frames only)\n"
        "  compact frames (control header v6c \"GM\", stats version >= 7): the\n"
        "            payload is only the w x h grey or RGB the model needs\n"
        "            (w = 512>>shift), no PL preprocessing; board-side tracking\n"
        "            state per connection (track 1 = previous result into G,\n"
        "            B = 128); replies b3 stats, b7 tile sums, b8 int8 logits +\n"
        "            CRC16, b9 bit-packed mask + CRC16, b10 PL post-processing (pp_layer:\n"
        "            threshold / bilinear x2 x4 / components / largest + fill holes): 12-byte\n"
        "            request after the program list, 32-byte block + bit mask + CRC16 last\n"
        "  case jobs (control header v7 \"HM\", src/job.h): upload a series once and run\n"
        "            the whole case on the board: 0 caps, 1 ct_seg (NIfTI file -> two-stage\n"
        "            spleen, 3-D component, volume), 2 reg_search (F5 search loop), 3 echo_ef\n"
        "            (grey clip -> EchoNet LV per frame, volumes, beats, EF, QC), 4 simpson_ef\n"
        "            (four CAMUS LV masks -> biplane Simpson EDV / ESV / EF); replies are\n"
        "            progress / blob / result (JSON) / end messages\n"
        "  aiparam:  bit0 AI input (0=raw RGB, 1=preprocessed), bits[4:2] AI\n"
        "            resolution (512>>shift), bits[7:5] model (0 = --model,\n"
        "            k = model k-1), bits[15:8] AI threshold\n"
        "  ROI is ignored for models trained at a reduced resolution\n"
        "  thr:      mode-1 threshold 0..255 (per frame; older headers use --thr)\n"
        "\n"
        "  mode:     0 norm / 1 threshold / 2 edge / 3 pcolor / 4 norm+pcolor\n"
        "            5 passthrough (no PL) / 6 edge overlay (PL runs mode 2,\n"
        "            then the PS paints the strong edges white on the original)\n"
        "\n"
        "Note: AXI DMA / GPIO base addresses are decided by the block design and\n"
        "      are shown in the exported XSA / device tree. Confirm before running.\n",
        prog, (unsigned)DEF_TCP_PORT,
        (unsigned long long)DEF_DDR_BASE,
        (unsigned long long)DEF_DMA_BASE,
        (unsigned long long)DEF_GPIO_BASE,
        DEF_MEM_DEV, DEF_DMA_TIMEOUT, (unsigned)DEF_THR,
        (unsigned long long)DEF_AI_CONV,
        (unsigned long long)DEF_AI_ELT,
        (unsigned long long)DEF_AI_BRAM, PROG_DEFAULT_DIR);
}

/* ------------------------------------------------------------------ */
/*  main                                                              */
/* ------------------------------------------------------------------ */

int main(int argc, char *argv[])
{
    /* 选项与默认值 */
    uint16_t  port        = (uint16_t)DEF_TCP_PORT;
    uint64_t  ddr_base    = DEF_DDR_BASE;
    uint64_t  dma_base    = DEF_DMA_BASE;
    uint64_t  gpio_base   = DEF_GPIO_BASE;
    uint64_t  axil_base   = 0u;    /* PS<->PL 寄存器通路基址; 0 = 不用 */
    uint64_t  ai_conv     = DEF_AI_CONV;   /* conv_layer 控制基址 */
    uint64_t  ai_elt      = DEF_AI_ELT;    /* eltwise_layer 控制基址 */
    uint64_t  ai_bram     = DEF_AI_BRAM;   /* 张量 BRAM 基址 */
    int       ai_shift    = 0;             /* 默认工作分辨率档位: 分辨率 = 512 >> shift */
    int       ai_shift_set = 0;            /* 给了 --ai-res: 客户端没选档位时用它, 否则用模型自己的 */
    int       ai_model    = 0;             /* 客户端模型字段为 0 时用的模型(--model) */
    const char *prog_dir  = PROG_DEFAULT_DIR;  /* 程序包目录(--programs), SIGHUP 重新加载 */
    int       use_pp      = 1;             /* PL 后处理(pp_layer); 只在固件指纹登记过时启用 */
    const char *mem_dev   = DEF_MEM_DEV;
    int       dma_timeout = DEF_DMA_TIMEOUT;
    uint32_t  thr         = DEF_THR;      /* 模式 1 的阈值 */
    long      frame_limit = 0;
    int       use_dma     = 1;
    int       use_anon    = 0;
    int       use_echo    = 0;
    int       use_ai      = 1;
    int       verbose     = 0;

    int       mem_fd      = -1;
    uint8_t  *img_buf     = NULL;   /* 图像最终目的地(可能是 /dev/mem 映射) */
    uint8_t  *rx_buf      = NULL;   /* recv 暂存区(普通内存) */
    uint8_t  *tx_buf      = NULL;   /* 回发暂存区: 装"处理后的图"的紧包副本 */
    static struct frame_status st_local;
    static uint8_t         ai_in[(size_t)UNET_IN_H * UNET_IN_W * 3u];       /* AI 输入(RGB888) */
    static int8_t          ai_logits[(size_t)UNET_OUT_H * UNET_OUT_W];      /* 网络输出 logit */
    static int8_t          ai_crop_logits[(size_t)UNET_OUT_H * UNET_OUT_W]; /* ROI: 裁剪窗口的输出 */
    static int8_t          ai_roi_logits[(size_t)UNET_OUT_H * UNET_OUT_W];  /* ROI: 只含 ROI 框 */
    static uint8_t         vm_mask[(size_t)UNET_OUT_H * UNET_OUT_W];        /* 形态: 分析用掩膜 */
    static uint8_t         vm_fov[(size_t)UNET_OUT_H * UNET_OUT_W];         /* 形态: 视野 */
    static uint8_t         vm_skel[(size_t)UNET_OUT_H * UNET_OUT_W];        /* 形态: 骨架图 */
    static uint8_t         vm_view[(size_t)IMG_TOTAL_BYTES];                /* 形态: 骨架视图 */
    struct frame_status *st = &st_local;
    volatile uint32_t   *dma_regs  = NULL;
    volatile uint32_t   *gpio_regs = NULL;
    axidma_t  dma  = { NULL, 0, 0u, 0u };   /* 预置零, 避免未初始化告警 */
    axigpio_t gpio = { NULL };
    int       lfd         = -1;
    int       cfd         = -1;    /* 跨帧保留: 一个连接上连收多帧 */
    uint32_t  frame_count = 0u;
    uint32_t  frame_mode  = 0u;      /* 最近一帧控制头里的处理模式(0..5) */
    uint32_t  frame_flags = FLAG_DEFAULT;  /* 最近一帧的回传内容位图 */
    uint32_t  frame_aiparam = AIPARAM_SRC_RAW | ((uint32_t)(uint8_t)AIPARAM_THR_DEFAULT << 8);
    uint32_t  frame_thr   = CTRL_THR_DEFAULT;  /* 最近一帧控制头里的阈值(仅 v4 带) */
    uint32_t  frame_roi[4] = { 0u, 0u, 0u, 0u };  /* 最近一帧的 ROI x,y,w,h(仅 v5 带; w=0 不用) */
    int       rc          = 0;      /* 进程退出码: 0 正常, 1 启动/致命错误 */
    int       frc;                  /* 最近一次"单帧处理"的返回码 */
    static struct v6c_req v6c;      /* 紧凑帧请求(负载最大 786 KB, 放静态区) */

    static const struct option long_opts[] = {
        { "port",        required_argument, NULL, 'p' },
        { "ddr-addr",    required_argument, NULL, 'a' },
        { "dma-addr",    required_argument, NULL, 'd' },
        { "gpio-addr",   required_argument, NULL, 'g' },
        { "axil-addr",   required_argument, NULL, 'x' },
        { "mem",         required_argument, NULL, 'm' },
        { "dma-timeout", required_argument, NULL, 't' },
        { "thr",         required_argument, NULL, 1000 },  /* 长选项专用 */
        { "ai-conv",     required_argument, NULL, 1001 },
        { "ai-elt",      required_argument, NULL, 1002 },
        { "ai-bram",     required_argument, NULL, 1003 },
        { "no-ai",       no_argument,       NULL, 1004 },
        { "ai-res",      required_argument, NULL, 1005 },
        { "model",       required_argument, NULL, 1006 },
        { "programs",    required_argument, NULL, 1007 },
        { "no-pp",       no_argument,       NULL, 1008 },
        { "frames",      required_argument, NULL, 'n' },
        { "no-dma",      no_argument,       NULL, 'D' },
        { "anon-buf",    no_argument,       NULL, 'A' },
        { "echo",        no_argument,       NULL, 'E' },
        { "verbose",     no_argument,       NULL, 'v' },
        { "help",        no_argument,       NULL, 'h' },
        { NULL,          0,                 NULL,  0  }
    };
    int opt;

    (void)setvbuf(stdout, NULL, _IOLBF, 0);
    install_signal_handlers();

    while ((opt = getopt_long(argc, argv, "p:a:d:g:x:m:t:n:DAEvh", long_opts, NULL)) != -1) {
        uint64_t v = 0u;

        switch (opt) {
        case 'p':
            if (parse_u64(optarg, &v) != 0 || v == 0u || v > 65535u) {
                printf("bad port: %s\n", optarg);
                return 2;
            }
            port = (uint16_t)v;
            break;
        case 'a':
            if (parse_u64(optarg, &ddr_base) != 0) {
                printf("bad ddr addr: %s\n", optarg);
                return 2;
            }
            break;
        case 'd':
            if (parse_u64(optarg, &dma_base) != 0) {
                printf("bad dma addr: %s\n", optarg);
                return 2;
            }
            break;
        case 'g':
            if (parse_u64(optarg, &gpio_base) != 0) {
                printf("bad gpio addr: %s\n", optarg);
                return 2;
            }
            break;
        case 'x':
            if (parse_u64(optarg, &axil_base) != 0) {
                printf("bad axil addr: %s\n", optarg);
                return 2;
            }
            break;
        case 'm':
            mem_dev = optarg;
            break;
        case 't':
            if (parse_u64(optarg, &v) != 0 || v == 0u || v > 600000u) {
                printf("bad timeout: %s\n", optarg);
                return 2;
            }
            dma_timeout = (int)v;
            break;
        case 1000:      /* --thr */
            if (parse_u64(optarg, &v) != 0 || v > 255u) {
                printf("bad threshold: %s (expect 0..255)\n", optarg);
                return 2;
            }
            thr = (uint32_t)v;
            break;
        case 1001:      /* --ai-conv */
            if (parse_u64(optarg, &ai_conv) != 0) {
                printf("bad ai-conv addr: %s\n", optarg);
                return 2;
            }
            break;
        case 1002:      /* --ai-elt */
            if (parse_u64(optarg, &ai_elt) != 0) {
                printf("bad ai-elt addr: %s\n", optarg);
                return 2;
            }
            break;
        case 1003:      /* --ai-bram */
            if (parse_u64(optarg, &ai_bram) != 0) {
                printf("bad ai-bram addr: %s\n", optarg);
                return 2;
            }
            break;
        case 1004:      /* --no-ai */
            use_ai = 0;
            break;
        case 1005:      /* --ai-res <32|64|128|256|512> */
            if (parse_u64(optarg, &v) != 0 || v < 32u || v > 512u) {
                printf("bad ai resolution: %s (expect 32|64|128|256|512)\n", optarg);
                return 2;
            }
            {
                uint32_t n = 512u;
                int      s = 0;

                while (n > (uint32_t)v && (n & 1u) == 0u) {
                    n >>= 1;
                    s++;
                }
                if (n != (uint32_t)v) {
                    printf("bad ai resolution: %s (must be a power of two <= 512)\n", optarg);
                    return 2;
                }
                ai_shift = s;
                ai_shift_set = 1;
            }
            break;
        case 1006:      /* --model <name|index> */
            {
                int k, found = -1;

                for (k = 0; k < (int)UNET_MODELS_COUNT; k++) {
                    if (strcmp(optarg, unet_models[k].name) == 0) {
                        found = k;
                    }
                }
                if (found < 0 && parse_u64(optarg, &v) == 0 && v < UNET_MODELS_COUNT) {
                    found = (int)v;
                }
                if (found < 0 || !UNET_MODEL_PRESENT(found)) {
                    printf("bad model: %s\n", optarg);
                    return 2;
                }
                ai_model = found;
            }
            break;
        case 1007:      /* --programs <dir> */
            prog_dir = optarg;
            break;
        case 1008:      /* --no-pp */
            use_pp = 0;
            break;
        case 'n':
            if (parse_u64(optarg, &v) != 0 || v > 1000000u) {
                printf("bad frame count: %s\n", optarg);
                return 2;
            }
            frame_limit = (long)v;
            break;
        case 'D':
            use_dma = 0;
            break;
        case 'A':
            use_anon = 1;
            break;
        case 'E':
            use_echo = 1;
            break;
        case 'v':
            verbose = 1;
            break;
        case 'h':
            usage(argv[0]);
            return 0;
        default:
            usage(argv[0]);
            return 2;
        }
    }

    printf("=== tcp_image_server ===\n");
    printf("[cfg] tcp port       : %u\n", (unsigned)port);
    printf("[cfg] ddr reserved   : 0x%llX (%u bytes, in=+0x%X out=+0x%X status=+0x%X)\n",
           (unsigned long long)ddr_base, (unsigned)RESV_WINDOW_BYTES,
           (unsigned)IMG_BUF_OFFSET, (unsigned)IMG_OUT_OFFSET, (unsigned)IMG_STAT_OFFSET);
    printf("[cfg] axi dma        : 0x%llX (%s)\n",
           (unsigned long long)dma_base, use_dma != 0 ? "enabled" : "disabled");
    printf("[cfg] axi gpio mode  : 0x%llX (%s)\n",
           (unsigned long long)gpio_base,
           (use_dma != 0 && gpio_base != 0u) ? "enabled" : "disabled");
    printf("[cfg] threshold m1   : %u\n", (unsigned)thr);
    printf("[cfg] ai conv/elt/bram: 0x%llX / 0x%llX / 0x%llX (%s)\n",
           (unsigned long long)ai_conv, (unsigned long long)ai_elt,
           (unsigned long long)ai_bram, (use_ai != 0) ? "enabled" : "disabled");
    if (ai_shift_set != 0) {
        printf("[cfg] ai resolution  : %u x %u (default; client may override per frame)\n",
               (unsigned)(512u >> ai_shift), (unsigned)(512u >> ai_shift));
    } else {
        printf("[cfg] ai resolution  : model default (client may override per frame)\n");
    }
    {
        int k;

        for (k = 0; k < (int)UNET_MODELS_COUNT; k++) {
            if (!UNET_MODEL_PRESENT(k)) {
                continue;                       /* v5.27: removed from this build */
            }
            printf("[cfg] ai model %d     : %-8s default %u x %u, thr %d, ROI %s%s\n", k,
                   unet_models[k].name,
                   (unsigned)(512u >> unet_models[k].default_shift),
                   (unsigned)(512u >> unet_models[k].default_shift),
                   (int)unet_models[k].default_thr, unet_models[k].roi_ok ? "yes" : "no",
                   (k == ai_model) ? "   <- default" : "");
        }
    }
    printf("[cfg] mem device     : %s\n", mem_dev);

    if (self_test() != 0) {
        return 1;
    }

    /* ---------------- 准备图像缓冲区 ---------------- */
    if (use_anon != 0) {
        /* bring-up 模式: 用匿名内存, 完全不碰 /dev/mem, 零风险 */
        void *p = anon_map((size_t)RESV_WINDOW_BYTES);

        if (p == NULL) {
            printf("[err] anonymous mmap of %u bytes failed: %s\n",
                   (unsigned)RESV_WINDOW_BYTES, strerror(errno));
            return 1;
        }
        img_buf = (uint8_t *)p;
        printf("[mem] ANON buffer %u B (bring-up mode; NOT DMA-coherent)\n",
               (unsigned)RESV_WINDOW_BYTES);
    } else {
        mem_fd = open(mem_dev, O_RDWR | O_SYNC);
        if (mem_fd < 0) {
            printf("[err] open %s: %s\n", mem_dev, strerror(errno));
            printf("[err] hint: need root, and /dev/mem must allow this region\n");
            return 1;
        }
        {
            void *p = map_phys(mem_fd, ddr_base, (size_t)RESV_WINDOW_BYTES);

            if (p == MAP_FAILED) {
                printf("[err] mmap ddr 0x%llX: %s\n",
                       (unsigned long long)ddr_base, strerror(errno));
                printf("[err] hint: this range must be reserved(no-map) in the device tree,\n");
                printf("[err]       otherwise STRICT_DEVMEM rejects it (or it corrupts RAM)\n");
                close(mem_fd);
                return 1;
            }
            img_buf = (uint8_t *)p;
        }
        printf("[mem] ddr mapped @0x%llX: img=%p\n",
               (unsigned long long)ddr_base, (void *)img_buf);
    }
    /* recv 不能直接写进 /dev/mem 映射区: 该内核的 copy_to_user 会返回 EFAULT
     * (映射是 VM_PFNMAP|VM_IO)。所以统一先收进普通内存, 收满一帧再 memcpy 过去。
     * 用户态自己的 memcpy 是普通 store, 不受这个限制。 */
    rx_buf = (uint8_t *)anon_map((size_t)IMG_TOTAL_BYTES);
    if (rx_buf == NULL) {
        printf("[err] cannot allocate %u B staging buffer\n",
               (unsigned)IMG_TOTAL_BYTES);
        return 1;
    }
    /* 回发暂存区: PL 处理结果在 /dev/mem 映射里, 而 send() 不能从那种映射读
     * (内核 copy_from_user 会 oops), 所以先按普通内存拷一份再发。 */
    tx_buf = (uint8_t *)anon_map((size_t)IMG_TOTAL_BYTES);
    if (tx_buf == NULL) {
        printf("[err] cannot allocate %u B send-back buffer\n",
               (unsigned)IMG_TOTAL_BYTES);
        return 1;
    }
    printf("[mem] recv staging = %p ; send staging = %p ; image destination = %p\n",
           (void *)rx_buf, (void *)tx_buf, (void *)img_buf);

    if ((use_anon != 0) && (use_dma != 0)) {
        printf("[warn] --anon-buf + DMA: anonymous pages are cached, the DMA may\n");
        printf("[warn]       read stale data. Use this combination for bring-up only.\n");
    }

    /* ---------------- 映射 AXI DMA 寄存器并复位 ---------------- */
    if (use_dma != 0) {
        void *p;

        if (mem_fd < 0) {          /* --anon-buf 时 /dev/mem 还没开 */
            mem_fd = open(mem_dev, O_RDWR | O_SYNC);
            if (mem_fd < 0) {
                printf("[err] open %s for DMA regs: %s\n", mem_dev, strerror(errno));
                munmap(img_buf, (size_t)RESV_WINDOW_BYTES);
                return 1;
            }
        }
        p = map_phys(mem_fd, dma_base, 4096u);

        if (p == MAP_FAILED) {
            printf("[err] mmap dma 0x%llX: %s\n",
                   (unsigned long long)dma_base, strerror(errno));
            munmap(img_buf, (size_t)RESV_WINDOW_BYTES);
            close(mem_fd);
            return 1;
        }
        dma_regs = (volatile uint32_t *)p;

        rc = axidma_init(&dma, dma_regs, 32);
        if (rc != 0) {
            printf("[err] axidma_init failed rc=%d (IP must be simple mode)\n", rc);
            munmap((void *)dma_regs, 4096u);
            munmap(img_buf, (size_t)RESV_WINDOW_BYTES);
            close(mem_fd);
            return 1;
        }

        rc = axidma_reset(&dma, 100);
        if (rc != 0) {
            printf("[err] axidma_reset failed rc=%d\n", rc);
            munmap((void *)dma_regs, 4096u);
            munmap(img_buf, (size_t)RESV_WINDOW_BYTES);
            close(mem_fd);
            return 1;
        }
        rc = axidma_reset_s2mm(&dma, 100);
        if (rc != 0) {
            printf("[err] axidma_reset_s2mm failed rc=%d\n", rc);
            munmap((void *)dma_regs, 4096u);
            munmap(img_buf, (size_t)RESV_WINDOW_BYTES);
            close(mem_fd);
            return 1;
        }
        printf("[dma] mm2s + s2mm ready, reset done\n");
    }

    /* ---------------- 映射 AXI GPIO(模式寄存器) ---------------- */
    if (use_dma != 0 && gpio_base != 0u) {        void *p = map_phys(mem_fd, gpio_base, 4096u);

        if (p == MAP_FAILED) {
            printf("[err] mmap gpio 0x%llX: %s\n",
                   (unsigned long long)gpio_base, strerror(errno));
            munmap((void *)dma_regs, 4096u);
            munmap(img_buf, (size_t)RESV_WINDOW_BYTES);
            close(mem_fd);
            return 1;
        }
        gpio_regs = (volatile uint32_t *)p;
        if (axigpio_init(&gpio, gpio_regs) != 0) {
            printf("[err] axigpio_init failed\n");
            munmap((void *)gpio_regs, 4096u);
            munmap((void *)dma_regs, 4096u);
            munmap(img_buf, (size_t)RESV_WINDOW_BYTES);
            close(mem_fd);
            return 1;
        }
        printf("[gpio] mode register mapped @%p (ctrl=[2:0]mode [15:8]thr)\n",
               (void *)gpio_regs);
    }

    /* PS<->PL 寄存器通路: 本板 PL->PS 从口不可用, 处理结果只能靠这条取回 */
    if (axil_base != 0u) {
        if (axilproc_init(axil_base) != 0) {
            printf("[err] axilproc_init(0x%llX) failed\n",
                   (unsigned long long)axil_base);
            return 1;
        }
    }

    /* AI 识别子系统(conv_layer + eltwise_layer + 张量 BRAM)。
     * 初始化失败**不致命**: 预处理链路照常工作, 只是 flags 里要 AI 的那几位会被忽略。 */
    if (use_ai != 0) {
        if (unet_init(ai_conv, ai_elt, ai_bram) != 0) {
            printf("[warn] U-Net init failed; AI-related flags will be ignored\n");
        } else {
            (void)prog_load_dir(prog_dir);
            /* ⚠ 0xA003_0000 只在带 pp_layer 的固件里有从机; 别的固件上访问会让 PS 永久挂死 */
            if (use_pp == 0) {
                printf("[pp] PL post-processing off (--no-pp)\n");
            } else if (pp_firmware_ok() == 0) {
                printf("[pp] PL post-processing off: firmware not listed in %s\n", PP_FIRMWARE_LIST);
            } else {
                (void)pp_init(PP_DEF_CTRL);
            }
            /* v5.27：vis_layer（配准 / 叠加）连同用它的作业一起删了，0xA004_0000 永远不碰（f150nv 固件里也没有它） */
        }
    }

    /* ---------------- 建立监听 socket ---------------- */
    lfd = socket(AF_INET, SOCK_STREAM, 0);
    if (lfd < 0) {
        printf("[err] socket: %s\n", strerror(errno));
        rc = 1;
        goto cleanup;
    }

    {
        int                 on = 1;
        struct sockaddr_in  addr;

        (void)setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on));

        memset(&addr, 0, sizeof(addr));
        addr.sin_family      = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_ANY);
        addr.sin_port        = htons(port);

        if (bind(lfd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
            printf("[err] bind port %u: %s\n", (unsigned)port, strerror(errno));
            rc = 1;
            goto cleanup;
        }
        if (listen(lfd, 1) < 0) {
            printf("[err] listen: %s\n", strerror(errno));
            rc = 1;
            goto cleanup;
        }
    }
    printf("[tcp] listening on 0.0.0.0:%u, waiting for sender...\n", (unsigned)port);

    /* ---------------- 主循环 ----------------
     * 一个连接上连续收多帧, 只有连接断了才回去 accept。
     *
     * 为什么不再"一帧一连接": 板子是单线程, 收完一帧还要把 786 KB 拷进 /dev/mem、
     * 回发、发布状态, 这期间它根本没回到 accept(), PC 的 SYN 只能干等。
     * 实测 PC 的 connect 在 0.7~22 ms 之间抖, 量级正好等于板端日志里的
     * wait-first-byte —— 往返延迟的波动几乎全来自这里。保持连接就绕开了整块开销。 */
    while (g_stop == 0) {
        struct sockaddr_in cli;
        socklen_t          cli_len = sizeof(cli);
        char               ip[INET_ADDRSTRLEN];
        uint64_t           t0;

        /* 单线程: 这里在两帧之间, PL 空闲, 换程序表是安全的 */
        if (g_reload != 0) {
            g_reload = 0;
            if (unet_ready() != 0) {
                (void)prog_load_dir(prog_dir);
            }
        }
        if (cfd < 0) {
            cfd = accept(lfd, (struct sockaddr *)&cli, &cli_len);
            if (cfd < 0) {
                if (errno == EINTR) {
                    continue;             /* 被信号打断, 回到 while 检查 g_stop */
                }
                printf("[err] accept: %s\n", strerror(errno));
                rc = 1;
                break;
            }

            (void)inet_ntop(AF_INET, &cli.sin_addr, ip, sizeof(ip));
            g_track.valid = 0;                    /* 板端跟踪状态只属于一个连接 */
            printf("\n[tcp] client %s:%u connected\n",
                   ip, (unsigned)ntohs(cli.sin_port));

            {
                int on = 1;
                int bufsz = 1 << 20;   /* 1 MiB: 把 TCP 窗口开大, 避免被反压 */
                struct timeval rto;

                (void)setsockopt(cfd, IPPROTO_TCP, TCP_NODELAY, &on, sizeof(on));
                (void)setsockopt(cfd, SOL_SOCKET, SO_RCVBUF, &bufsz, sizeof(bufsz));
                (void)setsockopt(cfd, SOL_SOCKET, SO_SNDBUF, &bufsz, sizeof(bufsz));

                /* 接收超时: 服务一次只服务一个连接, 若某客户端连上却不说话
                 * (界面被强杀、网络闪断...), 旧行为会**永远堵在这里**, 后面所有
                 * 连接都只能排在 backlog 里, 表现为"板子不响应"。给个 10 s 上限,
                 * 超时就断开回到 accept。整帧接收只要几十 ms, 10 s 非常宽松。 */
                rto.tv_sec  = 10;
                rto.tv_usec = 0;
                (void)setsockopt(cfd, SOL_SOCKET, SO_RCVTIMEO, &rto, sizeof(rto));
            }
        }

        t0 = now_ms();

        /* 让读端看到 magic 归 0, 知道新的一帧正在收 */
        st->magic = 0u;
        publish_status(img_buf, st);

        frc = receive_frame(cfd, rx_buf, st, &frame_mode,
                            &frame_flags, &frame_aiparam, &frame_thr, frame_roi, &v6c, verbose);

        if (frc == 2) {                      /* 病例任务: 上传一个序列, 整个病例在板上跑完(job.c) */
            static const uint8_t job_magic[2] = {(uint8_t)(CTRL_MAGIC_JOB & 0xFFu), (uint8_t)(CTRL_MAGIC_JOB >> 8)};

            frc = job_handle(cfd, job_magic, NULL, verbose);
        } else if (frc == 1) {               /* 紧凑帧(v6c): 快速通路, 不经 PL 预处理 / DMA */
            frc = handle_v6c(cfd, &v6c, frame_count + 1u, verbose);
            if (frc == 0) {
                frame_count++;
                st->frame_count = frame_count;
            }
        } else if (frc == 0) {
            uint64_t dt = now_ms() - t0;
            double   mbps = (dt > 0u)
                            ? ((double)STREAM_TOTAL_BYTES * 8.0 / ((double)dt * 1000.0))
                            : 0.0;
            int      dma_ok = 0;
            int      axil_ok = 0;
            int      ai_ok = 0;
            uint32_t ai_thr = 0u;        /* logit >= ai_thr 判为血管 */
            uint32_t ai_vessels = 0u;
            uint32_t ai_total = 0u;
            uint32_t ai_comps = 0u;
            uint32_t ai_rw = 0u, ai_rh = 0u;   /* 本帧实际用的 AI 输入分辨率 */
            uint32_t ai_ow = 0u, ai_oh = 0u;   /* 本帧 AI 输出分辨率 */
            int      ai_rsh = 0;               /* 本帧的分辨率档位 */
            int      ai_midx = 0;              /* 本帧用的模型(模型库下标) */
            int      roi_on = 0;               /* 本帧走 ROI 局部推理(v5 头带了 ROI) */
            int      roi_cx = 0, roi_cy = 0, roi_cw = 0, roi_ch = 0;   /* 裁剪窗口 */
            int      vm_ok = 0;                /* 本帧算出了形态指标(flags b6) */
            int      reg_ok = 0;               /* 本帧算出了分块求和(flags b7, F5 配准) */
            uint8_t  reg_blk[REG_BLOCK_BYTES];
            vm_result_t vm_res;
            unet_stats_t ai_st;
            const uint8_t *proc_img = NULL;   /* 预处理后的 RGB888, 可能为 NULL */

            memset(&ai_st, 0, sizeof(ai_st));

            frame_count++;
            st->frame_count = frame_count;
            st->mode        = frame_mode;
            printf("[ok ] frame %u stored (mode=%u thr=%u) at 0x%llX, %u ms, %.2f Mbit/s\n",
                   (unsigned)frame_count, (unsigned)frame_mode, (unsigned)frame_thr,
                   (unsigned long long)ddr_base, (unsigned)dt, mbps);

            /* ------- 1) 紧包 RGB888 -> DDR 32bit/像素 -------
             * 必须赶在启动 DMA 之前做完: MM2S 直接从输入图区读。 */
            {
                uint64_t tm0 = now_us();

                expand_rgb888_to_ddr(rx_buf, img_buf + IMG_BUF_OFFSET);
                if (verbose != 0) {
                    printf("[mem] expand to 32bpp = %u us\n",
                           (unsigned)(now_us() - tm0));
                }
            }

            /* ------- 2) PL 处理往返 -------
             * axil 通路优先: PS 逐像素推进 PL, 再逐像素把结果拉回来 —— 这是本板
             * 唯一可用的方向(PL->PS 从口没使能, DMA 进不来)。模式 5(直通) 不走 PL。 */
            if (axilproc_ready() != 0 && (frame_mode & 0x7u) != CTRL_MODE_BYPASS) {
                uint64_t ta = now_ms();
                /* 模式 6(边缘叠加) 在 PL 侧没有对应实现: 让 PL 跑模式 2 拿边缘图,
                 * 再由 PS 把边缘叠到原图上(原图就是 rx_buf, 不用再过一遍 PL)。 */
                int      pl_mode = (frame_mode == CTRL_MODE_EDGE_OVERLAY)
                                   ? (int)CTRL_MODE_EDGE_PL : (int)(frame_mode & 0x7u);
                int      arc = axilproc_frame(rx_buf, tx_buf, pl_mode, (int)frame_thr);
                unsigned mn = 0u, mx = 0u;

                axilproc_get_minmax(&mn, &mx);
                st->proc_min    = mn;
                st->proc_max    = mx;
                st->dma_status  = 0u;
                st->s2mm_status = 0u;
                axil_ok = (arc == 0);

                if (axil_ok != 0 && frame_mode == CTRL_MODE_EDGE_OVERLAY) {
                    /* 就地叠加: tx_buf 既是边缘图也是输出, 逐像素先读后写 */
                    ai_view_edge_overlay(rx_buf, tx_buf, tx_buf, (int)frame_thr,
                                         (int)(IMG_WIDTH * IMG_HEIGHT));
                }

                printf("[axil] %s: mode=%u (pl=%u) min=%u max=%u, %u ms\n",
                       (axil_ok != 0) ? "round-trip ok" : "round-trip FAILED",
                       (unsigned)(frame_mode & 0x7u), (unsigned)pl_mode, mn, mx,
                       (unsigned)(now_ms() - ta));
            } else if (use_dma == 0) {
                st->dma_status  = 0u;
                st->s2mm_status = 0u;
                st->proc_min    = 0u;
                st->proc_max    = 0u;
            } else if ((frame_mode & 0x7u) == CTRL_MODE_BYPASS) {
                /* 模式 5 = 原图直通: 完全不碰 PL/DMA, --echo 时回显收到的原图。
                 * dma_ok 保持 0, 下面回传逻辑自然走"原图"分支(复用行 CRC)。 */
                st->dma_status  = 0u;
                st->s2mm_status = 0u;
                st->proc_min    = 0u;
                st->proc_max    = 0u;
                printf("[bypass] mode=5 原图直通: 跳过 PL/DMA, 回显原图\n");
            } else {
                uint32_t ctrl = (frame_mode & 0x7u) | (frame_thr << 8);
                int      drc;

                st->proc_min = 0u;    /* 没接模式寄存器时保持 0, 不留上一帧的旧值 */
                st->proc_max = 0u;

                /* 模式必须在推流之前写进 PL, 否则处理 IP 会用上一帧的模式 */
                if (gpio.regs != NULL) {
                    (void)axigpio_write_ctrl(&gpio, ctrl);
                }

                drc = dma_process_frame(&dma, ddr_base, dma_timeout,
                                        &st->dma_status, &st->s2mm_status);
                dma_ok = (drc == 0);

                if (drc == 0) {
                    printf("[dma] round-trip ok: ctrl=0x%04X  %u B MM2S -> PL -> %u B S2MM\n",
                           (unsigned)ctrl, (unsigned)IMG_DDR_BYTES,
                           (unsigned)IMG_DDR_BYTES);
                } else {
                    char txt[128];
                    printf("[dma] round-trip FAILED rc=%d (ctrl=0x%04X)\n",
                           drc, (unsigned)ctrl);
                    axidma_format_status(st->dma_status, txt, sizeof(txt));
                    printf("[dma] %s\n", txt);
                    axidma_format_s2mm_status(st->s2mm_status, txt, sizeof(txt));
                    printf("[dma] %s\n", txt);
                }

                /* PL 帧统计: ch2 = {stat_max, stat_min}(确认处理流水线真跑过) */
                if (gpio.regs != NULL) {
                    uint32_t ch2 = axigpio_read_stat(&gpio);

                    st->proc_max = (ch2 >> 8) & 0xFFu;
                    st->proc_min =  ch2       & 0xFFu;
                    printf("[gpio] frame gray min=%u max=%u\n",
                           (unsigned)st->proc_min, (unsigned)st->proc_max);
                }
            }

            /* ------- 3) AI 识别 -------
             * 掩膜/叠加/统计/logit 图都需要 logits, 所以只要 flags 点到 AI, 就跑一次整网。
             * AI 输入可选"原始 RGB"或"预处理结果"(aiparam bit0)。 */
            if (axil_ok != 0) {
                proc_img = tx_buf;                       /* axilproc_frame 的产物 */
            } else if (use_dma != 0 && dma_ok != 0) {
                pack_ddr_to_rgb888(img_buf + IMG_OUT_OFFSET, tx_buf);
                proc_img = tx_buf;
            }

            /* 模型: aiparam bits[7:5], 0 = 板端默认(--model) */
            {
                int mf = (int)((frame_aiparam & AIPARAM_MODEL_MASK) >> AIPARAM_MODEL_SHIFT);

                ai_midx = (mf != 0) ? (mf - 1) : ai_model;
                if (ai_midx >= (int)UNET_MODELS_COUNT || !UNET_MODEL_PRESENT(ai_midx)) {
                    printf("[ai] model %d not in this build (%u models); using %s\n",
                           ai_midx, (unsigned)UNET_MODELS_COUNT, unet_models[ai_model].name);
                    ai_midx = ai_model;
                }
            }

            /* 分辨率档位: 客户端 aiparam bits[4:2] 优先; 0 = --ai-res(给了的话), 否则模型自己的默认 */
            {
                int rsh = (int)((frame_aiparam & AIPARAM_RES_MASK) >> AIPARAM_RES_SHIFT);

                if (rsh == 0) {
                    rsh = (ai_shift_set != 0) ? ai_shift : (int)unet_models[ai_midx].default_shift;
                }
                if (rsh > 5) {
                    rsh = 5;
                }
                ai_rsh = rsh;
                ai_rw = (uint32_t)UNET_IN_W  >> rsh;
                ai_rh = (uint32_t)UNET_IN_H  >> rsh;
                ai_ow = (uint32_t)UNET_OUT_W >> rsh;
                ai_oh = (uint32_t)UNET_OUT_H >> rsh;
            }

            /* ROI 局部推理(v5 头): 只在"ROI 外扩边距 + 对齐"的裁剪窗口上跑整网, 全分辨率;
             * 输出贴回 512x512, ROI 外为 ROI_OUTSIDE_LOGIT。分辨率档位对 ROI 不起作用。 */
            if (frame_roi[2] > 0u && frame_roi[3] > 0u && unet_models[ai_midx].roi_ok == 0u) {
                printf("[ai] ROI ignored: model %s was trained at %u x %u\n",
                       unet_models[ai_midx].name,
                       (unsigned)(512u >> unet_models[ai_midx].default_shift),
                       (unsigned)(512u >> unet_models[ai_midx].default_shift));
                frame_roi[0] = frame_roi[1] = frame_roi[2] = frame_roi[3] = 0u;
            }
            if (frame_roi[2] > 0u && frame_roi[3] > 0u) {
                roi_on = 1;
                ai_view_roi_crop((int)frame_roi[0], (int)frame_roi[1],
                                 (int)frame_roi[2], (int)frame_roi[3],
                                 &roi_cx, &roi_cy, &roi_cw, &roi_ch);
                ai_rw = (uint32_t)roi_cw;
                ai_rh = (uint32_t)roi_ch;
                ai_ow = (uint32_t)UNET_OUT_W;
                ai_oh = (uint32_t)UNET_OUT_H;
            }

            if (use_echo != 0 && unet_ready() != 0 &&
                (frame_flags & (FLAG_ECHO_MASK | FLAG_ECHO_OVER | FLAG_ECHO_LOGIT |
                                FLAG_MORPH | FLAG_REG | FLAG_STATS | FLAG_NO_IMAGE)) != 0u) {
                const uint8_t *ai_src = rx_buf;
                uint64_t       ta = now_ms();
                int            run_rc;

                (void)unet_select_model(ai_midx);          /* 下标上面已校验过 */

                if ((frame_aiparam & 0x3u) == AIPARAM_SRC_PROC) {
                    if (proc_img != NULL) {
                        ai_src = proc_img;
                    } else {
                        printf("[ai] preprocessed image not available; using raw RGB\n");
                    }
                }
                if (roi_on != 0) {
                    run_rc = unet_set_size(roi_ch, roi_cw);
                    if (run_rc == 0) {
                        ai_view_crop_rgb(ai_src, (int)IMG_WIDTH, roi_cx, roi_cy,
                                         roi_cw, roi_ch, ai_in);
                        run_rc = unet_run(ai_in, ai_crop_logits, &ai_st);
                    }
                    if (run_rc == 0) {
                        ai_view_roi_paste(ai_crop_logits, roi_cx, roi_cy, roi_cw,
                                          (int)frame_roi[0], (int)frame_roi[1],
                                          (int)frame_roi[2], (int)frame_roi[3],
                                          ai_logits, (int)ai_ow, (int)ai_oh, ai_roi_logits);
                    }
                    printf("[ai] ROI %u,%u %ux%u -> crop %d,%d %dx%d (%u%% of the frame)\n",
                           (unsigned)frame_roi[0], (unsigned)frame_roi[1],
                           (unsigned)frame_roi[2], (unsigned)frame_roi[3],
                           roi_cx, roi_cy, roi_cw, roi_ch,
                           (unsigned)((uint32_t)(roi_cw * roi_ch) * 100u /
                                      (IMG_WIDTH * IMG_HEIGHT)));
                } else {
                    (void)unet_set_shift(ai_rsh);
                    ai_view_downsample_rgb(ai_src, (int)IMG_WIDTH, (int)IMG_HEIGHT,
                                           ai_in, (int)ai_rw, (int)ai_rh);
                    run_rc = unet_run(ai_in, ai_logits, &ai_st);
                }
                if (run_rc != 0) {
                    printf("[ai] unet_run FAILED (image-only fallback)\n");
                } else {
                    ai_ok      = 1;
                    ai_thr     = (uint32_t)(int8_t)((frame_aiparam >> 8) & 0xFFu);
                    if (roi_on != 0) {           /* 统计只算 ROI 框内 */
                        ai_total   = frame_roi[2] * frame_roi[3];
                        ai_vessels = ai_view_count(ai_roi_logits, (int)frame_roi[2],
                                                   (int)frame_roi[3], (int)ai_thr, &ai_comps);
                    } else {
                        ai_total   = ai_ow * ai_oh;
                        ai_vessels = ai_view_count(ai_logits, (int)ai_ow, (int)ai_oh,
                                                   (int)ai_thr, &ai_comps);
                    }
                    st->ai_vessels = ai_vessels;
                    st->ai_comps   = ai_comps;
                    printf("[ai] model=%s src=%s %ux%u->%ux%u thr=%d  vessels=%u/%u (%u permille)  "
                           "comps=%u  logit[%d..%d]  %u ms\n",
                           unet_models[ai_midx].name, (ai_src == rx_buf) ? "raw" : "preproc",
                           (unsigned)ai_rw, (unsigned)ai_rh,
                           (unsigned)ai_ow, (unsigned)ai_oh, (int)ai_thr,
                           (unsigned)ai_vessels, (unsigned)ai_total,
                           (unsigned)((ai_vessels * 1000u) / (ai_total ? ai_total : 1u)),
                           (unsigned)ai_comps,
                           (int)ai_st.logit_min, (int)ai_st.logit_max,
                           (unsigned)(now_ms() - ta));
                }
            }

            /* ------- 3b) 血管形态指标(flags b6) -------
             * 在本帧血管判据(logit >= 门限)的掩膜上算; ROI 帧只分析 ROI 框(全分辨率),
             * 整图帧在 AI 分辨率的网格上分析。骨架视图在这里就画好(底图 = 叠加图的底图),
             * 因为下面回传时 tx_buf 会被叠加 / 掩膜 / logit 图依次覆盖。 */
            memset(&vm_res, 0, sizeof(vm_res));
            if (ai_ok != 0 && (frame_flags & FLAG_MORPH) != 0u) {
                const int8_t *lg = (roi_on != 0) ? ai_roi_logits : ai_logits;
                int  gw  = (roi_on != 0) ? (int)frame_roi[2] : (int)ai_ow;
                int  gh  = (roi_on != 0) ? (int)frame_roi[3] : (int)ai_oh;
                int  rx0 = (roi_on != 0) ? (int)frame_roi[0] : 0;
                int  ry0 = (roi_on != 0) ? (int)frame_roi[1] : 0;
                int  rww = (roi_on != 0) ? gw : (int)IMG_WIDTH;
                int  rhh = (roi_on != 0) ? gh : (int)IMG_HEIGHT;
                int  k;
                uint64_t tv = now_us();

                for (k = 0; k < gw * gh; k++) {
                    vm_mask[k] = (lg[k] >= (int8_t)ai_thr) ? 1u : 0u;
                }
                vm_fov_from_rgb(rx_buf, (int)IMG_WIDTH, rx0, ry0, rww, rhh, gw, gh, vm_fov);
                if (vm_analyze(vm_mask, vm_fov, gw, gh, (roi_on != 0) ? (int)IMG_WIDTH : gw,
                               vm_skel, &vm_res) == 0) {
                    vm_ok = 1;
                    vm_res.run_us = (uint32_t)(now_us() - tv);
                    if ((frame_flags & FLAG_NO_IMAGE) == 0u) {
                        vm_draw((proc_img != NULL) ? proc_img : rx_buf, vm_skel, gw, gh,
                                rx0, ry0, rww, rhh, vm_view);
                    }
                    printf("[vm] %dx%d  length %.1f  caliber %.2f  branches %u  ends %u  "
                           "segments %u  tortuosity %.3f  fractal %.3f  density %.1f%%  %u us\n",
                           gw, gh, vm_res.length_x10 / 10.0, vm_res.caliber_x100 / 100.0,
                           (unsigned)vm_res.branches, (unsigned)vm_res.endpoints,
                           (unsigned)vm_res.segments, vm_res.tortuosity_x1000 / 1000.0,
                           vm_res.fractal_x1000 / 1000.0, vm_res.density_x1000 / 10.0,
                           (unsigned)vm_res.run_us);
                }
            }

            /* ------- 3c) 分块求和(flags b7, F5 配准) -------
             * logit 图切成 REG_GRID x REG_GRID 块, 各块有符号求和。配 reg_absdiff 模型(PL 逐像素算
             * |a - b|)时, 上位机把 16 个候选平移拼成 4x4 一帧发来, 每块的和就是一个候选的 SAD。
             * 只对整图帧做(ROI 帧的 logit 布局不同), 并且要带统计块才发得出去。 */
            if (ai_ok != 0 && roi_on == 0 && (frame_flags & FLAG_REG) != 0u && (frame_flags & FLAG_STATS) != 0u) {
                uint64_t tr = now_us();

                ai_view_fill_reg(reg_blk, ai_logits, (int)ai_ow, (int)ai_oh, 0u);
                reg_ok = 1;
                {
                    uint32_t us = (uint32_t)(now_us() - tr);

                    reg_blk[20] = (uint8_t)(us & 0xFFu);          /* run_us, little endian */
                    reg_blk[21] = (uint8_t)((us >> 8) & 0xFFu);
                    reg_blk[22] = (uint8_t)((us >> 16) & 0xFFu);
                    reg_blk[23] = (uint8_t)((us >> 24) & 0xFFu);
                    printf("[reg] %ux%u in %dx%d tiles  %u us\n", (unsigned)ai_ow, (unsigned)ai_oh,
                           REG_GRID, REG_GRID, (unsigned)us);
                }
            }

            /* 发布状态块(含 DMA 结果); publish_status 会保证 magic 最后写 */
            st->magic = FRAME_MAGIC;
            publish_status(img_buf, st);
            printf("[ok ] status published: frame_count=%u mode=%u dma=%s\n",
                   (unsigned)frame_count, (unsigned)frame_mode,
                   dma_ok ? "ok" : ((use_dma != 0) ? "ERR" : "off"));

            /* ------- 4) 按 flags 回传 -------
             * 发的是**普通内存**里的副本, 不是 img_buf: 内核 send() 走 copy_from_user,
             * 而 /dev/mem 的 VM_PFNMAP 映射不支持 —— 实测会把内核打 oops, 进程一起死。
             *   b0 预处理图(没有预处理结果时回显原图, 复用行 CRC) / b1 掩膜 / b2 叠加 / b3 统计块
             *   b5 logit 图(R=G=B=logit+128) / b6 形态指标块 + 形态骨架视图
             * 顺序: 统计块 -> 形态指标块 -> 分块求和块 -> 预处理图 -> 叠加图 -> 掩膜图 -> logit 图 -> 形态视图。
             * (叠加是**就地**写在 proc 上的: ai_view_overlay_rgb 先读后写同一像素, 安全) */
            if (use_echo != 0) {
                uint64_t       te    = now_ms();
                uint32_t       flags = frame_flags;
                const uint8_t *proc  = (proc_img != NULL) ? proc_img : rx_buf;

                if ((flags & FLAG_STATS) != 0u) {
                    uint8_t blk[AI_STAT_BYTES];

                    /* b7 只在真的跟着分块求和块时留在回显的 flags 里, 上位机据此决定要不要收 */
                    ai_view_fill_stats(blk, (flags & 0xFFFFu & ~(reg_ok != 0 ? 0u : FLAG_REG)) |
                                       ((uint32_t)(ai_midx + 1) << 16), ai_ow,
                                       ai_ok ? ai_vessels : 0u, ai_total,
                                       ai_ok ? &ai_st : NULL, ai_comps,
                                       st->proc_min, st->proc_max,
                                       frame_mode, frame_count);
                    if (roi_on != 0) {
                        ai_view_set_stats_extra(blk, ((uint32_t)roi_ch << 16) | (uint32_t)roi_cw);
                    }
                    if (send_all(cfd, blk, AI_STAT_BYTES) != 0) {
                        printf("[echo] stats block send failed\n");
                    }
                    if ((flags & FLAG_MORPH) != 0u && vm_ok != 0) {   /* 形态指标块紧跟统计块 */
                        uint8_t vblk[VM_BLOCK_BYTES];

                        vm_fill_block(vblk, &vm_res);
                        if (send_all(cfd, vblk, VM_BLOCK_BYTES) != 0) {
                            printf("[echo] morphology block send failed\n");
                        }
                    }
                    if (reg_ok != 0) {                                 /* 分块求和块在形态块之后 */
                        if (send_all(cfd, reg_blk, REG_BLOCK_BYTES) != 0) {
                            printf("[echo] tile-sum block send failed\n");
                        }
                    }
                }

                if ((flags & FLAG_NO_IMAGE) == 0u) {
                    if ((flags & FLAG_ECHO_PROC) != 0u) {
                        int reuse = (proc_img == NULL) ? 1 : 0;

                        if (send_result_frame(cfd, proc, reuse) != 0) {
                            printf("[echo] proc image send failed\n");
                        }
                    }
                    if ((flags & FLAG_ECHO_OVER) != 0u && ai_ok != 0) {
                        ai_view_overlay_rgb(proc, ai_logits, (int)ai_ow, (int)ai_oh,
                                            (int)ai_thr, tx_buf, (int)IMG_WIDTH, (int)IMG_HEIGHT);
                        if (send_result_frame(cfd, tx_buf, 0) != 0) {
                            printf("[echo] overlay send failed\n");
                        }
                    }
                    if ((flags & FLAG_ECHO_MASK) != 0u && ai_ok != 0) {
                        (void)ai_view_mask_rgb(ai_logits, (int)ai_ow, (int)ai_oh,
                                               (int)ai_thr, tx_buf,
                                               (int)IMG_WIDTH, (int)IMG_HEIGHT, &ai_comps);
                        if (send_result_frame(cfd, tx_buf, 0) != 0) {
                            printf("[echo] mask send failed\n");
                        }
                    }
                    if ((flags & FLAG_ECHO_LOGIT) != 0u && ai_ok != 0) {
                        ai_view_logit_rgb(ai_logits, (int)ai_ow, (int)ai_oh,
                                          tx_buf, (int)IMG_WIDTH, (int)IMG_HEIGHT);
                        if (send_result_frame(cfd, tx_buf, 0) != 0) {
                            printf("[echo] logit send failed\n");
                        }
                    }
                    /* 和指标块一样要求同时带 b3: 上位机靠统计块的 version 决定等不等这幅图 */
                    if ((flags & FLAG_MORPH) != 0u && (flags & FLAG_STATS) != 0u && vm_ok != 0) {
                        if (send_result_frame(cfd, vm_view, 0) != 0) {
                            printf("[echo] morphology view send failed\n");
                        }
                    }
                }
                printf("[echo] flags=0x%04X sent in %u ms\n",
                       (unsigned)flags, (unsigned)(now_ms() - te));
            }
        } else {
            printf("[warn] frame not stored (rc=%d); buffer may be partial\n", frc);
        }
        if (frc == 1) {
            frc = 0;
        }

        if (frc != 0) {          /* 连接结束/出错: 断开, 下次重新 accept */
            close(cfd);
            cfd = -1;
        }

        if (frame_limit > 0 && (long)frame_count >= frame_limit) {
            printf("[tcp] reached frame limit %ld, exiting\n", frame_limit);
            break;
        }
    }

    if (cfd >= 0) {
        close(cfd);
    }

    printf("\n[tcp] shutting down\n");

cleanup:
    if (lfd >= 0) {
        close(lfd);
    }
    if (dma_regs != NULL) {
        munmap((void *)dma_regs, 4096u);
    }
    axilproc_close();
    unet_close();

    if (gpio_regs != NULL) {
        munmap((void *)gpio_regs, 4096u);
    }
    if (img_buf != NULL) {
        munmap((void *)img_buf, (size_t)RESV_WINDOW_BYTES);
    }
    if (rx_buf != NULL) {
        munmap((void *)rx_buf, (size_t)IMG_TOTAL_BYTES);
    }
    if (tx_buf != NULL) {
        munmap((void *)tx_buf, (size_t)IMG_TOTAL_BYTES);
    }
    if (mem_fd >= 0) {
        close(mem_fd);
    }
    return rc;
}
