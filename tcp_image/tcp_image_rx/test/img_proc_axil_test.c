/*
 * img_proc_axil_test.c —— 板上原生编译的 AXI4-Lite 图像处理端到端测试工具。
 *
 * 为什么用 C 而不是 Python:
 *   一帧 262144 个像素, 每个像素要一次寄存器往返。板上纯 Python 用 mmap 逐字读
 *   太慢, 实测会把板子 CPU 打满甚至拖死网络。用 C 直接写寄存器, 每像素几百 ns。
 *
 * 2026-10-01 改成**一次搬 4 个像素**(和现在的 RTL 一致):
 *   0x08 W  IN_PIX   一个 32bit 字 = 4 个灰度(byte0..byte3)
 *   0x0C R  OUT_PIX  一个 32bit 字 = 4 个处理结果(各取低 8bit)
 *   灰度在 PS 侧按 (77R+150G+29B)>>8 算(和 PL 一致)。
 *   加速: 每 64 个字才复核一次 STATUS(AXI 写/读本来就背压住 CPU, 盲访存是安全的)。
 *
 * 寄存器(基址默认 0xA0000000, 见 img_proc_axil.v):
 *   0x00 W  CTRL     [2:0]模式 [15:8]阈值
 *   0x04 R  STATUS   [0]in_ready [1]out_full [2]proc_busy [3]frame_done
 *                    [15:8]帧min [23:16]帧max
 *   0x08 W  IN_PIX   4 个灰度/字
 *   0x0C R  OUT_PIX  4 个结果/字
 *   0x10 R  VERSION  0x494D5002
 *
 * 用法:
 *   img_proc_axil_test [base_hex] [mode]
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <time.h>

#define REG_CTRL   0x00u
#define REG_STATUS 0x04u
#define REG_IN     0x08u
#define REG_OUT    0x0Cu
#define REG_VER    0x10u

#define IMG_W 512u
#define IMG_H 512u
#define NPIX  (IMG_W * IMG_H)
#define NWORD (NPIX / 4u)
#define POLL_STRIDE 64u

#define ST_IN_READY   (1u << 0)
#define ST_OUT_FULL   (1u << 1)

static uint64_t now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)ts.tv_nsec / 1000u;
}

/* 输入 R=G=B=gy, gy = (255*y)/511 -> 灰度 = gy */
static uint32_t ramp_gray(uint32_t idx)
{
    uint32_t y = idx / IMG_W;
    return (255u * y) / (IMG_H - 1u);
}

int main(int argc, char **argv)
{
    uint64_t base = 0xA0000000ULL;
    int      mode = 0;
    int      fd;
    volatile uint32_t *r;
    uint64_t t0, t1;
    uint32_t w, i, st, ver;
    uint8_t *out;
    int      bad = 0, first_bad = -1;

    if (argc > 1) { base = strtoull(argv[1], NULL, 0); }
    if (argc > 2) { mode = atoi(argv[2]); }

    fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (fd < 0) { perror("open /dev/mem"); return 1; }

    r = (volatile uint32_t *)mmap(NULL, 0x1000, PROT_READ | PROT_WRITE,
                                  MAP_SHARED, fd, (off_t)base);
    if (r == MAP_FAILED) { perror("mmap"); close(fd); return 1; }

    ver = r[REG_VER / 4];
    printf("base=0x%llX  VERSION=0x%08X (expect 0x494D5003)\n",
           (unsigned long long)base, ver);
    if (ver != 0x494D5003u) {
        printf("!! VERSION mismatch -- 很可能这个地址不是我们的 IP, 或 PL 是旧版\n");
    }

    out = (uint8_t *)malloc(NPIX);
    if (out == NULL) { printf("malloc failed\n"); return 1; }

    r[REG_CTRL / 4] = (uint32_t)(mode & 7) | (128u << 8);

    /* ---- 推一帧: 每字 4 个灰度 ---- */
    t0 = now_us();
    for (w = 0; w < NWORD; w++) {
        uint32_t i0 = w * 4u;
        uint32_t word = ramp_gray(i0) | (ramp_gray(i0 + 1) << 8)
                      | (ramp_gray(i0 + 2) << 16) | (ramp_gray(i0 + 3) << 24);
        if ((w & (POLL_STRIDE - 1u)) == 0u) {
            while ((r[REG_STATUS / 4] & ST_IN_READY) == 0u) { /* spin */ }
        }
        r[REG_IN / 4] = word;
    }
    t1 = now_us();
    printf("push %u words (%u px) : %llu us (%.0f ns/word)\n", NWORD, NPIX,
           (unsigned long long)(t1 - t0), (double)(t1 - t0) * 1000.0 / (double)NWORD);

    /* ---- 拉一帧: 每字 4 个结果 ---- */
    t0 = now_us();
    while ((r[REG_STATUS / 4] & ST_OUT_FULL) == 0u) { /* spin */ }
    for (w = 0; w < NWORD; w++) {
        uint32_t v;
        if (w != 0u && (w & (POLL_STRIDE - 1u)) == 0u) {
            while ((r[REG_STATUS / 4] & ST_OUT_FULL) == 0u) { /* spin */ }
        }
        v = r[REG_OUT / 4];
        out[(size_t)w * 4u + 0] = (uint8_t)(v & 0xFFu);
        out[(size_t)w * 4u + 1] = (uint8_t)((v >> 8) & 0xFFu);
        out[(size_t)w * 4u + 2] = (uint8_t)((v >> 16) & 0xFFu);
        out[(size_t)w * 4u + 3] = (uint8_t)((v >> 24) & 0xFFu);
    }
    t1 = now_us();
    printf("pull %u words (%u px) : %llu us (%.0f ns/word)\n", NWORD, NPIX,
           (unsigned long long)(t1 - t0), (double)(t1 - t0) * 1000.0 / (double)NWORD);

    st = r[REG_STATUS / 4];
    printf("STATUS=0x%08X  min=%u max=%u\n", st, (st >> 8) & 0xFFu, (st >> 16) & 0xFFu);

    if (mode == 0) {
        for (i = 0; i < NPIX; i++) {
            uint32_t ex = ramp_gray(i);
            if (out[i] != ex) {
                bad++;
                if (first_bad < 0) { first_bad = (int)i; }
            }
        }
        printf("mode0 check: mismatch = %d / %u\n", bad, NPIX);
        if (first_bad >= 0) {
            printf("  first mismatch px=%d (row=%u) got=%u expect=%u\n",
                   first_bad, (uint32_t)first_bad / IMG_W, out[first_bad], ramp_gray((uint32_t)first_bad));
        }
        for (i = 0; i < IMG_H; i += 128u) {
            printf("  out row %3u : gray=%u\n", i, out[i * IMG_W]);
        }
    } else {
        printf("mode %d: 只做通路验证, 未做数值比对\n", mode);
        for (i = 0; i < IMG_H; i += 128u) {
            printf("  out row %3u : gray=%u\n", i, out[i * IMG_W]);
        }
    }

    free(out);
    munmap((void *)r, 0x1000);
    close(fd);
    printf("DONE\n");
    return 0;
}
