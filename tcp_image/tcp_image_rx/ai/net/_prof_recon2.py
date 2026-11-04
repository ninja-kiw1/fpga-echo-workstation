# -*- coding: utf-8 -*-
"""AI 识别提速调研 —— 第 3 步: 读天花板卡在"每条指令"还是"每次 AXI 事务"?

第 1 步微基准给出: 64-bit load = ~197 ns/条, LDP(128-bit) = ~198 ns/条,
2/4/8 路交错也一样 —— 像"**每条 load 指令 ~198 ns、与宽度无关、完全不流水**"。
若真是按指令收费, 那么一条 `ld1 {v0..v3}` 指令搬 64B 就只要 ~198 ns (4 倍),
且是**纯软件**就能拿到的收益(只改 bram_get 的拷贝循环)。

板上 gcc 是 7.5, 没有 vld1q_u8_x4 这类 intrinsics(GCC 8 才有), 所以用
**固定寄存器(v0-v3)的内联汇编**, 并把 clobber 写全。
"""
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
RDIR = "/root/tcp_image_rx/ai_c"

SRC = r'''
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <arm_neon.h>

#define BRAM_BASE  0xB0000000UL
#define BRAM_BYTES (256u * 1024u)
#define REPS 8

static uint64_t now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000ull + (uint64_t)ts.tv_nsec / 1000ull;
}
static void rep(const char *tag, double bytes, uint64_t us, uint64_t acc)
{
    printf("  %-32s %8llu us | %7.2f ns/16B | %8.2f MB/s | chk=%llx\n",
           tag, (unsigned long long)us, us * 1000.0 / (bytes / 16.0),
           bytes / (double)us, (unsigned long long)acc);
}

/* ---- 一条指令读 16 / 32 / 64 字节 ---- */
static inline uint8x16_t ld1x1_acc(const void *p, uint8x16_t acc)
{
    __asm__ __volatile__("ld1 {v0.16b}, [%1]\n\t"
                         "eor %0.16b, %0.16b, v0.16b"
                         : "+w"(acc) : "r"(p) : "v0", "memory");
    return acc;
}
static inline uint8x16_t ld1x2_acc(const void *p, uint8x16_t acc)
{
    __asm__ __volatile__("ld1 {v0.16b, v1.16b}, [%1]\n\t"
                         "eor %0.16b, %0.16b, v0.16b\n\t"
                         "eor %0.16b, %0.16b, v1.16b"
                         : "+w"(acc) : "r"(p) : "v0", "v1", "memory");
    return acc;
}
static inline uint8x16_t ld1x4_acc(const void *p, uint8x16_t acc)
{
    __asm__ __volatile__("ld1 {v0.16b, v1.16b, v2.16b, v3.16b}, [%1]\n\t"
                         "eor %0.16b, %0.16b, v0.16b\n\t"
                         "eor %0.16b, %0.16b, v1.16b\n\t"
                         "eor %0.16b, %0.16b, v2.16b\n\t"
                         "eor %0.16b, %0.16b, v3.16b"
                         : "+w"(acc) : "r"(p) : "v0", "v1", "v2", "v3", "memory");
    return acc;
}
static inline uint8x16_t ld1x4x2_acc(const void *p, const void *q, uint8x16_t acc)
{
    __asm__ __volatile__("ld1 {v0.16b, v1.16b, v2.16b, v3.16b}, [%1]\n\t"
                         "ld1 {v4.16b, v5.16b, v6.16b, v7.16b}, [%2]\n\t"
                         "eor %0.16b, %0.16b, v0.16b\n\t"
                         "eor %0.16b, %0.16b, v3.16b\n\t"
                         "eor %0.16b, %0.16b, v4.16b\n\t"
                         "eor %0.16b, %0.16b, v7.16b"
                         : "+w"(acc) : "r"(p), "r"(q)
                         : "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7", "memory");
    return acc;
}
/* 真搬运: 一条 ld1 + 一条 st1 = 64B */
static inline void copy64(void *dst, const void *src)
{
    __asm__ __volatile__("ld1 {v0.16b, v1.16b, v2.16b, v3.16b}, [%0]\n\t"
                         "st1 {v0.16b, v1.16b, v2.16b, v3.16b}, [%1]"
                         :: "r"(src), "r"(dst)
                         : "v0", "v1", "v2", "v3", "memory");
}
static inline void store64(void *dst, uint8x16_t v)
{
    __asm__ __volatile__("mov v0.16b, %0.16b\n\t"
                         "mov v1.16b, %0.16b\n\t"
                         "mov v2.16b, %0.16b\n\t"
                         "mov v3.16b, %0.16b\n\t"
                         "st1 {v0.16b, v1.16b, v2.16b, v3.16b}, [%1]"
                         :: "w"(v), "r"(dst)
                         : "v0", "v1", "v2", "v3", "memory");
}

#define BENCH(tag, ptr, step, fn)                                            \
    do {                                                                     \
        uint8x16_t acc = vdupq_n_u8(0); size_t i; unsigned r;                 \
        uint64_t t0 = now_us();                                              \
        for (r = 0; r < REPS; r++)                                            \
            for (i = 0; i + (step) <= BRAM_BYTES; i += (step))                \
                acc = fn((const char *)(ptr) + i, acc);                       \
        rep(tag, (double)(BRAM_BYTES / (step) * (step)) * REPS,               \
            now_us() - t0, vgetq_lane_u8(acc, 0));                            \
    } while (0)

static void b_128(const char *tag, const uint8_t *p)
{
    uint8x16_t acc = vdupq_n_u8(0); size_t i; unsigned r;
    uint64_t t0 = now_us();
    for (r = 0; r < REPS; r++)
        for (i = 0; i + 128 <= BRAM_BYTES; i += 128)
            acc = ld1x4x2_acc(p + i, p + i + 64, acc);
    rep(tag, (double)(BRAM_BYTES / 128 * 128) * REPS, now_us() - t0, vgetq_lane_u8(acc, 0));
}
static void b_copy64(const char *tag, uint8_t *dst, const uint8_t *src)
{
    size_t i; unsigned r; uint64_t t0 = now_us();
    for (r = 0; r < REPS; r++)
        for (i = 0; i + 64 <= BRAM_BYTES; i += 64) copy64(dst + i, src + i);
    rep(tag, (double)(BRAM_BYTES / 64 * 64) * REPS, now_us() - t0, dst[0]);
}
static void b_memcpy(const char *tag, uint8_t *dst, const uint8_t *src)
{
    unsigned r; uint64_t t0 = now_us();
    for (r = 0; r < REPS; r++) memcpy(dst, src, BRAM_BYTES);
    rep(tag, (double)BRAM_BYTES * REPS, now_us() - t0, dst[0]);
}
static void b_write_plain(const char *tag, uint8_t *p)
{
    volatile uint64_t *q = (volatile uint64_t *)p;
    uint64_t t0 = now_us(); size_t i, n = BRAM_BYTES / 8; unsigned r;
    for (r = 0; r < REPS; r++)
        for (i = 0; i < n; i++) q[i] = 0x0101010101010101ull;
    rep(tag, (double)BRAM_BYTES * REPS, now_us() - t0, 0);
}
static void b_write64(const char *tag, uint8_t *p)
{
    uint8x16_t v = vdupq_n_u8(0x5a);
    uint64_t t0 = now_us(); size_t i; unsigned r;
    for (r = 0; r < REPS; r++)
        for (i = 0; i + 64 <= BRAM_BYTES; i += 64) store64(p + i, v);
    rep(tag, (double)(BRAM_BYTES / 64 * 64) * REPS, now_us() - t0, 0);
}
static void b_read_u64(const char *tag, const uint8_t *p)
{
    volatile const uint64_t *q = (volatile const uint64_t *)p;
    uint64_t t0 = now_us(); size_t i, a = 0; unsigned r;
    for (r = 0; r < REPS; r++)
        for (i = 0; i < BRAM_BYTES / 8; i++) a ^= q[i];
    rep(tag, (double)BRAM_BYTES * REPS, now_us() - t0, a);
}

int main(void)
{
    int fdm = open("/dev/mem", O_RDWR | O_SYNC);
    int fdc = open("/dev/plbram", O_RDWR | O_SYNC);
    uint8_t *dm, *nc;
    uint8_t *heap = malloc(BRAM_BYTES);

    if (fdm < 0) { perror("open /dev/mem"); return 1; }
    dm = mmap(NULL, BRAM_BYTES, PROT_READ|PROT_WRITE, MAP_SHARED, fdm, BRAM_BASE);
    if (dm == MAP_FAILED) { perror("mmap"); return 1; }
    nc = (fdc >= 0) ? mmap(NULL, BRAM_BYTES, PROT_READ|PROT_WRITE, MAP_SHARED, fdc, 0) : NULL;
    if (nc == MAP_FAILED) nc = NULL;
    memset(heap, 0x33, BRAM_BYTES);

    printf("== 读: 单条指令的宽度 (基准 ldr 64bit / ld1 16B / 32B / 64B / 128B每轮) ==\n");
    printf("   [Device /dev/mem  0xB0000000]\n");
    b_read_u64("ldr 64-bit", dm);
    BENCH("ld1 x1   16B/instr", dm, 16, ld1x1_acc);
    BENCH("ld1 x2   32B/instr", dm, 32, ld1x2_acc);
    BENCH("ld1 x4   64B/instr", dm, 64, ld1x4_acc);
    b_128("ld1 x4 twice 128B/iter", dm);
    if (nc) {
        printf("   [Normal-NC /dev/plbram 0xB0000000]\n");
        b_read_u64("ldr 64-bit", nc);
        BENCH("ld1 x1   16B/instr", nc, 16, ld1x1_acc);
        BENCH("ld1 x2   32B/instr", nc, 32, ld1x2_acc);
        BENCH("ld1 x4   64B/instr", nc, 64, ld1x4_acc);
        b_128("ld1 x4 twice 128B/iter", nc);
    }
    printf("   [普通可缓存堆内存 (上限参考)]\n");
    BENCH("ld1 x1   16B/instr", heap, 16, ld1x1_acc);
    BENCH("ld1 x4   64B/instr", heap, 64, ld1x4_acc);

    printf("== 搬运 BRAM -> heap ==\n");
    b_copy64("NEON 64B copy (ld1+st1)", heap, dm);
    b_memcpy("glibc memcpy (现用)", heap, dm);

    printf("== 写 ==\n");
    b_write_plain("Device 64-bit store", dm);
    b_write64("Device st1 x4 64B/instr", dm);
    if (nc) {
        b_write_plain("Normal-NC 64-bit store", nc);
        b_write64("Normal-NC st1 x4 64B/instr", nc);
    }
    return 0;
}
'''


def sh(c, cmd, timeout=120):
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    return o.read().decode(errors="replace"), e.read().decode(errors="replace")


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print("connecting...", flush=True)
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    print("connected", flush=True)

    c.exec_command("mkdir -p %s" % RDIR)[1].read()
    sftp = c.open_sftp()
    with sftp.open(RDIR + "/_probe_neon.c", "w") as fh:
        fh.write(SRC)
    sftp.close()
    print("uploaded", flush=True)

    print("\n===== 读宽度 / 指令数 实验 =====", flush=True)
    out, err = sh(c, "cd %s && gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE "
                     "_probe_neon.c -o _probe_neon 2>&1 && echo BUILD_OK && "
                     "./_probe_neon 2>&1; echo RC=$?; echo '--- disasm (找 ld1) ---'; "
                     "objdump -d _probe_neon | grep -cE 'ld1\\s+\\{v0.16b, v1.16b, v2.16b, v3.16b\\}'; "
                     "objdump -d _probe_neon | grep -E 'ld1' | head -6" % RDIR, 900)
    print(out.rstrip(), flush=True)
    if err.strip():
        print("[stderr] " + err.strip()[:2000], flush=True)
    c.close()
    print("\nRECON2_DONE", flush=True)


if __name__ == "__main__":
    main()
