# -*- coding: utf-8 -*-
"""AI 识别提速调研 —— 第 3b 步: 把"读"的单价彻底钉死。

第 3 步出现一个矛盾:
    glibc memcpy (LDP 16B/指令)            197.7 ns/16B   <- 最快
    单条 ld1 {v0.16b} (16B/指令)           394.8 ns/16B   <- 慢一倍
    单条 ld1 {v0-v3}  (64B/指令)           394.5 ns/16B
    单条 ldr 64bit                         398   ns/16B（=199 ns/8B）

要么"每条访问 198 ns、16B 是上限"(那 memcpy 已经到顶, 只能减访问次数),
要么"多条独立 load 能同时在飞"(那还有 2~8 倍空间, 纯软件能拿到)。
本脚本用 2 / 4 / 8 条**独立的** ld1(各 16B, 背靠背) + 独立累加器来判定。
"""
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
RDIR = "/root/tcp_image_rx/ai_c"

SRC = r'''
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
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
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000ull + (uint64_t)ts.tv_nsec / 1000ull;
}
static void rep(const char *tag, double bytes, uint64_t us, uint64_t acc)
{
    printf("  %-34s %8llu us | %7.2f ns/16B | %8.2f MB/s | chk=%llx\n",
           tag, (unsigned long long)us, us * 1000.0 / (bytes / 16.0),
           bytes / (double)us, (unsigned long long)acc);
}

/* 单条 16B 向量 load (LD1 单寄存器只支持后索引, 所以用 [x],#16) */
static inline uint8x16_t L1(const void *pp, uint8x16_t a)
{ const char *p = (const char *)pp;
  __asm__ __volatile__("ld1 {v0.16b}, [%1], #16\n\teor %0.16b, %0.16b, v0.16b"
                       : "+w"(a), "+r"(p) : : "v0", "memory"); return a; }
/* 2 条独立 16B ld1 */
static inline uint8x16_t L2(const void *pp, uint8x16_t a)
{ const char *p = (const char *)pp;
  __asm__ __volatile__("ld1 {v0.16b}, [%1], #16\n\tld1 {v1.16b}, [%1], #16\n\t"
                       "eor %0.16b, %0.16b, v0.16b\n\teor %0.16b, %0.16b, v1.16b"
                       : "+w"(a), "+r"(p) : : "v0","v1","memory"); return a; }
/* 4 条独立 16B ld1 (64B) */
static inline uint8x16_t L4(const void *pp, uint8x16_t a)
{ const char *p = (const char *)pp;
  __asm__ __volatile__("ld1 {v0.16b}, [%1], #16\n\tld1 {v1.16b}, [%1], #16\n\t"
                       "ld1 {v2.16b}, [%1], #16\n\tld1 {v3.16b}, [%1], #16\n\t"
                       "eor %0.16b, %0.16b, v0.16b\n\teor %0.16b, %0.16b, v1.16b\n\t"
                       "eor %0.16b, %0.16b, v2.16b\n\teor %0.16b, %0.16b, v3.16b"
                       : "+w"(a), "+r"(p) : : "v0","v1","v2","v3","memory"); return a; }
/* 8 条独立 16B ld1 (128B) */
static inline uint8x16_t L8(const void *pp, uint8x16_t a)
{ const char *p = (const char *)pp;
  __asm__ __volatile__("ld1 {v0.16b}, [%1], #16\n\tld1 {v1.16b}, [%1], #16\n\t"
                       "ld1 {v2.16b}, [%1], #16\n\tld1 {v3.16b}, [%1], #16\n\t"
                       "ld1 {v4.16b}, [%1], #16\n\tld1 {v5.16b}, [%1], #16\n\t"
                       "ld1 {v6.16b}, [%1], #16\n\tld1 {v7.16b}, [%1], #16\n\t"
                       "eor %0.16b, %0.16b, v0.16b\n\teor %0.16b, %0.16b, v1.16b\n\t"
                       "eor %0.16b, %0.16b, v2.16b\n\teor %0.16b, %0.16b, v3.16b\n\t"
                       "eor %0.16b, %0.16b, v4.16b\n\teor %0.16b, %0.16b, v5.16b\n\t"
                       "eor %0.16b, %0.16b, v6.16b\n\teor %0.16b, %0.16b, v7.16b"
                       : "+w"(a), "+r"(p) : : "v0","v1","v2","v3","v4","v5","v6","v7","memory"); return a; }
/* 1 条 LDP (16B, 不依赖) */
static inline uint64_t P1(const void *p, uint64_t a)
{ __asm__ __volatile__("ldp x0, x1, [%1]\n\teor %0, %0, x0\n\teor %0, %0, x1"
                       : "+r"(a) : "r"(p) : "x0","x1","memory"); return a; }
/* 4 条 LDP (64B, 不依赖) */
static inline uint64_t P4(const void *p, uint64_t a)
{ __asm__ __volatile__("ldp x0, x1, [%1]\n\tldp x2, x3, [%1, #16]\n\t"
                       "ldp x4, x5, [%1, #32]\n\tldp x6, x7, [%1, #48]\n\t"
                       "eor %0, %0, x0\n\teor %0, %0, x1\n\teor %0, %0, x2\n\t"
                       "eor %0, %0, x3\n\teor %0, %0, x4\n\teor %0, %0, x5\n\t"
                       "eor %0, %0, x6\n\teor %0, %0, x7"
                       : "+r"(a) : "r"(p) : "x0","x1","x2","x3","x4","x5","x6","x7","memory"); return a; }

static inline uint64_t chk64(uint64_t v) { return v; }
static inline uint64_t chkv(uint8x16_t v) { return (uint64_t)vgetq_lane_u8(v, 0); }
#define CHK(x) _Generic((x), uint8x16_t: chkv, default: chk64)(x)

#define BENCH(tag, ptr, step, T, fn, init)                                   \
    do {                                                                     \
        T acc = init; size_t i; unsigned r;                                  \
        uint64_t t0 = now_us();                                              \
        for (r = 0; r < REPS; r++)                                           \
            for (i = 0; i + (step) <= BRAM_BYTES; i += (step))               \
                acc = fn((const char *)(ptr) + i, acc);                      \
        rep(tag, (double)(BRAM_BYTES / (step) * (step)) * REPS,              \
            now_us() - t0, CHK(acc));                                        \
    } while (0)

int main(void)
{
    int fdm = open("/dev/mem", O_RDWR | O_SYNC);
    uint8_t *dm, *heap = malloc(BRAM_BYTES);

    if (fdm < 0) { perror("open"); return 1; }
    dm = mmap(NULL, BRAM_BYTES, PROT_READ|PROT_WRITE, MAP_SHARED, fdm, BRAM_BASE);
    if (dm == MAP_FAILED) { perror("mmap"); return 1; }
    memset(heap, 0x33, BRAM_BYTES);

    printf("== PL BRAM 0xB0000000 (/dev/mem, Device) ==\n");
    BENCH("LDP x1       16B/iter", dm, 16, uint64_t, P1, 0);
    BENCH("LDP x4       64B/iter", dm, 64, uint64_t, P4, 0);
    BENCH("ld1 x1       16B/iter", dm, 16, uint8x16_t, L1, vdupq_n_u8(0));
    BENCH("ld1 x2 indep 32B/iter", dm, 32, uint8x16_t, L2, vdupq_n_u8(0));
    BENCH("ld1 x4 indep 64B/iter", dm, 64, uint8x16_t, L4, vdupq_n_u8(0));
    BENCH("ld1 x8 indep 128B/iter", dm, 128, uint8x16_t, L8, vdupq_n_u8(0));
    {
        unsigned r; uint64_t t0 = now_us();
        for (r = 0; r < REPS; r++) memcpy(heap, dm, BRAM_BYTES);
        rep("glibc memcpy (参考)", (double)BRAM_BYTES * REPS, now_us() - t0, 0);
    }
    printf("== 对手: 普通可缓存堆内存 ==\n");
    BENCH("ld1 x1       16B/iter", heap, 16, uint8x16_t, L1, vdupq_n_u8(0));
    BENCH("ld1 x4 indep 64B/iter", heap, 64, uint8x16_t, L4, vdupq_n_u8(0));
    return 0;
}
'''


def sh(c, cmd, timeout=120):
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    return o.read().decode(errors="replace"), e.read().decode(errors="replace")


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    c.exec_command("mkdir -p %s" % RDIR)[1].read()
    sftp = c.open_sftp()
    with sftp.open(RDIR + "/_probe_mlp.c", "w") as fh:
        fh.write(SRC)
    sftp.close()
    print("uploaded; building...", flush=True)
    out, err = sh(c, "cd %s && gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE "
                     "_probe_mlp.c -o _probe_mlp 2>&1 && echo BUILD_OK && "
                     "./_probe_mlp 2>&1; echo RC=$?" % RDIR, 900)
    print(out.rstrip(), flush=True)
    if err.strip():
        print("[stderr] " + err.strip()[:2000], flush=True)
    c.close()
    print("\nRECON3_DONE", flush=True)


if __name__ == "__main__":
    main()
