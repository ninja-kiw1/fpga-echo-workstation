/*
 * plbram.c -- 把一个 PL 物理窗口(张量 BRAM)以 **Normal-NC(写合并)** 属性映射给用户态。
 *
 * 为什么需要它
 * ------------
 * `/dev/mem` 映射出来的是 **Device** 属性(强序、不可缓存)。本板实测(2026-10-02):
 *
 *     PL 张量 BRAM 0xB0    197.7 ns/次
 *     PS 自己的 DDR 0x40   102.9 ns/次   <-- 同样走 /dev/mem, 一样慢
 *     普通可缓存内存          4.2 ns/次
 *
 * 也就是说 198 ns 里约 103 ns 纯粹是 Device 映射语义造成的。换成 **Normal-NC**
 * 之后, CPU 可以有多笔未完成的访问、允许合并 —— 读会快得多, 而且因为**不缓存**,
 * 不需要任何 cache 维护(PL 与 CPU 之间不存在一致性问题)。
 *
 * 用法
 * ----
 *     insmod plbram.ko                 # 默认 base=0xB0000000 size=0x40000 (256 KB)
 *     insmod plbram.ko base=0xB0000000 size=0x40000
 *     # 用户态:
 *     fd = open("/dev/plbram", O_RDWR|O_SYNC);
 *     p  = mmap(NULL, 0x40000, PROT_READ|PROT_WRITE, MAP_SHARED, fd, 0);
 *
 * 安全
 * ----
 * 只做 mmap, 不碰硬件; 卸载即恢复。映射范围由 base/size 模块参数决定。
 */
#include <linux/module.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/io.h>
#include <linux/uaccess.h>

static unsigned long base = 0xB0000000UL;
static unsigned long size = 0x40000UL;      /* 256 KB = 16384 x 16 B */

module_param(base, ulong, 0444);
module_param(size, ulong, 0444);
MODULE_PARM_DESC(base, "PL window physical base address (default 0xB0000000)");
MODULE_PARM_DESC(size, "PL window size in bytes (default 0x40000 = 256 KB)");

static int plbram_mmap(struct file *filp, struct vm_area_struct *vma)
{
    unsigned long len = vma->vm_end - vma->vm_start;

    if (vma->vm_pgoff != 0) {
        pr_err("plbram: only offset 0 is supported\n");
        return -EINVAL;
    }
    if (len > size) {
        pr_err("plbram: mmap %lu > window %lu\n", len, size);
        return -EINVAL;
    }

    /* Normal-NC / write-combining: 不缓存 -> 不需要 cache 维护;
     * 但允许 CPU 有多笔未完成访问 —— 这正是我们要的。 */
    vma->vm_page_prot = pgprot_writecombine(vma->vm_page_prot);

    if (remap_pfn_range(vma, vma->vm_start, base >> PAGE_SHIFT, len,
                        vma->vm_page_prot)) {
        pr_err("plbram: remap_pfn_range failed\n");
        return -EAGAIN;
    }
    return 0;
}

static const struct file_operations plbram_fops = {
    .owner  = THIS_MODULE,
    .mmap   = plbram_mmap,
    .llseek = no_llseek,
};

static struct miscdevice plbram_dev = {
    .minor = MISC_DYNAMIC_MINOR,
    .name  = "plbram",
    .fops  = &plbram_fops,
    .mode  = 0666,
};

static int __init plbram_init(void)
{
    int rc;

    if (size == 0 || size > (1UL << 20)) {
        pr_err("plbram: bad size %lu\n", size);
        return -EINVAL;
    }
    rc = misc_register(&plbram_dev);
    if (rc) {
        pr_err("plbram: misc_register failed: %d\n", rc);
        return rc;
    }
    pr_info("plbram: /dev/plbram -> phys 0x%lx..0x%lx (%lu bytes, Normal-NC)\n",
            base, base + size - 1, size);
    return 0;
}

static void __exit plbram_exit(void)
{
    misc_deregister(&plbram_dev);      /* 4.14 里叫 misc_deregister, 没有 misc_unregister */
    pr_info("plbram: unloaded\n");
}

module_init(plbram_init);
module_exit(plbram_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("tcp_image_rx");
MODULE_DESCRIPTION("Map a PL window (tensor BRAM) to userspace as Normal-NC");
MODULE_VERSION("1.0");
