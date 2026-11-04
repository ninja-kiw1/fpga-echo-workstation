/* unet_ai.c -- see unet_ai.h
 *
 * 条带（strip）调度版：张量住在 PS 内存里，PL 的那片 128 KB BRAM 只当
 * "一层条带的暂存"。这样 512x512 也能跑（整层 4 MB，BRAM 永远放不下）。
 *
 * 行依赖（从 PLD 的 conv_layer.cpp / eltwise_layer.cpp 逐行推出来，见
 * docs/ai_full_resolution_strip_plan.md 第 2 节）：
 *   3x3 conv   输出行 y 需要输入行 y-1,y,y+1   -> 喂 S+2 行，丢首尾各 1 行
 *   1x1 conv   需要 y                          -> 喂 S 行，全有效
 *   2x2 转置   需要 y>>1（一行产两行）          -> 喂 n 行，产 2n 行，全有效
 *   Add/Concat 逐像素                           -> 喂 S 行，全有效
 *   MaxPool    需要 2y,2y+1                     -> 喂 2S 行，全有效
 *
 * 图像真正的上下边界由**我们**补零行（不依赖 IP 自己的边界填充）：
 * 3x3 的输入缓冲第 j 行 = 真实输入行 y0-1+j，越界整行写 0；只保留输出第 1..S 行。
 */
#include "unet_ai.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

/* ---- AXI-Lite register offsets (from the HLS driver headers) ---- */
#define AP_CTRL      0x00u
#define AP_RETURN    0x10u
#define AP_START     (1u << 0)
#define AP_DONE      (1u << 1)

#define C_IN         0x18u
#define C_OUT        0x24u
#define C_PARAMS     0x30u
#define C_MODE       0x3Cu
#define C_CIN        0x44u
#define C_COUT       0x4Cu
#define C_IN_H       0x54u
#define C_IN_W       0x5Cu
#define C_RELU       0x64u
#define C_SHIFT      0x6Cu
#define C_QMIN       0x74u
#define C_QMAX       0x7Cu

#define E_IN_A       0x18u
#define E_IN_B       0x24u
#define E_OUT        0x30u
#define E_MODE       0x3Cu
#define E_CA         0x44u
#define E_CB         0x4Cu
#define E_H          0x54u
#define E_W          0x5Cu
#define E_M0         0x64u
#define E_M1         0x6Cu
#define E_SHIFT      0x74u
#define E_QMIN       0x7Cu
#define E_QMAX       0x84u

#define CONV_MODE_CONV3   0
#define CONV_MODE_CONV1   1
#define CONV_MODE_TCONV2  2

#define MAP_SIZE     0x1000UL
#define BRAM_WORDS   ((uint32_t)UNET_BRAM_WORDS)
#define BRAM_BYTES   (BRAM_WORDS * 16u)
#define SPIN_LIMIT   400000000UL     /* ~ 十几秒; 正常一层几十微秒就完成 */

#define WORD_BYTES   16u
#define LANES        8

/* ------------------------------------------------------------------ */
/*  可选: 用 PS 自带的 GDMA 把张量从 BRAM 读回来                        */
/*                                                                     */
/*  实测(2026-10-06): CPU 读 BRAM 是"每次 16B 一笔往返、不许并发",       */
/*  钉死在 ~198ns/16B(81MB/s); 而 FPD GDMA 能发 burst, 读 BRAM          */
/*  1290MB/s(15.9x)。plbdma.ko 提供:                                     */
/*    * 一块物理连续的 bounce 缓冲, 已 mmap 给本进程(可缓存);            */
/*    * ioctl 只做 "BRAM <-> bounce" 这一段 DMA;                         */
/*    我们再把 bounce memcpy 到目标(可缓存内存, ~3GB/s, 整网 85MB 约 30ms, */
/*    相对省下的 ~950ms 可忽略)。                                        */
/*  取不到 /dev/plbdma 就自动回退到原来的 CPU memcpy。                    */
/*  UNET_NOGDMA=1 可强制走 CPU 做对照。                                  */
/* ------------------------------------------------------------------ */
struct plbdma_req {
    uint32_t bram_off;
    uint32_t len;
    uint32_t dir;                /* 0 = BRAM->bounce, 1 = bounce->BRAM */
    uint32_t pad;
};

struct plbdma_info {
    uint32_t bounce_size;
    uint32_t dma_ok;
    uint32_t xfers;
    uint32_t errs;
};

#define PLBDMA_IOC_INFO _IOR('D', 0x01, struct plbdma_info)
#define PLBDMA_IOC_XFER _IOW('D', 0x02, struct plbdma_req)

/* 推迟拷贝(2026-10-07): 条带循环里"收上一条带"那一笔只做 DMA, 把
 * bounce->张量的 memcpy 推到**下一条带 start_ap 之后**, 藏进 PL 计算(稳态 ~37ms)。
 * memcpy 只碰 DDR(bounce 与张量), 不碰 PL 的 BRAM, 所以不是 9.26 那种
 * "PS 与 IP 并发访问 BRAM"。同一时刻最多一笔待办; 下一次往 bounce 里 DMA
 * 之前、节点收尾时都会先落地。张量各自 malloc、互不重叠, 推迟写 O 不会
 * 碍着本节点从输入张量组装下一条带。UNET_NODEFER=1 关掉, 做对照。 */
static int      g_dfd = -1;
static uint8_t *g_dbuf;                                  /* bounce 缓冲(可缓存) */
static size_t   g_dsize;
static int      g_dma_fail;                              /* 出错次数(用于自动放弃) */
static uint64_t g_dma_io_us;                             /* 花在 ioctl(真 DMA) 上的时间 */
static uint64_t g_dma_mc_us;                             /* 花在 bounce->dst memcpy 上的时间 */
static uint64_t g_dma_xfers;                             /* DMA 笔数 */
static uint64_t g_dma_mc_hid_us;                         /* 其中藏在 IP 计算期间的 memcpy */
static int      g_no_defer;                              /* UNET_NODEFER=1: 不推迟 memcpy */
static uint8_t       *g_pend_dst;                        /* 待落地的那笔 bounce->张量 */
static const uint8_t *g_pend_src;
static size_t         g_pend_bytes;                      /* 0 = 没有待办 */

static uint64_t now_us(void);                            /* 定义在下面 */

static void gdma_open(void)
{
    struct plbdma_info info;
    void *p;

    if (getenv("UNET_NOGDMA") != NULL) {
        printf("[ai] GDMA disabled by UNET_NOGDMA\n");
        return;
    }
    g_dfd = open("/dev/plbdma", O_RDWR);
    if (g_dfd < 0) {
        printf("[ai] no /dev/plbdma (%s), BRAM read stays on CPU\n", strerror(errno));
        return;
    }
    memset(&info, 0, sizeof(info));
    if (ioctl(g_dfd, PLBDMA_IOC_INFO, &info) != 0 || info.dma_ok == 0u ||
        info.bounce_size == 0u) {
        printf("[ai] /dev/plbdma not usable (rc=%d dma_ok=%u size=%u)\n",
               errno, (unsigned)info.dma_ok, (unsigned)info.bounce_size);
        close(g_dfd);
        g_dfd = -1;
        return;
    }
    g_dsize = info.bounce_size;
    p = mmap(NULL, g_dsize, PROT_READ | PROT_WRITE, MAP_SHARED, g_dfd, 0);
    if (p == MAP_FAILED) {
        printf("[ai] /dev/plbdma mmap failed: %s\n", strerror(errno));
        close(g_dfd);
        g_dfd = -1;
        return;
    }
    g_dbuf = (uint8_t *)p;
    printf("[ai] BRAM read via GDMA: bounce %u KB @ %p\n",
           (unsigned)(g_dsize >> 10), (void *)g_dbuf);
}

static void gdma_close(void)
{
    if (g_dbuf != NULL) {
        munmap(g_dbuf, g_dsize);
        g_dbuf = NULL;
    }
    if (g_dfd >= 0) {
        close(g_dfd);
        g_dfd = -1;
    }
    g_dsize = 0;
    g_pend_bytes = 0;
}

/* 把推迟的那笔 bounce->张量 memcpy 落地, 返回花了多少微秒(没有待办就 0)。
 * 规矩: 往 bounce 里 DMA 下一笔之前、有人要读目标张量之前, 都必须先调它。 */
static uint64_t gdma_flush(void)
{
    uint64_t t0;

    if (g_pend_bytes == 0u) {
        return 0u;
    }
    t0 = now_us();
    memcpy(g_pend_dst, g_pend_src, g_pend_bytes);
    g_pend_bytes = 0u;
    t0 = now_us() - t0;
    g_dma_mc_us += t0;
    return t0;
}

/* BRAM -> dst, 走 GDMA + bounce memcpy。返回 0 成功, -1 表示该整段回退 CPU。
 * defer != 0: 最后一块只做 DMA, memcpy 记成待办(由 gdma_flush 落地)。 */
static int gdma_get(uint32_t word_off, void *dst, size_t bytes, int defer)
{
    uint8_t *d    = (uint8_t *)dst;
    size_t   off  = (size_t)word_off * WORD_BYTES;
    size_t   left = bytes;

    (void)gdma_flush();                   /* bounce 要被覆盖了: 上一笔先落地 */
    while (left > 0) {
        struct plbdma_req req;
        size_t            chunk = (left > g_dsize) ? g_dsize : left;
        uint64_t          t0, t1;

        req.bram_off = (uint32_t)off;
        req.len      = (uint32_t)chunk;
        req.dir      = 0u;
        req.pad      = 0u;
        t0 = now_us();
        if (ioctl(g_dfd, PLBDMA_IOC_XFER, &req) != 0) {
            g_dma_fail++;
            if (g_dma_fail == 1) {
                printf("[ai] GDMA xfer failed (%s) at off=0x%lX len=%u\n",
                       strerror(errno), (unsigned long)off, (unsigned)chunk);
            }
            return -1;
        }
        g_dma_xfers++;
        t1 = now_us();
        g_dma_io_us += t1 - t0;
        if (defer != 0 && chunk == left) {
            g_pend_dst   = d;
            g_pend_src   = g_dbuf;
            g_pend_bytes = chunk;
        } else {
            memcpy(d, g_dbuf, chunk);
            g_dma_mc_us += now_us() - t1;
        }
        d    += chunk;
        off  += chunk;
        left -= chunk;
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/*  模块状态                                                          */
/* ------------------------------------------------------------------ */
static volatile uint32_t *g_conv;
static volatile uint32_t *g_elt;
static uint8_t  *g_bram;             /* 用 memcpy 访问, 所以不加 volatile */
static uint8_t  *g_map_bram_v;
static void     *g_map_conv;
static void     *g_map_elt;
static int       g_fd = -1;
static uint64_t  g_bram_base;
static int       g_shift;            /* 分辨率档位: 节点尺寸 = 表里的值 >> g_shift */
static int       g_in_h = (int)UNET_IN_H;   /* 本次运行的输入尺寸: 整图 = 512>>shift,  */
static int       g_in_w = (int)UNET_IN_W;   /* ROI 局部推理 = 裁剪窗口(unet_set_size)  */

/* ------------------------------------------------------------------ */
/*  第 0 步插桩: 把一次整网的时间切成 PL 计算 / 搬运 / 其它            */
/*  全部在 unet_run 开头清零; 只在统计意义上用, 不影响功能。           */
/* ------------------------------------------------------------------ */
static uint64_t g_ap_us, g_put_us, g_get_us, g_zero_us;
static uint64_t g_strips, g_put_words, g_get_words, g_param_words;
static uint64_t g_dma_get_words, g_cpu_get_words;   /* 读回走了 GDMA / 走了 CPU */
static uint64_t g_node_ap[UNET_NODES_LEN];
static uint64_t g_node_bram[UNET_NODES_LEN];

static inline uint64_t bram_us_total(void) { return g_put_us + g_get_us + g_zero_us; }

typedef struct {
    int      h, w, g;                /* g = 补齐到 8 的倍数后的通道组数 */
    uint8_t *p;                      /* h*w*g*16 字节 */
} uten_t;

static uten_t g_ten[UNET_NODES_LEN];
static int    g_alloc_full;          /* 张量是按 shift=0 的尺寸分配的 */

static inline uint32_t rd(volatile uint32_t *r, uint32_t off) { return r[off >> 2]; }
static inline void     wr(volatile uint32_t *r, uint32_t off, uint32_t v) { r[off >> 2] = v; }

static inline void wr_addr(volatile uint32_t *r, uint32_t off, uint64_t addr)
{
    wr(r, off, (uint32_t)(addr & 0xFFFFFFFFu));
    wr(r, off + 4u, (uint32_t)(addr >> 32));
}

static uint64_t now_us(void)
{
    struct timespec ts;
    (void)clock_gettime(CLOCK_MONOTONIC, &ts);
    return ((uint64_t)ts.tv_sec * 1000000u) + ((uint64_t)ts.tv_nsec / 1000u);
}

static inline int groups_of(int c) { return (c + LANES - 1) / LANES; }
/* 表里的尺寸都是 512 / 2^k, 按本次输入尺寸等比缩放; 输入是 8 的倍数时两级池化后仍为整数 */
static inline int scale_h(int v) { return (int)(((long)v * g_in_h) / (long)UNET_IN_H); }
static inline int scale_w(int v) { return (int)(((long)v * g_in_w) / (long)UNET_IN_W); }

/* ------------------------------------------------------------------ */
/*  BRAM 存取                                                        */
/*                                                                     */
/*  ⚠ 这里踩过一个真实的大坑: glibc 的 **memset** 在大块且对齐时会用     */
/*  **`DC ZVA`**(数据缓存清零指令)。`DC ZVA` 只对 Normal 内存合法,       */
/*  对 /dev/mem 映射出来的 **Device 内存是未定义行为** —— 实测直接       */
/*  **SIGBUS(core dumped)**, 而且崩在第一个"补零行"上, 非常难猜。       */
/*                                                                     */
/*  对策:                                                              */
/*   * 数据搬运用 **memcpy**(不用 memset) —— memcpy 走 ldp/stp,         */
/*     地址是 16 字节对齐的, 对 Device 内存安全, 而且比逐字快得多;      */
/*   * **补零行**改成自己写的 volatile 32bit 循环(编译器绝不会换成库调用,*/
/*     也不可能用 DC ZVA)。补零只在每个节点的首/末条带各出现一次,       */
/*     走慢路径对总耗时没有影响。                                       */
/*  长度保证是 4 的倍数: 张量一行是 16 的倍数, params 是 8 的倍数。     */
/* ------------------------------------------------------------------ */
static inline uint8_t *bram_at(uint32_t word_off)
{
    return g_bram + ((size_t)word_off * WORD_BYTES);
}

static inline void bram_put(uint32_t word_off, const void *src, size_t bytes)
{
    uint64_t t0 = now_us();

    memcpy((void *)bram_at(word_off), src, bytes);
    g_put_us   += now_us() - t0;
    g_put_words += bytes >> 4;
}

static inline void bram_zero(uint32_t word_off, size_t bytes)
{
    volatile uint32_t *d = (volatile uint32_t *)(void *)bram_at(word_off);
    size_t             n = bytes >> 2;
    size_t             i;
    uint64_t           t0 = now_us();

    for (i = 0; i < n; i++) {
        d[i] = 0u;
    }
    g_zero_us += now_us() - t0;
}

/* 读回一条带的输出行。
 * 有 GDMA 就走 GDMA(BRAM->bounce->memcpy), 否则退回原来的 CPU memcpy。
 * 注意: 张量 dst 是**可缓存**内存 -> 千万不要让 DMA 直接写它(要么物理不连续、
 * 要么要自己管 cache)。所以这里 DMA 只打到 bounce, 由 CPU memcpy 落到 dst,
 * dst 上不存在任何 DMA/CPU 的缓存一致性风险。
 * defer != 0 时 bounce->dst 那次 memcpy 只记成待办, 见 bram_get_defer。 */
static inline void bram_get_x(uint32_t word_off, void *dst, size_t bytes, int defer)
{
    uint64_t t0 = now_us();
    int      via_dma = 0;

    if (g_dbuf != NULL) {
        if (gdma_get(word_off, dst, bytes, defer) == 0) {
            via_dma = 1;
        } else {
            g_dbuf = NULL;          /* 一次不成, 以后老实用 CPU */
        }
    }
    if (!via_dma) {
        memcpy(dst, (const void *)bram_at(word_off), bytes);
        g_cpu_get_words += bytes >> 4;
    } else {
        g_dma_get_words += bytes >> 4;
    }
    g_get_us   += now_us() - t0;
    g_get_words += bytes >> 4;
}

static inline void bram_get(uint32_t word_off, void *dst, size_t bytes)
{
    bram_get_x(word_off, dst, bytes, 0);
}

/* 只能在 IP 空闲时调(和 bram_get 一样)。走 GDMA 时只做 DMA, memcpy 留给
 * bram_get_settle_hidden(下一条带 start_ap 之后)或 bram_get_settle(节点收尾)。 */
static inline void bram_get_defer(uint32_t word_off, void *dst, size_t bytes)
{
    bram_get_x(word_off, dst, bytes, g_no_defer == 0);
}

/* 本条带已 start_ap: 让待办 memcpy 和 PL 计算并行。不在关键路径上, 所以
 * 不计入 get_us, 单独记 g_dma_mc_hid_us。它算在 ap 的计时窗口里 —— 只要
 * memcpy 比 IP 跑得快(一条带 ~45µs vs ~430µs), ap 读数就不受影响。 */
static inline void bram_get_settle_hidden(void)
{
    g_dma_mc_hid_us += gdma_flush();
}

/* 节点收尾: 还没落地的就地落地(在关键路径上, 计入 get_us)。 */
static inline void bram_get_settle(void)
{
    uint64_t t0 = now_us();

    (void)gdma_flush();
    g_get_us += now_us() - t0;
}

/* ⚠ Normal-NC(写合并)映射下, 前面写的字可能还在 CPU 的写缓冲里没到 BRAM。
 * 而 AP_START 是走**另一个口**(HPM0 控制口)发出去的, 两条路之间没有顺序保证
 * —— 不处理的话 IP 可能读到旧的输入。这里**读回程序顺序里最后一个写入字**:
 * 同一个 master 对同一个从机的写是保序的, 这次读一旦返回, 它之前的写就都已到 BRAM。
 * 代价是一次 ~200 ns 的读, 每条带一次, 可忽略。 */
static inline void bram_commit(uint32_t word_off)
{
    volatile const uint32_t *p = (volatile const uint32_t *)(const void *)bram_at(word_off);

    (void)p[0];
}

/* ------------------------------------------------------------------ */
/*  并发判定探针 (UNET_OVL_PROBE=1)                                    */
/*                                                                     */
/*  想做的事: 让 DMA 读 BRAM(取回上一段输出) 与 PL 的 IP 同时跑。         */
/*  2026-10-02 那次 A-ov 失败是 **CPU + IP** 并发 -> 数据全坏;           */
/*  这里要判的是 **DMA + IP** 并发行不行。                              */
/*                                                                     */
/*  做法: 在 start_ap 之前把 BRAM 输入区快照到 DDR(此时 IP 没跑),        */
/*        start_ap 之后立刻用 DMA 把同一段读回来比对 —— 输入区对 IP 是    */
/*        只读的, 所以两者必须逐字节相同。整网 golden 顺便验            */
/*        "IP 的输出有没有被并发的 DMA 搞坏"。                          */
/* ------------------------------------------------------------------ */
static int       g_ovl_probe;
static int       g_no_ovl;            /* UNET_NOOVL=1: 关掉重叠做对照 */
static int       g_use_dbuf;          /* UNET_DBUF=1: 双缓冲布局(实验, 见文件头说明) */
static size_t    g_ovl_bytes;         /* 本段要盯的输入区字节数 */
static uint8_t  *g_ovl_snap;
static uint8_t  *g_ovl_buf;
static uint64_t  g_ovl_ok, g_ovl_bad, g_ovl_words;

static void ovl_arm(size_t bytes)
{
    if (!g_ovl_probe) {
        return;
    }
    if (g_ovl_snap == NULL) {
        g_ovl_snap = (uint8_t *)malloc(64u * 1024u);
        g_ovl_buf  = (uint8_t *)malloc(64u * 1024u);
        if (g_ovl_snap == NULL || g_ovl_buf == NULL) {
            g_ovl_probe = 0;
            return;
        }
    }
    if (bytes == 0u || bytes > 64u * 1024u) {
        g_ovl_bytes = 0u;
        return;
    }
    memcpy(g_ovl_snap, bram_at(0), bytes);    /* IP 还没跑, 这拍是干净的 */
    g_ovl_bytes = bytes;
}

static void ovl_check(void)
{
    uint64_t w0;
    size_t   i;
    int      bad = 0;

    if (!g_ovl_probe || g_ovl_bytes == 0u) {
        return;
    }
    w0 = g_dma_get_words;
    bram_get(0, g_ovl_buf, g_ovl_bytes);      /* IP 正在跑, 并发 DMA 读 */
    if (g_dma_get_words == w0) {              /* 没走 DMA(回退了), 这次不算 */
        return;
    }
    for (i = 0; i < g_ovl_bytes; i++) {
        if (g_ovl_buf[i] != g_ovl_snap[i]) {
            bad++;
        }
    }
    g_ovl_words += g_ovl_bytes;
    if (bad != 0) {
        g_ovl_bad++;
        if (g_ovl_bad <= 3) {
            printf("[ovl] !! DMA+IP concurrent read mismatch=%d/%zu  (ok=%llu bad=%llu)\n",
                   bad, g_ovl_bytes, (unsigned long long)g_ovl_ok,
                   (unsigned long long)g_ovl_bad);
        }
    } else {
        g_ovl_ok++;
    }
}

static void ovl_report(void)
{
    if (g_ovl_probe) {
        printf("[ovl] DMA+IP concurrent-read check: ok=%llu / bad=%llu, %llu KB total\n",
               (unsigned long long)g_ovl_ok, (unsigned long long)g_ovl_bad,
               (unsigned long long)(g_ovl_words >> 10));
    }
}

/* 启动一次 IP 运行(**不等待**)。A-ov: 启动后 CPU 立刻去收上一条带的输出,
 * 让"读回"与本次计算重叠。 */
static void start_ap(volatile uint32_t *reg)
{
    wr(reg, AP_CTRL, AP_START);
}

/* 等 ap_done。t_start = start_ap 之前取的时刻 —— 用它统计 IP **真正跑了多久**
 * (而不是只在原地自旋的那一段), 这样重叠之后 ap 的读数仍然有意义。 */
static int wait_ap(volatile uint32_t *reg, const char *what, uint64_t t_start)
{
    unsigned long k = 0;
    int           rc = 0;

    while ((rd(reg, AP_CTRL) & AP_DONE) == 0u) {
        if (++k > SPIN_LIMIT) {
            printf("[ai] %s: TIMEOUT waiting ap_done\n", what);
            rc = -2;
            break;
        }
    }
    if (rc == 0) {
        uint32_t ret = rd(reg, AP_RETURN);
        if (ret != 0u) {
            printf("[ai] %s: kernel returned status %u (bad config)\n", what, ret);
            rc = -1;
        }
    }
    g_ap_us += now_us() - t_start;
    return rc;
}

/* 启动并立即等待。所有条带都走这条路 —— A-ov(读回与计算重叠)在**本板上
 * 行不通**: 实测 CPU 在 IP 运行期间访问同一片 BRAM 会把数据搞坏(见
 * docs/ai_full_resolution_strip_plan.md §11), 所以这里不做重叠。 */
/* 说明: 原来有个 start_and_wait()(起 IP 并立刻等完)。现在两个节点体都改成
 * "start_ap(k) -> 收上一条带的输出 -> 下一轮 wait_ap(k)" 的软件流水,
 * 好让"读回"与"PL 计算"重叠, 所以不再需要它了。 */

/* ------------------------------------------------------------------ */
/*  张量分配                                                          */
/* ------------------------------------------------------------------ */
static void free_tensors(void)
{
    int i;
    for (i = 0; i < (int)UNET_NODES_LEN; i++) {
        free(g_ten[i].p);
        g_ten[i].p = NULL;
    }
}

static int alloc_tensors(void)
{
    int i;
    size_t total = 0;

    for (i = 0; i < (int)UNET_NODES_LEN; i++) {
        const unet_node_t *n = &unet_nodes[i];
        size_t bytes = (size_t)n->out_h * n->out_w * groups_of(n->out_c) * WORD_BYTES;

        g_ten[i].h = n->out_h;
        g_ten[i].w = n->out_w;
        g_ten[i].g = groups_of(n->out_c);
        g_ten[i].p = (uint8_t *)malloc(bytes);
        if (g_ten[i].p == NULL) {
            printf("[ai] malloc %u bytes for node %d failed (total so far %u)\n",
                   (unsigned)bytes, i, (unsigned)total);
            free_tensors();
            return -1;
        }
        total += bytes;
    }
    g_alloc_full = 1;
    printf("[ai] tensors: %d nodes, %u MB resident in PS memory\n",
           (int)UNET_NODES_LEN, (unsigned)(total >> 20));
    return 0;
}

/* 张量 BRAM 用哪种映射:
 *   优先 /dev/plbram (plbram.ko 给的 Normal-NC, 实测**写快 ~2.7×**; 读不变,
 *   因为非缓存读本来就是一次完整往返、不流水 —— 见 docs/ai_full_resolution_strip_plan.md §14);
 *   没有就退回 /dev/mem (Device)。UNET_BRAM_MEM=1 可强制走 /dev/mem 做对照。 */
static void *map_bram(uint64_t bram_base, size_t bytes)
{
    int   fd;
    void *p;

    if (getenv("UNET_BRAM_MEM") == NULL) {
        fd = open("/dev/plbram", O_RDWR | O_SYNC);
        if (fd >= 0) {
            p = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
            close(fd);                       /* mmap 之后关 fd, 映射仍有效 */
            if (p != MAP_FAILED) {
                printf("[ai] tensor BRAM via /dev/plbram (Normal-NC)\n");
                return p;
            }
            printf("[ai] /dev/plbram mmap failed: %s\n", strerror(errno));
        } else {
            printf("[ai] no /dev/plbram (%s), using /dev/mem\n", strerror(errno));
        }
    }
    p = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, g_fd, (off_t)bram_base);
    if (p != MAP_FAILED) {
        printf("[ai] tensor BRAM via /dev/mem (Device)\n");
    }
    return p;
}

int unet_init(uint64_t conv_ctrl, uint64_t elt_ctrl, uint64_t bram_base)
{
    uint32_t v;

    if (g_conv != NULL) {
        return 0;
    }
    g_fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (g_fd < 0) {
        printf("[ai] open /dev/mem: %s\n", strerror(errno));
        return -1;
    }
    g_map_conv = mmap(NULL, MAP_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, g_fd, (off_t)conv_ctrl);
    g_map_elt  = mmap(NULL, MAP_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, g_fd, (off_t)elt_ctrl);
    g_map_bram_v = (uint8_t *)map_bram(bram_base, BRAM_BYTES);
    if (g_map_conv == MAP_FAILED || g_map_elt == MAP_FAILED || g_map_bram_v == MAP_FAILED) {
        printf("[ai] mmap failed: %s\n", strerror(errno));
        unet_close();
        return -1;
    }
    g_conv = (volatile uint32_t *)g_map_conv;
    g_elt  = (volatile uint32_t *)g_map_elt;
    g_bram = g_map_bram_v;
    g_bram_base = bram_base;
    g_shift = 0;
    g_in_h  = (int)UNET_IN_H;
    g_in_w  = (int)UNET_IN_W;

    /* 只读探测: 两个 kernel 都得是 idle（绝不碰没有从机的地址） */
    v = rd(g_conv, AP_CTRL);
    if (((v >> 2) & 1u) == 0u) {
        printf("[ai] conv ap_idle=0 (CTRL=0x%08X): PL not our design?\n", v);
        unet_close();
        return -2;
    }
    v = rd(g_elt, AP_CTRL);
    if (((v >> 2) & 1u) == 0u) {
        printf("[ai] eltwise ap_idle=0 (CTRL=0x%08X): PL not our design?\n", v);
        unet_close();
        return -2;
    }
    if (alloc_tensors() != 0) {
        unet_close();
        return -1;
    }
    gdma_open();                     /* 可选: 把"读回"换成 GDMA */
    if (getenv("UNET_NODEFER") != NULL) {
        g_no_defer = 1;
        printf("[ai] defer OFF (control): bounce memcpy stays on the critical path\n");
    }
    if (getenv("UNET_OVL_PROBE") != NULL) {
        g_ovl_probe = 1;
        printf("[ai] OVL probe ON: 每次 ap 期间并发一笔 DMA 读并校验\n");
    }
    if (getenv("UNET_NOOVL") != NULL) {
        g_no_ovl = 1;
        printf("[ai] overlap OFF (control): 双缓冲布局保留, 但读回不重叠\n");
    }
    /* ⚠ 双缓冲 + 重叠在本板**行不通**, 默认关闭(2026-10-06 实测):
     *   布局和流水记账都是对的(不重叠的对照组 golden 0/262144 PASS),
     *   但"PS 侧 DMA 读输出" 与 "IP 正在写另一块输出" 并发时会坏数据
     *   (mismatch 233879/262144, logits 饱和 ±127) —— 与 2026-10-02 A-ov 同一类
     *   硬件缺陷, 只是当年是 CPU∘IP, 这次是 DMA∘IP, 且只在"读∥写"时发作。
     *   另外即使用不重叠的双缓冲, 也**更慢**(891 vs 777ms): 条带数 826->1809,
     *   而 IP 每次启动有 ~100µs 固定开销(实测反推), 多出来的 983 次启动要付 ~100ms。
     *   实验代码保留在 ai/net/_unet_ai_OVERLAP_reference.c.txt。 */
    if (getenv("UNET_DBUF") != NULL) {
        g_use_dbuf = 1;
        printf("[ai] !! 双缓冲 + 重叠 = EXPERIMENTAL (实测会坏数据, 仅供复现)\n");
    }

    printf("[ai] U-Net ready: %u nodes, %ux%ux%u in -> %ux%ux%u out, BRAM @0x%llX (%u words)\n",
           (unsigned)UNET_NODES_LEN, (unsigned)UNET_IN_H, (unsigned)UNET_IN_W,
           (unsigned)UNET_IN_C, (unsigned)UNET_OUT_H, (unsigned)UNET_OUT_W,
           (unsigned)UNET_OUT_C, (unsigned long long)bram_base, (unsigned)BRAM_WORDS);
    return 0;
}

void unet_close(void)
{
    gdma_close();
    free_tensors();
    if (g_map_conv != NULL && g_map_conv != MAP_FAILED) { munmap(g_map_conv, MAP_SIZE); }
    if (g_map_elt  != NULL && g_map_elt  != MAP_FAILED) { munmap(g_map_elt,  MAP_SIZE); }
    if (g_map_bram_v != NULL && g_map_bram_v != MAP_FAILED) { munmap(g_map_bram_v, BRAM_BYTES); }
    g_map_conv = g_map_elt = g_map_bram_v = NULL;
    g_conv = NULL;
    g_elt  = NULL;
    g_bram = NULL;
    if (g_fd >= 0) {
        close(g_fd);
        g_fd = -1;
    }
}

int unet_ready(void) { return g_conv != NULL; }

int unet_set_shift(int shift)
{
    if (shift < 0 || shift > 5) {
        return -1;
    }
    if (!unet_ready() || !g_alloc_full) {
        return -1;
    }
    g_shift = shift;
    g_in_h  = (int)UNET_IN_H >> shift;
    g_in_w  = (int)UNET_IN_W >> shift;
    return 0;
}

int unet_get_shift(void) { return g_shift; }

int unet_set_size(int h, int w)
{
    if (h < (int)UNET_SIZE_ALIGN || w < (int)UNET_SIZE_ALIGN ||
        h > (int)UNET_IN_H || w > (int)UNET_IN_W ||
        (h % (int)UNET_SIZE_ALIGN) != 0 || (w % (int)UNET_SIZE_ALIGN) != 0) {
        return -1;
    }
    if (!unet_ready() || !g_alloc_full) {
        return -1;
    }
    g_shift = 0;
    g_in_h  = h;
    g_in_w  = w;
    return 0;
}

/* ------------------------------------------------------------------ */
/*  CONV 节点: 按输出行切条带                                          */
/* ------------------------------------------------------------------ */
static int run_conv_body(int idx)
{
    const unet_node_t *n = &unet_nodes[idx];
    const uten_t *A = &g_ten[n->in0];
    uten_t       *O = &g_ten[idx];
    const uint32_t par_bytes = unet_param_len[idx];
    const uint32_t par_words = (par_bytes + WORD_BYTES - 1u) / WORD_BYTES;
    const int w_in  = A->w;
    const int rin   = w_in * A->g;                 /* 输入一行多少字 */
    const int rout  = O->w * O->g;                 /* 输出一行多少字 */
    const uint32_t avail = (BRAM_WORDS > par_words) ? (BRAM_WORDS - par_words) : 0u;
    int S, y0, rc;

    if (avail == 0u) {
        printf("[ai] node %d: params %u words exceed BRAM\n", idx, par_words);
        return -1;
    }

    /* ---- 条带高度 ----
     * **双缓冲布局**: [in0][in1][out0][out1][params]
     * 目的: 让"收上一段输出(PS 侧 DMA)"与"本次 PL 计算"重叠 —— 实测 DMA 与 IP 并发
     * 是安全的(2026-10-06 探针: ok=114/bad=0, golden 仍 0/262144)。
     * 顺带"组装本次输入"也能与前一次计算重叠。
     * ⚠ IP 是按**输出像素**算的(halo 只多读不多算), 所以条带变小不会让 ap 变慢。 */
    if (g_use_dbuf != 0) {                             /* 实验: 双缓冲(条带高度减半) */
        if (n->mode == CONV_MODE_CONV3) {
            if (avail <= (uint32_t)(4 * (rin + rout))) {
                printf("[ai] node %d: two 3x3 halos do not fit (%d words)\n", idx, 4 * rin);
                return -1;
            }
            S = (int)((avail - (uint32_t)(4 * (rin + rout))) / (uint32_t)(2 * (rin + rout)));
        } else if (n->mode == CONV_MODE_CONV1) {
            S = (int)(avail / (uint32_t)(2 * (rin + rout)));
        } else {
            S = (int)(avail / (uint32_t)(rin + 2 * rout));
        }
    } else {                                       /* 默认: 单缓冲(已验证的那套) */
        if (n->mode == CONV_MODE_CONV3) {
            if (avail <= (uint32_t)(2 * (rin + rout))) {
                printf("[ai] node %d: one 3x3 halo does not fit (%d words)\n", idx, 2 * rin);
                return -1;
            }
            S = (int)((avail - (uint32_t)(2 * (rin + rout))) / (uint32_t)(rin + rout));
        } else if (n->mode == CONV_MODE_CONV1) {
            S = (int)(avail / (uint32_t)(rin + rout));
        } else {                                   /* 转置: n 行进 -> 2n 行出 */
            S = 2 * (int)(avail / (uint32_t)(rin + 2 * rout));
        }
    }
    if (S < 1) {
        printf("[ai] node %d: strip height computed as %d\n", idx, S);
        return -1;
    }
    if (S > O->h) {
        S = O->h;
    }
    if (n->mode == CONV_MODE_CONV3 && S > (int)UNET_IN_H - 2) {
        S = (int)UNET_IN_H - 2;             /* kernel input includes two halo rows */
    }
    if (n->mode == CONV_MODE_TCONV2 && (S & 1)) {
        S--;                                       /* 转置要求偶数输出行 */
    }
    if (S < 1) {
        return -1;
    }

    /* ---- BRAM 内的双缓冲布局（按最大条带固定，最后一条也用同样布局）---- */
    {
        const uint32_t in_words  = ((n->mode == CONV_MODE_CONV3) ? (uint32_t)(S + 2)
                                  : (n->mode == CONV_MODE_TCONV2) ? (uint32_t)(S / 2)
                                  : (uint32_t)S) * (uint32_t)rin;
        /* The kernel writes S+2 output rows for a 3x3 strip, including both
         * discarded halo rows. Reserve them before params/the next buffer;
         * reserving only S rows overran BRAM for ROI widths such as 176. */
        const uint32_t out_words = (uint32_t)(S +
                                  ((n->mode == CONV_MODE_CONV3) ? 2 : 0)) * (uint32_t)rout;
        const uint32_t in_off1   = in_words;
        const uint32_t out_off0  = g_use_dbuf ? (2u * in_words) : in_words;
        const uint32_t out_off1  = out_off0 + out_words;
        const uint32_t par_off   = out_off0 + (g_use_dbuf ? 2u : 1u) * out_words;
        const int      keep0     = (n->mode == CONV_MODE_CONV3) ? 1 : 0;
        int            slot = 0, have_prev = 0, prev_read = 0;
        int            prev_slot = 0, prev_y0 = 0, prev_Sact = 0;
        uint64_t       t_prev = 0;

        if (par_off + par_words > BRAM_WORDS) {
            printf("[ai] node %d: layout %u x2 + %u x2 + %u > %u\n", idx,
                   in_words, out_words, par_words, (unsigned)BRAM_WORDS);
            return -1;
        }
        if (getenv("UNET_DEBUG") != NULL) {
            printf("[ai]   conv S=%d dbuf: in=%u x2 out=%u x2 par=%u rin=%d rout=%d\n",
                   S, in_words, out_words, par_off, rin, rout);
        }

        for (y0 = 0; y0 < O->h; y0 += S) {
            int Sact = (O->h - y0 < S) ? (O->h - y0) : S;
            int nin, j;
            uint32_t in_off, out_off;

            g_strips++;

            if (n->mode == CONV_MODE_CONV3) {
                nin = Sact + 2;
            } else if (n->mode == CONV_MODE_TCONV2) {
                nin = Sact / 2;
            } else {
                nin = Sact;
            }

            if (g_use_dbuf != 0) {
                slot ^= 1;                         /* 与上一条带用不同的那一组缓冲 */
            }
            in_off  = slot ? in_off1 : 0u;
            out_off = slot ? out_off1 : out_off0;

            /* (1) 等上一条带的 IP 跑完 —— **必须在组装之前**: 单缓冲时 IP 就是在这块
             *     输入缓冲上读数据, 先写会把它的输入踩坏(2026-10-06 踩过这个竞态)。
             *     单体 IP: 不等它也不能起下一条。 */
            if (have_prev != 0) {
                rc = wait_ap(g_conv, "conv", t_prev);
                if (rc != 0) {
                    printf("[ai] node %d strip y=%d..%d failed\n", idx, prev_y0,
                           prev_y0 + prev_Sact);
                    return rc;
                }
                if (g_no_ovl != 0 || g_use_dbuf == 0) {   /* 对照/单缓冲: 不重叠, 立刻收 */
                    /* IP 空闲, 只做 DMA; 落到 O 的 memcpy 推到 (4) 之后 */
                    bram_get_defer((prev_slot ? out_off1 : out_off0) +
                                       (uint32_t)keep0 * (uint32_t)rout,
                                   O->p + ((size_t)prev_y0 * (size_t)rout * WORD_BYTES),
                                   (size_t)prev_Sact * (size_t)rout * WORD_BYTES);
                    prev_read = 1;
                }
            }

            /* (2) 组装输入条带（含我们自己补的零行） */
            for (j = 0; j < nin; j++) {
                int      ty;
                uint32_t doff = in_off + (uint32_t)j * (uint32_t)rin;

                if (n->mode == CONV_MODE_CONV3) {
                    ty = y0 - 1 + j;
                } else if (n->mode == CONV_MODE_TCONV2) {
                    ty = (y0 >> 1) + j;
                } else {
                    ty = y0 + j;
                }
                if (ty < 0 || ty >= A->h) {
                    bram_zero(doff, (size_t)rin * WORD_BYTES);
                } else {
                    bram_put(doff, A->p + ((size_t)ty * (size_t)rin * WORD_BYTES),
                             (size_t)rin * WORD_BYTES);
                }
            }

            /* (3) 参数 —— 也必须等上一条带跑完再写: IP 是边跑边从 BRAM 读参数的 */
            bram_put(par_off, unet_params + unet_param_off[idx], par_bytes);
            g_param_words += (uint64_t)(par_bytes >> 4);
            bram_commit(par_off + par_words - 1u);   /* 强制刷到 BRAM 再启动 IP */
            __sync_synchronize();
            ovl_arm((size_t)nin * (size_t)rin * WORD_BYTES);

            /* (4) 起本条带 */
            wr_addr(g_conv, C_IN,     g_bram_base + (uint64_t)in_off * WORD_BYTES);
            wr_addr(g_conv, C_OUT,    g_bram_base + (uint64_t)out_off * WORD_BYTES);
            wr_addr(g_conv, C_PARAMS, g_bram_base + (uint64_t)par_off * WORD_BYTES);
            wr(g_conv, C_MODE,  n->mode);
            wr(g_conv, C_CIN,   n->ca);
            wr(g_conv, C_COUT,  n->cb);
            wr(g_conv, C_IN_H,  (uint32_t)nin);
            wr(g_conv, C_IN_W,  (uint32_t)w_in);
            wr(g_conv, C_RELU,  n->relu);
            wr(g_conv, C_SHIFT, n->shift);
            wr(g_conv, C_QMIN,  (uint32_t)(int32_t)n->qmin);
            wr(g_conv, C_QMAX,  (uint32_t)(int32_t)n->qmax);
            t_prev = now_us();
            start_ap(g_conv);
            bram_get_settle_hidden();  /* 上一条带的 bounce->O memcpy, 与本条带计算并行 */
            ovl_check();               /* UNET_OVL_PROBE=1 时: IP 正在跑, 趁机判并发 */

            /* (5) 收**上一条带**的输出 —— 与本条带的 PL 计算并行。这就是重叠的全部。
             *     前一条带的输出在另一块 out 缓冲里, 不会被本次计算踩到。
             *     条带内的行在 BRAM 侧与 PS 侧都连续 -> 合并成一笔(每笔固定开销 ~25µs)。 */
            if (have_prev != 0 && prev_read == 0) {
                bram_get((prev_slot ? out_off1 : out_off0) +
                             (uint32_t)keep0 * (uint32_t)rout,
                         O->p + ((size_t)prev_y0 * (size_t)rout * WORD_BYTES),
                         (size_t)prev_Sact * (size_t)rout * WORD_BYTES);
            }

            /* (6) 本条带变成"上一条带" */
            have_prev = 1;
            prev_read = 0;
            prev_slot = slot;
            prev_y0   = y0;
            prev_Sact = Sact;
        }

        /* 收尾: 最后一条带的 IP 与读回 */
        if (have_prev != 0) {
            rc = wait_ap(g_conv, "conv", t_prev);
            if (rc != 0) {
                printf("[ai] node %d last strip failed\n", idx);
                return rc;
            }
            if (prev_read == 0) {
                bram_get((prev_slot ? out_off1 : out_off0) +
                             (uint32_t)keep0 * (uint32_t)rout,
                         O->p + ((size_t)prev_y0 * (size_t)rout * WORD_BYTES),
                         (size_t)prev_Sact * (size_t)rout * WORD_BYTES);
            }
        }
        bram_get_settle();             /* 下一个节点要读 O: 不留待办 */
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/*  ELT 节点: 逐像素, 只有 MaxPool 需要两行                            */
/* ------------------------------------------------------------------ */
static int run_elt_body(int idx)
{
    const unet_node_t *n = &unet_nodes[idx];
    const uten_t *A = &g_ten[n->in0];
    const uten_t *B = (n->in1 >= 0) ? &g_ten[n->in1] : A;
    uten_t       *O = &g_ten[idx];
    const int w_in = A->w;
    const int rin_a = w_in * A->g;
    const int rin_b = (n->mode == 0 || n->mode == 1) ? (w_in * B->g) : 0;
    const int rout  = O->w * O->g;
    const uint32_t avail = BRAM_WORDS;
    int S, y0, rc;

    /* 双缓冲(见 run_conv_body 的说明): [in_a0 in_b0][in_a1 in_b1][out0][out1] */
    if (g_use_dbuf != 0) {
        if (n->mode == 2) {                        /* MaxPool: 2S 行进 -> S 行出 */
            S = (int)(avail / (uint32_t)(4 * rin_a + 2 * rout));
        } else {                                   /* Add / Concat */
            S = (int)(avail / (uint32_t)(2 * (rin_a + rin_b + rout)));
        }
    } else {                                       /* 默认: 单缓冲 */
        if (n->mode == 2) {
            S = (int)(avail / (uint32_t)(2 * rin_a + rout));
        } else {
            S = (int)(avail / (uint32_t)(rin_a + rin_b + rout));
        }
    }
    if (S < 1) {
        printf("[ai] elt node %d: strip height %d (rin %d+%d rout %d)\n",
               idx, S, rin_a, rin_b, rout);
        return -1;
    }
    if (S > O->h) {
        S = O->h;
    }

    {
        const uint32_t in_rows = (n->mode == 2) ? (uint32_t)(2 * S) : (uint32_t)S;
        const uint32_t in_a_w  = in_rows * (uint32_t)rin_a;
        const uint32_t in_b_w  = in_rows * (uint32_t)rin_b;
        const uint32_t in_w    = in_a_w + in_b_w;
        const uint32_t out_w   = (uint32_t)S * (uint32_t)rout;
        const uint32_t out_off0 = g_use_dbuf ? (2u * in_w) : in_w;
        const uint32_t out_off1 = out_off0 + out_w;
        int            slot = 0, have_prev = 0, prev_read = 0;
        int            prev_slot = 0, prev_y0 = 0, prev_Sact = 0;
        uint64_t       t_prev = 0;

        if (g_use_dbuf) {
            if (2u * in_w + 2u * out_w > BRAM_WORDS) {
                printf("[ai] elt node %d: layout %u x2 + %u x2 > %u\n", idx, in_w, out_w,
                       (unsigned)BRAM_WORDS);
                return -1;
            }
        } else if (out_off0 + out_w > BRAM_WORDS) {
            printf("[ai] elt node %d: layout %u+%u > %u\n", idx, out_off0, out_w,
                   (unsigned)BRAM_WORDS);
            return -1;
        }
        if (getenv("UNET_DEBUG") != NULL) {
            printf("[ai]   elt S=%d dbuf: in=%u x2 out=%u x2 rin %d+%d\n",
                   S, in_w, out_w, rin_a, rin_b);
        }

        for (y0 = 0; y0 < O->h; y0 += S) {
            int      Sact = (O->h - y0 < S) ? (O->h - y0) : S;
            int      nin = (n->mode == 2) ? (2 * Sact) : Sact;
            int      j;
            uint32_t base, out_off;

            g_strips++;

            if (g_use_dbuf != 0) {
                slot ^= 1;
            }
            base    = slot ? in_w : 0u;
            out_off = slot ? out_off1 : out_off0;

            /* (1) 等上一条带 —— 必须在组装之前(单缓冲时 IP 正在读这块输入缓冲) */
            if (have_prev != 0) {
                rc = wait_ap(g_elt, "eltwise", t_prev);
                if (rc != 0) {
                    printf("[ai] elt node %d strip y=%d..%d failed\n", idx, prev_y0,
                           prev_y0 + prev_Sact);
                    return rc;
                }
                if (g_no_ovl != 0 || g_use_dbuf == 0) {   /* 对照/单缓冲: 立刻收 */
                    /* IP 空闲, 只做 DMA; 落到 O 的 memcpy 推到 (3) 之后 */
                    bram_get_defer(prev_slot ? out_off1 : out_off0,
                                   O->p + ((size_t)prev_y0 * (size_t)rout * WORD_BYTES),
                                   (size_t)prev_Sact * (size_t)rout * WORD_BYTES);
                    prev_read = 1;
                }
            }

            /* (2) 组装输入 */
            for (j = 0; j < nin; j++) {
                int ty = (n->mode == 2) ? (2 * y0 + j) : (y0 + j);

                bram_put(base + (uint32_t)j * (uint32_t)rin_a,
                         A->p + ((size_t)ty * (size_t)rin_a * WORD_BYTES),
                         (size_t)rin_a * WORD_BYTES);
                if (rin_b > 0) {
                    bram_put(base + in_a_w + (uint32_t)j * (uint32_t)rin_b,
                             B->p + ((size_t)ty * (size_t)rin_b * WORD_BYTES),
                             (size_t)rin_b * WORD_BYTES);
                }
            }
            bram_commit(in_b_w ? (base + in_a_w + in_b_w - 1u)
                               : (base + (uint32_t)nin * (uint32_t)rin_a - 1u));
            __sync_synchronize();
            ovl_arm((size_t)nin * (size_t)rin_a * WORD_BYTES);

            /* (3) 起本条带 */
            wr_addr(g_elt, E_IN_A, g_bram_base + (uint64_t)base * WORD_BYTES);
            wr_addr(g_elt, E_IN_B, g_bram_base +
                    (uint64_t)(rin_b ? (base + in_a_w) : base) * WORD_BYTES);
            wr_addr(g_elt, E_OUT,  g_bram_base + (uint64_t)out_off * WORD_BYTES);
            wr(g_elt, E_MODE,  n->mode);
            wr(g_elt, E_CA,    n->ca);
            wr(g_elt, E_CB,    n->cb);
            wr(g_elt, E_H,     (uint32_t)nin);
            wr(g_elt, E_W,     (uint32_t)w_in);
            wr(g_elt, E_M0,    (uint32_t)n->m0);
            wr(g_elt, E_M1,    (uint32_t)n->m1);
            wr(g_elt, E_SHIFT, n->shift);
            wr(g_elt, E_QMIN,  (uint32_t)(int32_t)n->qmin);
            wr(g_elt, E_QMAX,  (uint32_t)(int32_t)n->qmax);
            t_prev = now_us();
            start_ap(g_elt);
            bram_get_settle_hidden();  /* 上一条带的 bounce->O memcpy, 与本条带计算并行 */
            ovl_check();               /* UNET_OVL_PROBE=1 时: IP 正在跑, 趁机判并发 */

            /* (4) 收上一条带的输出(与本次计算并行) */
            if (have_prev != 0 && prev_read == 0) {
                bram_get(prev_slot ? out_off1 : out_off0,
                         O->p + ((size_t)prev_y0 * (size_t)rout * WORD_BYTES),
                         (size_t)prev_Sact * (size_t)rout * WORD_BYTES);
            }

            have_prev = 1;
            prev_read = 0;
            prev_slot = slot;
            prev_y0   = y0;
            prev_Sact = Sact;
        }

        if (have_prev != 0) {
            rc = wait_ap(g_elt, "eltwise", t_prev);
            if (rc != 0) {
                printf("[ai] elt node %d last strip failed\n", idx);
                return rc;
            }
            if (prev_read == 0) {
                bram_get(prev_slot ? out_off1 : out_off0,
                         O->p + ((size_t)prev_y0 * (size_t)rout * WORD_BYTES),
                         (size_t)prev_Sact * (size_t)rout * WORD_BYTES);
            }
        }
        bram_get_settle();             /* 下一个节点要读 O: 不留待办 */
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/*  输入张量: RGB888 -> 每像素 8 个 int16 通道(lane 0..2 = R,G,B)      */
/* ------------------------------------------------------------------ */
static void fill_input_tensor(const uint8_t *rgb)
{
    uten_t *T = &g_ten[0];
    int y, x;

    for (y = 0; y < T->h; y++) {
        uint8_t *row = T->p + ((size_t)y * (size_t)T->w * WORD_BYTES);
        for (x = 0; x < T->w; x++) {
            size_t   src = ((size_t)y * (size_t)T->w + (size_t)x) * 3u;
            uint8_t *d = row + ((size_t)x * WORD_BYTES);
            d[0] = rgb[src + 0u];      /* R -> lane0 int16 低字节 */
            d[2] = rgb[src + 1u];      /* G */
            d[4] = rgb[src + 2u];      /* B */
        }
    }
}

/* ------------------------------------------------------------------ */
/*  节点包装: 记录每个节点的 PL 计算 / 搬运耗时 (第 0 步插桩)          */
/* ------------------------------------------------------------------ */
static int run_conv(int idx)
{
    uint64_t b_ap = g_ap_us;
    uint64_t b_br = bram_us_total();
    int      rc   = run_conv_body(idx);

    g_node_ap[idx]   += g_ap_us - b_ap;
    g_node_bram[idx] += bram_us_total() - b_br;
    return rc;
}

static int run_elt(int idx)
{
    uint64_t b_ap = g_ap_us;
    uint64_t b_br = bram_us_total();
    int      rc   = run_elt_body(idx);

    g_node_ap[idx]   += g_ap_us - b_ap;
    g_node_bram[idx] += bram_us_total() - b_br;
    return rc;
}

/* ------------------------------------------------------------------ */
/*  整网                                                              */
/* ------------------------------------------------------------------ */
int unet_run(const uint8_t *rgb, int8_t *logits, unet_stats_t *st)
{
    uint64_t t0 = now_us();
    uint32_t i;
    int32_t  lo = 0, hi = 0;
    int      rc = 0;

    if (g_conv == NULL) {
        return -1;
    }

    /* 第 0 步插桩: 每帧清零 */
    g_ap_us = g_put_us = g_get_us = g_zero_us = 0u;
    g_strips = g_put_words = g_get_words = g_param_words = 0u;
    g_dma_get_words = g_cpu_get_words = 0u;
    g_dma_io_us = g_dma_mc_us = g_dma_mc_hid_us = 0u;
    g_dma_xfers = 0u;
    g_pend_bytes = 0u;              /* 上一帧中途失败留下的待办, 作废 */
    g_ovl_ok = g_ovl_bad = g_ovl_words = 0u;
    for (i = 0u; i < (uint32_t)UNET_NODES_LEN; i++) {
        g_node_ap[i]   = 0u;
        g_node_bram[i] = 0u;
    }

    /* 每次运行都按当前输入尺寸(shift 档位或 ROI 裁剪)重算各张量的 h/w/g（缓冲是按
     * 512x512 分配的，小尺寸只用前一段，所以不用重新 malloc）。 */
    for (i = 0u; i < (uint32_t)UNET_NODES_LEN; i++) {
        g_ten[i].h = scale_h(unet_nodes[i].out_h);
        g_ten[i].w = scale_w(unet_nodes[i].out_w);
        g_ten[i].g = groups_of(unet_nodes[i].out_c);
        if (g_ten[i].h < 1 || g_ten[i].w < 1) {
            printf("[ai] node %u dims collapse at input %dx%d\n", i, g_in_h, g_in_w);
            return -3;
        }
    }

    fill_input_tensor(rgb);
    if (getenv("UNET_DEBUG") != NULL) {
        printf("[ai] input tensor filled (%dx%d)\n", g_ten[0].h, g_ten[0].w);
    }

    for (i = 1u; i < (uint32_t)UNET_NODES_LEN; i++) {
        if (getenv("UNET_DEBUG") != NULL) {
            printf("[ai] node %u kind=%u mode=%u in=%dx%d out=%dx%d g=%d\n", i,
                   unet_nodes[i].kind, unet_nodes[i].mode,
                   g_ten[unet_nodes[i].in0].h, g_ten[unet_nodes[i].in0].w,
                   g_ten[i].h, g_ten[i].w, g_ten[i].g);
        }
        rc = (unet_nodes[i].kind == UNET_KIND_CONV) ? run_conv((int)i) : run_elt((int)i);
        if (rc != 0) {
            printf("[ai] node %u failed rc=%d\n", i, rc);
            return -4;
        }
        if (getenv("UNET_DEBUG") != NULL) {
            printf("[ai] node %u ok\n", i);
        }
    }

    {
        const unet_node_t *o = &unet_nodes[UNET_NODES_LEN - 1u];
        uten_t *T = &g_ten[UNET_NODES_LEN - 1u];
        int n = T->h * T->w;
        int k;

        (void)o;
        for (k = 0; k < n; k++) {
            logits[k] = (int8_t)T->p[(size_t)k * WORD_BYTES];
        }
        if (n > 0) {
            lo = hi = logits[0];
            for (k = 1; k < n; k++) {
                if (logits[k] < lo) { lo = logits[k]; }
                if (logits[k] > hi) { hi = logits[k]; }
            }
        }
    }

    {   /* ---- 第 0 步: 汇总并(可选)打印时间分解 ---- */
        uint64_t tend = now_us();

        ovl_report();

        if (st != NULL) {
            st->run_us      = (uint32_t)(tend - t0);
            st->nodes_run   = (uint32_t)UNET_NODES_LEN - 1u;
            st->logit_min   = lo;
            st->logit_max   = hi;
            st->ap_us       = (uint32_t)g_ap_us;
            st->put_us      = (uint32_t)g_put_us;
            st->get_us      = (uint32_t)g_get_us;
            st->zero_us     = (uint32_t)g_zero_us;
            st->strips      = (uint32_t)g_strips;
            st->put_words   = g_put_words;
            st->get_words   = g_get_words;
            st->param_words = g_param_words;
        }
        if (getenv("UNET_PROFILE") != NULL) {
            uint64_t tot = g_ap_us + bram_us_total();
            uint32_t j;

            printf("[prof] total=%llu us  ap=%llu  put=%llu  get=%llu  zero=%llu  other=%lld\n",
                   (unsigned long long)(tend - t0),
                   (unsigned long long)g_ap_us,
                   (unsigned long long)g_put_us,
                   (unsigned long long)g_get_us,
                   (unsigned long long)g_zero_us,
                   (long long)((int64_t)(tend - t0) - (int64_t)tot));
            printf("[prof] strips=%llu  put=%llu words  get=%llu words  params=%llu words\n",
                   (unsigned long long)g_strips,
                   (unsigned long long)g_put_words,
                   (unsigned long long)g_get_words,
                   (unsigned long long)g_param_words);
            printf("[prof] get split: DMA=%llu words  CPU=%llu words  (dma_fail=%d)\n",
                   (unsigned long long)g_dma_get_words,
                   (unsigned long long)g_cpu_get_words, g_dma_fail);
            printf("[prof] get DMA detail: xfers=%llu  ioctl=%llu us  bounce_memcpy=%llu us"
                   " (hidden in ap: %llu)\n",
                   (unsigned long long)g_dma_xfers,
                   (unsigned long long)g_dma_io_us,
                   (unsigned long long)g_dma_mc_us,
                   (unsigned long long)g_dma_mc_hid_us);
            printf("[prof] per-node: idx ap_us bram_us\n");
            for (j = 1u; j < (uint32_t)UNET_NODES_LEN; j++) {
                if (g_node_ap[j] == 0u && g_node_bram[j] == 0u) {
                    continue;
                }
                printf("[prof]   %2u  %8llu  %8llu\n", (unsigned)j,
                       (unsigned long long)g_node_ap[j],
                       (unsigned long long)g_node_bram[j]);
            }
        }
    }
    return 0;
}
