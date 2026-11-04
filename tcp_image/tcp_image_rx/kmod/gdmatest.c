/* gdmatest.c -- 用 PS 自带的 DMA (xilinx-zynqmp-dma) 搬 PL BRAM 数据的可行性/带宽探测
 *
 * 背景(2026-10-06 实测): CPU 读 PL BRAM 被钉死在 **每次 16B 访问 ~198ns(81MB/s)**,
 * 不流水、与指令宽度/映射属性无关; 而写侧(Normal-NC 写合并)能跑 1.08GB/s —— 说明
 * PL 互联通路本身带宽远高于 81MB/s, 卡住的是"CPU 单笔往返 + 不允许并发"。
 * 那么换成 **DMA 主设备**(能发 AXI burst)也许能把 5.34M 字的读回从 1113ms 压下来。
 *
 * 本模块要回答三个问题:
 *   1) 通道/prep/submit 这套走不走得通、DDR->DDR 带宽多少(自检, 零风险)
 *   2) FPD GDMA 的主口**能不能到达 PL 的 0xB0000000**(HPM1_FPD 窗口)  <-- 关键未知
 *   3) 到得了的话, BRAM 的读/写各能跑多快, 数据对不对
 *
 * 用法(板上):
 *   insmod gdmatest.ko mode=0                 # 只做 DDR->DDR 自检
 *   insmod gdmatest.ko mode=1                 # 再加 BRAM->DDR 读(4KB 起)
 *   insmod gdmatest.ko mode=2                 # 再加 DDR->BRAM 写 + 回读校验
 *   insmod gdmatest.ko mode=2 len=4096        # 改长度(默认 262144)
 *   insmod gdmatest.ko mode=2 dev=ffa80000.dma  # 换通道(默认 fd500000.dma = FPD 128bit)
 *   rmmod gdmatest
 * 看结果: dmesg | grep gdmatest
 *
 * ⚠ mode>=1 会访问 PL 地址。若该地址没有被任何从机应答, AXI 事务会**永久挂起** ——
 * 本工程 2026-10-01 就因此把板子搞掉线过(要断电重启)。所以 mode 一档一档来。
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/delay.h>
#include <linux/ktime.h>
#include <linux/completion.h>
#include <linux/slab.h>
#include <linux/io.h>
#include <linux/dmaengine.h>
#include <linux/dma-mapping.h>
#include <linux/err.h>

#define GD_TAG "gdmatest: "

static char   *dev   = "fd500000.dma";   /* 目标 dma 设备名(dev_name) */
static int     mode  = 0;                /* 0=DDR自检 1=+BRAM读 2=+BRAM写 3=+真实条带模拟 */
static int     len   = 256 * 1024;       /* 每次搬多少字节 */
static ulong   bram  = 0xB0000000UL;     /* PL 张量 BRAM 物理基址 */
static int     tmo_ms = 3000;            /* 单笔等待超时 */
static int     iters = 826;              /* mode=3: 模拟多少条带(实测整网 strips=826) */
static int     wlen  = 141840;           /* mode=3: 每条带写多少字节(put 7 322 564 字/826) */
static int     rlen  = 103456;           /* mode=3: 每条带读多少字节(get 5 341 184 字/826) */

module_param(dev, charp, 0444);
module_param(mode, int, 0444);
module_param(len, int, 0444);
module_param(bram, ulong, 0444);
module_param(tmo_ms, int, 0444);
module_param(iters, int, 0444);
module_param(wlen, int, 0444);
module_param(rlen, int, 0444);
MODULE_PARM_DESC(dev, "dma device name to use (dev_name), e.g. fd500000.dma");
MODULE_PARM_DESC(mode, "0=DDR->DDR only, 1=+BRAM read, 2=+BRAM write, 3=+realistic strip sim");
MODULE_PARM_DESC(len, "transfer length in bytes");
MODULE_PARM_DESC(bram, "PL BRAM physical base");
MODULE_PARM_DESC(tmo_ms, "per-transfer timeout in ms");
MODULE_PARM_DESC(iters, "mode=3: number of simulated strips");
MODULE_PARM_DESC(wlen, "mode=3: bytes written per strip (DDR->BRAM)");
MODULE_PARM_DESC(rlen, "mode=3: bytes read per strip (BRAM->DDR)");

struct gd_ctx {
    struct completion done;
    int               err;
};

static void gd_cb(void *p)
{
    struct gd_ctx *c = p;

    c->err = 0;
    complete(&c->done);
}

static bool gd_filter(struct dma_chan *chan, void *param)
{
    const char *want = param;

    if (!want || !want[0]) {
        return true;
    }
    return strcmp(dev_name(chan->device->dev), want) == 0;
}

/* 跑一笔 memcpy, 返回 0 成功; 用 ns 回填耗时 */
static int gd_memcpy(struct dma_chan *ch, dma_addr_t dst, dma_addr_t src,
                     size_t n, u64 *ns_out)
{
    struct gd_ctx                 ctx;
    struct dma_async_tx_descriptor *tx;
    struct dma_device             *dma = ch->device;
    dma_cookie_t                   cookie;
    ktime_t                        t0, t1;
    long                           left;

    init_completion(&ctx.done);
    ctx.err = -ETIMEDOUT;

    tx = dma->device_prep_dma_memcpy(ch, dst, src, n, DMA_CTRL_ACK);
    if (!tx) {
        pr_err(GD_TAG "device_prep_dma_memcpy failed (len=%zu)\n", n);
        return -EINVAL;
    }
    tx->callback       = gd_cb;
    tx->callback_param = &ctx;

    t0     = ktime_get();
    cookie = dmaengine_submit(tx);
    if (dma_submit_error(cookie)) {
        pr_err(GD_TAG "dmaengine_submit failed\n");
        return -EIO;
    }
    dma_async_issue_pending(ch);

    left = wait_for_completion_timeout(&ctx.done, msecs_to_jiffies(tmo_ms));
    t1   = ktime_get();
    if (ns_out) {
        *ns_out = (u64)ktime_to_ns(ktime_sub(t1, t0));
    }
    if (left <= 0) {
        pr_err(GD_TAG "!! TIMEOUT after %d ms (src=%pad dst=%pad len=%zu)\n",
               tmo_ms, &src, &dst, n);
        /* 不去 terminate(引擎可能已挂), 让调用者直接放弃后续测试 */
        return -ETIMEDOUT;
    }
    return 0;
}

/* 内核里不能有浮点(-mgeneral-regs-only), MB/s 用整数算到两位小数 */
static void gd_report(const char *tag, int rc, u64 ns, size_t n)
{
    u64 bps, mb_x100;

    if (rc != 0) {
        pr_info(GD_TAG "%-26s FAIL rc=%d\n", tag, rc);
        return;
    }
    if (ns == 0) {
        ns = 1;
    }
    bps     = (u64)n * 1000000000ULL / ns;   /* bytes/s */
    mb_x100 = bps / 10000ULL;                /* MB/s x 100 */
    pr_info(GD_TAG "%-26s ok  %6llu us  %llu.%02llu MB/s  (%zu B)\n", tag,
            (unsigned long long)(ns / 1000),
            (unsigned long long)(mb_x100 / 100ULL),
            (unsigned long long)(mb_x100 % 100ULL), n);
}

static struct dma_chan *g_ch;

static int __init gd_init(void)
{
    dma_cap_mask_t  mask;
    struct dma_device *dma;
    void           *buf_a = NULL, *buf_b = NULL;
    dma_addr_t      pa, pb;
    u64             ns;
    int             rc;
    size_t          n;
    int             i, bad;
    size_t          sz;

    if (len < 64 || (len & 63)) {
        pr_err(GD_TAG "len must be >=64 and 64-byte aligned\n");
        return -EINVAL;
    }
    n  = (size_t)len;
    sz = n;
    if (mode >= 3) {
        /* mode 3 要模拟真实条带: 缓冲区至少放得下最大的一笔 */
        if ((size_t)wlen > sz) {
            sz = (size_t)wlen;
        }
        if ((size_t)rlen > sz) {
            sz = (size_t)rlen;
        }
    }

    dma_cap_zero(mask);
    dma_cap_set(DMA_MEMCPY, mask);
    g_ch = dma_request_channel(mask, gd_filter, (void *)dev);
    if (!g_ch) {
        pr_err(GD_TAG "dma_request_channel(%s) failed\n", dev);
        return -ENODEV;
    }
    dma = g_ch->device;
    pr_info(GD_TAG "channel ok: %s  copy_align=%d  dirs=0x%lx\n",
            dev_name(dma->dev), (int)dma->copy_align,
            (unsigned long)dma->directions);
    pr_info(GD_TAG "params: mode=%d len=%zu bram=0x%lx tmo=%dms\n",
            mode, n, bram, tmo_ms);

    buf_a = dma_alloc_coherent(dma->dev, sz, &pa, GFP_KERNEL);
    buf_b = dma_alloc_coherent(dma->dev, sz, &pb, GFP_KERNEL);
    if (!buf_a || !buf_b) {
        pr_err(GD_TAG "dma_alloc_coherent failed (%zu x2)\n", sz);
        rc = -ENOMEM;
        goto out_chan;
    }
    pr_info(GD_TAG "coherent bufs: A=0x%llx B=0x%llx  %zu B each\n",
            (unsigned long long)pa, (unsigned long long)pb, sz);

    /* ---------------- [1] DDR -> DDR 自检(零风险) ---------------- */
    for (i = 0; i < (int)n; i++) {
        ((u8 *)buf_a)[i] = (u8)(i * 7 + 3);
    }
    memset(buf_b, 0, n);
    rc = gd_memcpy(g_ch, pb, pa, n, &ns);
    gd_report("[1] DDR -> DDR", rc, ns, n);
    if (rc == 0) {
        bad = 0;
        for (i = 0; i < (int)n; i++) {
            if (((u8 *)buf_b)[i] != (u8)(i * 7 + 3)) {
                bad++;
            }
        }
        pr_info(GD_TAG "    DDR->DDR verify: mismatch=%d/%zu\n", bad, n);
    }
    if (rc != 0) {
        goto out_bufs;                      /* 自检不过就别碰 PL */
    }

    if (mode >= 1) {
        /* ---------------- [2] BRAM -> DDR 读(有风险) ---------------- */
        phys_addr_t bphys = (phys_addr_t)bram;
        dma_addr_t  bdma;

        pr_info(GD_TAG "[2] mapping BRAM 0x%llx len=%zu for DMA\n",
                (unsigned long long)bphys, n);
        bdma = dma_map_resource(dma->dev, bphys, n, DMA_TO_DEVICE, 0);
        if (dma_mapping_error(dma->dev, bdma)) {
            pr_warn(GD_TAG "    dma_map_resource failed(%lld), fall back to phys\n",
                    (long long)bdma);
            bdma = (dma_addr_t)bphys;
        } else {
            pr_info(GD_TAG "    bram dma addr = 0x%llx\n",
                    (unsigned long long)bdma);
        }
        memset(buf_b, 0, n);
        rc = gd_memcpy(g_ch, pb, bdma, n, &ns);
        gd_report("[2] BRAM -> DDR (read)", rc, ns, n);
        if (rc != 0) {
            pr_err(GD_TAG "!! BRAM 读失败/超时 —— 不再做后续写测试(避免风险)\n");
            goto out_bufs;
        }
        /* 前 8 个字认一眼, 并和后续写回校验配合 */
        pr_info(GD_TAG "    head: %08x %08x %08x %08x\n",
                ((u32 *)buf_b)[0], ((u32 *)buf_b)[1],
                ((u32 *)buf_b)[2], ((u32 *)buf_b)[3]);

        /* CPU 侧读同一片 BRAM, 和 DMA 读的结果对账(证明 DMA 拿到的是真数据) */
        {
            void __iomem *va = ioremap(bphys, n);

            if (va) {
                bad = 0;
                for (i = 0; i < (int)n / 4; i++) {
                    if (readl(va + i * 4) != ((u32 *)buf_b)[i]) {
                        bad++;
                        if (bad < 4) {
                            pr_info(GD_TAG "    diff @%d: cpu=%08x dma=%08x\n",
                                    i, readl(va + i * 4), ((u32 *)buf_b)[i]);
                        }
                    }
                }
                pr_info(GD_TAG "    DMA vs CPU 逐字对账: mismatch=%d/%zu\n",
                        bad, n / 4);
                iounmap(va);
            } else {
                pr_warn(GD_TAG "    ioremap failed, skip CPU cross-check\n");
            }
        }
    }

    if (mode >= 2) {
        /* ---------------- [3] DDR -> BRAM 写 + 回读校验 ---------------- */
        phys_addr_t bphys = (phys_addr_t)bram;
        dma_addr_t  bdma;
        void __iomem *va;

        for (i = 0; i < (int)n; i++) {
            ((u8 *)buf_a)[i] = (u8)(0xA5 ^ (i & 0xFF));
        }
        bdma = dma_map_resource(dma->dev, bphys, n, DMA_FROM_DEVICE, 0);
        if (dma_mapping_error(dma->dev, bdma)) {
            bdma = (dma_addr_t)bphys;
        }
        rc = gd_memcpy(g_ch, bdma, pa, n, &ns);
        gd_report("[3] DDR -> BRAM (write)", rc, ns, n);
        if (rc == 0) {
            /* 回读: 先 DMA 读回来比, 再 CPU 读比 */
            memset(buf_b, 0, n);
            rc = gd_memcpy(g_ch, pb, bdma, n, &ns);
            gd_report("    BRAM -> DDR (re-read)", rc, ns, n);
            if (rc == 0) {
                bad = 0;
                for (i = 0; i < (int)n; i++) {
                    if (((u8 *)buf_b)[i] != (u8)(0xA5 ^ (i & 0xFF))) {
                        bad++;
                    }
                }
                pr_info(GD_TAG "    DMA 回读校验: mismatch=%d/%zu\n", bad, n);
            }
            va = ioremap(bphys, n);
            if (va) {
                bad = 0;
                for (i = 0; i < (int)n / 4; i++) {
                    if (readl(va + i * 4) != ((u32 *)buf_a)[i]) {
                        bad++;
                    }
                }
                pr_info(GD_TAG "    CPU 回读校验: mismatch=%d/%zu\n", bad, n / 4);
                iounmap(va);
            }
        }
    }

    if (mode >= 3) {
        /* ---------------- [4] 真实条带工作量模拟 ----------------
         * 现在工程是"每条带 memcpy 一次输入 + 每条带 memcpy 一次输出", 而实际代码
         * 是**逐行**调的(每帧约 2.5 万笔!) —— 这里顺便量一下"一笔一笔下"的开销,
         * 看小传输的固定成本值不值得把逐行合并成整条带。 */
        phys_addr_t bphys = (phys_addr_t)bram;
        dma_addr_t  bdma_rd, bdma_wr;
        static const int sweep[] = {4096, 8192, 16384, 32768, 65536, 131072, 262144};
        u64 t0, t1, tot;

        /* 映射整片 256KB 窗口(扫描会用到最大的那笔) */
        bdma_rd = dma_map_resource(dma->dev, bphys, 262144, DMA_TO_DEVICE, 0);
        bdma_wr = dma_map_resource(dma->dev, bphys, 262144, DMA_FROM_DEVICE, 0);
        if (dma_mapping_error(dma->dev, bdma_rd) ||
            dma_mapping_error(dma->dev, bdma_wr)) {
            pr_warn(GD_TAG "    dma_map_resource failed, fall back to phys\n");
            bdma_rd = (dma_addr_t)bphys;
            bdma_wr = (dma_addr_t)bphys;
        }

        pr_info(GD_TAG "[4a] BRAM 读的尺寸扫描(看每笔的固定开销)\n");
        for (i = 0; i < (int)ARRAY_SIZE(sweep); i++) {
            char tag[48];

            snprintf(tag, sizeof(tag), "    read %6d B", sweep[i]);
            rc = gd_memcpy(g_ch, pb, bdma_rd, (size_t)sweep[i], &ns);
            gd_report(tag, rc, ns, (size_t)sweep[i]);
            if (rc != 0) {
                goto out_bufs;
            }
        }

        pr_info(GD_TAG "[4b] 模拟整网: %d 条带 x (写 %d B + 读 %d B)\n",
                iters, wlen, rlen);
        for (i = 0; i < (int)sz; i++) {
            ((u8 *)buf_a)[i] = (u8)(i * 11 + 5);
        }
        t0 = ktime_get();
        for (i = 0; i < iters; i++) {
            rc = gd_memcpy(g_ch, bdma_wr, pa, (size_t)wlen, NULL);
            if (rc != 0) {
                pr_err(GD_TAG "    write @iter %d failed\n", i);
                break;
            }
            rc = gd_memcpy(g_ch, pb, bdma_rd, (size_t)rlen, NULL);
            if (rc != 0) {
                pr_err(GD_TAG "    read @iter %d failed\n", i);
                break;
            }
        }
        t1  = ktime_get();
        tot = (u64)ktime_to_ns(ktime_sub(t1, t0));
        if (rc == 0) {
            size_t bytes = (size_t)iters * ((size_t)wlen + (size_t)rlen);
            /* 现在 CPU 的单价: 写 1.05GB/s(≈0.95ns/B)、读 81MB/s(≈12.3ns/B) */
            u64    cpu_ns = (u64)iters *
                            ((u64)wlen * 95ULL / 100ULL +
                             (u64)rlen * 123ULL / 10ULL);

            pr_info(GD_TAG "    合计 %llu us, 搬了 %zu MB\n",
                    (unsigned long long)(tot / 1000), bytes / (1024 * 1024));
            gd_report("    strip-sim 有效带宽", 0, tot, bytes);
            pr_info(GD_TAG "    同样这些量, 现在 CPU 要 %llu ms —— 只这一项就能省 %lld ms\n",
                    (unsigned long long)(cpu_ns / 1000000ULL),
                    (long long)((cpu_ns - tot) / 1000000ULL));
        }
    }

out_bufs:
    if (buf_a) {
        dma_free_coherent(dma->dev, sz, buf_a, pa);
    }
    if (buf_b) {
        dma_free_coherent(dma->dev, sz, buf_b, pb);
    }
out_chan:
    /* 成功后也把通道放掉: 本模块只做一次性探测, 不常住 */
    if (g_ch) {
        dma_release_channel(g_ch);
        g_ch = NULL;
    }
    pr_info(GD_TAG "GDMA_DONE mode=%d\n", mode);
    return -ENODEV;      /* 故意不常驻: 探测完就退出, 免得占着通道 */
}

static void __exit gd_exit(void)
{
}

module_init(gd_init);
module_exit(gd_exit);

MODULE_LICENSE("GPL v2");
MODULE_DESCRIPTION("ZynqMP GDMA probe: DDR self-test + PL BRAM read/write bandwidth");
