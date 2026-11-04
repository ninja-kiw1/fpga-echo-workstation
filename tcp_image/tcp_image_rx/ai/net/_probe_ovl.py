# -*- coding: utf-8 -*-
"""重叠可行性预实验：CPU 写 BRAM 与 DMA 读 BRAM 能不能真正并发？

背景：想做的重叠是"组装下一段输入(CPU 写 BRAM) 与 DMA 读回上一段输出(PS 侧 DMA) 并行"。
但两者都走 PS->PL 那条通路。如果瓶颈在这条通路的带宽上，重叠就等于白做；如果
CPU 那侧另有瓶颈(store issue / 写合并缓冲)，重叠就能真省时间。

用**现有模块**就能测(/dev/plbdma 的同步 XFER + /dev/plbram 的 CPU 写):
  1) CPU 单独写 N 字节    ->  t_cpu
  2) DMA 单独读 M 字节    ->  t_dma
  3) 两个线程同时干       ->  t_both
判据: t_both ~ max(t_cpu,t_dma) => 能重叠;  t_both ~ t_cpu+t_dma => 抢同一资源, 别做。

数据量按真实比例: 写 117MB / 读 85MB (整网 put/get)。
CPU 写 128KB..256KB 区(模拟输入区), DMA 读 0..128KB 区(模拟输出区) —— 地址不冲突。
"""
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
RDIR = "/root/tcp_image_rx/ai_c"

SRC = r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define BRAM_BASE   0xB0000000UL
#define WIN         0x40000u          /* 256KB 窗口 */
#define IN_OFF      0x20000u          /* CPU 写区: 128K..256K */
#define DMA_OFF     0x00000u          /* DMA 读区: 0..128K   */
#define CHUNK_CPU   (8u * 1024u)      /* CPU 按 8KB 一段写(模拟逐行) */
#define CHUNK_DMA   (128u * 1024u)    /* DMA 按 128KB 一笔 */

struct plbdma_req { uint32_t bram_off, len, dir, pad; };
#define PLBDMA_IOC_INFO _IOR('D', 0x01, struct plbdma_info)
struct plbdma_info { uint32_t bounce_size, dma_ok, xfers, errs; };
#define PLBDMA_IOC_XFER _IOW('D', 0x02, struct plbdma_req)

#define PUT_BYTES (117u * 1024u * 1024u)
#define GET_BYTES (85u * 1024u * 1024u)

static uint64_t now_us(void)
{
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000ull + ts.tv_nsec / 1000ull;
}

static uint8_t *g_nc;                 /* /dev/plbram 映射(Normal-NC) */
static uint8_t *g_src;                /* 普通缓存内存, 数据源 */
static int      g_dfd = -1;
static volatile int g_stop;
static volatile uint64_t g_cpu_bytes, g_dma_bytes;

static void *cpu_thread(void *arg)
{
    size_t off = 0, n = 0;

    (void)arg;
    while (!g_stop) {
        memcpy(g_nc + IN_OFF + (off % (WIN - IN_OFF - CHUNK_CPU)), g_src, CHUNK_CPU);
        off += CHUNK_CPU;
        n += CHUNK_CPU;
        if (n >= PUT_BYTES) break;
    }
    g_cpu_bytes = n;
    return NULL;
}

static void *dma_thread(void *arg)
{
    struct plbdma_req req;
    size_t n = 0;

    (void)arg;
    req.bram_off = DMA_OFF; req.len = CHUNK_DMA; req.dir = 0; req.pad = 0;
    while (!g_stop) {
        if (ioctl(g_dfd, PLBDMA_IOC_XFER, &req) != 0) { printf("dma ioctl err %s\n", strerror(errno)); break; }
        n += CHUNK_DMA;
        if (n >= GET_BYTES) break;
    }
    g_dma_bytes = n;
    return NULL;
}

int main(void)
{
    int fdc = open("/dev/plbram", O_RDWR | O_SYNC);
    pthread_t th;
    uint64_t t0, t1;
    double rc, rd, rb, rcpu_in_both, rdma_in_both;

    g_dfd = open("/dev/plbdma", O_RDWR);
    if (g_dfd < 0 || fdc < 0) { perror("open"); return 1; }
    g_nc = mmap(NULL, WIN, PROT_READ | PROT_WRITE, MAP_SHARED, fdc, 0);
    if (g_nc == MAP_FAILED) { perror("mmap plbram"); return 1; }
    g_src = malloc(CHUNK_CPU);
    memset(g_src, 0x5a, CHUNK_CPU);

    /* [1] CPU 单独写 */
    g_stop = 0; g_cpu_bytes = 0;
    t0 = now_us();
    cpu_thread(NULL);
    t1 = now_us();
    rc = (double)g_cpu_bytes / ((t1 - t0) / 1e6) / 1e6;
    printf("  [1] CPU 写 %.0f MB 单独 : %8llu us -> %7.1f MB/s\n",
           g_cpu_bytes / 1048576.0, (unsigned long long)(t1 - t0), rc);

    /* [2] DMA 单独读 */
    g_stop = 0; g_dma_bytes = 0;
    t0 = now_us();
    dma_thread(NULL);
    t1 = now_us();
    rd = (double)g_dma_bytes / ((t1 - t0) / 1e6) / 1e6;
    printf("  [2] DMA 读 %.0f MB 单独 : %8llu us -> %7.1f MB/s\n",
           g_dma_bytes / 1048576.0, (unsigned long long)(t1 - t0), rd);

    /* [3] 同时 */
    g_stop = 0; g_cpu_bytes = 0; g_dma_bytes = 0;
    t0 = now_us();
    pthread_create(&th, NULL, dma_thread, NULL);
    cpu_thread(NULL);
    pthread_join(th, NULL);
    t1 = now_us();
    rcpu_in_both = (double)g_cpu_bytes / ((t1 - t0) / 1e6) / 1e6;
    rdma_in_both = (double)g_dma_bytes / ((t1 - t0) / 1e6) / 1e6;
    printf("  [3] 同时干             : %8llu us -> CPU %7.1f MB/s, DMA %7.1f MB/s (合计 %7.1f)\n",
           (unsigned long long)(t1 - t0), rcpu_in_both, rdma_in_both,
           rcpu_in_both + rdma_in_both);

    printf("\n  判据: 同时干的用时 vs (单独之和)\n");
    printf("        (单独之和需要重新按同样的字节数算)\n");
    {
        double t_cpu_alone = (double)PUT_BYTES / rc;      /* us */
        double t_dma_alone = (double)GET_BYTES / rd;      /* us */
        double t_both      = (double)(t1 - t0);           /* us */
        printf("        按真实工作量(写117MB+读85MB): 单独之和 = %.0f + %.0f = %.0f us\n",
               t_cpu_alone, t_dma_alone, t_cpu_alone + t_dma_alone);
        printf("        同时干(同样的量)              = %.0f us\n", t_both);
        printf("        => 重叠收益 %.0f us (%.1f%%)\n",
               t_cpu_alone + t_dma_alone - t_both,
               100.0 * (t_cpu_alone + t_dma_alone - t_both) / (t_cpu_alone + t_dma_alone));
    }
    return 0;
}
'''


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=10,
              banner_timeout=10, auth_timeout=10,
              look_for_keys=False, allow_agent=False)
    c.exec_command("mkdir -p %s" % RDIR)[1].read()
    sftp = c.open_sftp()
    with sftp.open(RDIR + "/_probe_ovl.c", "w") as fh:
        fh.write(SRC)
    sftp.close()
    print("uploaded; building...", flush=True)
    _i, o, _e = c.exec_command(
        "cd %s && gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE -pthread "
        "_probe_ovl.c -o _probe_ovl 2>&1 && echo BUILD_OK && "
        "nproc; ./_probe_ovl 2>&1; echo RC=$?" % RDIR, timeout=600)
    print(o.read().decode(errors="replace").rstrip(), flush=True)
    c.close()
    print("\nOVL_PROBE_DONE", flush=True)


if __name__ == "__main__":
    main()
