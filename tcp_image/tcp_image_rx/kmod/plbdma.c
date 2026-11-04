/* plbdma.c -- 用 PS 自带的 GDMA 在 PL BRAM 与 PS 内存之间搬数据
 *
 * 背景(2026-10-06 实测, 见 docs/ai_latency_breakdown_and_optimization.md):
 *   CPU 读 PL BRAM 被钉死在 **每次 16B 访问 ~198ns(81MB/s)**、不流水、与指令宽度/映射属性无关;
 *   而 PS 自带的 FPD GDMA (`fd500000.dma`) **能到达 PL 的 0xB0000000**,
 *   实测 BRAM->DDR **1290 MB/s (15.9x)**、DDR->BRAM 1346 MB/s。
 *
 * 本模块给用户态一个最简接口:
 *   * 模块自己分配一块**物理连续**的 bounce 缓冲(默认 256KB, 可调 order),
 *     并 **mmap 给用户态**(可缓存 -> CPU 用它做 memcpy 很快: ~3GB/s);
 *   * ioctl 只做"BRAM <-> bounce"这一段 DMA, 用户态负责 bounce <-> 自己缓冲的 memcpy。
 *
 * 为什么走 bounce 而不是直接把 DMA 打到用户 tensor 上:
 *   tensor 是进程 malloc 的, 物理**不连续** -> 要么拆成几十笔(每笔 12us 固定开销, 亏死),
 *   要么搞 get_user_pages + sg 映射(复杂, 且 cache 维护容易出错)。
 *   隔一层 bounce 之后: DMA 永远是一笔连续传输, cache 维护由 dma_map_single/unmap_single
 *   包办, 代价只是多一次 ~3GB/s 的内存 memcpy(整网 85MB -> ~30ms, 相对省下的 ~950ms 可忽略)。
 *
 * 用法(用户态):
 *   fd = open("/dev/plbdma", O_RDWR);
 *   p  = mmap(NULL, size, PROT_READ|PROT_WRITE, MAP_SHARED, fd, 0);   // size 用 INFO 问
 *   ioctl(fd, PLBDMA_IOC_XFER, &req);   // req = {bram_off, len, dir}
 *   // dir=0: BRAM -> bounce, 然后从 p 里 memcpy 走
 *   // dir=1: 先 memcpy 进 p, 再 dir=1 把它推到 BRAM
 *
 * 加载: insmod plbdma.ko            (默认 bram=0xB0000000, 自动挑最大能拿到的 order)
 *       insmod plbdma.ko dev=ffa80000.dma   (换通道, 默认 FPD GDMA)
 * 卸载: 有进程开着 /dev/plbdma 时会拒绝 rmmod。
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/gfp.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/mutex.h>
#include <linux/completion.h>
#include <linux/ktime.h>
#include <linux/io.h>
#include <linux/dmaengine.h>
#include <linux/dma-mapping.h>
#include <linux/ioctl.h>
#include <linux/err.h>
#include <linux/atomic.h>

#define PD_TAG "plbdma: "

#define PLBDMA_MAGIC 'D'

struct plbdma_req {
	__u32 bram_off;		/* 相对 BRAM 窗口基址的字节偏移(64 对齐) */
	__u32 len;		/* 字节数, <= bounce_size 且 64 对齐 */
	__u32 dir;		/* 0 = BRAM -> bounce(读回), 1 = bounce -> BRAM(写回) */
	__u32 pad;
};

struct plbdma_info {
	__u32 bounce_size;
	__u32 dma_ok;
	__u32 xfers;
	__u32 errs;
};

#define PLBDMA_IOC_INFO	_IOR(PLBDMA_MAGIC, 0x01, struct plbdma_info)
#define PLBDMA_IOC_XFER	_IOW(PLBDMA_MAGIC, 0x02, struct plbdma_req)

static char  *dma_dev_name = "fd500000.dma";
static ulong  bram     = 0xB0000000UL;
static int    order    = -1;		/* -1 = 自动从大到小试 */
static int    tmo_ms   = 2000;
static uint   window   = 0x40000u;	/* BRAM 窗口大小(默认 256KB) */

module_param(dma_dev_name, charp, 0444);
module_param(bram, ulong, 0444);
module_param(order, int, 0444);
module_param(tmo_ms, int, 0444);
module_param(window, uint, 0444);
MODULE_PARM_DESC(dma_dev_name, "dma device name (dev_name), default fd500000.dma (FPD GDMA 128bit)");
MODULE_PARM_DESC(bram, "PL BRAM physical base");
MODULE_PARM_DESC(order, "bounce buffer order (-1 = auto, try 9..6)");
MODULE_PARM_DESC(tmo_ms, "per-transfer timeout in ms");
MODULE_PARM_DESC(window, "PL BRAM window size in bytes (default 256KB)");

/* ---------------- 状态 ---------------- */
static struct dma_chan *pd_ch;
static struct device    *pd_dma_dev;
static void             *pd_buf;		/* 内核线性地址 */
static unsigned long     pd_pa;
static size_t            pd_size;
static struct page      *pd_page;
static struct mutex      pd_lock;
static atomic_t          pd_nopen;
static atomic_t          pd_xfers;
static atomic_t          pd_errs;
static struct completion pd_done;
static int               pd_rc;

static void pd_cb(void *p)
{
	pd_rc = 0;
	complete(&pd_done);
}

static bool pd_filter(struct dma_chan *chan, void *param)
{
	const char *want = param;

	if (!want || !want[0]) {
		return true;
	}
	return strcmp(dev_name(chan->device->dev), dma_dev_name) == 0;
}

/* 一笔 DMA(BRAM <-> bounce), 调用者持有 pd_lock */
static int pd_xfer(struct plbdma_req *r)
{
	struct dma_async_tx_descriptor *tx;
	struct dma_device *dma = pd_ch->device;
	dma_addr_t   b_dma;
	dma_addr_t   m_dma;
	dma_cookie_t cookie;
	enum dma_data_direction mdir;
	long         left;

	if ((r->len & 63u) || (r->bram_off & 63u)) {
		return -EINVAL;
	}
	if (r->len == 0 || r->len > pd_size) {
		return -EINVAL;
	}
	if ((u64)r->bram_off + (u64)r->len > (u64)window) {
		return -EINVAL;
	}

	/* dir=0: DMA 从 BRAM 读、写进 bounce -> BRAM 侧 TO_DEVICE, 内存侧 FROM_DEVICE */
	mdir = r->dir ? DMA_TO_DEVICE : DMA_FROM_DEVICE;

	b_dma = dma_map_resource(pd_dma_dev, (phys_addr_t)(bram + r->bram_off),
				 (size_t)r->len,
				 r->dir ? DMA_FROM_DEVICE : DMA_TO_DEVICE, 0);
	if (dma_mapping_error(pd_dma_dev, b_dma)) {
		b_dma = (dma_addr_t)(bram + r->bram_off);
	}
	m_dma = dma_map_single(pd_dma_dev, pd_buf, (size_t)r->len, mdir);
	if (dma_mapping_error(pd_dma_dev, m_dma)) {
		return -EIO;
	}

	init_completion(&pd_done);
	pd_rc = -ETIMEDOUT;

	if (r->dir) {
		tx = dma->device_prep_dma_memcpy(pd_ch, b_dma, m_dma, (size_t)r->len,
						 DMA_CTRL_ACK);
	} else {
		tx = dma->device_prep_dma_memcpy(pd_ch, m_dma, b_dma, (size_t)r->len,
						 DMA_CTRL_ACK);
	}
	if (!tx) {
		dma_unmap_single(pd_dma_dev, m_dma, (size_t)r->len, mdir);
		atomic_inc(&pd_errs);
		return -EINVAL;
	}
	tx->callback       = pd_cb;
	tx->callback_param = NULL;

	cookie = dmaengine_submit(tx);
	if (dma_submit_error(cookie)) {
		dma_unmap_single(pd_dma_dev, m_dma, (size_t)r->len, mdir);
		atomic_inc(&pd_errs);
		return -EIO;
	}
	dma_async_issue_pending(pd_ch);

	left = wait_for_completion_timeout(&pd_done, msecs_to_jiffies(tmo_ms));
	if (left <= 0) {
		pr_err(PD_TAG "!! xfer TIMEOUT %dms (off=0x%x len=%u dir=%u)\n",
		       tmo_ms, r->bram_off, r->len, r->dir);
		atomic_inc(&pd_errs);
		return -ETIMEDOUT;
	}

	/* unmap 会把 cache 维护做完: FROM_DEVICE -> invalidate(用户态能看到新数据),
	 * TO_DEVICE -> clean(用户态刚写的脏行落回内存, DMA 才读得到) */
	dma_unmap_single(pd_dma_dev, m_dma, (size_t)r->len, mdir);

	atomic_inc(&pd_xfers);
	return 0;
}

/* ---------------- fops ---------------- */
static int pd_open(struct inode *inode, struct file *f)
{
	atomic_inc(&pd_nopen);
	return 0;
}

static int pd_release(struct inode *inode, struct file *f)
{
	atomic_dec(&pd_nopen);
	return 0;
}

static long pd_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
	struct plbdma_req  req;
	struct plbdma_info info;
	int rc;

	switch (cmd) {
	case PLBDMA_IOC_INFO:
		memset(&info, 0, sizeof(info));
		info.bounce_size = (__u32)pd_size;
		info.dma_ok      = pd_ch ? 1u : 0u;
		info.xfers       = (__u32)atomic_read(&pd_xfers);
		info.errs        = (__u32)atomic_read(&pd_errs);
		if (copy_to_user((void __user *)arg, &info, sizeof(info))) {
			return -EFAULT;
		}
		return 0;

	case PLBDMA_IOC_XFER:
		if (copy_from_user(&req, (void __user *)arg, sizeof(req))) {
			return -EFAULT;
		}
		if (!pd_ch) {
			return -ENODEV;
		}
		mutex_lock(&pd_lock);
		rc = pd_xfer(&req);
		mutex_unlock(&pd_lock);
		return rc;

	default:
		return -EINVAL;
	}
}

static int pd_mmap(struct file *f, struct vm_area_struct *vma)
{
	unsigned long sz = vma->vm_end - vma->vm_start;

	if (!pd_buf) {
		return -ENODEV;
	}
	if (sz > pd_size) {
		return -EINVAL;
	}
	/* 保持 vma->vm_page_prot 默认(可缓存 Normal-WB): CPU 在 bounce 上做 memcpy 要快。
	 * 与 DMA 的一致性由 dma_map_single/unmap_single 的 cache 维护负责。 */
	return remap_pfn_range(vma, vma->vm_start, pd_pa >> PAGE_SHIFT, sz,
			       vma->vm_page_prot);
}

static const struct file_operations pd_fops = {
	.owner          = THIS_MODULE,
	.open           = pd_open,
	.release        = pd_release,
	.unlocked_ioctl = pd_ioctl,
	.mmap           = pd_mmap,
	.llseek         = no_llseek,
};

static struct miscdevice pd_misc = {
	.minor = MISC_DYNAMIC_MINOR,
	.name  = "plbdma",
	.fops  = &pd_fops,
	.mode  = 0666,
};

static int __init pd_init(void)
{
	dma_cap_mask_t mask;
	int ord, rc;

	mutex_init(&pd_lock);
	init_completion(&pd_done);
	atomic_set(&pd_nopen, 0);

	/* ---- [1] 通道 ---- */
	dma_cap_zero(mask);
	dma_cap_set(DMA_MEMCPY, mask);
	pd_ch = dma_request_channel(mask, pd_filter, (void *)dev_name);
	if (!pd_ch) {
		pr_err(PD_TAG "dma_request_channel(%s) failed\n", dma_dev_name);
		return -ENODEV;
	}
	pd_dma_dev = pd_ch->device->dev;
	pr_info(PD_TAG "dma channel: %s\n", dma_dev_name);

	/* ---- [2] 物理连续的 bounce 缓冲 ---- */
	if (order >= 0) {
		ord = order;
		pd_page = alloc_pages(GFP_KERNEL | __GFP_ZERO, ord);
	} else {
		for (ord = 9; ord >= 6; ord--) {
			pd_page = alloc_pages(GFP_KERNEL | __GFP_ZERO, ord);
			if (pd_page) {
				break;
			}
		}
		if (ord < 6) {
			pd_page = NULL;
		}
	}
	if (!pd_page) {
		pr_err(PD_TAG "alloc_pages failed\n");
		rc = -ENOMEM;
		goto out_chan;
	}
	pd_size = (size_t)PAGE_SIZE << ord;
	pd_buf  = page_address(pd_page);
	pd_pa   = virt_to_phys(pd_buf);
	pr_info(PD_TAG "bounce buffer: %zu KB (order %d) phys 0x%lx\n",
		pd_size / 1024, ord, pd_pa);

	/* ---- [3] 起设备 ---- */
	rc = misc_register(&pd_misc);
	if (rc) {
		pr_err(PD_TAG "misc_register failed rc=%d\n", rc);
		goto out_pages;
	}
	pr_info(PD_TAG "ready: bram=0x%lx window 256KB, /dev/plbdma\n", bram);
	return 0;

out_pages:
	__free_pages(pd_page, ord);
	pd_page = NULL;
out_chan:
	dma_release_channel(pd_ch);
	pd_ch = NULL;
	return rc;
}

static void __exit pd_exit(void)
{
	int ord = get_order(pd_size);

	if (atomic_read(&pd_nopen) > 0) {
		pr_warn(PD_TAG "still open, refusing to unload\n");
		return;
	}
	misc_deregister(&pd_misc);
	if (pd_page) {
		__free_pages(pd_page, ord);
	}
	if (pd_ch) {
		dma_release_channel(pd_ch);
	}
	pr_info(PD_TAG "unloaded (xfers=%d errs=%d)\n",
		atomic_read(&pd_xfers), atomic_read(&pd_errs));
}

module_init(pd_init);
module_exit(pd_exit);

MODULE_LICENSE("GPL v2");
MODULE_DESCRIPTION("ZynqMP GDMA bounce-buffer service for PL BRAM (/dev/plbdma)");
