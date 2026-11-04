/*
 * host_test_defer.c —— 主机端功能测试(测试用, 不是交付物)。
 *
 * 测 src/unet_ai.c 里"推迟 bounce->张量 memcpy"那套机制(2026-10-07)。
 * 和 host_test_rx.c 同一个思路: 直接 #include 交付的 .c, 原样调用它的
 * static 函数; 只把 /dev/plbdma 的 ioctl 换成一个假的 GDMA(从假 BRAM
 * 拷进 bounce)。假 GDMA 每次都会覆盖 bounce —— 所以"下一笔 DMA 之前
 * 没把上一笔落地"这类错误会直接表现为目标数据不对。
 *
 * 覆盖:
 *   1. defer 之后目标没动、记成待办; settle_hidden 之后逐字节正确;
 *   2. 连续两笔 defer(中间不 settle): 第二笔 DMA 前必须先落地第一笔;
 *   3. 超过 bounce 大小的分块传输: 前几块立刻拷, 只有最后一块推迟;
 *   4. UNET_NODEFER 对照: 不推迟;
 *   5. 待办期间来一笔普通 bram_get(如 ovl 探针): 先落地待办;
 *   6. settle(节点收尾)计入 get_us, settle_hidden 不计入;
 *   7. DMA 出错: 退回 CPU 读, 之后的 defer 也走 CPU, 不留待办。
 *
 * 编译运行见 build_host_test_defer.bat。
 */
#include "../src/unet_ai.c"

#include <stdarg.h>
#include <stdio.h>

#define FAKE_BRAM_BYTES (64u * 1024u)
#define FAKE_DSIZE      4096u            /* 故意很小, 好测分块 */

static uint8_t g_fake_bram[FAKE_BRAM_BYTES];
static uint8_t g_fake_bounce[FAKE_DSIZE];
static int     g_ioctl_calls;
static int     g_ioctl_fail;             /* 非 0: 下一次 XFER 失败 */
static int     g_failures;

/* ---- 假的系统调用(unet_init 之类的路径本测试不走, 只为链接) ---- */
void *mmap(void *addr, size_t length, int prot, int flags, int fd, off_t offset)
{
    (void)addr; (void)length; (void)prot; (void)flags; (void)fd; (void)offset;
    return MAP_FAILED;
}

int munmap(void *addr, size_t length)
{
    (void)addr; (void)length;
    return 0;
}

int ioctl(int fd, unsigned long req, ...)
{
    va_list            ap;
    struct plbdma_req *r;

    (void)fd;
    va_start(ap, req);
    r = va_arg(ap, struct plbdma_req *);
    va_end(ap);
    if (req != (unsigned long)PLBDMA_IOC_XFER || r->dir != 0u) {
        return -1;
    }
    g_ioctl_calls++;
    if (g_ioctl_fail != 0) {
        g_ioctl_fail = 0;
        errno = EIO;
        return -1;
    }
    if (r->len > FAKE_DSIZE || (size_t)r->bram_off + r->len > FAKE_BRAM_BYTES) {
        return -1;
    }
    memcpy(g_fake_bounce, g_fake_bram + r->bram_off, r->len);
    return 0;
}

/* ---- 小工具 ---- */
static void check(int ok, const char *what)
{
    printf("  [%s] %s\n", ok ? "PASS" : "FAIL", what);
    if (!ok) {
        g_failures++;
    }
}

static void reset(void)
{
    size_t i;

    for (i = 0; i < FAKE_BRAM_BYTES; i++) {
        g_fake_bram[i] = (uint8_t)((i * 131u + 7u) ^ (i >> 8));
    }
    memset(g_fake_bounce, 0xEE, sizeof(g_fake_bounce));
    g_bram        = g_fake_bram;         /* CPU 回退路径读这里 */
    g_dbuf        = g_fake_bounce;
    g_dsize       = FAKE_DSIZE;
    g_dfd         = 3;
    g_no_defer    = 0;
    g_pend_bytes  = 0;
    g_ioctl_calls = 0;
    g_ioctl_fail  = 0;
    g_dma_fail    = 0;
    g_get_us = g_dma_mc_us = g_dma_mc_hid_us = 0;
}

static int same(const uint8_t *dst, uint32_t word_off, size_t bytes)
{
    return memcmp(dst, g_fake_bram + (size_t)word_off * WORD_BYTES, bytes) == 0;
}

static int untouched(const uint8_t *dst, size_t bytes)
{
    size_t i;

    for (i = 0; i < bytes; i++) {
        if (dst[i] != 0u) {
            return 0;
        }
    }
    return 1;
}

int main(void)
{
    static uint8_t a[16384], b[16384];

    printf("1. defer -> settle_hidden\n");
    reset();
    memset(a, 0, sizeof(a));
    bram_get_defer(16, a, 2048);
    check(untouched(a, 2048), "target untouched right after defer");
    check(g_pend_bytes == 2048u && g_ioctl_calls == 1, "one DMA done, 2048 B pending");
    bram_put(512, g_fake_bounce + 4000, 16);   /* 模拟组装下一条带(只写 BRAM) */
    bram_get_settle_hidden();
    check(same(a, 16, 2048), "target correct after settle_hidden");
    check(g_pend_bytes == 0u, "nothing pending");

    printf("2. two defers back to back\n");
    reset();
    memset(a, 0, sizeof(a));
    memset(b, 0, sizeof(b));
    bram_get_defer(0, a, 4096);
    bram_get_defer(1000, b, 3072);             /* 会覆盖 bounce */
    check(same(a, 0, 4096), "first target landed before second DMA");
    check(untouched(b, 3072) && g_pend_bytes == 3072u, "second one pending");
    bram_get_settle_hidden();
    check(same(b, 1000, 3072), "second target correct");

    printf("3. chunked (larger than bounce)\n");
    reset();
    memset(a, 0, sizeof(a));
    bram_get_defer(64, a, 10240);              /* 4096 + 4096 + 2048 */
    check(g_ioctl_calls == 3, "three DMA chunks");
    check(same(a, 64, 8192), "first two chunks copied immediately");
    check(untouched(a + 8192, 2048) && g_pend_bytes == 2048u, "only the last chunk pending");
    bram_get_settle_hidden();
    check(same(a, 64, 10240), "whole target correct");

    printf("4. UNET_NODEFER control\n");
    reset();
    g_no_defer = 1;
    memset(a, 0, sizeof(a));
    bram_get_defer(32, a, 1024);
    check(same(a, 32, 1024) && g_pend_bytes == 0u, "copied immediately, nothing pending");

    printf("5. plain bram_get while something is pending\n");
    reset();
    memset(a, 0, sizeof(a));
    memset(b, 0, sizeof(b));
    bram_get_defer(100, a, 1600);
    bram_get(2000, b, 800);
    check(same(a, 100, 1600), "pending one landed first");
    check(same(b, 2000, 800) && g_pend_bytes == 0u, "plain get correct, nothing pending");

    printf("6. accounting\n");
    reset();
    memset(a, 0, sizeof(a));
    bram_get_defer(0, a, 4096);
    {
        uint64_t get0 = g_get_us;
        bram_get_settle_hidden();
        check(g_get_us == get0, "settle_hidden not added to get_us");
    }
    bram_get_defer(256, a, 4096);
    bram_get_settle();
    check(same(a, 256, 4096) && g_pend_bytes == 0u, "settle lands it");
    check(g_dma_mc_us >= g_dma_mc_hid_us, "hidden is a part of bounce_memcpy");

    printf("7. DMA failure falls back to CPU\n");
    reset();
    memset(a, 0, sizeof(a));
    memset(b, 0, sizeof(b));
    g_ioctl_fail = 1;
    bram_get_defer(300, a, 2048);
    check(same(a, 300, 2048), "fallback read correct");
    check(g_dbuf == NULL && g_pend_bytes == 0u, "GDMA disabled, nothing pending");
    bram_get_defer(400, b, 2048);
    check(same(b, 400, 2048) && g_pend_bytes == 0u, "later defer goes straight to CPU");

    printf("TOTAL FAILURES: %d\n", g_failures);
    return g_failures == 0 ? 0 : 1;
}
