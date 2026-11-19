/* axilproc.c -- see axilproc.h */
#include "axilproc.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>

#define R_CTRL    0x00u
#define R_STATUS  0x04u
#define R_IN      0x08u
#define R_OUT     0x0Cu
#define R_VER     0x10u
#define R_LEVEL   0x14u   /* VERSION 4 only: output pixels not yet read */

#define ST_IN_READY   (1u << 0)
#define ST_OUT_FULL   (1u << 1)
#define ST_DONE       (1u << 3)
#define ST_ERR_MASK   (3u << 4)   /* VERSION 4: [4] read-empty, [5] overflow (sticky) */
#define CTRL_STAT_ONLY (1u << 3)  /* VERSION 4: this pass only gathers min/max */

#define MAP_SIZE      0x1000UL
#define SPIN_LIMIT    20000000UL

/* 2026-10-01 加速: 原来每个像素都先读一次 STATUS 再访存(2 次 AXI 事务/像素)。
 * 其实 **AXI 写 IN_PIX 只有在 PL 真的收下时才会完成**(否则背压住 CPU),
 * 读 OUT_PIX 也一样 —— PL 的产出一个像素只要 4 个周期, 比 PS 的访存快得多,
 * 所以完全可以"盲"访存。这里改成每 POLL_STRIDE 个像素才复核一次 STATUS,
 * 把 AXI 事务数从 2/像素降到 ~1/像素, 往返耗时几乎减半; 同时保留卡死检测。 */
#define POLL_STRIDE   64u

static volatile uint32_t *g_reg;
static void   *g_map;
static int     g_fd = -1;
static unsigned g_min, g_max;
static uint32_t g_ver;
static uint32_t g_words[AXIL_WORDS];   /* VERSION 4: grey words, pushed once or twice */

static inline uint32_t r32(uint32_t off) { return g_reg[off >> 2]; }
static inline void     w32(uint32_t off, uint32_t v) { g_reg[off >> 2] = v; }

/* RGB -> 灰度, 与 PL 里 img_proc_axis 的 (77R + 150G + 29B) >> 8 完全一致 */
static inline uint8_t rgb_gray(const uint8_t *p)
{
    return (uint8_t)(((77u * p[0]) + (150u * p[1]) + (29u * p[2])) >> 8);
}

int axilproc_init(uint64_t base)
{
    uint32_t ver;

    if (g_reg != NULL) {
        return 0;
    }
    g_fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (g_fd < 0) {
        printf("[axil] open /dev/mem: %s\n", strerror(errno));
        return -1;
    }
    g_map = mmap(NULL, MAP_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED,
                 g_fd, (off_t)base);
    if (g_map == MAP_FAILED) {
        printf("[axil] mmap 0x%llX: %s\n",
               (unsigned long long)base, strerror(errno));
        close(g_fd);
        g_fd = -1;
        return -1;
    }
    g_reg = (volatile uint32_t *)g_map;
    g_ver = 0;

    ver = r32(R_VER);
    if (ver != AXIL_VERSION && ver != AXIL_VERSION_LB) {
        printf("[axil] VERSION=0x%08X, expected 0x%08X or 0x%08X -- PL not loaded?\n",
               ver, AXIL_VERSION, AXIL_VERSION_LB);
        munmap(g_map, MAP_SIZE);
        close(g_fd);
        g_map = NULL;
        g_fd = -1;
        g_reg = NULL;
        return -2;
    }
    g_ver = ver;
    printf("[axil] register file at 0x%llX, VERSION 0x%08X ok (4 px/word, %s)\n",
           (unsigned long long)base, ver,
           ver == AXIL_VERSION_LB ? "line buffer, row-interleaved" : "frame buffer");
    return 0;
}

void axilproc_close(void)
{
    if (g_map != NULL) {
        munmap(g_map, MAP_SIZE);
        g_map = NULL;
    }
    if (g_fd >= 0) {
        close(g_fd);
        g_fd = -1;
    }
    g_reg = NULL;
    g_ver = 0;
}

int axilproc_ready(void) { return g_reg != NULL; }

uint32_t axilproc_version(void) { return g_reg != NULL ? g_ver : 0u; }

void axilproc_get_minmax(unsigned *mn, unsigned *mx)
{
    if (mn) *mn = g_min;
    if (mx) *mx = g_max;
}

/* ---------------- VERSION 4: line-buffer PL (rtl_lb/) ----------------
 * Output trails input by one row, and the output FIFO holds a bit over one row, so
 * the frame goes row by row: write row r (128 words), read row r-1; after row 511 the
 * PL flushes row 511 by itself.  Writes never stall for long: the PL takes a word in
 * 8 cycles, and its FIFO never holds more than ~1 row because we drain each row before
 * pushing the next.  A read of OUT_PIX waits up to 64 PL cycles for a word and then
 * sets the sticky STATUS[4] instead of hanging the bus, so we check it once per frame. */
static int lb_push(uint32_t w0, uint32_t n)
{
    uint32_t w;
    for (w = w0; w < w0 + n; w++) {
        if ((w & (POLL_STRIDE - 1u)) == 0u) {
            unsigned long k = 0;
            while (!(r32(R_STATUS) & ST_IN_READY)) {
                if (++k > SPIN_LIMIT) {
                    printf("[axil] stuck waiting in_ready at word %u\n", w);
                    return -2;
                }
            }
        }
        w32(R_IN, g_words[w]);
    }
    return 0;
}

static int lb_read_row(uint32_t r, int color, uint8_t *rgb_out)
{
    unsigned long k = 0;
    uint8_t *q = rgb_out + (size_t)r * 512u * 3u;
    uint32_t w;

    while ((r32(R_LEVEL) & 0xFFFu) < 512u) {
        if (++k > SPIN_LIMIT) {
            printf("[axil] stuck waiting for output row %u\n", r);
            return -3;
        }
    }
    if (color) {
        for (w = 0; w < 512u; w++, q += 3) {
            uint32_t v = r32(R_OUT);
            q[0] = (uint8_t)((v >> 16) & 0xFFu);   /* R */
            q[1] = (uint8_t)((v >> 8) & 0xFFu);    /* G */
            q[2] = (uint8_t)(v & 0xFFu);           /* B */
        }
    } else {
        for (w = 0; w < 128u; w++, q += 12) {
            uint32_t v = r32(R_OUT);
            q[0] = q[1] = q[2] = (uint8_t)(v & 0xFFu);
            q[3] = q[4] = q[5] = (uint8_t)((v >> 8) & 0xFFu);
            q[6] = q[7] = q[8] = (uint8_t)((v >> 16) & 0xFFu);
            q[9] = q[10] = q[11] = (uint8_t)((v >> 24) & 0xFFu);
        }
    }
    return 0;
}

static int axilproc_frame_lb(const uint8_t *rgb_in, uint8_t *rgb_out, int mode, int thr)
{
    const uint32_t ctrl = (uint32_t)((mode & 7) | ((thr & 0xFF) << 8));
    const int color = AXIL_MODE_IS_COLOR(mode);
    uint32_t w, r, st;
    int rc;

    for (w = 0; w < AXIL_WORDS; w++) {
        const uint8_t *p = rgb_in + (size_t)w * 12u;
        g_words[w] = ((uint32_t)rgb_gray(p + 0))
                   | ((uint32_t)rgb_gray(p + 3)  << 8)
                   | ((uint32_t)rgb_gray(p + 6)  << 16)
                   | ((uint32_t)rgb_gray(p + 9)  << 24);
    }

    /* modes 0 / 4 normalise with the whole frame's min/max: a statistics-only pass first */
    if ((mode & 7) == 0 || (mode & 7) == 4) {
        unsigned long k = 0;
        w32(R_CTRL, ctrl | CTRL_STAT_ONLY);
        rc = lb_push(0, AXIL_WORDS);
        if (rc != 0) {
            return rc;
        }
        while (!(r32(R_STATUS) & ST_DONE)) {
            if (++k > SPIN_LIMIT) {
                printf("[axil] statistics pass never finished\n");
                return -3;
            }
        }
    }

    w32(R_CTRL, ctrl);
    for (r = 0; r < 512u; r++) {
        rc = lb_push(r * 128u, 128u);
        if (rc != 0) {
            return rc;
        }
        if (r != 0u) {
            rc = lb_read_row(r - 1u, color, rgb_out);
            if (rc != 0) {
                return rc;
            }
        }
    }
    rc = lb_read_row(511u, color, rgb_out);
    if (rc != 0) {
        return rc;
    }

    st = r32(R_STATUS);
    g_min = (st >> 8) & 0xFFu;
    g_max = (st >> 16) & 0xFFu;
    if (st & ST_ERR_MASK) {
        printf("[axil] frame error: STATUS=0x%08X (read-empty/overflow)\n", st);
        return -4;
    }
    return 0;
}

int axilproc_frame(const uint8_t *rgb_in, uint8_t *rgb_out, int mode, int thr)
{
    uint32_t w;

    if (g_reg == NULL) {
        return -1;
    }
    if (g_ver == AXIL_VERSION_LB) {
        return axilproc_frame_lb(rgb_in, rgb_out, mode, thr);
    }

    /* writing CTRL latches the mode/threshold and restarts the frame counter */
    w32(R_CTRL, (uint32_t)((mode & 7) | ((thr & 0xFF) << 8)));

    /* ---- 推: 一次 4 个像素(灰度), 盲写 + 每 POLL_STRIDE 个字复核一次 ---- */
    for (w = 0; w < AXIL_WORDS; w++) {
        const uint8_t *p = rgb_in + (size_t)w * 12u;
        uint32_t word = ((uint32_t)rgb_gray(p + 0))
                      | ((uint32_t)rgb_gray(p + 3)  << 8)
                      | ((uint32_t)rgb_gray(p + 6)  << 16)
                      | ((uint32_t)rgb_gray(p + 9)  << 24);
        if ((w & (POLL_STRIDE - 1u)) == 0u) {
            unsigned long k = 0;
            while (!(r32(R_STATUS) & ST_IN_READY)) {
                if (++k > SPIN_LIMIT) {
                    printf("[axil] stuck waiting in_ready at word %u\n", w);
                    return -2;
                }
            }
        }
        w32(R_IN, word);
    }

    /* ---- 拉 ----
     *   灰度模式(0/1/2): 一个字 = 4 个结果(各取低字节) -> 展开成 (g,g,g)
     *   伪彩模式(3/4)  : 一个字 = 1 个真彩色像素 {8'h00,R,G,B} -> 直接拆 R/G/B */
    {
        unsigned long k = 0;
        while (!(r32(R_STATUS) & ST_OUT_FULL)) {
            if (++k > SPIN_LIMIT) {
                printf("[axil] stuck waiting out_full at word 0\n");
                return -3;
            }
        }
    }
    if (AXIL_MODE_IS_COLOR(mode)) {
        for (w = 0; w < AXIL_IMG_PIXELS; w++) {
            uint32_t v;
            uint8_t *q;
            if (w != 0u && (w & (POLL_STRIDE - 1u)) == 0u) {
                unsigned long k = 0;
                while (!(r32(R_STATUS) & ST_OUT_FULL)) {
                    if (++k > SPIN_LIMIT) {
                        printf("[axil] stuck waiting out_full at px %u\n", w);
                        return -3;
                    }
                }
            }
            v = r32(R_OUT);
            q = rgb_out + (size_t)w * 3u;
            q[0] = (uint8_t)((v >> 16) & 0xFFu);   /* R */
            q[1] = (uint8_t)((v >> 8) & 0xFFu);    /* G */
            q[2] = (uint8_t)(v & 0xFFu);           /* B */
        }
    } else {
        for (w = 0; w < AXIL_WORDS; w++) {
            uint32_t v;
            uint8_t *q;
            if (w != 0u && (w & (POLL_STRIDE - 1u)) == 0u) {
                unsigned long k = 0;
                while (!(r32(R_STATUS) & ST_OUT_FULL)) {
                    if (++k > SPIN_LIMIT) {
                        printf("[axil] stuck waiting out_full at word %u\n", w);
                        return -3;
                    }
                }
            }
            v = r32(R_OUT);
            q = rgb_out + (size_t)w * 12u;
            q[0] = q[1] = q[2] = (uint8_t)(v & 0xFFu);           /* 第 1 个像素 */
            q[3] = q[4] = q[5] = (uint8_t)((v >> 8) & 0xFFu);    /* 第 2 个 */
            q[6] = q[7] = q[8] = (uint8_t)((v >> 16) & 0xFFu);   /* 第 3 个 */
            q[9] = q[10] = q[11] = (uint8_t)((v >> 24) & 0xFFu); /* 第 4 个 */
        }
    }

    {
        uint32_t st = r32(R_STATUS);
        g_min = (st >> 8) & 0xFFu;
        g_max = (st >> 16) & 0xFFu;
    }
    return 0;
}
