# -*- coding: utf-8 -*-
"""AI 识别提速调研 —— 第 1 步: 板子现状体检 + 访存天花板微基准。

只读 + 微基准, 不改任何东西。产出全部打印到 stdout (由 .bat 重定向到日志)。

用法: python _prof_recon.py
"""
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
RDIR = "/root/tcp_image_rx/ai_c"

SRC = r'''
/* 访存天花板探针: 在同一片 PL BRAM 上比较
 *   - 顺序读 vs 多流交错读(软件流水, 看能不能拿到 MLP)
 *   - /dev/mem(Device) vs /dev/plbram(Normal-NC)
 *   - 读 / 写 / memcpy
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define BRAM_BASE  0xB0000000UL
#define BRAM_BYTES (256u * 1024u)

static uint64_t now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000ull + (uint64_t)ts.tv_nsec / 1000ull;
}

static void rep(const char *tag, unsigned words, uint64_t us, uint64_t acc)
{
    double per = (double)us * 1000.0 / (double)words;      /* ns per 16B access */
    double bw  = (double)words * 16.0 / (double)us;        /* MB/s */
    printf("  %-26s %8llu us | %7.1f ns/16B | %8.2f MB/s | chk=%llx\n",
           tag, (unsigned long long)us, per, bw,
           (unsigned long long)acc);
}

/* ---- 顺序读 ---- */
static void bench_seq(const char *tag, uint8_t *p, unsigned words, unsigned reps)
{
    volatile uint64_t *q = (volatile uint64_t *)p;
    unsigned r, i;
    uint64_t a = 0, t0;
    unsigned n64 = words * 2u;                 /* 16B = 2 x 64bit */

    t0 = now_us();
    for (r = 0; r < reps; r++) {
        for (i = 0; i < n64; i += 2) {
            a ^= q[i] ^ q[i + 1];
        }
    }
    rep(tag, words * reps, now_us() - t0, a);
}

/* ---- N 路交错读 (同一个循环里发 N 个独立地址的 load) ---- */
#define MK_STREAMS(N)                                                        \
static void bench_s##N(const char *tag, uint8_t *p, unsigned words, unsigned reps) \
{                                                                            \
    volatile uint64_t *q = (volatile uint64_t *)p;                           \
    size_t stride = (size_t)(words / N) * 2u;   /* in 64-bit units */        \
    unsigned r, i;                                                           \
    uint64_t a0 = 0, a1 = 0, a2 = 0, a3 = 0, a4 = 0, a5 = 0, a6 = 0, a7 = 0; \
    uint64_t t0;                                                             \
    t0 = now_us();                                                           \
    for (r = 0; r < reps; r++) {                                             \
        for (i = 0; i + 1 < (unsigned)stride; i += 2) {                      \
            if (N > 0) a0 ^= q[i]     ^ q[i + 1];                            \
            if (N > 1) a1 ^= q[stride + i] ^ q[stride + i + 1];              \
            if (N > 2) a2 ^= q[2*stride + i] ^ q[2*stride + i + 1];          \
            if (N > 3) a3 ^= q[3*stride + i] ^ q[3*stride + i + 1];          \
            if (N > 4) a4 ^= q[4*stride + i] ^ q[4*stride + i + 1];          \
            if (N > 5) a5 ^= q[5*stride + i] ^ q[5*stride + i + 1];          \
            if (N > 6) a6 ^= q[6*stride + i] ^ q[6*stride + i + 1];          \
            if (N > 7) a7 ^= q[7*stride + i] ^ q[7*stride + i + 1];          \
        }                                                                    \
    }                                                                        \
    a0 ^= a1 ^ a2 ^ a3 ^ a4 ^ a5 ^ a6 ^ a7;                                  \
    if (a0 == 0xdeadbeefull) printf(" ");                                    \
    rep(tag, words * reps, now_us() - t0, a0);                               \
}
MK_STREAMS(2)
MK_STREAMS(4)
MK_STREAMS(8)

static void bench_write(const char *tag, uint8_t *p, unsigned words, unsigned reps)
{
    volatile uint64_t *q = (volatile uint64_t *)p;
    unsigned r, i;
    uint64_t t0;
    unsigned n64 = words * 2u;

    t0 = now_us();
    for (r = 0; r < reps; r++)
        for (i = 0; i < n64; i += 2) { q[i] = 0x0101010101010101ull; q[i+1] = q[i]; }
    rep(tag, words * reps, now_us() - t0, 0);
}

static void bench_memcpy(const char *tag, uint8_t *dst, uint8_t *src,
                         unsigned bytes, unsigned reps)
{
    unsigned r;
    uint64_t t0 = now_us();

    for (r = 0; r < reps; r++) {
        memcpy(dst, src, bytes);
    }
    rep(tag, (bytes / 16u) * reps, now_us() - t0, (uint64_t)dst[0]);
}

int main(void)
{
    int fdm = open("/dev/mem", O_RDWR | O_SYNC);
    int fdc = open("/dev/plbram", O_RDWR | O_SYNC);
    uint8_t *dm, *nc;
    uint8_t *heap = malloc(BRAM_BYTES);
    unsigned words = BRAM_BYTES / 16u;      /* 16384 */
    unsigned reps = 8;

    if (fdm < 0) { perror("open /dev/mem"); return 1; }
    dm = mmap(NULL, BRAM_BYTES, PROT_READ | PROT_WRITE, MAP_SHARED, fdm, BRAM_BASE);
    if (dm == MAP_FAILED) { perror("mmap bram"); return 1; }
    if (fdc >= 0) {
        nc = mmap(NULL, BRAM_BYTES, PROT_READ | PROT_WRITE, MAP_SHARED, fdc, 0);
        if (nc == MAP_FAILED) { printf("[warn] /dev/plbram mmap failed: %s\n", strerror(errno)); nc = NULL; }
    } else {
        nc = NULL;
        printf("[info] no /dev/plbram (module not loaded) -- Normal-NC tests skipped\n");
    }
    memset(heap, 0x5a, BRAM_BYTES);

    printf("== A) /dev/mem (Device 强序) ==\n");
    bench_seq  ("seq 1 stream", dm, words, reps);
    bench_s2   ("interleave 2", dm, words, reps);
    bench_s4   ("interleave 4", dm, words, reps);
    bench_s8   ("interleave 8", dm, words, reps);
    bench_write("write (16B store)", dm, words, reps);
    bench_memcpy("memcpy BRAM->heap", heap, dm, BRAM_BYTES, reps);

    if (nc != NULL) {
        printf("== B) /dev/plbram (Normal-NC 写合并) ==\n");
        bench_seq  ("seq 1 stream", nc, words, reps);
        bench_s2   ("interleave 2", nc, words, reps);
        bench_s4   ("interleave 4", nc, words, reps);
        bench_s8   ("interleave 8", nc, words, reps);
        bench_write("write (16B store)", nc, words, reps);
        bench_memcpy("memcpy BRAM->heap", heap, nc, BRAM_BYTES, reps);
    }
    printf("== C) 参考: 普通可缓存堆内存 ==\n");
    bench_seq("seq 1 stream", heap, words, reps);
    bench_s4 ("interleave 4", heap, words, reps);

    (void)words;
    return 0;
}
'''

RECON = r'''
echo "--- uname/uptime ---"; uname -a; uptime
echo "--- fpga_manager ---"; cat /sys/class/fpga_manager/fpga0/state 2>&1
echo "--- PL 指纹 (0xA0000010 应为 0x494D5003) ---"
python3 - <<'EOF'
import mmap, struct
fd = __import__('os').open('/dev/mem', __import__('os').O_RDWR | __import__('os').O_SYNC)
def r(b, o=0):
    m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=b)
    v = struct.unpack_from('<I', m, o)[0]; m.close(); return v
print('img_proc VERSION = 0x%08X' % r(0xA0000000, 0x10))
print('conv ctrl  0xA0010000 = 0x%08X' % r(0xA0010000))
print('elt  ctrl  0xA0020000 = 0x%08X' % r(0xA0020000))
print('BRAM  0xB0000000 = 0x%08X' % r(0xB0000000))
EOF
echo "--- service ---"; systemctl is-active tcp-image-server; systemctl is-enabled tcp-image-server
systemctl cat tcp-image-server 2>/dev/null | grep -E "ExecStart|WorkingDirectory"
echo "--- plbram ---"; systemctl is-active plbram.service 2>&1; ls -l /dev/plbram 2>&1; lsmod | grep -E "plbram|fpga"
echo "--- 开机自启单元 ---"; ls /etc/systemd/system/ | grep -iE "ai|unet|tcp|plbram|bitstream"
echo "--- dma 引擎 ---"; ls /sys/class/dma/ 2>&1 | head -30; echo "count=$(ls /sys/class/dma/ 2>/dev/null | wc -l)"
cat /sys/class/dma/dma0chan0/device/uevent 2>/dev/null | head -6
echo "--- dmesg dma ---"; dmesg 2>/dev/null | grep -iE "dma|pl330|zynqmp" | tail -15
echo "--- 时钟 ---"; grep -E "pl0_ref|pl1_ref|dp_apb|dp_video" /sys/kernel/debug/clk/clk_summary 2>/dev/null | head -10
echo "--- PL 业务程序 ---"; ls -l /root/tcp_image_rx/tcp_image_server /root/tcp_image_rx/*.log 2>&1 | head
'''


def sh(c, cmd, timeout=120):
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    return o.read().decode(errors="replace"), e.read().decode(errors="replace")


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    print("### SSH OK -> %s" % HOST, flush=True)

    print("\n===== 0) 板子现状 =====", flush=True)
    out, err = sh(c, RECON, 180)
    print(out.rstrip(), flush=True)
    if err.strip():
        print("[stderr] " + err.strip()[:2000], flush=True)

    sh(c, "mkdir -p %s" % RDIR)
    sftp = c.open_sftp()
    with sftp.open(RDIR + "/_probe_mem.c", "w") as fh:
        fh.write(SRC)
    sftp.close()

    print("\n===== 1) 访存天花板微基准 =====", flush=True)
    out, err = sh(c, "cd %s && gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE "
                     "_probe_mem.c -o _probe_mem 2>&1 && echo BUILD_OK && "
                     "./_probe_mem 2>&1; echo RC=$?" % RDIR, 900)
    print(out.rstrip(), flush=True)
    if err.strip():
        print("[stderr] " + err.strip()[:2000], flush=True)

    c.close()
    print("\nRECON_DONE", flush=True)


if __name__ == "__main__":
    main()
