/* pp_ai.c -- PS driver for the pp_layer PL post-processing kernel (see pp_ai.h). */
#include "pp_ai.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include "unet_ai.h"

/* register map: ai/build/pp_ip_v3/drivers/pp_layer_v1_0/src/xpp_layer_hw.h */
#define R_AP_CTRL   0x00u
#define R_RETURN    0x10u
#define R_IN        0x18u      /* 64-bit: low, high at +4 */
#define R_OUT       0x24u
#define R_OP        0x30u
#define R_H         0x38u
#define R_W         0x40u
#define R_X0        0x48u
#define R_Y0        0x50u
#define R_CW        0x58u
#define R_CH        0x60u
#define R_K         0x68u
#define R_THR       0x70u
#define R_TN        0x78u
#define R_YA        0x80u
#define R_YB        0x88u
#define R_IN_ROW0   0x90u
#define AP_START    (1u << 0)
#define AP_DONE     (1u << 1)
#define AP_IDLE     (1u << 2)
#define OP_FIRST    16u
#define OP_LAST     32u
#define MAP_SIZE    0x10000u
#define BRAM_WORDS  16384u
#define WORD_BYTES  16u
#define SPIN_LIMIT  200000000ul

static int                g_fd = -1;
static void              *g_map;
static volatile uint32_t *g_reg;

static uint64_t now_us(void)
{
    struct timespec ts;

    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)(ts.tv_nsec / 1000);
}

static inline void wr(uint32_t off, uint32_t v) { g_reg[off >> 2] = v; }
static inline uint32_t rd(uint32_t off) { return g_reg[off >> 2]; }

int pp_firmware_ok(void)
{
    char  line[256], hash[65] = "";
    FILE *p = popen("sha256sum " PP_FIRMWARE " 2>/dev/null", "r");
    FILE *f;
    int   ok = 0;

    if (p == NULL) {
        return 0;
    }
    if (fgets(line, sizeof(line), p) != NULL && strlen(line) >= 64u) {
        memcpy(hash, line, 64u);
        hash[64] = '\0';
    }
    pclose(p);
    f = fopen(PP_FIRMWARE_LIST, "r");
    if (hash[0] == '\0' || f == NULL) {
        if (f != NULL) {
            fclose(f);
        }
        return 0;
    }
    while (fgets(line, sizeof(line), f) != NULL) {
        if (strncmp(line, hash, 64u) == 0) {
            ok = 1;
        }
    }
    fclose(f);
    return ok;
}

int pp_init(uint64_t ctrl_base)
{
    uint32_t v;

    if (g_reg != NULL) {
        return 0;
    }
    if (unet_ready() == 0 || unet_bram_ptr() == NULL) {
        printf("[pp] needs unet_init() first (shared tensor BRAM)\n");
        return -1;
    }
    g_fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (g_fd < 0) {
        printf("[pp] open /dev/mem: %s\n", strerror(errno));
        return -1;
    }
    g_map = mmap(NULL, MAP_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, g_fd, (off_t)ctrl_base);
    if (g_map == MAP_FAILED) {
        printf("[pp] mmap 0x%llX: %s\n", (unsigned long long)ctrl_base, strerror(errno));
        close(g_fd);
        g_fd = -1;
        g_map = NULL;
        return -1;
    }
    g_reg = (volatile uint32_t *)g_map;
    v = rd(R_AP_CTRL);                         /* read once before any write (handoff §9.6 ③) */
    if ((v & AP_IDLE) == 0u) {
        printf("[pp] ap_idle=0 (CTRL=0x%08X): not the pp_layer bitstream?\n", v);
        pp_close();
        return -1;
    }
    printf("[pp] pp_layer ready @0x%llX\n", (unsigned long long)ctrl_base);
    return 0;
}

int pp_ready(void) { return g_reg != NULL; }

void pp_close(void)
{
    if (g_map != NULL && g_map != MAP_FAILED) {
        munmap(g_map, MAP_SIZE);
    }
    if (g_fd >= 0) {
        close(g_fd);
    }
    g_map = NULL;
    g_reg = NULL;
    g_fd = -1;
}

size_t pp_mask_row_bytes(int ow) { return (size_t)((ow + 127) / 128) * WORD_BYTES; }

/* one kernel call; the logits of frame rows [in_row0, ..) are already at BRAM word 0, results go to out_word */
static int run_kernel(unsigned op, int h, int w, int x0, int y0, int cw, int ch, int k, int thr, int tn,
                      int ya, int yb, int in_row0, uint32_t out_word, uint32_t *pl_us)
{
    const uint64_t in_pa = unet_bram_phys(), out_pa = in_pa + (uint64_t)out_word * WORD_BYTES;
    unsigned long  spin = 0;
    uint64_t       t0;
    uint32_t       ret;

    wr(R_IN, (uint32_t)in_pa);
    wr(R_IN + 4u, (uint32_t)(in_pa >> 32));
    wr(R_OUT, (uint32_t)out_pa);
    wr(R_OUT + 4u, (uint32_t)(out_pa >> 32));
    wr(R_OP, op);
    wr(R_H, (uint32_t)h);
    wr(R_W, (uint32_t)w);
    wr(R_X0, (uint32_t)x0);
    wr(R_Y0, (uint32_t)y0);
    wr(R_CW, (uint32_t)cw);
    wr(R_CH, (uint32_t)ch);
    wr(R_K, (uint32_t)k);
    wr(R_THR, (uint32_t)thr);
    wr(R_TN, (uint32_t)tn);
    wr(R_YA, (uint32_t)ya);
    wr(R_YB, (uint32_t)yb);
    wr(R_IN_ROW0, (uint32_t)in_row0);
    t0 = now_us();
    wr(R_AP_CTRL, AP_START);
    while ((rd(R_AP_CTRL) & AP_DONE) == 0u) {
        if (++spin > SPIN_LIMIT) {
            printf("[pp] TIMEOUT waiting ap_done\n");
            return -2;
        }
    }
    *pl_us += (uint32_t)(now_us() - t0);
    ret = rd(R_RETURN);
    return (ret & 4u) != 0u ? -3 : 0;
}

/* Normal-NC writes may still sit in the write buffer when AP_START goes out on the other port:
 * read back the last word written (same master, same slave: ordered), as unet_ai.c bram_commit(). */
static void bram_commit(uint32_t word)
{
    volatile const uint32_t *p = (volatile const uint32_t *)(const void *)(unet_bram_ptr() + (size_t)word * WORD_BYTES);

    (void)p[0];
}

int pp_run(const int8_t *q, int h, int w, unsigned op, int x0, int y0, int cw, int ch, int k, int thr, int tn,
           uint8_t *mask, pp_result_t *res)
{
    uint8_t       *bram = unet_bram_ptr();
    const int      oh = ch * k, ow = cw * k, stride = w / 16;
    const uint32_t orow = (uint32_t)((ow + 127) / 128);
    const size_t   mrow = pp_mask_row_bytes(ow);
    uint64_t       t0 = now_us();
    int            rows, ya, rc = 0;
    uint8_t        word0[5 * WORD_BYTES];

    if (g_reg == NULL || q == NULL || res == NULL || bram == NULL || w % 16 != 0 || h < 1 || h > 512 ||
        w > 512 || (k != 1 && k != 2 && k != 4) || ((op & PP_OP_MASK) != 0u && mask == NULL)) {
        return -1;
    }
    memset(res, 0, sizeof(*res));
    op &= PP_OP_MASK | PP_OP_CLEAN | PP_OP_CONN8 | PP_OP_TILES;
    if ((uint32_t)h * (uint32_t)stride + 8u + (uint32_t)oh * orow <= BRAM_WORDS) {
        rows = oh;                                  /* whole frame in one call */
    } else {
        if (k != 1 || (op & PP_OP_CLEAN) != 0u) {
            return -1;
        }
        rows = (int)((BRAM_WORDS - 8u) / ((uint32_t)stride + orow));
    }
    for (ya = 0; ya < oh && rc == 0; ya += rows) {
        const int      yb = (ya + rows < oh) ? ya + rows : oh;
        const int      whole = (ya == 0 && yb == oh);
        const int      r0 = whole ? 0 : y0 + ya;           /* first frame row in the BRAM */
        const int      nr = whole ? h : yb - ya;
        const uint32_t out_word = (uint32_t)nr * (uint32_t)stride;
        const unsigned f = op | (ya == 0 ? OP_FIRST : 0u) | (yb == oh ? OP_LAST : 0u);

        memcpy(bram, q + (size_t)r0 * (size_t)w, (size_t)nr * (size_t)w);
        bram_commit(out_word - 1u);
        rc = run_kernel(f, h, w, x0, y0, cw, ch, k, thr, tn, ya, yb, r0, out_word, &res->pl_us);
        res->calls++;
        if (rc == 0 && (op & PP_OP_MASK) != 0u) {
            memcpy(mask + (size_t)ya * mrow, bram + ((size_t)out_word + 8u) * WORD_BYTES, (size_t)(yb - ya) * mrow);
        }
        if (rc == 0 && yb == oh) {
            int i;

            memcpy(word0, bram + (size_t)out_word * WORD_BYTES, sizeof(word0));
            memcpy(&res->count, word0, 4u);
            memcpy(&res->comps, word0 + 4, 4u);
            memcpy(&res->largest, word0 + 8, 4u);
            memcpy(&res->status, word0 + 12, 4u);
            for (i = 0; i < 16; i++) {
                memcpy(&res->tiles[i], word0 + WORD_BYTES + 4 * i, 4u);
            }
        }
    }
    res->total_us = (uint32_t)(now_us() - t0);
    return rc;
}
